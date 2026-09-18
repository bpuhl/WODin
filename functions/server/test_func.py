"""
Tests for the WODin static server, with byte fidelity as the point.

The failure this guards against is silent: decoding a .woff2 as UTF-8
either raises or mangles it, and a mangled font still returns HTTP 200.
So the central test feeds a REAL font file from public/fonts/ through the
handler and compares SHA-256 in and out.

Run: python3 -m unittest discover -s functions/server
"""

import hashlib
import io
import os
import sys
import types
import unittest

# The function image supplies these; stub them so the logic is testable
# without Docker or an OCI account.
_oci = types.ModuleType("oci")


class _ServiceError(Exception):
    def __init__(self, status=500):
        super().__init__("service error %s" % status)
        self.status = status


_oci.exceptions = types.SimpleNamespace(ServiceError=_ServiceError)
_oci.auth = types.SimpleNamespace(
    signers=types.SimpleNamespace(get_resource_principals_signer=lambda: None))
_oci.object_storage = types.SimpleNamespace(ObjectStorageClient=object)
sys.modules.setdefault("oci", _oci)

_fdk = types.ModuleType("fdk")


class _Response:
    """Stands in for fdk.response.Response, keeping what was handed to it.

    body_bytes() mirrors the real implementation exactly (verified against
    fdk/response.py): bytes pass through untouched, everything else is
    str()-coerced and encoded. That is the behaviour under test.
    """

    def __init__(self, ctx, response_data=None, headers=None, status_code=200):
        self.response_data = response_data if response_data else ""
        self.headers = headers or {}
        self.status_code = status_code

    def body_bytes(self):
        if isinstance(self.response_data, (bytes, bytearray)):
            return self.response_data
        return str(self.response_data).encode("utf-8")


_fdk.response = types.SimpleNamespace(Response=_Response)
sys.modules.setdefault("fdk", _fdk)
sys.modules.setdefault("fdk.response", types.ModuleType("fdk.response"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import func  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeBucket:
    """Minimal stand-in for ObjectStorageClient."""

    def __init__(self, objects):
        self.objects = objects

    def get_object(self, namespace, bucket, name):
        if name not in self.objects:
            raise _ServiceError(404)
        return types.SimpleNamespace(data=types.SimpleNamespace(content=self.objects[name]))


def serve(name, payload):
    client = FakeBucket({name: payload})
    return func.serve(None, client, "ns", "bucket", name)


class TestBinaryFidelity(unittest.TestCase):
    """The reason this module exists."""

    def test_real_font_survives_byte_for_byte(self):
        src = os.path.join(REPO, "public", "fonts", "barlow-400.woff2")
        if not os.path.exists(src):
            self.skipTest("font not present")
        with open(src, "rb") as fh:
            original = fh.read()

        res = serve("fonts/barlow-400.woff2", original)

        self.assertEqual(hashlib.sha256(res.body_bytes()).hexdigest(),
                         hashlib.sha256(original).hexdigest(),
                         "the font came back altered")
        self.assertEqual(res.headers["Content-Type"], "font/woff2")
        self.assertTrue(original.startswith(b"wOF2"), "sanity: source is really woff2")
        self.assertTrue(res.body_bytes().startswith(b"wOF2"),
                        "served bytes lost the woff2 signature")

    def test_every_shipped_font_survives(self):
        fonts = os.path.join(REPO, "public", "fonts")
        if not os.path.isdir(fonts):
            self.skipTest("fonts not present")
        checked = 0
        for name in sorted(os.listdir(fonts)):
            if not name.endswith(".woff2"):
                continue
            with open(os.path.join(fonts, name), "rb") as fh:
                original = fh.read()
            res = serve("fonts/" + name, original)
            self.assertEqual(res.body_bytes(), original, "%s was altered" % name)
            checked += 1
        self.assertGreater(checked, 0, "expected shipped fonts to test against")

    def test_png_bytes_that_are_not_valid_utf8(self):
        # A PNG header contains 0x89, which is not valid UTF-8 on its own --
        # precisely the byte that makes a decode() approach blow up.
        png = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
        with self.assertRaises(UnicodeDecodeError):
            png.decode("utf-8")           # confirms the hazard is real
        res = serve("icons/icon-192.png", png)
        self.assertEqual(res.body_bytes(), png)
        self.assertEqual(res.headers["Content-Type"], "image/png")

    def test_unknown_extension_is_passed_through_not_decoded(self):
        blob = b"\xff\xfe\x00binary"
        res = serve("weird.bin", blob)
        self.assertEqual(res.body_bytes(), blob)


class TestTextStillWorks(unittest.TestCase):
    def test_html_is_decoded_and_typed(self):
        res = serve("index.html", "<!DOCTYPE html><p>hi</p>".encode("utf-8"))
        self.assertIsInstance(res.response_data, str)
        self.assertIn("text/html", res.headers["Content-Type"])

    def test_utf8_text_round_trips(self):
        body = "WODin — RPE 9 · 47:12".encode("utf-8")
        res = serve("wods/2026-09-18.json", body)
        self.assertEqual(res.body_bytes(), body, "non-ASCII text was mangled")

    def test_content_types(self):
        for name, expected in [("src/main.js", "application/javascript"),
                               ("styles/tokens.css", "text/css"),
                               ("manifest.webmanifest", "application/manifest+json"),
                               ("schema/wod.schema.json", "application/json"),
                               ("public/icon.svg", "image/svg+xml")]:
            res = serve(name, b"x")
            self.assertIn(expected, res.headers["Content-Type"], name)


class TestRouting(unittest.TestCase):
    def test_root_is_index(self):
        self.assertEqual(func._resolve_object_name("/"), "index.html")

    def test_no_pretty_url_suffixing(self):
        # Unlike Perch. WODin routes on the fragment and ?d=, so a bare path
        # is a literal file and must not silently become <path>.html.
        self.assertEqual(func._resolve_object_name("/wods"), "wods")

    def test_traversal_refused(self):
        for evil in ("/../secrets", "/a/../../etc/passwd"):
            self.assertIsNone(func._resolve_object_name(evil), evil)

    def test_missing_object_is_404(self):
        res = func.serve(None, FakeBucket({}), "ns", "bucket", "nope.js")
        self.assertEqual(res.status_code, 404)


class TestCaching(unittest.TestCase):
    def test_shell_is_not_cached_hard(self):
        # A cached sw.js or index.html pins installed PWAs to an old build
        # for the life of the TTL.
        for name in ("index.html", "sw.js", "manifest.webmanifest"):
            self.assertEqual(func._cache_control(name), "no-cache", name)

    def test_fonts_and_icons_are_immutable(self):
        for name in ("fonts/barlow-400.woff2", "icons/icon-192.png"):
            self.assertIn("immutable", func._cache_control(name), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
