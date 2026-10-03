#!/usr/bin/env bash
# Virtual DKMS layer: do the Realtek out-of-tree drivers COMPILE and LINK against the REAL Raspberry Pi kernel
# headers (Pi OS bookworm 6.12.x and trixie 6.18.x; flavours rpi-v8 = Pi 4/4K pages, rpi-2712 = Pi 5/16K pages),
# and (rpi-v8 only, best effort) do they LOAD into the real Pi kernel booted under QEMU raspi3b?
#
# Proves: build + link + modpost symbol resolution + vermagic + CRC (modversions) agreement with the shipped kernel,
#         insmod accepted by the real kernel (module init path up to usb_register, no device).
# Does NOT prove: USB probe, firmware/EEPROM, monitor mode, injection, timing, any 16K-page RUNTIME behaviour.
#
# Modes (see docs/SIM-DKMS.md):
#   --check   offline: manifest, expectation table, patch series and patch syntax (no network, no root, <1 s)
#   --fetch   download .debs + git sources into $WORK, record/verify sha256 in manifest.txt (network)
#   --build   cross-build every driver x kernel, vermagic + CRC checks, compare with expect.txt
#   --load    boot the real v8 kernels in QEMU raspi3b, insmod the built modules, capture dmesg
#   --all     --fetch + --build + --load
# Env: ONLY=<driver id> (build just one), NOPATCH=1 (build the pristine pins: A/B evidence for patches/), SIM_DKMS_WORK (default ${TMPDIR:-/tmp}/sim-dkms, ~3 GB), JOBS (default nproc), KEEP=1 (keep build trees).
# Root is NOT needed (everything lives in $WORK; apt is needed once to install the packages listed by --build).
# Exit: 0 pass (results match expect.txt), 1 failure/mismatch, 2 usage, 77 prerequisite missing (nothing tested).
set -u -o pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${SIM_DKMS_WORK:-${TMPDIR:-/tmp}/sim-dkms}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 2)}"
MANIFEST="$HERE/manifest.txt"
EXPECT="$HERE/expect.txt"
SERIES="$HERE/patches/series"
RPI=https://archive.raspberrypi.com/debian
DEB=https://deb.debian.org/debian

die() { echo "FAIL sim-dkms: $*" >&2; exit 1; }
skip() { echo "SKIP sim-dkms: $*" >&2; exit 77; }
note() { echo "sim-dkms: $*"; }

# ---------------------------------------------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------------------------------------------
# kernel id | release | flavour | cross-gcc suffix (the compiler Pi OS built that kernel with, .kernelvariables) | page size
KERNELS=(
	"k612v8|6.12.109+rpt|rpi-v8|12|4K"
	"k612p5|6.12.109+rpt|rpi-2712|12|16K"
	"k618v8|6.18.50+rpt|rpi-v8|14|4K"
	"k618p5|6.18.50+rpt|rpi-2712|14|16K"
)
# release -> Debian package version
declare -A PKGVER=( ["6.12.109+rpt"]="6.12.109-1+rpt1" ["6.18.50+rpt"]="6.18.50-1+rpt1" )
# driver id | git url | pinned commit | module file built
GITS=(
	"rtl8812au|https://github.com/svpcom/rtl8812au.git|6e75916416de1dce5ecd37f824896bebf96aaf8f|88XXau_wfb.ko"
	"8814au|https://github.com/morrownr/8814au.git|1840d7b23bf2350a3e9e22448a93c251d2fec73c|8814au.ko"
	"rtl8812eu|https://github.com/svpcom/rtl8812eu.git|48e6e449e089fa954e4e15079bd864039e2960da|8812eu.ko"
	"rtl88x2eu|https://github.com/libc0607/rtl88x2eu-20230815.git|12977e4013ecafbae993b3b08d46bff87d6d7c67|8812eu.ko"
)
# arm64 userspace libs for the prebuilt (aarch64) kbuild host tools, and the guest busybox
DEBIAN_FILES=(
	"pool/main/b/bzip2/libbz2-1.0_1.0.8-6_arm64.deb"
	"pool/main/e/elfutils/libelf1t64_0.192-4_arm64.deb"
	"pool/main/libz/libzstd/libzstd1_1.5.7+dfsg-1_arm64.deb"
	"pool/main/o/openssl/libssl3t64_3.5.7-1~deb13u2_arm64.deb"
	"pool/main/x/xz-utils/liblzma5_5.8.1-1+deb13u1_arm64.deb"
	"pool/main/z/zlib/zlib1g_1.3.dfsg+really1.3.1-1+b1_arm64.deb"
	"pool/main/b/busybox/busybox-static_1.37.0-6+b9_arm64.deb"
)

