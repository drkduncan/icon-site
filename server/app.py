"""Tiny server for icon-site: serves the page, proxies remote logos and hosts
saved contact photos at stable public URLs.

Standard library only, so the container needs nothing but Python.

Endpoints (all relative to wherever the app is mounted):
  GET  /                    the page and its static files
  GET  /api/health          liveness check
  GET  /api/logo?url=...    fetch a remote image server-side (same-origin, so
                            the canvas export is never tainted by CORS)
  POST /api/photos[?name=]  store a PNG/JPEG export, returns its public URL
  GET  /photos/<file>       serve a stored photo (the URL for Google Contacts)
"""

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urljoin, urlsplit

STATIC_DIR = os.path.realpath(os.environ.get("STATIC_DIR", os.path.join(os.path.dirname(__file__), "..")))
DATA_DIR = os.environ.get("DATA_DIR", "/data")
PHOTO_DIR = os.path.join(DATA_DIR, "photos")
PORT = int(os.environ.get("PORT", "8080"))
# Public base URL, e.g. https://icons.example.com or https://example.com/icons.
# When unset it is built from the X-Forwarded-* headers SWAG sends.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
# Path prefix the app is mounted under when the proxy does not strip it.
BASE_PATH = "/" + os.environ.get("BASE_PATH", "").strip("/") if os.environ.get("BASE_PATH", "").strip("/") else ""

MAX_LOGO_BYTES = int(os.environ.get("MAX_LOGO_BYTES", str(5 * 1024 * 1024)))
MAX_PHOTO_BYTES = int(os.environ.get("MAX_PHOTO_BYTES", str(5 * 1024 * 1024)))
FETCH_TIMEOUT = float(os.environ.get("FETCH_TIMEOUT", "10"))
MAX_REDIRECTS = 3
ALLOWED_PORTS = {80, 443}
# Only for tests: lets the logo proxy reach loopback/private addresses.
ALLOW_PRIVATE = os.environ.get("LOGO_ALLOW_PRIVATE") == "1"

USER_AGENT = "Mozilla/5.0 (compatible; icon-site/1.0; +https://github.com/drkduncan/icon-site)"

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

LOGO_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml",
    "image/x-icon", "image/vnd.microsoft.icon", "image/avif", "image/bmp",
}

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
PHOTO_RE = re.compile(r"^([a-z0-9][a-z0-9-]{0,63})\.(png|jpg)$")


class FetchError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def sniff_image(data):
    """Returns the image MIME type from magic bytes, or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "image/avif"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if data.startswith(b"BM"):
        return "image/bmp"
    head = data[:1024].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head) or \
            (head.startswith(b"<!doctype svg")):
        return "image/svg+xml"
    return None


def address_allowed(ip):
    ip = ipaddress.ip_address(ip)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ALLOW_PRIVATE:
        return True
    return ip.is_global and not ip.is_multicast


def resolve_public(host, port):
    """Resolves host and returns the addresses to try, IPv4 first. Refuses the host
    if any of its addresses is private, loopback, link-local or reserved, so
    a name cannot point the proxy at the internal network."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise FetchError(502, "Could not resolve the logo's host name.")
    addrs = [info[4][0] for info in infos]
    if not addrs:
        raise FetchError(502, "Could not resolve the logo's host name.")
    for addr in addrs:
        if not address_allowed(addr):
            raise FetchError(403, "That address is not allowed.")
    # Docker networks are often IPv4-only, so try those first.
    return sorted(set(addrs), key=lambda a: ":" in a)


def _connect(addrs, port, timeout):
    err = None
    for addr in addrs:
        try:
            return socket.create_connection((addr, port), timeout)
        except OSError as e:
            err = e
    raise err


