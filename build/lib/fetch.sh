#!/usr/bin/env bash
# Verified-download helpers for build/build.sh (docs/REPRODUCIBLE-BUILD.md, M5 step 1).
# SOURCE this file; it defines functions only and changes no shell options, so it is safe under
# `set -e`, `set -u`, `set -x` and `set -o pipefail`. Functions report errors with `return`
# (never `exit`) and print diagnostics to stderr.
#
#   fetch_file <url> <dest> <sha256>   download over https, verify sha256, else fail and leave no file
#   git_pin <repo-url> <dest> <sha>    checkout exactly the given 40-hex commit
#   pin_from_manifest <NAME> <dest>    read NAME_PIN and NAME_URL (file) or NAME_REPO (git) and dispatch
#
# Environment:
#   GS_ALLOW_UNPINNED=1  allow an empty/non-SHA pin: warn loudly and print the computed value to paste
#                        into build/versions.env. Default: refuse.
#   GS_FETCH_CMD         download primitive, called as `$GS_FETCH_CMD <url> <dest>` (tests set a `cp`
#                        shim, because file:// is rejected by --proto '=https'). Default: curl, https
#                        only, TLS >= 1.2, certificate checks always on, with retries.

_gs_err()  { printf 'fetch: ERROR: %s\n' "$*" >&2; }
_gs_warn() { printf 'fetch: WARNING: %s\n' "$*" >&2; }

_gs_curl_fetch() {  # <url> <dest>
	curl --fail --location --silent --show-error \
		--proto '=https' --proto-redir '=https' --tlsv1.2 \
		--retry 3 --retry-delay 2 --connect-timeout 20 \
		--output "$2" "$1"
}

fetch_file() {  # <url> <dest> <sha256>
	local url="${1:-}" dest="${2:-}" want="${3:-}" tmp got
	if [ -z "$url" ] || [ -z "$dest" ]; then _gs_err "usage: fetch_file <url> <dest> <sha256>"; return 2; fi
	want="$(printf '%s' "$want" | tr 'A-F' 'a-f')"
	if [ -z "$want" ]; then
		if [ "${GS_ALLOW_UNPINNED:-0}" != "1" ]; then
			_gs_err "no sha256 pinned for $url (refusing; set GS_ALLOW_UNPINNED=1 to see the hash and pin it)"
			return 2
		fi
	elif ! printf '%s' "$want" | grep -Eq '^[0-9a-f]{64}$'; then
		_gs_err "not a sha256 (need 64 hex chars): '$want'"
		return 2
	fi
	tmp="$dest.part.$$"
	rm -f "$tmp"
	if ! "${GS_FETCH_CMD:-_gs_curl_fetch}" "$url" "$tmp"; then
		rm -f "$tmp"
		_gs_err "download failed: $url"
		return 1
	fi
	got="$(sha256sum "$tmp" | awk '{print $1}')"
	if [ -z "$want" ]; then
		_gs_warn "UNPINNED download accepted: $url"
		_gs_warn "computed sha256: $got   (paste it into build/versions.env as the *_PIN of this component)"
		mv -f "$tmp" "$dest"
		return 0
	fi
	if ! printf '%s  %s\n' "$want" "$tmp" | sha256sum -c --status - 2>/dev/null; then
		rm -f "$tmp" "$dest"
		_gs_err "sha256 MISMATCH for $url: expected $want, got $got; file removed"
		return 1
	fi
	mv -f "$tmp" "$dest"
	echo "fetch: ok $dest sha256=$got"
}

git_pin() {  # <repo-url> <dest> <40-hex-sha>
	local repo="${1:-}" dest="${2:-}" ref="${3:-}" got
	if [ -z "$repo" ] || [ -z "$dest" ]; then _gs_err "usage: git_pin <repo-url> <dest> <sha>"; return 2; fi
	ref="$(printf '%s' "$ref" | tr 'A-F' 'a-f')"
	if ! printf '%s' "$ref" | grep -Eq '^[0-9a-f]{40}$'; then
		if [ "${GS_ALLOW_UNPINNED:-0}" != "1" ]; then
			_gs_err "git ref '$ref' for $repo is not a 40-hex commit SHA (refusing; set GS_ALLOW_UNPINNED=1 to see the SHA and pin it)"
			return 2
		fi
		[ -n "$ref" ] || ref=HEAD
		_gs_warn "UNPINNED git ref '$ref' for $repo accepted"
	fi
	if [ -e "$dest" ] && [ ! -d "$dest/.git" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ]; then
		_gs_err "$dest exists, is not empty and is not a git checkout"
		return 1
	fi
	mkdir -p "$dest" || return 1
	# Only https and local (file) transports; no git://, http://, ssh://, ext::.
	if ! (
		export GIT_ALLOW_PROTOCOL=https:file GIT_TERMINAL_PROMPT=0
		git -C "$dest" init -q &&
		{ git -C "$dest" remote set-url origin "$repo" 2>/dev/null || git -C "$dest" remote add origin "$repo"; } &&
		git -C "$dest" fetch -q --depth 1 origin "$ref" &&
		git -C "$dest" -c advice.detachedHead=false checkout -q FETCH_HEAD
	); then
		_gs_err "git fetch/checkout of '$ref' from $repo failed"
		return 1
	fi
	got="$(git -C "$dest" rev-parse HEAD)" || return 1
	if printf '%s' "$ref" | grep -Eq '^[0-9a-f]{40}$'; then
		if [ "$got" != "$ref" ]; then
			_gs_err "HEAD is $got, expected $ref"
			return 1
		fi
		echo "fetch: ok $dest commit=$got"
	else
		_gs_warn "resolved commit: $got   (paste it into build/versions.env as the *_PIN of this component)"
	fi
}

pin_from_manifest() {  # <NAME> <dest>; reads NAME_REPO or NAME_URL, and NAME_PIN, from shell variables (e.g. after . build/versions.env)
	local name="${1:-}" dest="${2:-}" rv uv pv
	if ! printf '%s' "$name" | grep -Eq '^[A-Z][A-Z0-9_]*$' || [ -z "$dest" ]; then
		_gs_err "usage: pin_from_manifest <NAME> <dest> (NAME like WFB_NG)"
		return 2
	fi
	rv="${name}_REPO"; uv="${name}_URL"; pv="${name}_PIN"
	rv="${!rv:-}"; uv="${!uv:-}"; pv="${!pv:-}"
	if [ -n "$rv" ] && [ -n "$uv" ]; then
		_gs_err "${name}: both ${name}_REPO and ${name}_URL are set; ambiguous"
		return 2
	elif [ -n "$rv" ]; then
		git_pin "$rv" "$dest" "$pv"
	elif [ -n "$uv" ]; then
		fetch_file "$uv" "$dest" "$pv"
	else
		_gs_err "${name}: neither ${name}_REPO nor ${name}_URL is set"
		return 2
	fi
}