all_urls() {
	local rel v f
	for rel in "${!PKGVER[@]}"; do
		v="${PKGVER[$rel]}"
		echo "$RPI/pool/main/l/linux/linux-headers-$rel-common-rpi_${v}_all.deb"
		echo "$RPI/pool/main/l/linux/linux-kbuild-${rel}_${v}_arm64.deb"
		for f in rpi-v8 rpi-2712; do
			echo "$RPI/pool/main/l/linux/linux-headers-$rel-${f}_${v}_arm64.deb"
			echo "$RPI/pool/main/l/linux/linux-image-$rel-${f}_${v}_arm64.deb"
		done
	done | sort
	for f in "${DEBIAN_FILES[@]}"; do echo "$DEB/$f"; done
}

# ---------------------------------------------------------------------------------------------------------------
# --check: offline validation
# ---------------------------------------------------------------------------------------------------------------
do_check() {
	local bad=0 n=0 url line f urls gits
	urls="$(all_urls)"; gits="$(printf '%s\n' "${GITS[@]}")"
	err() { echo "check: $*" >&2; bad=1; }
	[ -f "$MANIFEST" ] || { err "manifest.txt missing (run --fetch)"; }
	if [ -f "$MANIFEST" ]; then
		while IFS= read -r url; do
			n=$((n + 1))
			line="$(awk -F'\t' -v u="$url" '$1=="file" && $4==u' "$MANIFEST")"
			[ -n "$line" ] || { err "no manifest entry for $url"; continue; }
			[ "$(printf '%s\n' "$line" | wc -l)" = 1 ] || err "duplicate manifest entry for $url"
			f="$(printf '%s' "$line" | awk -F'\t' '{print $2}')"
			[[ $f =~ ^[0-9a-f]{64}$ ]] || err "bad sha256 for $url"
			f="$(printf '%s' "$line" | awk -F'\t' '{print $3}')"
			[[ $f =~ ^[0-9]+$ ]] && [ "$f" -gt 0 ] || err "bad size for $url"
		done < <(all_urls)
		local g name gurl sha
		for g in "${GITS[@]}"; do
			IFS='|' read -r name gurl sha _ <<<"$g"
			[[ $sha =~ ^[0-9a-f]{40}$ ]] || err "$name: pin in run.sh is not a 40-hex SHA"
			line="$(awk -F'\t' -v u="$gurl" '$1=="git" && $4==u {print $2}' "$MANIFEST")"
			[ "$line" = "$sha" ] || err "$name: manifest git SHA '$line' != run.sh pin '$sha'"
		done
		# no manifest line that the script does not know about (stale entries hide drift)
		while IFS=$'\t' read -r kind _ _ url; do
			case "$kind" in
				file) grep -qxF "$url" <<<"$urls" || err "stale manifest file entry: $url" ;;
				git) grep -qF "|$url|" <<<"$gits" || err "stale manifest git entry: $url" ;;
				\#* | '') ;;
				*) err "unknown manifest kind '$kind'" ;;
			esac
		done < <(grep -v '^#' "$MANIFEST")
	fi
	# expectation table: every driver x kernel exactly once, verdict PASS|FAIL, optional load verdict
	local d k seen
	if [ -f "$EXPECT" ]; then
		for g in "${GITS[@]}"; do
			d="${g%%|*}"
			for k in "${KERNELS[@]}"; do
				k="${k%%|*}"
				seen="$(grep -cE "^${d}[[:space:]]+${k}[[:space:]]+(PASS|FAIL)([[:space:]]|$)" "$EXPECT")"
				[ "$seen" = 1 ] || err "expect.txt: $d $k present $seen times (want 1)"
			done
		done
	else err "expect.txt missing"; fi
	# patches: listed <-> present, parseable by git apply, header names the target driver
	if [ -f "$SERIES" ]; then
		local pname pdrv
		while IFS=$'\t' read -r pname pdrv _; do
			case "$pname" in '' | \#*) continue ;; esac
			[ -f "$HERE/patches/$pname" ] || { err "series lists missing patch $pname"; continue; }
			git apply --numstat "$HERE/patches/$pname" >/dev/null 2>&1 || err "patch $pname does not parse (git apply --numstat)"
			grep -q "^$pdrv|" <<<"$gits" || err "patch $pname: unknown driver '$pdrv'"
			head -5 "$HERE/patches/$pname" | grep -qi '^# *Target:' || err "patch $pname: no '# Target:' header"
			n=$((n + 1))
		done <"$SERIES"
		for f in "$HERE"/patches/*.patch; do
			[ -e "$f" ] || continue
			grep -qE "^$(basename "$f")[[:space:]]" "$SERIES" || err "patch $(basename "$f") is not in patches/series"
		done
	else err "patches/series missing"; fi
	# if sources were fetched, patches must apply cleanly to the pinned trees
	if [ -f "$SERIES" ] && [ -d "$WORK/src" ]; then
		while IFS=$'\t' read -r pname pdrv _; do
			case "$pname" in '' | \#*) continue ;; esac
			[ -d "$WORK/src/$pdrv" ] || continue
			git -C "$WORK/src/$pdrv" apply --check "$HERE/patches/$pname" 2>/dev/null \
				|| git -C "$WORK/src/$pdrv" apply --check -R "$HERE/patches/$pname" 2>/dev/null \
				|| err "patch $pname no longer applies to $pdrv"
		done <"$SERIES"
	fi
	[ "$bad" = 0 ] || exit 1
	note "check OK ($n items: manifest, expect.txt, patches)"
}

# ---------------------------------------------------------------------------------------------------------------
# --fetch
# ---------------------------------------------------------------------------------------------------------------
manifest_get() { awk -F'\t' -v u="$1" '$1=="file" && $4==u {print $2" "$3}' "$MANIFEST" 2>/dev/null; }

do_fetch() {
	for t in curl sha256sum git; do command -v "$t" >/dev/null || skip "$t not installed"; done
	mkdir -p "$WORK/dl" "$WORK/src"
	[ -f "$MANIFEST" ] || printf '# kind\tsha256|git-sha1\tsize\turl   (generated by run.sh --fetch; sha256 of the bytes AS DOWNLOADED)\n' >"$MANIFEST"
	local url base want_sha got_sha got_size new=0 rc
	while IFS= read -r url; do
		base="$WORK/dl/$(basename "$url")"
		read -r want_sha _ <<<"$(manifest_get "$url")"
		if [ -f "$base" ] && [ -n "${want_sha:-}" ] && [ "$(sha256sum "$base" | cut -d' ' -f1)" = "$want_sha" ]; then continue; fi
		rm -f "$base"
		curl -sSfL --retry 3 -m 900 -o "$base.part" "$url"; rc=$?
		if [ "$rc" != 0 ]; then
			rm -f "$base.part"
			case "$rc" in
				22) die "HTTP error fetching $url (host blocked by proxy policy, or the package left the archive)" ;;
				*) die "curl rc=$rc for $url" ;;
			esac
		fi
		mv "$base.part" "$base"
		got_sha="$(sha256sum "$base" | cut -d' ' -f1)"; got_size="$(stat -c %s "$base")"
		if [ -n "${want_sha:-}" ]; then
			[ "$got_sha" = "$want_sha" ] || { rm -f "$base"; die "sha256 mismatch for $url: manifest $want_sha, downloaded $got_sha"; }
		else
			printf 'file\t%s\t%s\t%s\n' "$got_sha" "$got_size" "$url" >>"$MANIFEST"; new=$((new + 1))
		fi
	done < <(all_urls)
	local g name gurl sha mod have
	for g in "${GITS[@]}"; do
		IFS='|' read -r name gurl sha mod <<<"$g"
		have=""
		[ -d "$WORK/src/$name/.git" ] && have="$(git -C "$WORK/src/$name" rev-parse HEAD 2>/dev/null)"
		if [ "$have" != "$sha" ]; then
			rm -rf "$WORK/src/$name"; mkdir -p "$WORK/src/$name"
			( cd "$WORK/src/$name" && git init -q && git fetch -q --depth 1 "$gurl" "$sha" && git checkout -q FETCH_HEAD ) \
				|| die "cannot fetch $gurl @ $sha"
		fi
		[ "$(git -C "$WORK/src/$name" rev-parse HEAD)" = "$sha" ] || die "$name: HEAD != pin $sha"
		grep -q "^git	$sha	-	$gurl\$" "$MANIFEST" || { printf 'git\t%s\t-\t%s\n' "$sha" "$gurl" >>"$MANIFEST"; new=$((new + 1)); }
	done
	note "fetch OK ($new new manifest entries; $(du -sh "$WORK/dl" | cut -f1) in $WORK/dl)"
}

# ---------------------------------------------------------------------------------------------------------------
# --build
# ---------------------------------------------------------------------------------------------------------------
need_tools() {
	local t miss=() pk=()
	for t in make file dpkg-deb xz python3 git modinfo modprobe qemu-aarch64-static awk; do command -v "$t" >/dev/null || miss+=("$t"); done
	for t in 12 14; do command -v "aarch64-linux-gnu-gcc-$t" >/dev/null || pk+=("gcc-$t-aarch64-linux-gnu"); done
	command -v aarch64-linux-gnu-strip >/dev/null || pk+=(binutils-aarch64-linux-gnu)
	[ -d /usr/aarch64-linux-gnu/lib ] || pk+=(libc6-arm64-cross)
	[ ${#miss[@]} = 0 ] && [ ${#pk[@]} = 0 ] && return 0
	echo "missing: ${miss[*]:-} ${pk[*]:-}" >&2
	skip "install (root, once): apt-get install -y make file dpkg kmod xz-utils python3 git qemu-user-static ${pk[*]:-} gcc-12-aarch64-linux-gnu gcc-14-aarch64-linux-gnu"
}

# arm64 sysroot for qemu-user: glibc from the cross toolchain + the few arm64 libs the kbuild host tools link
prep_sysroot() {
	local st="$WORK/.sysroot.ok" f
	[ -f "$st" ] && return 0
	rm -rf "$WORK/sr" "$WORK/x/libs"; mkdir -p "$WORK/sr/lib" "$WORK/x/libs"
	for f in "${DEBIAN_FILES[@]}"; do
		case "$f" in */busybox-static*) continue ;; esac
		dpkg-deb -x "$WORK/dl/$(basename "$f")" "$WORK/x/libs" || die "extract $f"
	done
	cp -a /usr/aarch64-linux-gnu/lib/. "$WORK/sr/lib/"
	cp -a "$WORK/x/libs/usr/lib/aarch64-linux-gnu/." "$WORK/sr/lib/"
	touch "$st"
}