def validate_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError(400, "Only http and https logo URLs are supported.")
    if not parts.hostname:
        raise FetchError(400, "The logo URL has no host.")
    if parts.username or parts.password:
        raise FetchError(400, "Logo URLs with credentials are not allowed.")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise FetchError(400, "The logo URL has an invalid port.")
    if port not in ALLOWED_PORTS and not ALLOW_PRIVATE:
        raise FetchError(403, "Only ports 80 and 443 are allowed.")
    return parts, port


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Connects to pre-resolved addresses while keeping the Host header."""

    def __init__(self, host, port, addrs, **kw):
        super().__init__(host, port, **kw)
        self._addrs = addrs

    def connect(self):
        self.sock = _connect(self._addrs, self.port, self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to pre-resolved addresses; SNI and certificate checks still
    use the host name."""

    def __init__(self, host, port, addrs, **kw):
        super().__init__(host, port, context=ssl.create_default_context(), **kw)
        self._addrs = addrs

    def connect(self):
        sock = _connect(self._addrs, self.port, self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def fetch_logo(url):
    """Fetches a remote image with SSRF, type and size guards.
    Returns (bytes, content_type)."""
    for _ in range(MAX_REDIRECTS + 1):
        parts, port = validate_url(url)
        addrs = resolve_public(parts.hostname, port)
        cls = _PinnedHTTPSConnection if parts.scheme == "https" else _PinnedHTTPConnection
        conn = cls(parts.hostname, port, addrs, timeout=FETCH_TIMEOUT)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        try:
            conn.request("GET", path, headers={
                "User-Agent": USER_AGENT,
                "Accept": "image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8",
                "Accept-Encoding": "identity",
            })
            resp = conn.getresponse()
            if resp.status in (301, 302, 303, 307, 308):
                location = resp.getheader("Location")
                if not location:
                    raise FetchError(502, "The logo server sent a redirect with no location.")
                url = urljoin(url, location)
                continue
            if resp.status != 200:
                raise FetchError(502, f"The logo server answered {resp.status}.")
            length = resp.getheader("Content-Length")
            if length and length.isdigit() and int(length) > MAX_LOGO_BYTES:
                raise FetchError(413, "The logo is too large.")
            data = resp.read(MAX_LOGO_BYTES + 1)
            if len(data) > MAX_LOGO_BYTES:
                raise FetchError(413, "The logo is too large.")
            declared = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
            sniffed = sniff_image(data)
            ctype = sniffed or (declared if declared in LOGO_TYPES else None)
            if not ctype:
                raise FetchError(415, "That URL did not return an image.")
            return data, ctype
        except (OSError, http.client.HTTPException) as e:
            raise FetchError(502, f"Could not fetch the logo: {e.__class__.__name__}.")
        finally:
            conn.close()
    raise FetchError(502, "Too many redirects.")


def save_photo(data, name=None):
    """Stores an exported PNG/JPEG and returns its file name. With a name the
    file is overwritten on each save, so its URL never changes; without one
    the name comes from the content hash."""
    ctype = sniff_image(data)
    if ctype not in ("image/png", "image/jpeg"):
        raise FetchError(415, "Only PNG and JPEG photos can be saved.")
    ext = "png" if ctype == "image/png" else "jpg"
    if name:
        name = name.strip().lower()
        if not NAME_RE.match(name):
            raise FetchError(400, "Names may use a-z, 0-9 and dashes (up to 64 characters).")
    else:
        name = hashlib.sha256(data).hexdigest()[:16]
    os.makedirs(PHOTO_DIR, exist_ok=True)
    # A name keeps one file: drop the other format's copy if the format changed.
    for other in ("png", "jpg"):
        if other != ext:
            try:
                os.remove(os.path.join(PHOTO_DIR, f"{name}.{other}"))
            except FileNotFoundError:
                pass
    filename = f"{name}.{ext}"
    tmp = os.path.join(PHOTO_DIR, f".{filename}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, os.path.join(PHOTO_DIR, filename))
    return filename


class Handler(BaseHTTPRequestHandler):
    server_version = "icon-site"
    sys_version = ""

    def _route_path(self):
        path = urlsplit(self.path).path
        if BASE_PATH and (path == BASE_PATH or path.startswith(BASE_PATH + "/")):
            path = path[len(BASE_PATH):] or "/"
        return path

    def _public_base(self):
        if PUBLIC_URL:
            return PUBLIC_URL
        proto = self.headers.get("X-Forwarded-Proto", "http").split(",")[0].strip()
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or f"localhost:{PORT}"
        host = host.split(",")[0].strip()
        prefix = (self.headers.get("X-Forwarded-Prefix") or BASE_PATH).rstrip("/")
        return f"{proto}://{host}{prefix}"

    def _send(self, status, body, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj).encode(), "application/json", {"Cache-Control": "no-store"})

    def _image(self, data, ctype, cache):
        # The sandbox CSP stops a proxied SVG from running script if opened directly.
        self._send(200, data, ctype, {
            "Cache-Control": cache,
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        })

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self._route_path()
        if path == "/api/health":
            return self._json(200, {"ok": True})
        if path == "/api/logo":
            url = parse_qs(urlsplit(self.path).query).get("url", [""])[0]
            if not url:
                return self._json(400, {"error": "Missing url parameter."})
            try:
                data, ctype = fetch_logo(url)
            except FetchError as e:
                return self._json(e.status, {"error": str(e)})
            return self._image(data, ctype, "private, max-age=600")
        if path.startswith("/photos/"):
            m = PHOTO_RE.match(path[len("/photos/"):])
            file = os.path.join(PHOTO_DIR, m.group(0)) if m else None
            if not file or not os.path.isfile(file):
                return self._json(404, {"error": "Not found."})
            with open(file, "rb") as f:
                data = f.read()
            ctype = "image/png" if m.group(2) == "png" else "image/jpeg"
            # Named photos can be re-saved, so keep caches short.
            return self._image(data, ctype, "public, max-age=300")
        return self._static(path)

    def _static(self, path):
        if path.endswith("/"):
            path += "index.html"
        full = os.path.realpath(os.path.join(STATIC_DIR, path.lstrip("/")))
        ext = os.path.splitext(full)[1]
        if not full.startswith(STATIC_DIR + os.sep) or ext not in STATIC_TYPES or not os.path.isfile(full):
            return self._json(404, {"error": "Not found."})
        with open(full, "rb") as f:
            data = f.read()
        self._send(200, data, STATIC_TYPES[ext], {"Cache-Control": "no-cache"})

    def do_POST(self):
        path = self._route_path()
        if path != "/api/photos":
            return self._json(404, {"error": "Not found."})
        length = self.headers.get("Content-Length", "")
        if not length.isdigit():
            return self._json(411, {"error": "Content-Length required."})
        if int(length) > MAX_PHOTO_BYTES:
            return self._json(413, {"error": "The photo is too large."})
        data = self.rfile.read(int(length))
        name = parse_qs(urlsplit(self.path).query).get("name", [""])[0] or None
        try:
            filename = save_photo(data, name)
        except FetchError as e:
            return self._json(e.status, {"error": str(e)})
        self._json(201, {"url": f"{self._public_base()}/photos/{filename}", "file": filename})


def main():
    os.makedirs(PHOTO_DIR, exist_ok=True)
    server = ThreadingHTTPServer(("", PORT), Handler)
    server.daemon_threads = True
    print(f"icon-site listening on :{PORT} (static {STATIC_DIR}, data {DATA_DIR})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
