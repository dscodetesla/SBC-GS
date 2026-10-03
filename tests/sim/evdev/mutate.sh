#!/usr/bin/env bash
# Mutation check of the evdev tests, on a COPY of the needed parts of the repo (never in place): every mutation must make
# `tests/sim/evdev/run.sh --check` FAIL. One line per mutation and a summary; exit 0 only if all were killed.
#   tests/sim/evdev/mutate.sh          all mutations (~1-2 min, JOBS parallel)
#   tests/sim/evdev/mutate.sh M1 M5    only the named ones (table: mutants.py)
# Env: PY (python with pymavlink; without it the main-loop tests SKIP and some mutants would survive), JOBS (default 3).
# The kernel-device layer (QEMU) is not part of this loop; run `EVDEV_ROOT=<mutated copy> tests/sim/evdev/run.sh all` by hand for that.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
PY="${PY:-}"
if [ -z "$PY" ]; then [ -x /opt/sbcvenv/bin/python ] && PY=/opt/sbcvenv/bin/python || PY=python3; fi
JOBS="${JOBS:-3}"
export PYTHONDONTWRITEBYTECODE=1 PY
unset EVDEV_ROOT
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
want=("$@")

run_one() {
	local id="$1" c="$tmp/$1" name rc
	name="$("$PY" "$HERE/mutants.py" name "$id")"
	mkdir -p "$c/tests/sim"
	cp -r "$REPO/bench" "$c/bench"; cp -r "$REPO/config" "$c/config"; cp -r "$HERE" "$c/tests/sim/evdev"
	find "$c" -name __pycache__ -type d -prune -exec rm -rf {} +
	if ! "$PY" "$HERE/mutants.py" apply "$id" "$c" >"$tmp/$id.apply" 2>&1; then
		echo "ERROR    $id: mutation not applied ($name): $(cat "$tmp/$id.apply")" >"$tmp/$id.res"; return
	fi
	for f in "$c/bench/tx12_bridge.py" "$c/tests/sim/evdev/vdev.py"; do
		"$PY" -m py_compile "$f" 2>/dev/null || { echo "ERROR    $id: mutant is not valid Python ($name)" >"$tmp/$id.res"; return; }
	done
	timeout 120 "$c/tests/sim/evdev/run.sh" --check >"$tmp/$id.log" 2>&1
	rc=$?
	if [ "$rc" = 0 ]; then echo "SURVIVED $id: $name" >"$tmp/$id.res"
	elif [ "$rc" = 124 ]; then echo "KILLED   $id: $name  <- (timeout: the mutant hangs the tests)" >"$tmp/$id.res"
	else echo "KILLED   $id: $name  <- $(grep -m1 -E '(FAIL|ERROR): test' "$tmp/$id.log" | cut -c1-100)" >"$tmp/$id.res"; fi
	rm -rf "$c"
}

"$HERE/run.sh" --check >"$tmp/baseline.log" 2>&1 || { cat "$tmp/baseline.log"; echo "baseline (unmutated) run.sh --check fails: nothing to mutate"; exit 1; }
ids=()
for id in $("$PY" "$HERE/mutants.py" list); do
	if [ ${#want[@]} -gt 0 ]; then
		hit=0; for w in "${want[@]}"; do [ "$w" = "$id" ] && hit=1; done
		[ "$hit" = 1 ] || continue
	fi
	ids+=("$id")
	run_one "$id" &
	while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do sleep 0.2; done
done
wait
fail=0; killed=0
for id in "${ids[@]}"; do
	cat "$tmp/$id.res"
	case "$(cut -c1-3 "$tmp/$id.res")" in KIL) killed=$((killed + 1)) ;; *) fail=1 ;; esac
done
echo "mutation check: killed $killed of ${#ids[@]}"
[ "$fail" = 0 ] && echo "mutation check: all mutations killed" || echo "mutation check: SURVIVORS or errors"
exit "$fail"