# extract the headers of one kernel release into $WORK/kt/<rel>, make the Debian layout relocatable and wrap the
# prebuilt arm64 kbuild tools (modpost, fixdep, ...) so they run on x86_64 through qemu-user
prep_kernel() {
	local rel="$1" v="${PKGVER[$1]}" st="$WORK/.kt-$1.ok" kt="$WORK/kt/$1" d fl f
	[ -f "$st" ] && return 0
	rm -rf "$kt"; mkdir -p "$kt"
	for f in "linux-headers-$rel-common-rpi_${v}_all" "linux-kbuild-${rel}_${v}_arm64" "linux-headers-$rel-rpi-v8_${v}_arm64" "linux-headers-$rel-rpi-2712_${v}_arm64"; do
		dpkg-deb -x "$WORK/dl/$f.deb" "$kt" || die "extract $f"
	done
	for fl in rpi-v8 rpi-2712; do
		d="$kt/usr/src/linux-headers-$rel-$fl"
		# the flavour Makefile includes the common Makefile by ABSOLUTE path (/usr/src/...) and 6.18 sets KBUILD_OUTPUT likewise
		sed -i "s#^include /usr/src/#include $kt/usr/src/#; s#^KBUILD_OUTPUT=/usr/src/#KBUILD_OUTPUT=$kt/usr/src/#" "$d/Makefile"
	done
	while IFS= read -r -d '' f; do
		if file -b "$f" | grep -q 'ELF 64-bit.*ARM aarch64'; then
			mv "$f" "$f.aarch64"
			# -0 keeps argv[0]: modpost derives the name of its modpost.real-<endian>-<bits> helper from it
			printf '#!/bin/sh\nexec qemu-aarch64-static -0 "$0" -L %s "$0.aarch64" "$@"\n' "$WORK/sr" >"$f"
			chmod +x "$f"
		fi
	done < <(find "$kt/usr/lib" -type f ! -name '*.aarch64' -print0)
	touch "$st"
}

