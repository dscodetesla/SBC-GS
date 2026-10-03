"""Deterministic loopback HTTP(S) fixture for build/lib/fetch.sh (stdlib + the `openssl` CLI for throw-away certificates).

Binds 127.0.0.1 only, on ephemeral ports. Three listeners:
  https  certificate signed by a throw-away test CA (the tests hand the CA file to curl through CURL_CA_BUNDLE: this is a
         trust anchor, not a verification bypass; a client without the CA must reject the server)
  selfs  HTTPS with a self-signed certificate that no CA vouches for (must be REJECTED)
  plain  plain HTTP (must never be reached by fetch.sh: used to prove that scheme downgrades are refused)
  proxy  a CONNECT proxy (loopback targets only), to prove that proxy variables are honoured and TLS is still verified through it
Behaviour is selected by the URL path (see Handler.route); every request is logged in Fixture.log as (listener, path).
Extra listeners (wrong host name, expired certificate, protocol cap) are added with Fixture.add_tls().
"""
import hashlib
import os
import random
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.parse
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OK_BODY = b"payload-one\n"
OK_SHA = hashlib.sha256(OK_BODY).hexdigest()


def big_body(mib=4):
    """Deterministic pseudo-random bytes (stdlib Mersenne Twister, fixed seed)."""
    r = random.Random(20261003)
    return r.getrandbits(8 * 1024 * 1024 * mib).to_bytes(1024 * 1024 * mib, "big")


PERMISSIVE_OPENSSL_CNF = """openssl_conf = default_conf
[default_conf]
ssl_conf = ssl_sect
[ssl_sect]
system_default = system_default_sect
[system_default_sect]
MinProtocol = TLSv1
CipherString = DEFAULT:@SECLEVEL=0
"""


def have_openssl():
    return shutil.which("openssl") is not None


