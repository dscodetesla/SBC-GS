#!/usr/bin/env bash
# Static check of gs/mavlink/gs-mavlink.sh --print against fixture configs (no router, no hardware needed):
# prints stdout+stderr and exit code of each config; compared with tests/golden/static/gs-mavlink.out.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="${GS_MAVLINK_SCRIPT:-$REPO/gs/mavlink/gs-mavlink.sh}"
FIX="$REPO/tests/fixtures/gs-mavlink"
for name in defaults mavlink-router two-gcs tcp serial bad-port bad-sysid bad-backend dup-port; do
	echo "== $name"
	GS_MAVLINK_CONF="$FIX/$name.conf" bash "$SCRIPT" --print 2>&1
	echo "exit=$?"
done
echo "== missing-file"
GS_MAVLINK_CONF="$FIX/does-not-exist.conf" bash "$SCRIPT" --print 2>&1
echo "exit=$?"
