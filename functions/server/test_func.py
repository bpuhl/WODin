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
import json
import os
import sys
import time
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


def make_athlete(key, athlete_id="brian", disabled=False):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", key.encode(), salt, 1000).hex()
    a = {"id": athlete_id, "name": athlete_id.title(), "salt": salt.hex(),
         "iterations": 1000, "hash": digest}
    if disabled:
        a["disabled"] = True
    return a


class TestTheGate(unittest.TestCase):
    """Which paths need a key. Getting this wrong either breaks the app for
    everyone or publishes the history, so it is asserted explicitly."""

    def test_app_shell_and_assets_are_public(self):
        for name in ("index.html", "sw.js", "manifest.webmanifest",
                     "src/main.js", "styles/tokens.css",
                     "fonts/barlow-400.woff2", "icons/icon-192.png"):
            self.assertTrue(func._is_public(name), name)

    def test_protocol_schemas_stay_public(self):
        # An agent resolves $id to these; gating them breaks the contract.
        for name in ("schema/wod.schema.json", "schema/result.schema.json",
                     "examples/minimal.json"):
            self.assertTrue(func._is_public(name), name)

    def test_private_things_are_not_public(self):
        for name in ("wods/2026-09-20.json", "api/history", "api/log",
                     "auth.json", "results/brian/2026-09-20.json"):
            self.assertFalse(func._is_public(name), name)

    def test_registry_and_results_are_never_served(self):
        # Not merely gated: an authenticated athlete must not be able to
        # fetch the signing secret, other athletes' hashes, or another
        # athlete's sessions by guessing an object path.
        self.assertTrue(func._is_never_served("auth.json"))
        self.assertTrue(func._is_never_served("results/brian/2026-09-20.json"))
        self.assertFalse(func._is_never_served("wods/2026-09-20.json"))

    def test_unknown_paths_fail_closed(self):
        # A new asset forgotten here is gated, not published.
        self.assertFalse(func._is_public("secret-notes.txt"))


class TestDeviceKeys(unittest.TestCase):
    def test_correct_key_resolves_to_its_athlete(self):
        doc = {"athletes": [make_athlete("aaa", "brian"),
                            make_athlete("bbb", "sam")]}
        self.assertEqual(func._verify_key(doc, "bbb")["id"], "sam")

    def test_wrong_and_empty_keys_are_rejected(self):
        doc = {"athletes": [make_athlete("aaa", "brian")]}
        for bad in ("", None, "aab", "AAA"):
            self.assertIsNone(func._verify_key(doc, bad), repr(bad))

    def test_disabled_athlete_is_rejected(self):
        doc = {"athletes": [make_athlete("aaa", "brian", disabled=True)]}
        self.assertIsNone(func._verify_key(doc, "aaa"))

    def test_missing_registry_fails_closed(self):
        # No auth.json means nothing validates, rather than everything.
        self.assertIsNone(func._verify_key({"athletes": []}, "aaa"))
        self.assertIsNone(func._verify_session("", "anything"))


class TestSessions(unittest.TestCase):
    secret = "a-signing-secret"

    def test_roundtrip_carries_the_athlete(self):
        t = func._make_session(self.secret, "brian", time.time() + 60)
        self.assertEqual(func._verify_session(self.secret, t), "brian")

    def test_expired_is_rejected(self):
        t = func._make_session(self.secret, "brian", time.time() - 1)
        self.assertIsNone(func._verify_session(self.secret, t))

    def test_forged_athlete_is_rejected(self):
        t = func._make_session(self.secret, "brian", time.time() + 60)
        _, _, sig = t.rpartition(".")
        forged = func._b64e(json.dumps({"a": "sam", "exp": 9e9}).encode()) + "." + sig
        self.assertIsNone(func._verify_session(self.secret, forged),
                          "a re-signed payload must not impersonate another athlete")

    def test_other_secret_is_rejected(self):
        t = func._make_session("different", "brian", time.time() + 60)
        self.assertIsNone(func._verify_session(self.secret, t))


class TestCookieParsing(unittest.TestCase):
    class Ctx:
        def __init__(self, h): self._h = h
        def HTTPHeaders(self): return self._h

    def test_picks_the_right_cookie(self):
        ctx = self.Ctx({"Cookie": "a=1; wodin_session=tok.sig; b=2"})
        self.assertEqual(func._cookie(ctx, "wodin_session"), "tok.sig")

    def test_case_and_list_values(self):
        ctx = self.Ctx({"cookie": ["wodin_session=xyz"]})
        self.assertEqual(func._cookie(ctx, "wodin_session"), "xyz")

    def test_absent(self):
        self.assertIsNone(func._cookie(self.Ctx({"Cookie": "other=1"}), "wodin_session"))


class TestRouting(unittest.TestCase):
    def test_root_is_index(self):
        self.assertEqual(func._resolve_object_name("/"), "index.html")

    def test_extract_path_splits_off_the_query(self):
        # ?key= enrolment depends on this, and the return shape changed
        # from a bare string when it was added.
        class Ctx:
            def RequestURL(self): return "/wods/2026-09-20.json?key=abc123"
        path, query = func._extract_path(Ctx())
        self.assertEqual(path, "/wods/2026-09-20.json")
        self.assertEqual(query, "key=abc123")

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
