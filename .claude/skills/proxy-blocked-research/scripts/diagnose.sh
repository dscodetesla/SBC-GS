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
    hdr=$(mktemp); body=$(mktemp)
    code=$(curl -sS -D "$hdr" -o "$body" -w '%{http_code}' --max-time 15 --cacert /root/.ccr/ca-bundle.crt "$u" 2>/dev/null); rc=$?
    if [ "$code" = "000" ]; then
      layer="A: egress policy (CONNECT refused) or network error - see recentRelayFailures"
    elif [[ "$code" =~ ^(403|429|503)$ ]] && { grep -qiE 'cf-mitigated|server: cloudflare' "$hdr" || grep -qiE 'Just a moment|cf-challenge|captcha|Anubis|Attention Required' "$body"; }; then
      layer="B: origin bot-protection (WAF/Cloudflare) - NOT fixable via environment settings; do not bypass"
    elif [[ "$code" =~ ^[23] ]]; then layer="OK"
    else layer="C: origin/other error"; fi
    echo "$u -> $code  [$layer]"
    rm -f "$hdr" "$body"
  done
done
echo "== recent failures (after probes) =="
curl -sS --max-time 10 "$P/__agentproxy/status" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("recentRelayFailures"))'
