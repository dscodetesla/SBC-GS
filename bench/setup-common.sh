#!/usr/bin/env bash
# Install OS packages and the Python venv used by the bench tools.
# Usage: sudo ./setup-common.sh <air|gs>
. "$(dirname "$0")/lib.sh"
role_check "${1:-}"; need_root "$@"

pkgs=(git curl gnupg ca-certificates iw ethtool python3-venv python3-pip socat
      gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good
      gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-x)

log "apt update + packages for role $1"
run apt-get update
run apt-get install -y "${pkgs[@]}"

log "python venv with pymavlink at $VENV"
run mkdir -p "$(dirname "$VENV")"
[ -x "$VENV/bin/python" ] || run python3 -m venv "$VENV"
run "$VENV/bin/pip" install --upgrade pip pymavlink
log "done. next: sudo ./install-driver.sh && sudo ./install-wfb.sh $1"
