#!/usr/bin/env bash
# Diagnose proxy blocking. Usage: diagnose.sh [url-or-host ...]
P="${HTTPS_PROXY:-http://127.0.0.1:38645}"
echo "== proxy status =="
curl -sS --max-time 10 "$P/__agentproxy/status" | python3 -c '
import sys,json
try:
    d=json.load(sys.stdin)
    for k in ("enabled","hasSystemCa","bundleCoversEveryHost","gitSshRewrite","gitConfigConflicts","toolTrustFailureCodes","recentRelayFailures"):
        print(k,"=",d.get(k))
except Exception as e: print("status unreadable:",e)'
echo "== env =="; env | grep -iE '^(https?_proxy|no_proxy|ssl_cert_file|node_extra_ca_certs|requests_ca_bundle|git_ssl_cainfo)=' | sed 's/=.*@/=***@/'
echo "== git proxy overrides =="; git config --get-regexp 'http\..*(proxy|sslcainfo)' 2>/dev/null || echo none
for t in "${@:-https://github.com https://api.github.com https://raw.githubusercontent.com}"; do
  for u in $t; do
    case "$u" in http*) ;; *) u="https://$u";; esac
    code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 --cacert /root/.ccr/ca-bundle.crt "$u" 2>&1 | tail -1)
    echo "$u -> $code"
  done
done
echo "== recent failures (after probes) =="
curl -sS --max-time 10 "$P/__agentproxy/status" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("recentRelayFailures"))'