prep_image() { # extract the linux-image deb (config, vmlinuz, dtb, modules) into $WORK/img/<rel>-<flavour>
	local rel="$1" fl="$2" dst="$WORK/img/$1-$2"
	[ -f "$dst/.ok" ] && return 0
	rm -rf "$dst"; mkdir -p "$dst"
	dpkg-deb -x "$WORK/dl/linux-image-$rel-${fl}_${PKGVER[$rel]}_arm64.deb" "$dst" || die "extract image $rel $fl"
	touch "$dst/.ok"
}

apply_patches() { # $1 driver, $2 kernel id, $3 tree
	local pname pdrv pk n=0
	[ -f "$SERIES" ] || return 0
	while IFS=$'\t' read -r pname pdrv pk _; do
		case "$pname" in '' | \#*) continue ;; esac
		[ "$pdrv" = "$1" ] || continue
		# shellcheck disable=SC2254 # pk is deliberately a shell glob over kernel ids, e.g. k618*
		case "$2" in $pk) ;; *) continue ;; esac
		git -C "$3" apply "$HERE/patches/$pname" || return 1
		n=$((n + 1))
	done <"$SERIES"
	echo "$n"
}

vermagic_of() { modinfo -F vermagic "$1" 2>/dev/null; }

build_one() { # $1 driver line  $2 kernel line
	local name gurl sha mod kid rel fl bt log out kt tree n rc warns vm ref imp mis
	IFS='|' read -r name gurl sha mod <<<"$1"
	IFS='|' read -r kid rel fl _ <<<"$2"
	kt="$WORK/kt/$rel"; tree="$kt/usr/src/linux-headers-$rel-$fl"
	bt="$WORK/build/$name-$kid"; log="$WORK/logs/build-$name-$kid.log"; out="$WORK/out/$name-$kid"
	mkdir -p "$WORK/logs" "$WORK/out"
	rm -rf "$bt" "$out"; mkdir -p "$bt" "$out"
	git -C "$WORK/src/$name" archive HEAD | tar -x -C "$bt" || die "archive $name"
	git -C "$bt" init -q 2>/dev/null; git -C "$bt" add -A >/dev/null 2>&1
	n=0; [ "${NOPATCH:-0}" = 1 ] || n="$(apply_patches "$name" "$kid" "$bt")" || { printf '%s\t%s\t99\t0\t0\t-\t-\t-\t-\n' "$name" "$kid" >>"$WORK/results.tsv"; return 1; }
	# exact command a Pi would run via DKMS, plus the cross variables
	( cd "$bt" && nice make "-j$JOBS" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- "KSRC=$tree" "KVER=$rel-$fl" ) >"$log" 2>&1
	rc=$?
	warns="$(grep -c 'warning:' "$log")"
	vm="-"; ref="-"; imp="-"; mis="-"
	if [ -f "$bt/$mod" ]; then
		aarch64-linux-gnu-strip --strip-debug -o "$out/$mod" "$bt/$mod"
		vm="$(vermagic_of "$out/$mod")"
		ref="$(cat "$WORK/img/$rel-$fl/.vermagic")"
		read -r imp mis <<<"$(crc_check "$out/$mod" "$tree/Module.symvers")"
	fi
	printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$kid" "$rc" "$warns" "${n:-0}" "$vm" "$ref" "$imp" "$mis" >>"$WORK/results.tsv"
	[ "$rc" = 0 ]
}

