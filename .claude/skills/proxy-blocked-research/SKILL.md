---
name: proxy-blocked-research
description: Use when researching GitHub or other external sources (web, docs, package registries, raw files, APIs) and requests fail with proxy blocks (403/405/407, CONNECT denied, TLS/cert errors, connection reset, timeouts). Diagnoses the failure class, picks the sanctioned fallback path (MCP github tools, WebFetch/WebSearch, gh CLI, git clone via proxy), and reports policy denials instead of bypassing them.
---

# Proxy-blocked research

## Hard rules (never violate)
- Never disable TLS verification, never unset `HTTPS_PROXY`, never use other proxies/tunnels/mirrors to evade policy.
- 403/407 = org egress policy. Do **not** retry or route around: record the host, report it, continue with other sources.
- Scope: only repos in session scope (`dscodetesla/sbc-gs` + `add_repo`-attached). Don't search outside it.

## Step 1 — Diagnose (one command, ≤1 min)
```
bash .claude/skills/proxy-blocked-research/scripts/diagnose.sh [host-or-url ...]
```
It prints proxy status, `recentRelayFailures`, git conflicts, and per-URL HTTP/CONNECT result. Then map the symptom:

| Symptom | Cause | Action |
|---|---|---|
| cert verify failed / PKIX | tool ignores CA | set tool CA var to `/root/.ccr/ca-bundle.crt` (SSL_CERT_FILE, NODE_EXTRA_CA_CERTS, REQUESTS_CA_BUNDLE, GIT_SSL_CAINFO, `--cacert`); check tool config files overriding env |
| 405 | plain-HTTP/`HTTP_PROXY` use, old axios | unset `HTTP_PROXY` for that tool; axios ≥1.16.1 |
| 403/407 on CONNECT | host not allowed by policy | STOP retrying; report host; use fallback source (Step 2) |
| reset / RPC failed mid-transfer | tunnel aborted | read `recentRelayFailures` (host+reason) before blaming remote |
| timeout, no proxy error | client ignores proxy | Node fetch: `NODE_USE_ENV_PROXY=1`; aiohttp `trust_env=True`; git: remove `http.proxy` overrides |
| git hangs/fails | gitconfig conflicts | check `gitConfigConflicts`; SSH remotes auto-rewritten to HTTPS |
| WebSocket/gRPC/non-443/mTLS/pinned | unsupported | report, don't work around |

## Step 2 — Fallback ladder (stop at first that works)
1. **GitHub MCP tools** (`mcp__github__*`: get_file_contents, search_code, list_commits, pull_request_read, issue_read) — preferred for GitHub; not blocked by egress to raw/api hosts. Use pagination (5–10 items), `minimal_output`.
2. `git clone/fetch` over HTTPS through the proxy (fetch specific branch; retry network errors 2s/4s/8s/16s only for transient errors, never for 403/407).
3. `gh` CLI (pre-configured for proxy) if allowed.
4. `WebFetch` / `WebSearch` (ToolSearch-load first) for public docs.
5. Package metadata: registries in `noProxy` (npm, PyPI, crates, Go proxy) are direct — use them for package docs.
6. Ask the user to paste/upload the content or request an allowlist; run `read_documentation` topic `environment.network` for the exact setting.

## Step 3 — Control quality while researching
- **Focus**: write the research question + success criterion in 2 lines first; re-read before each new source. Drop sources that don't answer it.
- **Hallucination**: every claim cites a fetched source (URL/`file:line`/commit). Unfetched = "unverified". Never invent API names, versions, or file contents when a fetch failed — say it failed.
- **Redundancy**: don't re-fetch the same URL; cache results in the scratchpad dir; one retry max per failure class.
- **Degradation/context loss**: keep a running ledger in scratchpad (`blocked-hosts.md`: host, status, fallback used, result). Summarize before context grows; large outputs → files, not chat.
- **Delegation**: broad sweeps over many files/sources → `Explore` agent (read-only); multi-source synthesis → `general-purpose` agents in parallel, each with one source class and a one-line deliverable; give each the Hard rules above. Verify their claims against cited sources before relying on them.

## Roles / domains to activate
Network/infra engineer (proxy, TLS, DNS) · Security/compliance (policy denials are reported, not bypassed) · Git/VCS specialist · Research analyst (source triage, citation) · Technical writer (concise report). Cross-domain: security×network (why blocked), research×VCS (where truth lives: commits/PRs/issues).

## Output format
`Question → Sources used (cited) → Blocked hosts + reason + fallback → Findings (verified/unverified) → Open items needing user/admin`.
