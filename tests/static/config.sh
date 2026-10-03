#!/usr/bin/env bash
# Static check of the layered configuration (docs/CONFIG.md), no hardware/root/pymavlink needed:
#  1. registry sanity (10 fields, unique keys, SAFETY keys have hard bounds),
#  2. golden of `sbc-gs-config show --defaults` and equality with the legacy constants (tests/fixtures/config/legacy-defaults.txt),
#  3. unit tests of both loaders + the real tools with no overrides (tests/static/config_unit.py),
#  4. profiles and bench/env.example validate,
#  5. audit S1: gs-mavlink.sh must NOT execute its config file ($(...), backticks, ';'),
#  6. LISTEN_ADDR is honoured and the default address is unchanged,
#  7. hardcode ratchet (tests/static/config_scan.py): numeric literals/ports/IPs outside the registry may not grow per file;
#     the baseline is the "== hardcode counts" section of tests/golden/static/config.out (CONFIG_BASELINE overrides).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
BASE="${CONFIG_BASELINE:-$HERE/../golden/static/config.out}"
cd "$REPO" || exit 2
bad=0
export SBC_GS_CONFIG=/nonexistent/sbc-gs.env
for v in $(env | sed -n 's/^\(SBC_GS_[A-Z0-9_]*\)=.*/\1/p'); do [ "$v" = SBC_GS_CONFIG ] || unset "$v"; done
CLI=config/sbc-gs-config
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT

echo "== registry"
reg=config/registry.tsv
nrow="$(grep -vc '^#' "$reg")"
nbad="$(awk -F'\t' '!/^#/ && NF!=10 {c++} END{print c+0}' "$reg")"
nempty="$(awk -F'\t' '!/^#/ {for(i=1;i<=NF;i++) if($i=="") {c++; break}} END{print c+0}' "$reg")"
ndup="$(grep -v '^#' "$reg" | cut -f1 | sort | uniq -d | wc -l)"
nsafe="$(awk -F'\t' '!/^#/ && $7=="y"' "$reg" | wc -l)"
nsafebad="$(awk -F'\t' '!/^#/ && $7=="y" && ($4=="-" || $5=="-")' "$reg" | wc -l)"
echo "keys=$nrow bad_field_count=$nbad empty_fields=$nempty duplicates=$ndup safety_keys=$nsafe safety_without_bounds=$nsafebad"
[ "$nbad" = 0 ] && [ "$nempty" = 0 ] && [ "$ndup" = 0 ] && [ "$nsafebad" = 0 ] || { echo "FAIL registry"; bad=1; }

echo "== defaults (sbc-gs-config show --defaults)"
"$CLI" show --defaults > "$T/defaults.txt" 2>"$T/defaults.err"; rc=$?
cat "$T/defaults.txt"
[ "$rc" = 0 ] || { echo "FAIL show --defaults rc=$rc"; bad=1; }
sed 's/\t#.*//' "$T/defaults.txt" > "$T/defaults.kv"
grep -v '^#' tests/fixtures/config/legacy-defaults.txt | diff -u - "$T/defaults.kv" > "$T/legacy.diff" \
	&& echo "legacy constants: identical ($(grep -vc '^#' tests/fixtures/config/legacy-defaults.txt) keys)" \
	|| { echo "FAIL effective defaults differ from the legacy constants:"; sed 's/^/    /' "$T/legacy.diff" | head -20; bad=1; }
python3 config/load.py show --defaults | cmp -s - "$T/defaults.txt" && echo "python loader: identical output" || { echo "FAIL python loader differs"; bad=1; }

echo "== unit tests"
python3 tests/static/config_unit.py > "$T/unit.out" 2>&1; rc=$?
echo "ok lines: $(grep -c '^ok' "$T/unit.out"), $(grep '^unit failures' "$T/unit.out")"
grep -E '^(FAIL|   diff)' "$T/unit.out" | head -20
[ "$rc" = 0 ] || { echo "FAIL unit tests rc=$rc"; bad=1; }