# compare every symbol CRC the module imports (modprobe --dump-modversions) with the headers' Module.symvers
crc_check() { # $1 ko $2 symvers -> "imported mismatches"
	modprobe --dump-modversions "$1" 2>/dev/null | awk -F'\t' 'NR==FNR{crc[$2]=$1; next} {n++; if(!($2 in crc) || crc[$2]!=$1) m++} END{print n+0, m+0}' "$2" -
}

ref_vermagic() { # vermagic + CRC cross-check taken from a module SHIPPED in the linux-image deb (cfg80211)
	local rel="$1" fl="$2" dst="$WORK/img/$1-$2" f
	f="$(find "$dst/usr" "$dst/lib" -name 'cfg80211.ko*' 2>/dev/null | head -1)"
	[ -n "$f" ] || die "no cfg80211 in $dst"
	case "$f" in *.xz) xz -dc "$f" >"$dst/cfg80211.ko" ;; *) cp "$f" "$dst/cfg80211.ko" ;; esac
	vermagic_of "$dst/cfg80211.ko" >"$dst/.vermagic"
	read -r imp mis <<<"$(crc_check "$dst/cfg80211.ko" "$WORK/kt/$rel/usr/src/linux-headers-$rel-$fl/Module.symvers")"
	echo "$imp $mis" >"$dst/.imagecrc"
}

