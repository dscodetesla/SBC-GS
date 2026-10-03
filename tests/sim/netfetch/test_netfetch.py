#!/usr/bin/env python3
"""build/lib/fetch.sh against a REAL curl and a deterministic loopback HTTP(S) server (no external network; stdlib + `openssl` CLI).

The offline test tests/static/fetch.sh replaces the download with a `cp` shim (GS_FETCH_CMD); this one exercises the default
primitive: the real curl command line of fetch_file, TLS verification, redirects, HTTP errors, dropped/stalled transfers,
signals, proxies, idempotence. Everything runs on 127.0.0.1; a test CA is trusted through CURL_CA_BUNDLE (a trust anchor,
not a verification bypass). Proxy variables of the host are scrubbed so the result does not depend on the environment.

Env: NETFETCH_REPO (repository root to test; mutate.sh points it at a COPY), NETFETCH_SEED, NETFETCH_ITERS (scale of the randomized
test), NETFETCH_LONG=1 (slow scenarios: full default retry chains, stall after a burst, many more random scenarios).
Run through run.sh (it shards the classes over processes). Direct: python3 test_netfetch.py [-v] [TestClass ...]
"""
import hashlib
import os
import random
import re
import shutil
import signal
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import netserver as ns  # noqa: E402

REPO = os.path.realpath(os.environ.get("NETFETCH_REPO") or os.path.join(HERE, "..", "..", ".."))
LIB = os.path.join(REPO, "build", "lib", "fetch.sh")
SEED = int(os.environ.get("NETFETCH_SEED", "20261003"))
SCALE = float(os.environ.get("NETFETCH_ITERS", "1"))
LONG = os.environ.get("NETFETCH_LONG", "0") == "1"
SHA_EMPTY = hashlib.sha256(b"").hexdigest()
NASTY_NAMES = ["plain", "sp ace", "q'uote", 'dq"x', "$HOME", "$(true)", "a;b", "a`b`", "*", "?[x]", "\u00fcn\u00ef-\u00e7", "back\\slash",
               "new\nline", "-dash", "--", "-", "~", ".hidden", "a" * 200, "-n", "-rf", "x y\tz"]
