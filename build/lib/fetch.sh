#!/usr/bin/env bash
# Verified-download helpers for build/build.sh (docs/REPRODUCIBLE-BUILD.md, M5 step 1).
# SOURCE this file; it defines functions only and changes no shell options, so it is safe under
# `set -e`, `set -u`, `set -x` and `set -o pipefail`. Functions report errors with `return`
# (never `exit`) and print diagnostics to stderr.
#
#   fetch_file <url> <dest> <sha256>   download over https, verify sha256, else fail and leave no file; a dest that already has the
#                                      pinned hash is kept and not downloaded again (a different existing file is removed first)
#   git_pin <repo-url> <dest> <sha>    checkout exactly the given 40-hex commit
#   pin_from_manifest <NAME> <dest>    read NAME_PIN and NAME_URL (file) or NAME_REPO (git) and dispatch
#
# Environment:
#   GS_ALLOW_UNPINNED=1  allow an empty/non-SHA pin: warn loudly and print the computed value to paste
#                        into build/versions.env. Default: refuse.
#   GS_FETCH_CMD         download primitive, called as `$GS_FETCH_CMD <url> <dest>` (tests set a `cp`
#                        shim, because file:// is rejected by --proto '=https'). Default: curl, https
#                        only, TLS >= 1.2, certificate checks always on, with retries.
#   GS_FETCH_RETRY       retries of the default curl after a transient failure (default 3)
#   GS_FETCH_STALL_SECS  the default curl aborts an attempt that moves < 1 byte/s for this many seconds (default 60)

_gs_err()  { printf 'fetch: ERROR: %s\n' "$*" >&2; }
_gs_warn() { printf 'fetch: WARNING: %s\n' "$*" >&2; }

# D13: a signal during the download must not leave "$dest.part.<pid>" behind. fetch_file installs INT/TERM (and EXIT, unless the
# caller already has an EXIT trap) handlers for the duration of the call, then restores the caller's traps. The handler stops the
# download, removes the partial file and re-raises the signal so that the caller dies as before (a trap would otherwise swallow it).
# The download runs as a BACKGROUND job and the shell `wait`s for it: bash defers a trap until a foreground child exits, so with a
# foreground curl a signal sent to the bash process alone (not to the whole process group) would only be handled when curl finished,
# i.e. never for a stalled server.
_GS_FETCH_TMP=""
_GS_FETCH_PID=""
_GS_FETCH_OLD_TRAPS=""
_gs_fetch_untrap() {
	trap - EXIT INT TERM
	[ -z "$_GS_FETCH_OLD_TRAPS" ] || eval "$_GS_FETCH_OLD_TRAPS"
	_GS_FETCH_OLD_TRAPS=""
}
_gs_fetch_on_signal() {  # <signal>
	if [ -n "$_GS_FETCH_PID" ]; then
		kill -s TERM "$_GS_FETCH_PID" 2>/dev/null
		wait "$_GS_FETCH_PID" 2>/dev/null
		_GS_FETCH_PID=""
	fi
	[ -z "$_GS_FETCH_TMP" ] || rm -f -- "$_GS_FETCH_TMP"
	_gs_fetch_untrap
	kill -s "$1" "${BASHPID:-$$}"
}

_gs_run_bg() {  # <external command...>: run it as a background job and wait for it (see above); returns its status
	local rc=0
	"$@" &
	_GS_FETCH_PID=$!
	wait "$_GS_FETCH_PID" || rc=$?
	_GS_FETCH_PID=""
	return "$rc"
}

# Default download primitive (GS_FETCH_CMD unset): curl, https only (also across redirects), TLS >= 1.2, certificate and host name
# checks always on (no option that relaxes them is used anywhere).
#  - `-q` must stay the first argument: it makes curl ignore ~/.curlrc and $CURL_HOME/.curlrc, where a stray `insecure` line would
#    silently turn verification off.
#  - The URL goes after --url so that a value starting with '-' can never be parsed as an option (e.g. a capital-K config-file
#    option, which could bring `insecure` back).
#  - Proxy variables (https_proxy, all_proxy, ...) stay in force: they are honoured, never bypassed.
#  - `--retry` covers timeouts and HTTP 408/429/5xx. A connection dropped in the middle of the body (curl exit 18/52/56) is NOT
#    retried by curl itself, so it is retried here (same GS_FETCH_RETRY).
#  - `--speed-limit/--speed-time` abort a transfer that has stalled (--connect-timeout covers only the connection); curl then retries
#    it like any timeout. A slow but progressing download is not affected.
_gs_download() {  # <url> <dest.part>
	local url="$1" tmp="$2" rc try=0 retries="${GS_FETCH_RETRY:-3}" stall="${GS_FETCH_STALL_SECS:-60}"   # validated by the caller
	while :; do
		rc=0
		if [ -n "${GS_FETCH_CMD:-}" ]; then
			_gs_run_bg "$GS_FETCH_CMD" "$url" "$tmp" || rc=$?
			return "$rc"
		fi
		_gs_run_bg curl -q --fail --location --silent --show-error \
			--proto '=https' --proto-redir '=https' --tlsv1.2 \
			--retry "$retries" --retry-delay 2 --connect-timeout 20 \
			--speed-limit 1 --speed-time "$stall" \
			--output "$tmp" --url "$url" || rc=$?
		case "$rc" in
			0) return 0 ;;
			18|52|56)
				try=$((try + 1))
				[ "$try" -le "$retries" ] || return "$rc"
				_gs_warn "transfer interrupted (curl exit $rc), retry $try of $retries: $url"
				_gs_run_bg sleep 2 || true ;;
			*) return "$rc" ;;
		esac
	done
}