do_build() {
	need_tools
	[ -d "$WORK/dl" ] && [ -d "$WORK/src" ] || skip "nothing fetched yet (run --fetch)"
	local k kid rel fl g line fail=0
	prep_sysroot
	for k in "${KERNELS[@]}"; do
		IFS='|' read -r kid rel fl _ <<<"$k"
		prep_kernel "$rel"; prep_image "$rel" "$fl"; ref_vermagic "$rel" "$fl"
	done
	if [ -n "${ONLY:-}" ] && [ -f "$WORK/results.tsv" ]; then grep -v "^$ONLY	" "$WORK/results.tsv" >"$WORK/results.tsv.new"; mv "$WORK/results.tsv.new" "$WORK/results.tsv"; else : >"$WORK/results.tsv"; fi
	for g in "${GITS[@]}"; do
		[ -z "${ONLY:-}" ] || [ "${g%%|*}" = "$ONLY" ] || continue
		for k in "${KERNELS[@]}"; do
			build_one "$g" "$k" || true
			[ "${KEEP:-0}" = 1 ] || rm -rf "$WORK/build/${g%%|*}-${k%%|*}"
		done
	done
	# compare with expectations
	: >"$WORK/summary.txt"
	while IFS=$'\t' read -r name kid rc warns np vm ref imp mis; do
		local want got vmok="vermagic=$vm"
		want="$(awk -v d="$name" -v k="$kid" '$1==d && $2==k {print $3}' "$EXPECT")"
		got=FAIL; [ "$rc" = 0 ] && got=PASS
		[ "$rc" = 0 ] && { [ "$vm" = "$ref" ] || got=FAIL; [ "${mis:-1}" = 0 ] || got=FAIL; }
		line="$(printf '%-10s %-7s want=%s got=%s warns=%s patches=%s imports=%s crc_mismatch=%s %s' "$name" "$kid" "$want" "$got" "$warns" "${np:-0}" "$imp" "$mis" "$vmok")"
		echo "$line" >>"$WORK/summary.txt"
		[ "$want" = "$got" ] || { echo "MISMATCH $line" >&2; fail=1; }
	done <"$WORK/results.tsv"
	cat "$WORK/summary.txt"
	for k in "${KERNELS[@]}"; do
		IFS='|' read -r kid rel fl _ <<<"$k"
		echo "image $rel-$fl: shipped cfg80211 vermagic='$(cat "$WORK/img/$rel-$fl/.vermagic")' crc imported/mismatch=$(cat "$WORK/img/$rel-$fl/.imagecrc")"
	done
	[ "$fail" = 0 ] || die "results differ from expect.txt (logs: $WORK/logs)"
	note "build OK (all results match expect.txt)"
}

