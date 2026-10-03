#!/usr/bin/env bash
# Mutation check of the fuzz layer on a COPY of the project (never in place): every mutation must make the fuzz tests fail.
#   tests/sim/fuzz/mutate.sh            all mutations (about 2-3 minutes)
#   tests/sim/fuzz/mutate.sh M1 M8      only the named ones
# Env: PY (python3 default). Exit 0 only if every mutation was killed. The mutation table is in mutations.py.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "SKIP mutate: $PY not found"; exit 77; }
export PYTHONDONTWRITEBYTECODE=1
exec "$PY" "$HERE/mutations.py" "$@"