fetch_file() {  # <url> <dest> <sha256>
	local rc=0 old=""
	# in a subshell (e.g. out="$(fetch_file ...)") `trap -p` reports the PARENT's traps, which are not in force there: restoring them
	# would make the subshell run the caller's EXIT trap. So the caller's traps are saved and restored only in the main shell.
	[ "${BASHPID:-$$}" != "$$" ] || old="$(trap -p EXIT INT TERM)"
	_GS_FETCH_OLD_TRAPS="$old"
	_GS_FETCH_TMP="${2:-}.part.${BASHPID:-$$}"
	trap '_gs_fetch_on_signal INT' INT
	trap '_gs_fetch_on_signal TERM' TERM
	case "$old" in *" EXIT"*) ;; *) trap '[ -z "$_GS_FETCH_TMP" ] || rm -f -- "$_GS_FETCH_TMP"' EXIT ;; esac
	_gs_fetch_file_impl "$@" || rc=$?
	_GS_FETCH_TMP=""
	_gs_fetch_untrap
	return "$rc"
}

_gs_fetch_file_impl() {  # <url> <dest> <sha256>
	local url="${1:-}" dest="${2:-}" want="${3:-}" tmp got
	if [ -z "$url" ] || [ -z "$dest" ]; then _gs_err "usage: fetch_file <url> <dest> <sha256>"; return 2; fi
	want="$(printf '%s' "$want" | tr 'A-F' 'a-f')"
	if [ -z "$want" ]; then
		if [ "${GS_ALLOW_UNPINNED:-0}" != "1" ]; then
			_gs_err "no sha256 pinned for $url (refusing; set GS_ALLOW_UNPINNED=1 to see the hash and pin it)"
			return 2
		fi
	elif ! [[ "$want" =~ ^[0-9a-f]{64}$ ]]; then   # whole string: grep -E would test each line of a multi-line value (D12)
		_gs_err "not a sha256 (need 64 hex chars): '$want'"
		return 2
	fi
	if ! [[ "${GS_FETCH_RETRY:-3}" =~ ^[0-9]+$ ]] || ! [[ "${GS_FETCH_STALL_SECS:-60}" =~ ^[1-9][0-9]*$ ]]; then
		_gs_err "GS_FETCH_RETRY must be a number >= 0 and GS_FETCH_STALL_SECS a number >= 1"
		return 2
	fi
	if [ -e "$dest" ] && [ ! -f "$dest" ]; then   # a directory would receive the file under the temp name; /dev/null would be replaced
		_gs_err "$dest exists and is not a regular file"
		return 2
	fi
	if [ -n "$want" ] && [ -f "$dest" ]; then     # idempotent: a file that already has the pinned hash is not downloaded again
		got="$(sha256sum < "$dest" | awk '{print $1}')"
		if [ "$got" = "$want" ]; then
			echo "fetch: ok $dest sha256=$got (already present)"
			return 0
		fi
		_gs_warn "$dest has sha256 $got, not the pinned one; removed, downloading again"
		rm -f -- "$dest"   # whatever follows (even a failed download), an untrusted file must not stay in place
	fi
	tmp="$_GS_FETCH_TMP"
	rm -f -- "$tmp"
	if ! _gs_download "$url" "$tmp"; then
		rm -f -- "$tmp"
		_gs_err "download failed: $url"
		return 1
	fi
	if [ ! -f "$tmp" ]; then
		_gs_err "download of $url reported success but produced no file"
		return 1
	fi
	# hash from stdin: a name with a backslash or newline would make `sha256sum FILE` print an escaped line, `sha256sum -c` misparse it
	got="$(sha256sum < "$tmp" | awk '{print $1}')"
	if [ -z "$want" ]; then
		_gs_warn "UNPINNED download accepted: $url"
		_gs_warn "computed sha256: $got   (paste it into build/versions.env as the *_PIN of this component)"
		mv -f -- "$tmp" "$dest" || { rm -f -- "$tmp"; _gs_err "cannot move the download to $dest"; return 1; }
		return 0
	fi
	if [ "$got" != "$want" ]; then
		rm -f -- "$tmp" "$dest"
		_gs_err "sha256 MISMATCH for $url: expected $want, got $got; file removed"
		return 1
	fi
	mv -f -- "$tmp" "$dest" || { rm -f -- "$tmp"; _gs_err "cannot move the download to $dest"; return 1; }
	echo "fetch: ok $dest sha256=$got"
}

git_pin() {  # <repo-url> <dest> <40-hex-sha>
	local repo="${1:-}" dest="${2:-}" ref="${3:-}" got
	if [ -z "$repo" ] || [ -z "$dest" ]; then _gs_err "usage: git_pin <repo-url> <dest> <sha>"; return 2; fi
	ref="$(printf '%s' "$ref" | tr 'A-F' 'a-f')"
	if ! [[ "$ref" =~ ^[0-9a-f]{40}$ ]]; then
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
	if [[ "$ref" =~ ^[0-9a-f]{40}$ ]]; then
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
	if ! [[ "$name" =~ ^[A-Z][A-Z0-9_]*$ ]] || [ -z "$dest" ]; then
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