# ---------------------------------------------------------------------------------------------------------------
# --load: real v8 kernels under QEMU raspi3b
# ---------------------------------------------------------------------------------------------------------------
# decompress the kernel-shipped dependencies of a module (recursively) into $2/deps; appends dependency-first names to $2/order
resolve_deps() { # $1 ko, $2 initramfs mods dir, $3 image dir
	local d f
	for d in $(modinfo -F depends "$1" | tr ',' ' '); do
		[ -f "$2/deps/$d.ko" ] && continue
		f="$(find "$3" \( -name "$d.ko*" -o -name "${d//_/-}.ko*" \) ! -path '*/deps/*' ! -name "$d.ko" | head -1)"
		if [ -z "$f" ]; then
			grep -q "/${d//_/[-_]}.ko" "$3"/usr/lib/modules/*/modules.builtin 2>/dev/null || echo "load: dependency $d of $(basename "$1") not found (neither module nor builtin)" >&2
			continue
		fi
		case "$f" in *.xz) xz -dc "$f" >"$2/deps/$d.ko" ;; *) cp "$f" "$2/deps/$d.ko" ;; esac
		resolve_deps "$2/deps/$d.ko" "$2" "$3"
		echo "$d" >>"$2/order"
	done
}

do_load() {
	need_tools
	for t in qemu-system-aarch64 cpio gzip; do command -v "$t" >/dev/null || skip "$t not installed (apt: qemu-system-arm cpio)"; done
	[ -f "$WORK/results.tsv" ] || skip "run --build first"
	local k kid rel fl rd img dtb log g name mod other fail=0 n=0
	for k in "${KERNELS[@]}"; do
		IFS='|' read -r kid rel fl _ <<<"$k"
		[ "$fl" = rpi-v8 ] || continue # rpi-2712 (16K pages) cannot boot on raspi3b (4K only): build-only
		prep_image "$rel" "$fl"
		img="$WORK/img/$rel-$fl"; rd="$WORK/rd/$kid"; log="$WORK/logs/load-$kid.log"
		rm -rf "$rd"; mkdir -p "$rd"/{bin,proc,sys,dev,tmp,mods/deps}; : >"$rd/mods/order"
		mkdir -p "$WORK/x/bb"; dpkg-deb -x "$WORK/dl/busybox-static_1.37.0-6+b9_arm64.deb" "$WORK/x/bb" || die "busybox"
		cp "$WORK/x/bb/usr/bin/busybox" "$rd/bin/busybox"
		for a in sh cat mount insmod rmmod lsmod poweroff uname sed tr cut; do ln -sf busybox "$rd/bin/$a"; done
		for g in "${GITS[@]}"; do
			IFS='|' read -r name _ _ mod <<<"$g"
			[ -f "$WORK/out/$name-$kid/$mod" ] || continue
			cp "$WORK/out/$name-$kid/$mod" "$rd/mods/$name.ko"; n=$((n + 1))
			resolve_deps "$rd/mods/$name.ko" "$rd/mods" "$img"
		done
		[ "$n" -gt 0 ] || skip "no built modules for $kid (run --build)"
		# negative control: a module built for the OTHER release must be refused by the real kernel (vermagic / CRC)
		other=k612v8; [ "$kid" = k612v8 ] && other=k618v8
		[ -f "$WORK/out/rtl8812au-$other/88XXau_wfb.ko" ] && cp "$WORK/out/rtl8812au-$other/88XXau_wfb.ko" "$rd/mods/WRONGKERNEL.ko"
		awk '!s[$0]++' "$rd/mods/order" >"$rd/mods/order.u" && mv "$rd/mods/order.u" "$rd/mods/order"
		cp "$HERE/guest-init.sh" "$rd/init"; chmod +x "$rd/init"
		( cd "$rd" && find . | cpio -o -H newc --quiet | gzip -1 >"$WORK/rd/$kid.gz" ) || die "cpio"
		zcat "$img"/boot/vmlinuz-* >"$WORK/rd/$kid.Image" 2>/dev/null || cp "$img"/boot/vmlinuz-* "$WORK/rd/$kid.Image"
		dtb="$(find "$img" -name bcm2710-rpi-3-b.dtb | head -1)"
		timeout 400 qemu-system-aarch64 -M raspi3b -m 1G -kernel "$WORK/rd/$kid.Image" -dtb "$dtb" -initrd "$WORK/rd/$kid.gz" \
			-append "earlycon=pl011,0x3f201000 keep_bootcon rdinit=/init loglevel=7 printk.devkmsg=on" \
			-display none -no-reboot -serial "file:$log" -monitor none >/dev/null 2>&1
		grep -aq 'SIM-DKMS-BOOT' "$log" || { echo "load $kid: guest did not boot (see $log)" >&2; fail=1; continue; }
		grep -a 'SIM-DKMS-\(BOOT\|DEP\|INSMOD\|NEG\|DONE\)' "$log" | sed "s/^/[$kid] /"
		grep -aq 'SIM-DKMS-DONE' "$log" || fail=1
		grep -aq 'SIM-DKMS-INSMOD [^ ]* rc=[1-9]' "$log" && fail=1
		grep -aq 'SIM-DKMS-DEP [^ ]* FAIL' "$log" && fail=1
		grep -aq 'SIM-DKMS-NEG-OK' "$log" || fail=1
	done
	[ "$fail" = 0 ] || die "load test failed (logs: $WORK/logs/load-*.log)"
	note "load OK (v8 kernels only; rpi-2712 is build-only)"
}

# ---------------------------------------------------------------------------------------------------------------
case "${1:-}" in
	--check) do_check ;;
	--fetch) do_fetch ;;
	--build) do_build ;;
	--load) do_load ;;
	--all) do_fetch && do_build && do_load ;;
	*) echo "usage: $0 --check | --fetch | --build | --load | --all   (see header and docs/SIM-DKMS.md)" >&2; exit 2 ;;
esac
