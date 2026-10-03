#!/usr/bin/env bash
# Offline "--check" of every simulation layer in one go (no hardware, no network, no root, no QEMU).
#   tests/sim/layers.sh            run all, one summary line each, exit 1 if any layer fails
#   LAYERS="models twin" tests/sim/layers.sh     only the named layers
# Layers: models validate twin fuzz bio netfetch powerlab evdev dkms. (virt needs QEMU/kernel packages: tests/sim/virt/run.sh, opt-in.)
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"
export PY
SKIP_RC=77 # cfg-ok: exit status of a layer with a missing prerequisite (convention of all run.sh)
tail_lines="${LAYERS_TAIL:-15}"
declare -A CMD=(
	[models]="$HERE/models/run.sh --check"
	[validate]="$HERE/models/validate/run.sh --check"
	[twin]="$HERE/twin/run.sh --check"
	[fuzz]="$HERE/fuzz/run.sh --check"
	[bio]="$HERE/bio/run.sh --check"
	[netfetch]="$HERE/netfetch/run.sh --check"
	[powerlab]="$HERE/powerlab/run.sh --check"
	[evdev]="$HERE/evdev/run.sh --check"
	[dkms]="$HERE/dkms/run.sh --check"
)
want=${LAYERS:-"models validate twin fuzz bio netfetch powerlab evdev dkms"}
fail=0
for l in $want; do
	if [ -z "${CMD[$l]:-}" ]; then
		echo "FAIL  $l: unknown layer" >&2
		fail=1
		continue
	fi
	t0=$SECONDS
	log="$(mktemp)"
	# shellcheck disable=SC2086  # CMD holds a command and its flag
	if ${CMD[$l]} >"$log" 2>&1; then
		echo "PASS  $l ($((SECONDS - t0)) s): $(tail -n 1 "$log" | cut -c1-110)"
	else
		rc=$?
		if [ "$rc" = "$SKIP_RC" ]; then
			echo "SKIP  $l: missing prerequisite (skip status)"
		else
			echo "FAIL  $l (exit $rc):"
			tail -n "$tail_lines" "$log" | sed 's/^/      /'
			fail=1
		fi
	fi
	rm -f "$log"
done
[ "$fail" = 0 ] && echo "layers: all passed" || echo "layers: FAILED"
exit "$fail"