echo "== profiles and bench/env.example"
for f in config/profiles/*.env bench/env.example; do
	own=""; case "$f" in bench/*) own=bench ;; esac
	out="$("$CLI" check "$f" $own 2>&1)" && echo "ok   $f: $out" || { echo "FAIL $f: $out"; bad=1; }
done
bad_prof=0
for f in config/profiles/*.env; do
	n="$(basename "$f" .env)"
	SBC_GS_PROFILE="$n" "$CLI" show >/dev/null 2>&1 || { echo "FAIL profile $n does not load"; bad_prof=1; }
done
bad=$((bad | bad_prof))

echo "== gs-mavlink S1: the config file is data, never code"
GM=gs/mavlink/gs-mavlink.sh
inj() {  # name, config text with MARK as the marker path
	local name="$1" text="${2//MARK/$T/pwned}"
	printf '%s\n' "$text" > "$T/inj.conf"
	rm -f "$T/pwned"
	GS_MAVLINK_CONF="$T/inj.conf" bash "$GM" --print >"$T/inj.out" 2>&1; local rc=$?
	local m=absent; [ -e "$T/pwned" ] && m=PRESENT
	echo "$name: rc=$rc marker=$m"
	{ [ "$rc" = 2 ] && [ "$m" = absent ]; } || { echo "FAIL $name: payload was not rejected or was executed"; bad=1; }
}
inj 'command substitution' 'HB_SYSID=$(touch MARK; echo 125)'
inj 'backticks' 'HB_SYSID=`touch MARK`'
inj 'semicolon after value' "ROUTER='mavp2p'; touch MARK"
inj 'semicolon with space' "HB_SYSID='125' ; touch MARK"
inj 'quoted command substitution' "HB_SYSID='\$(touch MARK)'"
inj 'double-quoted substitution' 'DUMP_PATH="/tmp/x$(touch MARK)"'
inj 'plain command line' 'touch MARK'
inj 'source line' '. /dev/null; touch MARK'
inj 'export prefix' "export HB_SYSID='125'; touch MARK"
inj 'ampersand inside quotes (data, fails validation)' "GCS_UDP_PORTS='14560 && touch MARK'"
inj 'redirect' "HB_SYSID=125 > MARK"
inj 'unknown key' "SOMETHING_ELSE='1'"
inj 'key of another owner' "TX12_SYSID='7'"
printf "HB_SYSID='126'  # a valid trailing comment\nDUMP_ENABLE=1\n" > "$T/ok.conf"
GS_MAVLINK_CONF="$T/ok.conf" bash "$GM" --print > "$T/ok.out" 2>&1 && grep -q -- '--hb-systemid=126' "$T/ok.out" && echo "valid data config still works" || { echo "FAIL valid config rejected"; bad=1; }
grep -nE '^[[:space:]]*(\.|source)[[:space:]]+"?\$CONF' "$GM" >/dev/null && { echo "FAIL gs-mavlink.sh sources \$CONF"; bad=1; } || echo "gs-mavlink.sh has no 'source \$CONF'"

echo "== LISTEN_ADDR"
printf "LISTEN_ADDR='127.0.0.1'\nTCP_ENABLE='1'\nGCS_UDP_PORTS='14560 14561'\n" > "$T/la.conf"
GS_MAVLINK_CONF="$T/la.conf" bash "$GM" --print 2>&1 | tail -1
printf "LISTEN_ADDR='999.1.1.1'\n" > "$T/la.conf"
GS_MAVLINK_CONF="$T/la.conf" bash "$GM" --print 2>&1; echo "exit=$?"
SBC_GS_PROFILE=gs-hardened GS_MAVLINK_CONF=/nonexistent bash "$GM" --print 2>&1 | tail -1
GS_MAVLINK_CONF=/nonexistent bash "$GM" --print 2>&1 | tail -1

echo "== hardcode counts"
cur="$(python3 tests/static/config_scan.py)"
printf '%s\n' "$cur"
if [ -f "$BASE" ]; then
	while read -r f n; do
		[ "$f" = total ] && continue
		b="$(awk '/^== hardcode counts/{s=1;next} /^== /{s=0} s&&$1==f{print $2}' f="$f" "$BASE")"
		if [ -z "$b" ]; then [ "$n" = 0 ] || { echo "FAIL $f: $n hardcoded literals, not in the baseline"; bad=1; }
		elif [ "$n" -gt "$b" ]; then echo "FAIL $f: $n > baseline $b (move the new constant into config/registry.tsv or mark a protocol constant 'cfg-ok')"; bad=1; fi
	done <<<"$cur"
	tb="$(awk '/^== hardcode counts/{s=1;next} /^== /{s=0} s&&$1=="total"{print $2}' "$BASE")"
	tc="$(printf '%s\n' "$cur" | awk '$1=="total"{print $2}')"
	[ -z "$tb" ] || [ "$tc" -le "$tb" ] || { echo "FAIL total $tc > baseline $tb"; bad=1; }
else
	echo "no baseline ($BASE)"
fi
echo "bad=$bad"
exit $bad
