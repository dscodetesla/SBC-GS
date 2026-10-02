#!/usr/bin/env bash
# Ratchet for unpinned/unverified fetches in build/*.sh (no network needed).
# Prints "file:line category" for every finding, then per-category counts, and
# FAILS if any category count is higher than the baseline stored in
# tests/golden/static/pins.out (section "== counts"). Fewer findings pass but
# change the output, so run.sh shows a DIFF until `tests/run.sh --update static/pins`.
# Categories:
#   git-unpinned  git clone without -b/--branch <tag> (tag = v?<digit>... or $*version)
#   dl-latest     wget/curl URL containing /latest/, /main/ or refs/heads/
#   dl-nosum      wget/curl download with no sha256sum/shasum within the next 3 lines
#   api-lookup    api.github.com used to resolve a version at build time
#   pipe-exec     curl/wget output piped into gpg, bash or sh
#   pip-unpinned  pip install of a package without ==  (-r file and --upgrade pip are not exempt)
#   http-plain    http:// (not https) URL in a download or apt source
# Comment lines are ignored. Run on another tree: PINS_ROOT=<dir> tests/static/pins.sh
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${PINS_ROOT:-$(cd "$HERE/../.." && pwd)}"
BASE="${PINS_BASELINE:-$HERE/../golden/static/pins.out}"
cd "$REPO" || exit 2

scan() {  # prints "<file>:<line> <category>"
	local f
	for f in build/*.sh; do
		awk -v F="$f" '
		{ L[NR]=$0 }
		END {
			for (i=1; i<=NR; i++) {
				s=L[i]; if (s ~ /^[ \t]*#/) continue
				if (s ~ /git clone/) {
					tag=0
					if (match(s, /(-b|--branch)[ =]+[^ ]+/)) {
						v=substr(s,RSTART,RLENGTH); sub(/^(-b|--branch)[ =]+/,"",v)
						if (v ~ /^v?[0-9]/ || v ~ /^\$\{?[A-Za-z_]*version\}?$/) tag=1
					}
					if (!tag) print F ":" i " git-unpinned"
				}
				if (s ~ /(wget|curl)[ \t]/ && s ~ /https?:\/\//) {
					if (s ~ /\/latest\// || s ~ /\/main\// || s ~ /refs\/heads\//) print F ":" i " dl-latest"
					if (s ~ /api\.github\.com/) print F ":" i " api-lookup"
					if (s ~ /http:\/\//) print F ":" i " http-plain"
					if (s ~ /api\.github\.com/ && s !~ /(wget|curl)[^|]*-O/) { } 
					ok=0; for (j=i; j<=i+3 && j<=NR; j++) if (L[j] ~ /(sha256sum|shasum)/) ok=1
					if (!ok && s !~ /api\.github\.com/) print F ":" i " dl-nosum"
				}
				if (s ~ /deb .*http:\/\//) print F ":" i " http-plain"
				if (s ~ /(curl|wget)[^#]*\|[ \t]*(sudo[ \t]+)?(gpg|bash|sh)([ \t]|$)/) print F ":" i " pipe-exec"
				if (s ~ /pip3?[ \t]+install/ && s !~ /==/) print F ":" i " pip-unpinned"
			}
		}' "$f"
	done
}

cats="git-unpinned dl-latest dl-nosum api-lookup pipe-exec pip-unpinned http-plain"
cur="$(scan)"
echo "== findings"
printf '%s\n' "$cur" | grep . | LC_ALL=C sort -t: -k1,1 -k2,2n
echo "== counts"
for c in $cats; do printf '%s %d\n' "$c" "$(printf '%s\n' "$cur" | grep -c " $c\$")"; done

fail=0
if [ -f "$BASE" ]; then
	for c in $cats; do
		n="$(printf '%s\n' "$cur" | grep -c " $c\$")"
		b="$(awk -v c="$c" '/^== counts/{s=1;next} /^== /{s=0} s&&$1==c{print $2}' "$BASE")"
		if [ -n "$b" ] && [ "$n" -gt "$b" ]; then echo "FAIL $c: $n > baseline $b"; fail=1; fi
	done
else
	echo "no baseline ($BASE)"
fi
[ "$fail" = 0 ] && echo "pins ratchet: ok"
exit $fail