_PROXY_VARS = ("http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "no_proxy")


def need_tools():
    for t in ("curl", "openssl", "bash", "sha256sum", "awk"):
        if shutil.which(t) is None:
            raise unittest.SkipTest(f"{t} not found")


class Res:
    def __init__(self, rc, out, err, secs):
        self.rc, self.out, self.err, self.secs = rc, out, err, secs

    def __repr__(self):
        return f"rc={self.rc} {self.secs:.1f}s out={self.out!r} err={self.err[-400:]!r}"


class Base(unittest.TestCase):
    fx = None

    @classmethod
    def setUpClass(cls):
        need_tools()
        cls.fx = ns.Fixture()

    @classmethod
    def tearDownClass(cls):
        if cls.fx:
            cls.fx.close()

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="nf-")
        self.home = os.path.join(self.d, "home")
        os.mkdir(self.home)
        self.work = os.path.join(self.d, "w")
        os.mkdir(self.work)
        self.tag = hashlib.sha256(self.id().encode()).hexdigest()[:10]
        self._procs = []

    def tearDown(self):
        for p in self._procs:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
        self.kill_curls()
        shutil.rmtree(self.d, ignore_errors=True)

    # -- helpers ------------------------------------------------------------------------------------------------------------
    def path(self, p):
        """URL path made unique per test (counters and logs are keyed by the full path)."""
        return p + ("&" if "?" in p else "?") + "t=" + self.tag

    def url(self, name, p):
        return self.fx.url(name, self.path(p))

    def hits(self, name, p):
        return len(self.fx.hits(name, self.path(p)))

    def env(self, **extra):
        e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.home, "LC_ALL": "C", "CURL_CA_BUNDLE": self.fx.ca,
             "NO_PROXY": "127.0.0.1,localhost", "L": LIB, "TMPDIR": self.d}
        e.update(extra)
        return {k: v for k, v in e.items() if v is not None}

    def spawn(self, code, args, env=None, cwd=None):
        p = subprocess.Popen(["bash", "-c", '. "$L"; ' + code, "_"] + list(args), env=env or self.env(), cwd=cwd or self.work,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, text=True, errors="replace",
                             stdin=subprocess.DEVNULL, preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
        self._procs.append(p)
        return p

    def run_code(self, code, args, env=None, cwd=None, timeout=40):
        t0 = time.time()
        p = self.spawn(code, args, env, cwd)
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.communicate()
            self.fail(f"fetch hung: no result after {timeout} s ({code} {args})")
        return Res(p.returncode, out, err, time.time() - t0)

    def fetch(self, url, dest, want, env=None, cwd=None, timeout=40, **extra):
        e = self.env(**extra) if env is None else env
        return self.run_code('fetch_file "$1" "$2" "$3"', [url, dest, want], e, cwd, timeout)

    def dest(self, name="out"):
        return os.path.join(self.work, name)

    def parts(self):
        found = []
        for root, _, files in os.walk(self.work):
            found += [os.path.join(root, f) for f in files if ".part." in f]
        return sorted(found)

    def assertClean(self):
        self.assertEqual(self.parts(), [], "temporary .part file left behind")

    def assertOk(self, r, dest, body):
        self.assertEqual(r.rc, 0, r)
        with open(dest, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), hashlib.sha256(body).hexdigest(), r)
        self.assertClean()

    def assertFailed(self, r, dest, rc=1):
        self.assertEqual(r.rc, rc, r)
        self.assertFalse(os.path.lexists(dest), f"a file was left behind: {r}")
        self.assertClean()

    @staticmethod
    def curls(marker=None):
        """PIDs of running `curl` processes (optionally whose command line contains marker)."""
        found = []
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    argv = f.read().split(b"\0")
            except OSError:
                continue
            if argv and os.path.basename(argv[0]) == b"curl" and (marker is None or any(marker.encode() in a for a in argv)):
                found.append(int(pid))
        return found

    def kill_curls(self):
        for pid in self.curls(self.tag):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def wait_for(self, cond, timeout=8.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if cond():
                return True
            time.sleep(0.02)
        return False


# ========================================================================================== basic transfers
class TestBasics(Base):
    def test_success_and_hash(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/ok"), d, ns.OK_SHA)
        self.assertOk(r, d, ns.OK_BODY)
        self.assertIn("fetch: ok " + d + " sha256=" + ns.OK_SHA, r.out)
        self.assertEqual(self.hits("https", "/ok"), 1)

    def test_uppercase_pin_accepted(self):
        d = self.dest()
        self.assertOk(self.fetch(self.url("https", "/ok"), d, ns.OK_SHA.upper()), d, ns.OK_BODY)

    def test_wrong_hash_removes_file(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/ok"), d, "0" * 64)
        self.assertFailed(r, d)
        self.assertIn("sha256 MISMATCH", r.err)
        self.assertIn(ns.OK_SHA, r.err)
        self.assertEqual(self.hits("https", "/ok"), 1)

    def test_wrong_hash_removes_preexisting_file_too(self):
        d = self.dest()
        with open(d, "w") as f:
            f.write("stale")
        self.assertFailed(self.fetch(self.url("https", "/ok"), d, "0" * 64), d)

    def test_malformed_pins_do_not_touch_the_network(self):
        for pin in ("", "abc", ns.OK_SHA[:63], ns.OK_SHA + "0", "g" * 64, ns.OK_SHA + "\n" + ns.OK_SHA, "x\n" + ns.OK_SHA):
            r = self.fetch(self.url("https", "/ok"), self.dest(), pin)
            self.assertFailed(r, self.dest(), 2)
        self.assertEqual(self.hits("https", "/ok"), 0)

    def test_empty_file(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/empty"), d, SHA_EMPTY)
        self.assertOk(r, d, b"")
        self.assertEqual(os.path.getsize(d), 0)
        self.assertFailed(self.fetch(self.url("https", "/empty"), self.dest("o2"), ns.OK_SHA), self.dest("o2"))

    def test_big_file_4mib(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/big"), d, self.fx.big_sha)
        self.assertOk(r, d, self.fx.big)
        self.assertEqual(os.path.getsize(d), len(self.fx.big))
        self.assertFailed(self.fetch(self.url("https", "/big"), self.dest("o2"), ns.OK_SHA), self.dest("o2"))

    def test_url_with_special_characters(self):
        for p in ("/files/a%20b%27c%3Bd.bin", "/files/x?y=1&z=%22q%22", "/files/%E2%82%AC.bin", "/files/a+b", "/files/%2e%2e%2fx"):
            d = self.dest("u")
            self.assertOk(self.fetch(self.url("https", p), d, ns.OK_SHA), d, ns.OK_BODY)
            os.unlink(d)

    def test_url_fragment_is_not_sent(self):
        d = self.dest()
        self.assertOk(self.fetch(self.url("https", "/ok") + "#frag/../x", d, ns.OK_SHA), d, ns.OK_BODY)

    def test_destination_names_with_special_characters(self):
        for i, name in enumerate(NASTY_NAMES):
            r = self.fetch(self.url("https", "/ok"), name, ns.OK_SHA, cwd=self.work)   # relative: a leading '-' must not become an option
            self.assertEqual(r.rc, 0, (name, r))
            with open(os.path.join(self.work, name), "rb") as f:
                self.assertEqual(f.read(), ns.OK_BODY, name)
            self.assertIn("sha256=" + ns.OK_SHA, r.out, name)   # the hash itself, without a leading backslash for names with \ or newline
            self.assertEqual(sorted(os.listdir(self.work)), sorted(NASTY_NAMES[:i + 1]), "stray file next to the destination")
        self.assertEqual(self.hits("https", "/ok"), len(NASTY_NAMES))

    def test_destination_name_too_long_for_the_temp_file_fails_cleanly(self):
        # LIMIT (documented): dest basename + ".part.<pid>" must fit NAME_MAX (255); a 250-char name is legal but cannot be fetched
        d = self.dest("n" * 250)
        r = self.fetch(self.url("https", "/ok"), d, ns.OK_SHA)
        self.assertFailed(r, d)

    def test_destination_is_not_a_regular_file(self):
        os.mkdir(self.dest("adir"))
        os.symlink(self.dest("adir"), self.dest("linkdir"))
        os.mkfifo(self.dest("afifo"))
        for name in ("adir", "linkdir", "afifo"):
            r = self.fetch(self.url("https", "/ok"), self.dest(name), ns.OK_SHA)
            self.assertEqual(r.rc, 2, (name, r))
            self.assertIn("not a regular file", r.err)
        self.assertEqual(os.listdir(self.dest("adir")), [], "the download was moved INTO the directory")
        self.assertTrue(stat.S_ISFIFO(os.stat(self.dest("afifo")).st_mode))
        self.assertEqual(self.hits("https", "/ok"), 0)
        self.assertClean()

    def test_destination_in_missing_directory_fails_cleanly(self):
        d = os.path.join(self.work, "no", "such", "dir", "f")
        self.assertFailed(self.fetch(self.url("https", "/ok"), d, ns.OK_SHA), d)

    def test_download_primitive_that_succeeds_without_a_file_is_a_failure(self):
        shim = os.path.join(self.d, "true.sh")
        with open(shim, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(shim, 0o755)
        for want, extra in ((ns.OK_SHA, {}), ("", {"GS_ALLOW_UNPINNED": "1"})):
            r = self.fetch("https://x.invalid/f", self.dest(), want, GS_FETCH_CMD=shim, **extra)
            self.assertFailed(r, self.dest())
            self.assertIn("produced no file", r.err)

    def test_url_cannot_be_parsed_as_a_curl_option(self):
        # an argument starting with '-' must be treated as a (bad) URL, never as an option: -K reads a config file that can say
        # `insecure` and `url=...`; against the self-signed server that would be a TLS bypass
        cfg = os.path.join(self.d, "k.cfg")
        with open(cfg, "w") as f:
            f.write('insecure\nurl = "%s"\n' % self.url("selfs", "/ok"))
        for arg in ("-K" + cfg, "--insecure", "-k", "--config", "-o/tmp/x", "-"):
            d = self.dest()
            self.assertFailed(self.fetch(arg, d, ns.OK_SHA), d)
        self.assertEqual(self.hits("selfs", "/ok"), 0)

    def test_empty_args(self):
        r = self.run_code('fetch_file "" "" ""', [])
        self.assertEqual(r.rc, 2, r)
        r = self.run_code('fetch_file', [])
        self.assertEqual(r.rc, 2, r)


# ========================================================================================== unpinned mode
class TestUnpinned(Base):
    def test_matrix(self):
        for v, ok in ((None, False), ("", False), ("0", False), ("1", True), ("yes", False), ("true", False), (" 1", False), ("1 ", False)):
            d = self.dest("u%s" % v)
            r = self.fetch(self.url("https", "/ok"), d, "", GS_ALLOW_UNPINNED=v)
            if ok:
                self.assertOk(r, d, ns.OK_BODY)
                self.assertIn("WARNING", r.err)
                self.assertIn(ns.OK_SHA, r.err)
            else:
                self.assertFailed(r, d, 2)
        self.assertEqual(self.hits("https", "/ok"), 1, "only the opt-in value may reach the network")

    def test_unpinned_always_downloads_and_overwrites(self):
        d = self.dest()
        with open(d, "w") as f:
            f.write("old")
        r = self.fetch(self.url("https", "/ok"), d, "", GS_ALLOW_UNPINNED="1")
        self.assertOk(r, d, ns.OK_BODY)
        r = self.fetch(self.url("https", "/ok"), d, "", GS_ALLOW_UNPINNED="1")
        self.assertOk(r, d, ns.OK_BODY)
        self.assertEqual(self.hits("https", "/ok"), 2)

    def test_unpinned_does_not_accept_http_error_pages(self):
        # without --fail an unpinned 404 body would be accepted as the file
        for code in (404, 403, 500):
            d = self.dest()
            r = self.fetch(self.url("https", "/status/%d" % code), d, "", GS_ALLOW_UNPINNED="1", GS_FETCH_RETRY="0")
            self.assertFailed(r, d)

    def test_unpinned_reports_the_hash_to_pin(self):
        r = self.fetch(self.url("https", "/big"), self.dest(), "", GS_ALLOW_UNPINNED="1")
        self.assertIn("computed sha256: " + self.fx.big_sha, r.err)

    def test_unpinned_failed_download_keeps_the_old_file(self):
        # documented: without a pin there is no notion of an untrusted old file; the caller sees rc 1
        d = self.dest()
        with open(d, "w") as f:
            f.write("old")
        r = self.fetch(self.url("https", "/status/404"), d, "", GS_ALLOW_UNPINNED="1")
        self.assertEqual(r.rc, 1)
        with open(d) as f:
            self.assertEqual(f.read(), "old")


# ========================================================================================== HTTP errors
class TestHttpErrors(Base):
    def test_404_403_are_not_retried(self):
        for code in (404, 403, 410, 401):
            d = self.dest()
            r = self.fetch(self.url("https", "/status/%d" % code), d, ns.OK_SHA)
            self.assertFailed(r, d)
            self.assertIn("download failed", r.err)
            self.assertIn(str(code), r.err)
            self.assertEqual(self.hits("https", "/status/%d" % code), 1, "a permanent error must not be retried")
            self.assertLess(r.secs, 1.5)

    def test_503_with_retry_knob(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/status/503"), d, ns.OK_SHA, GS_FETCH_RETRY="1")
        self.assertFailed(r, d)
        self.assertEqual(self.hits("https", "/status/503"), 2, r)
        r = self.fetch(self.url("https", "/status/503?b"), d, ns.OK_SHA, GS_FETCH_RETRY="0")
        self.assertEqual(self.hits("https", "/status/503?b"), 1, r)

    def test_transient_500_recovers(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/flaky500"), d, ns.OK_SHA)
        self.assertOk(r, d, ns.OK_BODY)
        self.assertEqual(self.hits("https", "/flaky500"), 2)

    def test_failure_leaves_nothing_even_if_the_error_page_is_large(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/status/404"), d, "0" * 64)
        self.assertFailed(r, d)

    def test_refused_connection(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        d = self.dest()
        r = self.fetch("https://127.0.0.1:%d/ok" % port, d, ns.OK_SHA)
        self.assertFailed(r, d)
        self.assertIn("download failed", r.err)
        self.assertLess(r.secs, 5)


class TestHttpRetryChain(Base):
    def test_500_is_retried_then_fails(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/status/500"), d, ns.OK_SHA)    # default: 1 try + 3 retries, 2 s apart
        self.assertFailed(r, d)
        self.assertEqual(self.hits("https", "/status/500"), 4, r)
        self.assertGreater(r.secs, 5.0, "retry delay not honoured")


# ========================================================================================== redirects
class TestRedirects(Base):
    def test_same_scheme_redirects_are_followed(self):
        for code in (301, 302, 303, 307, 308):
            d = self.dest()
            r = self.fetch(self.url("https", "/redir?code=%d&to=/files/final%d" % (code, code)), d, ns.OK_SHA)
            self.assertOk(r, d, ns.OK_BODY)
            os.unlink(d)

    def test_redirect_to_other_host_name_and_port(self):
        d = self.dest()
        to = "https://localhost:%d/files/x" % self.fx.port("https")
        self.assertOk(self.fetch(self.url("https", "/redir?code=302&to=" + to), d, ns.OK_SHA), d, ns.OK_BODY)

    def test_redirect_chain(self):
        d = self.dest()
        self.assertOk(self.fetch(self.url("https", "/hop/6"), d, ns.OK_SHA), d, ns.OK_BODY)
        self.assertEqual(self.hits("https", "/hop/0"), 1)

    def test_redirect_to_plain_http_is_refused(self):
        d = self.dest()
        target = self.fx.url("plain", "/ok")
        for code in (301, 302, 307):
            r = self.fetch(self.url("https", "/redir?code=%d&to=%s" % (code, target)), d, ns.OK_SHA)
            self.assertFailed(r, d)
        self.assertEqual(self.fx.hits("plain"), [], "the downgraded request must never leave the machine")

    def test_redirect_to_other_schemes_is_refused(self):
        secret = os.path.join(self.d, "secret.txt")
        with open(secret, "wb") as f:
            f.write(ns.OK_BODY)       # a file:// redirect that WOULD verify against the pin if it were followed
        for to in ("file://" + secret, "ftp://127.0.0.1/x", "gopher://127.0.0.1/x", "scp://127.0.0.1/x", "dict://127.0.0.1/x",
                   "//127.0.0.1:%d/ok" % self.fx.port("plain")):
            d = self.dest()
            r = self.fetch(self.url("https", "/redir?code=302&to=" + to), d, ns.OK_SHA)
            self.assertFailed(r, d)
        self.assertEqual(self.fx.hits("plain"), [])

    def test_redirect_loop_terminates(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/loop"), d, ns.OK_SHA)
        self.assertFailed(r, d)
        self.assertLessEqual(self.hits("https", "/loop"), 51)
        self.assertLess(r.secs, 20)

    def test_direct_non_https_urls_are_refused(self):
        secret = os.path.join(self.d, "secret.txt")
        with open(secret, "wb") as f:
            f.write(ns.OK_BODY)
        for u in (self.url("plain", "/ok"), "file://" + secret, "ftp://127.0.0.1/x", "HTTP://127.0.0.1:%d/ok" % self.fx.port("plain"),
                  "127.0.0.1:%d/ok" % self.fx.port("plain"), "//127.0.0.1/x", "gopher://127.0.0.1/x"):
            d = self.dest()
            self.assertFailed(self.fetch(u, d, ns.OK_SHA), d)
        self.assertEqual(self.fx.hits("plain"), [])


# ========================================================================================== interrupted transfers
class TestTransferFaults(Base):
    def test_connection_dropped_mid_body_is_retried_and_recovers(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/drop-once"), d, self.fx.big_sha)
        self.assertOk(r, d, self.fx.big)
        self.assertEqual(self.hits("https", "/drop-once"), 2)
        self.assertIn("retry 1 of 3", r.err)

    def test_retry_knob_zero_gives_up_at_once(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/drop-once"), d, self.fx.big_sha, GS_FETCH_RETRY="0")
        self.assertFailed(r, d)
        self.assertEqual(self.hits("https", "/drop-once"), 1)

    def test_permanent_drop_fails_after_the_retries_and_leaves_nothing(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/drop"), d, ns.OK_SHA, GS_FETCH_RETRY="1")
        self.assertFailed(r, d)
        self.assertEqual(self.hits("https", "/drop"), 2, r)
        self.assertIn("download failed", r.err)

    @unittest.skipUnless(LONG, "NETFETCH_LONG=1: full default retry chain (3 retries, 2 s apart)")
    def test_permanent_drop_default_retry_chain(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/drop"), d, ns.OK_SHA)
        self.assertFailed(r, d)
        self.assertEqual(self.hits("https", "/drop"), 4, r)

    def test_chunked_body_without_terminator(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/drop-chunked"), d, ns.OK_SHA, GS_FETCH_RETRY="0")
        self.assertFailed(r, d)

    def test_truncated_body_never_passes_for_a_complete_file(self):
        # even when the pin is that of the (truncated) bytes the server sent: the transfer error comes first
        d = self.dest()
        r = self.fetch(self.url("https", "/drop"), d, hashlib.sha256(b"y" * (64 << 10)).hexdigest(), GS_FETCH_RETRY="0")
        self.assertFailed(r, d)


# ========================================================================================== stalled servers
class TestStall(Base):
    def test_knob_validation(self):
        for k, v in (("GS_FETCH_RETRY", "x"), ("GS_FETCH_RETRY", "-1"), ("GS_FETCH_RETRY", "1;id"), ("GS_FETCH_RETRY", "--insecure"),
                     ("GS_FETCH_STALL_SECS", "0"), ("GS_FETCH_STALL_SECS", "x"), ("GS_FETCH_STALL_SECS", "1 2"), ("GS_FETCH_STALL_SECS", "-5")):
            d = self.dest()
            r = self.fetch(self.url("https", "/ok"), d, ns.OK_SHA, **{k: v})
            self.assertFailed(r, d, 2)
        self.assertEqual(self.hits("https", "/ok"), 0)

    def test_server_that_never_answers_is_aborted(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/hold-hdr"), d, ns.OK_SHA, timeout=15, GS_FETCH_STALL_SECS="1", GS_FETCH_RETRY="0")
        self.assertFailed(r, d)
        self.assertLess(r.secs, 8)
        self.assertEqual(self.hits("https", "/hold-hdr"), 1)

    def test_server_that_sends_headers_but_no_body_is_aborted(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/hold-body"), d, ns.OK_SHA, timeout=15, GS_FETCH_STALL_SECS="1", GS_FETCH_RETRY="0")
        self.assertFailed(r, d)
        self.assertLess(r.secs, 8)

    def test_slow_but_progressing_download_is_not_aborted(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/trickle"), d, ns.OK_SHA, GS_FETCH_STALL_SECS="2", GS_FETCH_RETRY="0")
        self.assertOk(r, d, ns.OK_BODY)
        self.assertGreater(r.secs, 2.0)

    @unittest.skipUnless(LONG, "NETFETCH_LONG=1: burst then silence takes ~8 s to be detected (curl averages the speed)")
    def test_burst_then_silence_is_aborted(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/hold"), d, ns.OK_SHA, timeout=30, GS_FETCH_STALL_SECS="2", GS_FETCH_RETRY="0")
        self.assertFailed(r, d)


class TestStallRetry(Base):
    def test_stall_is_retried(self):
        d = self.dest()
        r = self.fetch(self.url("https", "/hold-hdr"), d, ns.OK_SHA, timeout=25, GS_FETCH_STALL_SECS="1", GS_FETCH_RETRY="1")
        self.assertFailed(r, d)
        self.assertEqual(self.hits("https", "/hold-hdr"), 2, r)


# ========================================================================================== signals
class TestSignals(Base):
    def start(self, path, code='fetch_file "$1" "$2" "$3"', want=ns.OK_SHA, **extra):
        """Start fetch_file against a server that stalls; return when curl has begun writing (or is waiting)."""
        d = self.dest()
        self._n = getattr(self, "_n", 0) + 1
        q = "%s?n=%d" % (path, self._n)          # unique per call: the server counts hits per URL
        p = self.spawn(code, [self.url("https", q), d, want], env=self.env(**extra))
        self.assertTrue(self.wait_for(lambda: self.hits("https", q) >= 1 and bool(self.curls(self.tag)), 8), "download never started")
        if path == "/hold":      # curl creates the output file with the first body bytes
            self.assertTrue(self.wait_for(lambda: any(os.path.getsize(x) > 0 for x in self.parts()), 8), "no body received yet")
        return p, d

    def check_died(self, p, d, sig, secs=4):
        try:
            p.communicate(timeout=secs)
        except subprocess.TimeoutExpired:
            self.fail("fetch_file did not react to the signal within %d s (a foreground curl defers the trap)" % secs)
        self.assertEqual(p.returncode, -sig, "the signal must be re-raised: the caller dies of it")
        self.assertEqual(self.parts(), [], "partial download left behind")
        self.assertFalse(os.path.exists(d))
        self.assertTrue(self.wait_for(lambda: not self.curls(self.tag), 3), "curl orphaned after the signal")

    def test_signal_to_the_whole_process_group(self):
        for path in ("/hold", "/hold-hdr"):
            for sig in (signal.SIGTERM, signal.SIGINT):
                p, d = self.start(path)
                os.killpg(p.pid, sig)
                self.check_died(p, d, sig)

    def test_signal_to_the_bash_process_only(self):
        # the boundary named in docs/SIM-FUZZ.md D13: curl is NOT signalled, only the shell. It used to wait for curl (forever)
        for path in ("/hold", "/hold-hdr"):
            for sig in (signal.SIGTERM, signal.SIGINT):
                p, d = self.start(path)
                os.kill(p.pid, sig)
                self.check_died(p, d, sig)

    def test_signal_during_retry_pause(self):
        # connection dropped, fetch_file sleeps before the retry: the signal must not wait for the sleep to end
        d = self.dest()
        p = self.spawn('fetch_file "$1" "$2" "$3"', [self.url("https", "/drop"), d, ns.OK_SHA])
        self.assertTrue(self.wait_for(lambda: self.hits("https", "/drop") >= 1, 8))
        time.sleep(0.4)
        t0 = time.time()
        os.kill(p.pid, signal.SIGTERM)
        p.communicate(timeout=5)
        self.assertLess(time.time() - t0, 1.5)
        self.assertEqual(p.returncode, -signal.SIGTERM)
        self.assertEqual(self.parts(), [])

    def test_timeout_wrapper_kills_a_stalled_fetch_without_leftovers(self):
        # `timeout 2 bash -c ...`: the wrapper's signal reaches the bash process (and, by default, its group)
        if shutil.which("timeout") is None:
            self.skipTest("timeout(1) not found")
        d = self.dest()
        r = self.run_code('timeout 2 bash -c \'. "$L"; fetch_file "$1" "$2" "$3"\' _ "$1" "$2" "$3"',
                          [self.url("https", "/hold-hdr"), d, ns.OK_SHA], timeout=15)
        self.assertEqual(r.rc, 124, r)
        self.assertFalse(os.path.exists(d))
        self.assertTrue(self.wait_for(lambda: not self.curls(self.tag), 3), "curl still running after the wrapper timed out")
        self.assertClean()

    def test_callers_exit_trap_still_runs_and_traps_are_restored(self):
        code = 'trap "echo CALLER_EXIT" EXIT; fetch_file "$1" "$2" "$3"'
        p, d = self.start("/hold-hdr", code)
        os.kill(p.pid, signal.SIGTERM)
        out, _ = p.communicate(timeout=5)
        self.assertEqual(p.returncode, -signal.SIGTERM)
        self.assertIn("CALLER_EXIT", out)
        self.assertEqual(self.parts(), [])
        r = self.run_code('trap "echo CALLER_EXIT" EXIT; trap "echo CALLER_TERM" TERM; fetch_file "$1" "$2" "$3" >/dev/null; trap -p EXIT TERM',
                          [self.url("https", "/ok"), self.dest("t"), ns.OK_SHA])
        self.assertEqual(r.rc, 0, r)
        self.assertIn("echo CALLER_EXIT", r.out)
        self.assertIn("echo CALLER_TERM", r.out)
        self.assertNotIn("_gs_fetch", r.out)

    def test_command_substitution_caller(self):
        d = self.dest()
        r = self.run_code('trap "echo CALLER_EXIT" EXIT; out="$(fetch_file "$1" "$2" "$3" 2>&1)"; echo "rc=$? [$out]"',
                          [self.url("https", "/ok"), d, ns.OK_SHA])
        self.assertEqual(r.out.count("CALLER_EXIT"), 1, r)
        self.assertTrue(r.out.startswith("rc=0"), r)
        self.assertOk(Res(0, "", "", 0), d, ns.OK_BODY)

    def test_set_e_and_set_x_callers(self):
        for mode in ("-e", "-ex", "-euo pipefail"):
            d = self.dest("m" + mode.replace(" ", ""))
            r = self.run_code('set %s; fetch_file "$1" "$2" "$3"; echo reached-end' % mode, [self.url("https", "/ok"), d, ns.OK_SHA])
            self.assertEqual(r.rc, 0, r)
            self.assertIn("reached-end", r.out)
            r = self.run_code('set %s; fetch_file "$1" "$2" "$3"; echo NOT-REACHED' % mode, [self.url("https", "/status/404"), d + "x", ns.OK_SHA])
            self.assertNotEqual(r.rc, 0)
            self.assertNotIn("NOT-REACHED", r.out)

    def test_sigkill_limit(self):
        # LIMIT (documented): SIGKILL cannot be trapped; the .part file stays (a later run with another pid does not reuse it)
        p, d = self.start("/hold")
        os.kill(p.pid, signal.SIGKILL)
        p.wait(timeout=5)
        self.assertEqual(len(self.parts()), 1)
        self.kill_curls()      # the orphaned curl keeps the output pipes open until it is gone
        p.communicate(timeout=5)

    def test_parallel_fetches_of_the_same_destination(self):
        # four subshells in one script share $$; the temp name must still be distinct per subshell
        d = self.dest()
        r = self.run_code('for i in 1 2 3 4; do ( fetch_file "$1" "$2" "$3" >/dev/null 2>&1; echo "rc$i=$?" ) & done; wait',
                          [self.url("https", "/big"), d, self.fx.big_sha])
        self.assertEqual(sorted(r.out.split()), ["rc1=0", "rc2=0", "rc3=0", "rc4=0"], r)
        self.assertOk(Res(0, "", "", 0), d, self.fx.big)


# ========================================================================================== TLS and proxies
class TestTls(Base):
    def reject(self, url, **extra):
        self._rej = getattr(self, "_rej", 0) + 1
        d = self.dest("rej%d" % self._rej)      # distinct: an existing file with the pinned hash would be accepted without a download
        r = self.fetch(url, d, ns.OK_SHA, GS_FETCH_RETRY="0", **extra)
        self.assertFailed(r, d)
        self.assertIn("download failed", r.err)
        return r

    def test_ca_signed_certificate_accepted_only_with_its_ca(self):
        d = self.dest()
        self.assertOk(self.fetch(self.url("https", "/ok"), d, ns.OK_SHA), d, ns.OK_BODY)
        other = os.path.join(self.fx.dir, "self.crt")     # a bundle that does not contain the test CA
        self.reject(self.url("https", "/ok"), CURL_CA_BUNDLE=other)
        self.reject(self.url("https", "/ok"), CURL_CA_BUNDLE="/nonexistent/ca.pem")

    def test_self_signed_certificate_rejected(self):
        r = self.reject(self.url("selfs", "/ok"))
        self.assertIn("certificate", r.err.lower())
        self.assertEqual(self.hits("selfs", "/ok"), 0, "the request must not be sent after a failed handshake")

    def test_wrong_host_name_rejected(self):
        port = self.fx.add_tls("wrongname", "wrongname")
        r = self.reject("https://127.0.0.1:%d%s" % (port, self.path("/ok")))
        self.assertIn("name", r.err.lower())
        self.assertEqual(self.hits("wrongname", "/ok"), 0)

    def test_expired_certificate_rejected(self):
        port = self.fx.add_tls("expired", "expired")
        r = self.reject("https://127.0.0.1:%d%s" % (port, self.path("/ok")))
        self.assertIn("expired", r.err.lower())
        self.assertEqual(self.hits("expired", "/ok"), 0)

    def test_redirect_to_a_self_signed_server_rejected(self):
        self.reject(self.url("https", "/redir?code=302&to=" + self.fx.url("selfs", "/ok")))
        self.assertEqual(self.hits("selfs", "/ok"), 0)

    def test_curlrc_with_insecure_is_ignored(self):
        # ~/.curlrc and $CURL_HOME/.curlrc are read by a plain `curl`: `insecure` there would silently disable verification
        home2 = os.path.join(self.d, "home2")
        os.mkdir(home2)
        for h in (self.home, home2):
            with open(os.path.join(h, ".curlrc"), "w") as f:
                f.write("insecure\nproxy-insecure\n")
        r = self.reject(self.url("selfs", "/ok"))
        self.reject(self.url("selfs", "/ok"), CURL_HOME=home2, HOME="/nonexistent")
        self.assertEqual(self.hits("selfs", "/ok"), 0)
        # control: a plain curl in the same environment DOES honour it (otherwise this test proves nothing)
        c = subprocess.run(["curl", "-sS", "--noproxy", "*", "-o", "-", self.fx.url("selfs", "/ok")], env=self.env(), capture_output=True)
        self.assertEqual(c.returncode, 0, "control failed: this curl ignores .curlrc")
        del r

    def test_minimum_tls_version_is_1_2(self):
        port = self.fx.add_tls("old", "srv", ssl.TLSVersion.TLSv1_1)
        cnf = os.path.join(self.d, "permissive.cnf")
        with open(cnf, "w") as f:
            f.write(ns.PERMISSIVE_OPENSSL_CNF)
        url = "https://127.0.0.1:%d%s" % (port, self.path("/ok"))
        ctl = subprocess.run(["curl", "-sS", "--noproxy", "*", "--cacert", self.fx.ca, "-o", "-", url], env=self.env(OPENSSL_CONF=cnf),
                             capture_output=True)
        if ctl.returncode != 0:
            self.skipTest("this OpenSSL cannot be configured to speak TLS 1.1 (control curl failed: %s)" % ctl.stderr[-100:].decode("latin-1"))
        before = self.hits("old", "/ok")       # the control request above reached the server
        self.reject(url, OPENSSL_CONF=cnf)
        self.assertEqual(self.hits("old", "/ok"), before)

    def test_https_proxy_variables_are_honoured_never_bypassed(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
        s.close()
        for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            d = self.dest()
            r = self.fetch(self.url("https", "/ok"), d, ns.OK_SHA, env=self.env(NO_PROXY=None, **{var: "http://127.0.0.1:%d" % closed}))
            self.assertFailed(r, d)
            self.assertIn("download failed", r.err)
        for v in ("socks5h://127.0.0.1:%d" % closed, "http://user:pw@127.0.0.1:%d" % closed):
            d = self.dest()
            self.assertFailed(self.fetch(self.url("https", "/ok"), d, ns.OK_SHA, env=self.env(NO_PROXY=None, ALL_PROXY=v)), d)
        self.assertEqual(self.hits("https", "/ok"), 0, "the request reached the server although a (dead) proxy was configured")

    def test_working_proxy_is_used_and_tls_is_still_verified_through_it(self):
        px = "http://127.0.0.1:%d" % self.fx.proxy.port
        d = self.dest()
        r = self.fetch(self.url("https", "/ok"), d, ns.OK_SHA, env=self.env(NO_PROXY=None, HTTPS_PROXY=px))
        self.assertOk(r, d, ns.OK_BODY)
        self.assertTrue(any(l.startswith("CONNECT 127.0.0.1:%d " % self.fx.port("https")) for l in self.fx.proxy_log), self.fx.proxy_log)
        before = len(self.fx.proxy_log)
        d2 = self.dest("o2")
        r = self.fetch(self.url("selfs", "/ok"), d2, ns.OK_SHA, env=self.env(NO_PROXY=None, HTTPS_PROXY=px, GS_FETCH_RETRY="0"))
        self.assertFailed(r, d2)
        self.assertGreater(len(self.fx.proxy_log), before, "the tunnel was not used")
        self.assertEqual(self.hits("selfs", "/ok"), 0)
        # a plain-http proxy variable must not downgrade an https URL
        d3 = self.dest("o3")
        r = self.fetch(self.url("https", "/ok?x"), d3, ns.OK_SHA, env=self.env(NO_PROXY=None, http_proxy=px, HTTP_PROXY=px))
        self.assertOk(r, d3, ns.OK_BODY)

    def test_static_command_line_has_no_tls_or_proxy_bypass(self):
        with open(LIB) as f:
            code = [ln for ln in f.read().splitlines() if not ln.lstrip().startswith("#")]
        text = "\n".join(code)
        bad = [r"(^|\s)-k(\s|$)", r"--insecure", r"--proxy-insecure", r"--no-check-certificate", r"(^|\s)-[a-zA-Z]*k[a-zA-Z]*(\s|$)", r"--noproxy",
               r"--proxy\b", r"(^|\s)-x\s", r"--cacert", r"--capath", r"--ssl-no-revoke", r"--tls-max", r"--tlsv1\.[01]\b", r"--sslv[23]",
               r"--proto\s+'?=?[^']*\bhttp\b(?!s)", r"--proto-redir\s+'?=?[^']*\b(http|file|ftp)\b(?!s)", r"--ciphers", r"--resolve", r"--connect-to",
               r"GIT_SSL_NO_VERIFY", r"sslverify\s*=\s*false", r"unset\s+(https?|all)_proxy", r"unset\s+(HTTPS?|ALL)_PROXY"]
        for pat in bad:
            self.assertIsNone(re.search(pat, text), "forbidden pattern %r found in %s" % (pat, LIB))
        for must in (r"curl -q --fail --location", r"--proto '=https'", r"--proto-redir '=https'", r"--tlsv1\.2", r'--url "\$url"'):
            self.assertIsNotNone(re.search(must, text), "missing %r" % must)


# ========================================================================================== idempotence
class TestIdempotence(Base):
    def test_existing_file_with_the_pinned_hash_is_not_downloaded_again(self):
        d = self.dest()
        self.assertOk(self.fetch(self.url("https", "/ok"), d, ns.OK_SHA), d, ns.OK_BODY)
        st = os.stat(d)
        time.sleep(0.05)
        r = self.fetch(self.url("https", "/ok"), d, ns.OK_SHA)
        self.assertOk(r, d, ns.OK_BODY)
        self.assertIn("already present", r.out)
        self.assertEqual(self.hits("https", "/ok"), 1)
        self.assertEqual((st.st_ino, st.st_mtime_ns), (os.stat(d).st_ino, os.stat(d).st_mtime_ns), "file was rewritten")

    def test_pinned_file_present_works_without_any_server(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        d = self.dest()
        with open(d, "wb") as f:
            f.write(ns.OK_BODY)
        r = self.fetch("https://127.0.0.1:%d/ok" % port, d, ns.OK_SHA.upper())
        self.assertOk(r, d, ns.OK_BODY)

    def test_existing_file_with_a_wrong_hash_is_replaced(self):
        d = self.dest()
        with open(d, "wb") as f:
            f.write(b"corrupted")
        r = self.fetch(self.url("https", "/ok"), d, ns.OK_SHA)
        self.assertOk(r, d, ns.OK_BODY)
        self.assertEqual(self.hits("https", "/ok"), 1)
        self.assertIn("downloading again", r.err)

    def test_wrong_file_does_not_survive_a_failed_download(self):
        for p in ("/status/404", "/drop"):
            d = self.dest()
            with open(d, "wb") as f:
                f.write(b"corrupted")
            r = self.fetch(self.url("https", p), d, ns.OK_SHA, GS_FETCH_RETRY="0")
            self.assertFailed(r, d)
        d = self.dest()
        with open(d, "wb") as f:
            f.write(b"corrupted")
        self.assertFailed(self.fetch(self.url("selfs", "/ok"), d, ns.OK_SHA), d)

    def test_wrong_file_is_replaced_even_if_the_new_download_has_another_wrong_hash(self):
        d = self.dest()
        with open(d, "wb") as f:
            f.write(b"corrupted")
        self.assertFailed(self.fetch(self.url("https", "/ok"), d, "1" * 64), d)

    def test_symlink_destination_is_never_written_through(self):
        target = os.path.join(self.d, "target")
        with open(target, "wb") as f:
            f.write(b"precious")
        link = self.dest("link")
        os.symlink(target, link)
        r = self.fetch(self.url("https", "/ok"), link, ns.OK_SHA)
        self.assertOk(r, link, ns.OK_BODY)
        self.assertFalse(os.path.islink(link))
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"precious")
        # dangling link: replaced by the file
        link2 = self.dest("link2")
        os.symlink(os.path.join(self.d, "nowhere"), link2)
        self.assertOk(self.fetch(self.url("https", "/ok"), link2, ns.OK_SHA), link2, ns.OK_BODY)
        # a link to a file that already has the pinned hash is accepted without a download
        link3 = self.dest("link3")
        good = os.path.join(self.d, "good")
        with open(good, "wb") as f:
            f.write(ns.OK_BODY)
        os.symlink(good, link3)
        before = self.hits("https", "/ok")
        self.assertOk(self.fetch(self.url("https", "/ok"), link3, ns.OK_SHA), link3, ns.OK_BODY)
        self.assertEqual(self.hits("https", "/ok"), before)

    def test_repeated_runs_converge(self):
        d = self.dest()
        for _ in range(3):
            self.assertOk(self.fetch(self.url("https", "/big"), d, self.fx.big_sha), d, self.fx.big)
        self.assertEqual(self.hits("https", "/big"), 1)

    def test_pin_from_manifest_with_real_curl(self):
        d = self.dest()
        r = self.run_code('FOO_URL="$1" FOO_PIN="$3"; pin_from_manifest FOO "$2"', [self.url("https", "/ok"), d, ns.OK_SHA])
        self.assertOk(r, d, ns.OK_BODY)
        r = self.run_code('FOO_URL="$1" FOO_PIN=""; pin_from_manifest FOO "$2"', [self.url("https", "/ok?m"), self.dest("m2"), ns.OK_SHA])
        self.assertFailed(r, self.dest("m2"), 2)


# ========================================================================================== randomized invariants
class TestRandomized(Base):
    """Seeded random sequences of (server behaviour, destination name, destination pre-state, pin); the oracle below says what the
    outcome must be. Only fast scenarios (no retry pauses) unless NETFETCH_LONG=1."""

    def scenarios(self):
        fx = self.fx
        sc = [  # (label, kind, path, served body or None for 'cannot succeed'[, GS_FETCH_RETRY, default 0])
            ("ok", "https", "/ok", ns.OK_BODY), ("empty", "https", "/empty", b""), ("big", "https", "/big", fx.big),
            ("404", "https", "/status/404", None), ("403", "https", "/status/403", None), ("401", "https", "/status/401", None),
            ("selfs", "selfs", "/ok", None), ("plain", "plain", "/ok", None),
            ("redir", "https", "/redir?code=302&to=/ok", ns.OK_BODY), ("redir-plain", "https", "/redir?code=302&to=" + fx.url("plain", "/ok"), None),
            ("files", "https", "/files/r%20s", ns.OK_BODY),
        ]
        if LONG:
            sc += [("flaky", "https", "/flaky500", ns.OK_BODY, "1"), ("drop-once", "https", "/drop-once", fx.big, "1"),
                   ("500", "https", "/status/500", None, "0")]
        return sc

    def test_oracle(self):
        R = random.Random(SEED)
        sc = self.scenarios()
        for i in range(int(round((250 if LONG else 36) * SCALE))):
            label, kind, path, body, *retry = R.choice(sc)
            name = R.choice(NASTY_NAMES)
            pre = R.choice(["none", "none", "good", "bad", "unpinned"])
            pin_kind = R.choice(["right", "right", "wrong", "format", "upper"]) if pre != "unpinned" else "unpinned"
            right = hashlib.sha256(body if body is not None else ns.OK_BODY).hexdigest()
            want = {"right": right, "wrong": "f" * 64 if right[0] != "f" else "e" * 64, "format": R.choice(["zz", right[:63], ""]),
                    "upper": right.upper(), "unpinned": ""}[pin_kind]
            wd = os.path.join(self.work, "r%d" % i)
            os.mkdir(wd)
            if pre == "good":
                with open(os.path.join(wd, name), "wb") as f:
                    f.write(body if body is not None else ns.OK_BODY)
            elif pre in ("bad", "unpinned"):
                with open(os.path.join(wd, name), "wb") as f:
                    f.write(b"old-content")
            url = self.fx.url(kind, self.path(path) + "&i=%d" % i)   # path() always leaves a query: ...?t=<tag>
            extra = {"GS_FETCH_RETRY": retry[0] if retry else "0"}
            if pin_kind == "unpinned":
                extra["GS_ALLOW_UNPINNED"] = "1"
            r = self.fetch(url, name, want, cwd=wd, **extra)
            ctx = f"#{i} {label} name={name!r} pre={pre} pin={pin_kind}: {r}"
            dest = os.path.join(wd, name)
            exists = os.path.lexists(dest)
            content = None
            if exists:
                with open(dest, "rb") as f:
                    content = f.read()
            self.assertEqual([f for f in os.listdir(wd) if ".part." in f], [], ctx)
            valid = bool(re.fullmatch(r"[0-9a-fA-F]{64}", want)) or pin_kind == "unpinned"
            if not valid:
                self.assertEqual(r.rc, 2, ctx)
                self.assertEqual(content, {"none": None, "good": body if body is not None else ns.OK_BODY, "bad": b"old-content",
                                           "unpinned": b"old-content"}[pre], ctx)
                continue
            if pin_kind != "unpinned" and pre == "good" and want.lower() == right:
                self.assertEqual((r.rc, content), (0, body if body is not None else ns.OK_BODY), ctx)
                self.assertIn("already present", r.out, ctx)
                continue
            served_ok = body is not None and hashlib.sha256(body).hexdigest() == want.lower() or (pin_kind == "unpinned" and body is not None)
            if served_ok:
                self.assertEqual((r.rc, content), (0, body), ctx)
            else:
                self.assertEqual(r.rc, 1, ctx)
                if pin_kind == "unpinned":
                    self.assertEqual(content, b"old-content", ctx)
                else:
                    self.assertIsNone(content, ctx)


if __name__ == "__main__":
    unittest.main(verbosity=1)
