#!/usr/bin/env bash
# Mutation check of the netfetch tests on a COPY of build/lib/fetch.sh (never in place): every mutation must make the tests fail.
#   tests/sim/netfetch/mutate.sh            all mutations (about 1-2 minutes, 4 in parallel; NETFETCH_MUT_JOBS=n)
#   tests/sim/netfetch/mutate.sh N01 N06    only the named ones
# Env: PY (python3 default). Exit 0 only if every mutation was killed. The table is in mutations.py.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "SKIP mutate: $PY not found"; exit 77; }
for t in curl openssl; do command -v "$t" >/dev/null 2>&1 || { echo "SKIP mutate: $t not found"; exit 77; }; done
export PYTHONDONTWRITEBYTECODE=1
exec "$PY" "$HERE/mutations.py" "$@"
