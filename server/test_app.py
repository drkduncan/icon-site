"""Run with: python -m unittest discover server"""

import http.server
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

os.environ["DATA_DIR"] = tempfile.mkdtemp()
import app  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


class GuardTests(unittest.TestCase):
    def test_private_and_special_addresses_are_refused(self):
        for ip in ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
                   "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1",
                   "::ffff:127.0.0.1", "::ffff:192.168.0.1"]:
            self.assertFalse(app.address_allowed(ip), ip)

    def test_public_addresses_are_allowed(self):
        for ip in ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"]:
            self.assertTrue(app.address_allowed(ip), ip)

    def test_localhost_names_are_refused(self):
        with self.assertRaises(app.FetchError) as e:
            app.fetch_logo("http://localhost/logo.png")
        self.assertEqual(e.exception.status, 403)

    def test_bad_urls_are_refused(self):
        for url in ["file:///etc/passwd", "ftp://example.com/a.png", "gopher://x",
                    "http://user:pw@example.com/a.png", "http://example.com:22/a.png", "http:///a"]:
            with self.assertRaises(app.FetchError, msg=url):
                app.validate_url(url)

    def test_sniffing(self):
        self.assertEqual(app.sniff_image(PNG), "image/png")
        self.assertEqual(app.sniff_image(JPEG), "image/jpeg")
        self.assertEqual(app.sniff_image(b"  <svg xmlns='http://www.w3.org/2000/svg'/>"), "image/svg+xml")
        self.assertEqual(app.sniff_image(b"<?xml version='1.0'?><svg/>"), "image/svg+xml")
        self.assertIsNone(app.sniff_image(b"<html><body>hi</body></html>"))


class _Upstream(http.server.BaseHTTPRequestHandler):
    """Fake logo host used with LOGO_ALLOW_PRIVATE."""

    def do_GET(self):
        routes = {
            "/logo.png": (200, "image/png", PNG),
            "/octet.png": (200, "application/octet-stream", PNG),
            "/page": (200, "text/html", b"<html></html>"),
            "/big.png": (200, "image/png", PNG + b"\x00" * 2048),
        }
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/logo.png")
            self.end_headers()
            return
        status, ctype, body = routes.get(self.path, (404, "text/plain", b"no"))
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _serve(handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = _serve(_Upstream)
        cls.app = _serve(app.Handler)
        cls.base = f"http://127.0.0.1:{cls.app.server_port}"
        cls.up = f"http://127.0.0.1:{cls.upstream.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown()
        cls.app.shutdown()

    def setUp(self):
        app.ALLOW_PRIVATE = True

    def tearDown(self):
        app.ALLOW_PRIVATE = False

    def get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def post(self, path, data, headers=None):
        req = urllib.request.Request(self.base + path, data=data, method="POST", headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_page_and_health(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body)
        self.assertEqual(self.get("/api/health")[0], 200)
        self.assertEqual(self.get("/../server/app.py")[0], 404)

    def test_logo_proxy(self):
        status, headers, body = self.get("/api/logo?url=" + self.up + "/logo.png")
        self.assertEqual((status, headers["Content-Type"], body), (200, "image/png", PNG))
        # Sniffed even when the upstream type is generic, and redirects are followed.
        self.assertEqual(self.get("/api/logo?url=" + self.up + "/octet.png")[0], 200)
        self.assertEqual(self.get("/api/logo?url=" + self.up + "/redirect")[2], PNG)
        self.assertEqual(self.get("/api/logo?url=" + self.up + "/page")[0], 415)
        self.assertEqual(self.get("/api/logo?url=" + self.up + "/missing")[0], 502)

    def test_logo_size_limit(self):
        old, app.MAX_LOGO_BYTES = app.MAX_LOGO_BYTES, 1024
        try:
            self.assertEqual(self.get("/api/logo?url=" + self.up + "/big.png")[0], 413)
        finally:
            app.MAX_LOGO_BYTES = old

    def test_proxy_refuses_private_hosts_by_default(self):
        app.ALLOW_PRIVATE = False
        self.assertEqual(self.get("/api/logo?url=http://127.0.0.1/logo.png")[0], 403)

    def test_save_and_serve_photo(self):
        status, result = self.post("/api/photos", PNG, {
            "X-Forwarded-Proto": "https", "X-Forwarded-Host": "example.com", "X-Forwarded-Prefix": "/icons"})
        self.assertEqual(status, 201)
        self.assertRegex(result["url"], r"^https://example\.com/icons/photos/[0-9a-f]{16}\.png$")
        status, headers, body = self.get("/photos/" + result["file"])
        self.assertEqual((status, headers["Content-Type"], body), (200, "image/png", PNG))

    def test_named_photo_keeps_its_url(self):
        s1, r1 = self.post("/api/photos?name=Acme", PNG)
        s2, r2 = self.post("/api/photos?name=acme", PNG + b"\x01")
        self.assertEqual((s1, s2), (201, 201))
        self.assertEqual(r1["url"], r2["url"])
        self.assertTrue(r1["url"].endswith("/photos/acme.png"))
        self.assertEqual(self.get("/photos/acme.png")[2], PNG + b"\x01")
        # Switching format replaces the old file rather than leaving both.
        self.post("/api/photos?name=acme", JPEG)
        self.assertEqual(self.get("/photos/acme.png")[0], 404)
        self.assertEqual(self.get("/photos/acme.jpg")[2], JPEG)

    def test_bad_uploads(self):
        self.assertEqual(self.post("/api/photos", b"<svg/>")[0], 415)
        self.assertEqual(self.post("/api/photos?name=../x", PNG)[0], 400)
        old, app.MAX_PHOTO_BYTES = app.MAX_PHOTO_BYTES, 10
        try:
            self.assertEqual(self.post("/api/photos", PNG)[0], 413)
        finally:
            app.MAX_PHOTO_BYTES = old
        self.assertEqual(self.get("/photos/../../etc/passwd")[0], 404)


if __name__ == "__main__":
    unittest.main()