def _run(*cmd, cwd=None):
    subprocess.run(cmd, cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def make_pki(d):
    """Create d/ca.pem, d/srv.{crt,key} (CA-signed, SAN 127.0.0.1+localhost), d/self.{crt,key} (self-signed),
    d/wrongname.{crt,key} (CA-signed for another host name), d/expired.{crt,key} (CA-signed, validity in the past)."""
    ext = os.path.join(d, "san.ext")
    with open(ext, "w") as f:
        f.write("subjectAltName=IP:127.0.0.1,DNS:localhost\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature\nextendedKeyUsage=serverAuth\n")
    ext2 = os.path.join(d, "wrong.ext")
    with open(ext2, "w") as f:
        f.write("subjectAltName=DNS:other.invalid\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature\nextendedKeyUsage=serverAuth\n")
    _run("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "ca.key", cwd=d)
    _run("openssl", "req", "-x509", "-new", "-key", "ca.key", "-subj", "/CN=netfetch test CA", "-days", "2", "-out", "ca.pem", cwd=d,
         )
    for name, ex in (("srv", ext), ("wrongname", ext2)):
        _run("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", name + ".key", cwd=d)
        _run("openssl", "req", "-new", "-key", name + ".key", "-subj", "/CN=" + name, "-out", name + ".csr", cwd=d)
        _run("openssl", "x509", "-req", "-in", name + ".csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
             "-days", "2", "-extfile", ex, "-out", name + ".crt", cwd=d)
    _run("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "self.key", cwd=d)
    _run("openssl", "req", "-x509", "-new", "-key", "self.key", "-subj", "/CN=localhost", "-days", "2",
         "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost", "-out", "self.crt", cwd=d)
    # expired: `openssl ca` is the only OpenSSL 3.0 CLI path that takes explicit start/end dates
    os.makedirs(os.path.join(d, "ca"), exist_ok=True)
    open(os.path.join(d, "ca", "index.txt"), "w").close()
    with open(os.path.join(d, "ca", "serial"), "w") as f:
        f.write("1000\n")
    with open(os.path.join(d, "ca.cnf"), "w") as f:
        f.write("[ca]\ndefault_ca=c\n[c]\ndatabase=%s/ca/index.txt\nserial=%s/ca/serial\nnew_certs_dir=%s/ca\n"
                "default_md=sha256\npolicy=p\nunique_subject=no\ncopy_extensions=none\n[p]\ncommonName=supplied\n" % (d, d, d))
    _run("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "expired.key", cwd=d)
    _run("openssl", "req", "-new", "-key", "expired.key", "-subj", "/CN=expired", "-out", "expired.csr", cwd=d)
    _run("openssl", "ca", "-batch", "-config", "ca.cnf", "-cert", "ca.pem", "-keyfile", "ca.key", "-in", "expired.csr",
         "-out", "expired.crt", "-startdate", "20000101000000Z", "-enddate", "20000102000000Z", "-extfile", ext, cwd=d)


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass   # handshake aborts by clients that reject the certificate are the expected outcome of the TLS tests


class Fixture:
    """Starts the listeners; use as a context manager. Attributes: ca, log; port(name), url(name, path)."""

    def __init__(self, big_mib=4):
        self.dir = tempfile.mkdtemp(prefix="nf-pki-")
        make_pki(self.dir)
        self.ca = os.path.join(self.dir, "ca.pem")
        self.big = big_body(big_mib)
        self.big_sha = hashlib.sha256(self.big).hexdigest()
        self.log = []
        self.proxy_log = []
        self.counts = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.servers = {}
        self.extra = {}          # name -> (certfile, keyfile) for add_tls()
        self._start("https", self._ctx("srv"))
        self._start("selfs", self._ctx("self"))
        self._start("plain", None)
        self.proxy = ConnectProxy(self)

    def _ctx(self, name):
        c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        c.load_cert_chain(os.path.join(self.dir, name + ".crt"), os.path.join(self.dir, name + ".key"))
        return c

    def _start(self, name, ctx):
        srv = QuietServer(("127.0.0.1", 0), Handler)
        srv.fx, srv.kind, srv.ctx = self, name, ctx
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.servers[name] = srv

    def add_tls(self, name, certname, max_version=None):
        """Extra HTTPS listener with another certificate (wrongname / expired) or a protocol cap."""
        ctx = self._ctx(certname)
        if max_version is not None:
            ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
            with warnings.catch_warnings():       # TLS 1.1 is used on purpose (the client must refuse it); unittest re-enables the warning
                warnings.simplefilter("ignore", DeprecationWarning)
                ctx.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
                ctx.maximum_version = max_version
        self._start(name, ctx)
        return self.port(name)

    def port(self, name):
        return self.servers[name].server_address[1]

    def url(self, name, path):
        scheme = "http" if name == "plain" else "https"
        return "%s://127.0.0.1:%d%s" % (scheme, self.port(name), path)

    def hits(self, name=None, path=None):
        with self.lock:
            return [e for e in self.log if (name is None or e[0] == name) and (path is None or e[1] == path)]

    def count(self, key):
        with self.lock:
            self.counts[key] = self.counts.get(key, 0) + 1
            return self.counts[key]

    def close(self):
        self.stop.set()
        self.proxy.close()
        for s in self.servers.values():
            s.shutdown()
            s.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class ConnectProxy:
    """Minimal HTTP CONNECT proxy on loopback (tunnels bytes, never looks inside TLS). Fixture.proxy_log lists the CONNECT targets."""

    def __init__(self, fx):
        self.fx = fx
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        try:
            data = b""
            while b"\r\n\r\n" not in data and len(data) < 8192:
                chunk = c.recv(4096)
                if not chunk:
                    return
                data += chunk
            first = data.split(b"\r\n", 1)[0].decode("latin-1")
            parts = first.split()
            with self.fx.lock:
                self.fx.proxy_log.append(first)
            if len(parts) < 2 or parts[0] != "CONNECT":
                c.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
                return
            host, _, port = parts[1].rpartition(":")
            if host != "127.0.0.1":     # loopback only, whatever the client asks for
                c.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                return
            u = socket.create_connection((host, int(port)), timeout=10)
            try:
                c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                t = threading.Thread(target=self._pipe, args=(u, c), daemon=True)
                t.start()
                self._pipe(c, u)
                t.join(5)
            finally:
                u.close()
        except OSError:
            pass
        finally:
            c.close()

    @staticmethod
    def _pipe(a, b):
        try:
            while True:
                d = a.recv(65536)
                if not d:
                    break
                b.sendall(d)
        except OSError:
            pass
        try:
            b.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    def close(self):
        self.sock.close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 90

    def setup(self):
        ctx = self.server.ctx
        if ctx is not None:
            self.request = ctx.wrap_socket(self.request, server_side=True)   # handshake here, in the handler thread
        super().setup()

    def finish(self):
        try:
            super().finish()
        finally:
            try:
                self.request.close()     # the TLS wrapper owns the descriptor (wrap_socket detached the original socket)
            except OSError:
                pass

    def handle(self):
        try:
            super().handle()
        except (ssl.SSLError, OSError, ValueError):
            pass   # a client that rejects our certificate aborts the handshake: expected, not an error

    def log_message(self, *a):
        pass

    def send_body(self, body, code=200, extra=()):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/octet-stream")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def hard_close(self):
        self.close_connection = True
        try:
            self.wfile.flush()
            self.request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.connection.close()

    def do_GET(self):
        fx, kind = self.server.fx, self.server.kind
        parsed = urllib.parse.urlsplit(self.path)
        p = parsed.path
        q = urllib.parse.parse_qs(parsed.query)
        with fx.lock:
            fx.log.append((kind, self.path))
        n = fx.count((kind, self.path))      # keyed by the full path+query: tests use a unique ?t=<name> per scenario
        if p == "/ok" or p.startswith("/files/"):
            return self.send_body(OK_BODY)
        if p == "/empty":
            return self.send_body(b"")
        if p == "/big":
            return self.send_body(fx.big)
        if p.startswith("/status/"):
            code = int(p.split("/")[2])
            return self.send_body(b"error body\n", code)
        if p == "/flaky500":        # first request -> 500, then fine (curl --retry retries 5xx)
            return self.send_body(b"boom\n", 500) if n == 1 else self.send_body(OK_BODY)
        if p == "/redir":           # /redir?code=302&to=<absolute or relative URL>
            return self.send_body(b"moved\n", int(q["code"][0]), (("Location", q["to"][0]),))
        if p.startswith("/hop/"):   # /hop/<n>: n redirects (301, 302, 307, 308, ...) ending at the normal body
            k = int(p.split("/")[2])
            if k == 0:
                return self.send_body(OK_BODY)
            loc = "/hop/%d?%s" % (k - 1, parsed.query)
            return self.send_body(b"moved\n", (301, 302, 307, 308)[k % 4], (("Location", loc),))
        if p == "/loop":
            return self.send_body(b"loop\n", 302, (("Location", "/loop"),))
        if p == "/drop":            # promise 1 MiB, send 64 KiB, close the socket
            return self.partial(1 << 20, 64 << 10, always=True, n=n)
        if p == "/drop-once":       # first attempt dropped mid-transfer, second attempt is complete
            return self.partial(len(fx.big), len(fx.big) // 2, always=False, n=n)
        if p == "/drop-chunked":    # chunked body without the terminating chunk, then close
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(b"5\r\nhello\r\n")
            return self.hard_close()
        if p == "/hold":            # headers + 64 KiB then silence until the fixture stops (stalled mid-transfer)
            self.send_response(200)
            self.send_header("Content-Length", str(1 << 20))
            self.end_headers()
            self.wfile.write(b"x" * (64 << 10))
            self.wfile.flush()
            fx.stop.wait(120)
            return self.hard_close()
        if p == "/hold-body":       # complete headers promising a body, no body byte ever
            self.send_response(200)
            self.send_header("Content-Length", str(1 << 20))
            self.end_headers()
            self.wfile.flush()
            fx.stop.wait(120)
            return self.hard_close()
        if p == "/hold-hdr":        # accept and read the request, never answer
            fx.stop.wait(120)
            return self.hard_close()
        if p == "/trickle":         # complete but slow: 1 byte every 0.2 s (12 bytes)
            self.send_response(200)
            self.send_header("Content-Length", str(len(OK_BODY)))
            self.end_headers()
            for b in OK_BODY:
                self.wfile.write(bytes([b]))
                self.wfile.flush()
                time.sleep(0.2)
            return
        return self.send_body(b"no such fixture path\n", 404)

    do_HEAD = do_GET

    def partial(self, promised, sent, always, n):
        fx = self.server.fx
        if not always and n > 1:
            return self.send_body(fx.big)
        body = (fx.big if promised == len(fx.big) else b"y" * sent)[:sent]
        self.send_response(200)
        self.send_header("Content-Length", str(promised))
        self.end_headers()
        self.wfile.write(body)
        return self.hard_close()
