#!/usr/bin/env bash
# Mutation check of the watchdog tests on a COPY (mutate.py). Usage: tests/sim/watchdog/mutate.sh [M1 M5 ...]   Env: PY, MAVP2P
exec "${PY:-python3}" "$(dirname "${BASH_SOURCE[0]}")/mutate.py" "$@"
