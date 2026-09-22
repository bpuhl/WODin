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
        # (bucket, name) pairs, so tests can assert WHICH bucket was used --
        # the whole point of the split is that auth.json never comes from
        # the one the agent can reach.
        self.reads = []
        self.writes = []

    def get_object(self, namespace, bucket, name):
        self.reads.append((bucket, name))
        if name not in self.objects:
            raise _ServiceError(404)
        return types.SimpleNamespace(data=types.SimpleNamespace(content=self.objects[name]))

    def put_object(self, namespace, bucket, name, body, **kw):
        self.writes.append((bucket, name))
        self.objects[name] = body

    def list_objects(self, namespace, bucket, prefix=None, start=None, limit=None):
        names = sorted(n for n in self.objects if not prefix or n.startswith(prefix))
        objs = [types.SimpleNamespace(name=n) for n in names]
        return types.SimpleNamespace(
            data=types.SimpleNamespace(objects=objs, next_start_with=None))


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


def make_pin(pin):
    salt = os.urandom(16)
    return salt.hex(), hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 1000).hex()


def make_athlete(key, athlete_id="brian", disabled=False, pin=None):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", key.encode(), salt, 1000).hex()
    a = {"id": athlete_id, "name": athlete_id.title(), "salt": salt.hex(),
         "iterations": 1000, "hash": digest}
    if disabled:
        a["disabled"] = True
    if pin is not None:
        a["pin_salt"], a["pin_hash"] = make_pin(pin)
        a["pin_iterations"] = 1000
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
        # No secrets in it, but one athlete has no need for the others' names.
        self.assertTrue(func._is_never_served("roster.json"))
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


class TestResultCollection(unittest.TestCase):
    """Phase 3: POST /api/log and GET /api/history."""

    def setUp(self):
        self.bucket = FakeBucket({})

    def post(self, payload, athlete="brian"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return func._handle_log(None, io.BytesIO(body), self.bucket,
                                "ns", "b", athlete)

    def history(self, tail="", athlete="brian"):
        return func._handle_history(None, self.bucket, "ns", "b", athlete, tail)

    def body(self, res):
        return json.loads(res.response_data)

    RESULT = {"schema": "wodin/result@1", "workoutId": "2026-09-20",
              "title": "Routine 2", "duration": "47:12", "durationSec": 2832,
              "rpe": 9, "log": {"ex5.s1": {"load": 95, "reps": 5}},
              "skipped": ["ex3"]}

    def test_a_result_is_stored_under_its_athlete(self):
        res = self.post(self.RESULT)
        self.assertEqual(res.status_code, 201)
        self.assertIn("results/brian/2026-09-20.json", self.bucket.objects)

    def test_athletes_cannot_collide(self):
        self.post(self.RESULT, athlete="brian")
        self.post(self.RESULT, athlete="sam")
        self.assertIn("results/brian/2026-09-20.json", self.bucket.objects)
        self.assertIn("results/sam/2026-09-20.json", self.bucket.objects)

    def test_resubmitting_overwrites_rather_than_duplicating(self):
        self.post(self.RESULT)
        corrected = dict(self.RESULT, rpe=7)
        self.post(corrected)
        index = self.body(self.history())
        self.assertEqual(len(index["sessions"]), 1, "a correction must not add a row")
        self.assertEqual(index["sessions"][0]["rpe"], 7)

    def test_server_records_its_own_receipt_time_and_athlete(self):
        # A phone at the gym may have any clock at all.
        self.post(self.RESULT)
        stored = json.loads(self.bucket.objects["results/brian/2026-09-20.json"])
        self.assertIn("receivedAt", stored)
        self.assertEqual(stored["athleteId"], "brian")

    def test_history_is_scoped_to_the_session_athlete(self):
        self.post(dict(self.RESULT, workoutId="secret-session"), athlete="sam")
        # brian asking for sam's session id must not get it
        res = self.history("secret-session", athlete="brian")
        self.assertEqual(res.status_code, 404)

    def test_history_detail_returns_the_full_result(self):
        self.post(self.RESULT)
        got = self.body(self.history("2026-09-20"))
        self.assertEqual(got["log"], self.RESULT["log"])

    def test_empty_history_is_not_an_error(self):
        res = self.history()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.body(res)["sessions"], [])

    def test_bad_bodies_are_rejected(self):
        for bad, why in ((b"not json", "garbage"),
                         (b'"a string"', "not an object"),
                         ({"log": {}}, "no workoutId"),
                         ({"workoutId": "x", "log": []}, "log not an object")):
            res = self.post(bad)
            self.assertIn(res.status_code, (400,), why)

    def test_workout_id_cannot_escape_the_prefix(self):
        # It arrives from the client and becomes part of an object name.
        for evil in ("../../auth", "a/b", "_index", ".hidden", "x" * 100):
            self.assertIsNone(func._safe_id(evil), evil)
        self.assertEqual(func._safe_id("2026-09-20"), "2026-09-20")

    def test_oversized_body_is_refused(self):
        res = self.post(b"x" * (func._MAX_BODY_BYTES + 1))
        self.assertEqual(res.status_code, 413)

    def test_api_responses_are_never_cacheable(self):
        res = self.post(self.RESULT)
        self.assertIn("no-store", res.headers["Cache-Control"])
        self.assertIn("no-store", self.history().headers["Cache-Control"])


class TestHandlerEndToEnd(unittest.TestCase):
    """Drives handler() itself.

    Every piece below was already unit tested and all of them passed while
    enrolment was completely broken in production: handler() checked
    _is_public() before looking for ?key=, so the enrolment URL people are
    actually given -- the site root -- served index.html and ignored the
    key. Testing the parts in isolation could not see it. These drive the
    real entry point.
    """

    class Ctx:
        def __init__(self, url, method="GET", cookie=None):
            self._url, self._method = url, method
            self._headers = {"Cookie": cookie} if cookie else {}
            self.sent = {}
        def RequestURL(self): return self._url
        def Method(self): return self._method
        def HTTPHeaders(self): return self._headers

    def setUp(self):
        self.key = "test-device-key"
        athlete = make_athlete(self.key, "brian", pin="987654")
        auth = {"session_secret": "s" * 64, "session_days": 365,
                "athletes": [athlete]}
        self.bucket = FakeBucket({
            "auth.json": json.dumps(auth).encode(),
            "index.html": b"<!doctype html><title>WODin</title>",
            "wods/brian/2026-09-20.json": b'{"schema":"wodin/wod@1","for":"brian"}',
            "wods/sam/2026-09-20.json": b'{"schema":"wodin/wod@1","for":"sam"}',
        })
        func._cache["doc"] = None      # the registry cache is module-level
        func._cache["at"] = 0.0
        self._real = func.oci.object_storage.ObjectStorageClient
        func.oci.object_storage.ObjectStorageClient = lambda **kw: self.bucket
        os.environ["NAMESPACE"] = "ns"
        os.environ["BUCKET_NAME"] = "site"
        os.environ["DATA_BUCKET_NAME"] = "data"

    def tearDown(self):
        func.oci.object_storage.ObjectStorageClient = self._real
        func._cache["doc"] = None

    def cookie_from(self, res):
        raw = res.headers.get("Set-Cookie", "")
        return raw.split(";")[0] if raw else None

    def test_enrolling_at_the_site_root_works(self):
        # The regression. This is the URL manage-athletes.py prints.
        res = func.handler(self.Ctx("/?key=" + self.key))
        self.assertEqual(res.status_code, 303, "root enrolment must set a session")
        self.assertIn("wodin_session=", res.headers.get("Set-Cookie", ""))
        self.assertIn("HttpOnly", res.headers["Set-Cookie"])
        self.assertEqual(res.headers["Location"], "/", "key must be stripped")

    def test_the_session_then_opens_a_gated_path(self):
        enrol = func.handler(self.Ctx("/?key=" + self.key))
        res = func.handler(self.Ctx("/wods/2026-09-20.json",
                                    cookie=self.cookie_from(enrol)))
        self.assertEqual(res.status_code, 200)

    def test_one_link_can_enrol_and_open_a_workout(self):
        # "?key=...&d=<date>" is the link an athlete is actually sent.
        # Redirecting to the bare path dropped the d and landed them on the
        # library instead of their workout.
        res = func.handler(self.Ctx("/?key=%s&d=2026-09-20" % self.key))
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["Location"], "/?d=2026-09-20")
        self.assertNotIn("key=", res.headers["Location"],
                         "the key must not survive the redirect")

    def test_multiple_params_survive_enrolment(self):
        res = func.handler(self.Ctx("/?d=2026-09-20&key=%s&x=1" % self.key))
        loc = res.headers["Location"]
        self.assertIn("d=2026-09-20", loc)
        self.assertIn("x=1", loc)
        self.assertNotIn("key=", loc)

    def test_enrolling_on_a_gated_path_redirects_back_to_it(self):
        res = func.handler(self.Ctx("/wods/2026-09-20.json?key=" + self.key))
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["Location"], "/wods/2026-09-20.json")

    def test_app_still_loads_with_no_key_at_all(self):
        res = func.handler(self.Ctx("/"))
        self.assertEqual(res.status_code, 200)

    def test_a_bad_key_does_not_lock_the_app(self):
        # A mistyped key on a public path must serve the page, not 401 --
        # otherwise one bad link makes the site look broken.
        res = func.handler(self.Ctx("/?key=wrong"))
        self.assertEqual(res.status_code, 200)

    def test_a_bad_key_on_a_gated_path_still_refuses(self):
        res = func.handler(self.Ctx("/wods/2026-09-20.json?key=wrong"))
        self.assertEqual(res.status_code, 401)

    def test_gated_path_without_a_session_refuses(self):
        res = func.handler(self.Ctx("/wods/2026-09-20.json"))
        self.assertEqual(res.status_code, 401)

    def test_post_and_read_back_through_the_handler(self):
        enrol = func.handler(self.Ctx("/?key=" + self.key))
        c = self.cookie_from(enrol)
        body = json.dumps({"schema": "wodin/result@1", "workoutId": "2026-09-20",
                           "rpe": 8, "log": {"ex1.s1": {"reps": 5}}}).encode()
        posted = func.handler(self.Ctx("/api/log", method="POST", cookie=c),
                              io.BytesIO(body))
        self.assertEqual(posted.status_code, 201)
        hist = func.handler(self.Ctx("/api/history", cookie=c))
        self.assertEqual(json.loads(hist.response_data)["sessions"][0]["rpe"], 8)

    def test_login_page_is_public(self):
        self.bucket.objects["login.html"] = b"<form>"
        self.assertEqual(func.handler(self.Ctx("/login")).status_code, 200)

    def test_athlete_path_serves_the_login_page(self):
        self.bucket.objects["login.html"] = b"<form>"
        res = func.handler(self.Ctx("/brian"))
        self.assertEqual(res.status_code, 200)

    def test_signing_in_with_a_pin_yields_a_working_session(self):
        self.bucket.objects["login.html"] = b"<form>"
        body = b"athlete=brian&pin=987654&next=%2F"
        res = func.handler(self.Ctx("/login", method="POST"), io.BytesIO(body))
        self.assertEqual(res.status_code, 303)
        self.assertIn("wodin_session=", res.headers.get("Set-Cookie", ""))
        self.assertIn("HttpOnly", res.headers["Set-Cookie"])
        # and that session opens a gated path
        got = func.handler(self.Ctx("/wods/2026-09-20.json",
                                    cookie=self.cookie_from(res)))
        self.assertEqual(got.status_code, 200)

    def test_a_wrong_pin_sets_no_session(self):
        res = func.handler(self.Ctx("/login", method="POST"),
                           io.BytesIO(b"athlete=brian&pin=000000"))
        self.assertEqual(res.status_code, 303)
        self.assertNotIn("Set-Cookie", res.headers)
        self.assertIn("e=1", res.headers["Location"])

    def test_failed_login_does_not_reveal_whether_the_athlete_exists(self):
        a = func.handler(self.Ctx("/login", method="POST"),
                         io.BytesIO(b"athlete=brian&pin=000000"))
        b = func.handler(self.Ctx("/login", method="POST"),
                         io.BytesIO(b"athlete=nobody&pin=000000"))
        self.assertEqual(a.status_code, b.status_code)
        self.assertEqual(a.headers["Location"].split("&a=")[0],
                         b.headers["Location"].split("&a=")[0])

    def test_api_requires_a_session(self):
        res = func.handler(self.Ctx("/api/history"))
        self.assertEqual(res.status_code, 401)

    # --- per-athlete workouts (#12) -------------------------------------

    def enrolled(self):
        return self.cookie_from(func.handler(self.Ctx("/?key=" + self.key)))

    def test_athlete_gets_their_own_workout(self):
        res = func.handler(self.Ctx("/wods/2026-09-20.json", cookie=self.enrolled()))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(json.loads(res.response_data)["for"], "brian",
                         "the session's athlete decides which workout is served")

    def test_cannot_reach_another_athletes_workout_by_url(self):
        # There is no athlete in the URL to change, so this resolves under
        # the caller's own prefix and simply misses.
        res = func.handler(self.Ctx("/wods/sam/2026-09-20.json", cookie=self.enrolled()))
        self.assertEqual(res.status_code, 404)

    def test_traversal_out_of_the_athlete_prefix_is_refused(self):
        for evil in ("/wods/../auth.json", "/wods/../../auth.json",
                     "/wods/brian/../../auth.json"):
            res = func.handler(self.Ctx(evil, cookie=self.enrolled()))
            self.assertIn(res.status_code, (404,), evil)

    def test_a_missing_workout_is_a_clean_404(self):
        res = func.handler(self.Ctx("/wods/2099-01-01.json", cookie=self.enrolled()))
        self.assertEqual(res.status_code, 404)

    def test_workouts_still_need_a_session(self):
        self.assertEqual(func.handler(self.Ctx("/wods/2026-09-20.json")).status_code, 401)

    # --- the two-bucket boundary (#5) -----------------------------------

    def test_auth_is_read_from_the_site_bucket_not_the_data_bucket(self):
        # The agent has a grant on the data bucket only. If the signing
        # secret were read from there, the split would buy nothing.
        self.bucket.reads = []
        func.handler(self.Ctx("/?key=" + self.key))
        self.assertIn(("site", "auth.json"), self.bucket.reads,
                      "auth.json must come from the site bucket")
        self.assertNotIn(("data", "auth.json"), self.bucket.reads)

    def test_workouts_and_results_use_the_data_bucket(self):
        c = self.enrolled()
        self.bucket.reads = []
        func.handler(self.Ctx("/wods/2026-09-20.json", cookie=c))
        self.assertTrue(any(b == "data" for b, _ in self.bucket.reads),
                        "a workout must be read from the data bucket")
        body = json.dumps({"workoutId": "d1", "log": {}}).encode()
        func.handler(self.Ctx("/api/log", method="POST", cookie=c), io.BytesIO(body))
        self.assertTrue(any(b == "data" for b, _ in self.bucket.writes),
                        "a result must be written to the data bucket")

    def test_app_assets_stay_on_the_site_bucket(self):
        self.bucket.reads = []
        func.handler(self.Ctx("/"))
        self.assertIn(("site", "index.html"), self.bucket.reads)

    def test_the_prefix_mapping_itself(self):
        self.assertEqual(func._athlete_workout_object("brian", "/wods/2026-09-20.json"),
                         "wods/brian/2026-09-20.json")
        self.assertIsNone(func._athlete_workout_object("brian", "/wods/"))
        self.assertIsNone(func._athlete_workout_object("brian", "/wods/../x"))


class TestTypeableLogin(unittest.TestCase):
    """Phase 6: athlete + PIN, so a device with nothing pasted onto it can
    get in."""

    def setUp(self):
        self.doc = {"session_secret": "s" * 64, "session_days": 365,
                    "athletes": [make_athlete("k1", "brian", pin="123456"),
                                 make_athlete("k2", "sam", pin="654321"),
                                 make_athlete("k3", "old", pin="111111", disabled=True),
                                 make_athlete("k4", "nopin")]}

    def test_correct_pin_resolves_to_that_athlete(self):
        self.assertEqual(func._verify_pin(self.doc, "brian", "123456")["id"], "brian")

    def test_a_pin_only_works_for_its_own_athlete(self):
        # The whole reason a 6-digit secret is defensible: it is checked
        # against ONE named athlete, not swept across the registry.
        self.assertIsNone(func._verify_pin(self.doc, "brian", "654321"))
        self.assertEqual(func._verify_pin(self.doc, "sam", "654321")["id"], "sam")

    def test_wrong_unknown_and_malformed_are_rejected(self):
        for who, pin in [("brian", "123457"), ("brian", ""), ("brian", "abc123"),
                         ("ghost", "123456"), ("", "123456"), ("brian", None)]:
            self.assertIsNone(func._verify_pin(self.doc, who, pin), (who, pin))

    def test_disabled_athlete_cannot_sign_in(self):
        self.assertIsNone(func._verify_pin(self.doc, "old", "111111"))

    def test_an_athlete_with_no_pin_set_cannot_sign_in(self):
        self.assertIsNone(func._verify_pin(self.doc, "nopin", "123456"))

    def test_athlete_paths_that_should_show_a_login_page(self):
        for p in ("/brian", "/sam/", "/someone-else"):
            self.assertIsNotNone(func._looks_like_athlete_path(p), p)

    def test_paths_that_must_not_be_mistaken_for_an_athlete(self):
        # Reserved routes, nested paths and anything with an extension --
        # otherwise a real asset would be answered with a login page.
        for p in ("/api", "/wods", "/login", "/src", "/schema",
                  "/src/main.js", "/wods/2026-09-22.json", "/sw.js",
                  "/Brian", "/", "/a/b"):
            self.assertIsNone(func._looks_like_athlete_path(p), p)


class TestAvailableDays(unittest.TestCase):
    """#13 needs to know which dates exist, so "next" is only offered when
    there is a next."""

    def setUp(self):
        self.bucket = FakeBucket({
            "wods/brian/2026-09-20.json": b"{}",
            "wods/brian/2026-09-22.json": b"{}",
            "wods/brian/2026-09-18.json": b"{}",
            "wods/sam/2026-09-21.json": b"{}",
            "wods/brian/notes.txt": b"x",
        })

    def days(self, athlete="brian"):
        res = func._handle_days(None, self.bucket, "ns", "data", athlete)
        return json.loads(res.response_data)

    def test_returns_this_athletes_dates_sorted(self):
        # Sorted, because prev/next is meaningless on an arbitrary order,
        # and gapped, because rest days exist -- "previous" must mean the
        # previous day that HAS one, not yesterday.
        self.assertEqual(self.days()["dates"],
                         ["2026-09-18", "2026-09-20", "2026-09-22"])

    def test_does_not_leak_another_athletes_days(self):
        self.assertNotIn("2026-09-21", self.days()["dates"])
        self.assertEqual(self.days("sam")["dates"], ["2026-09-21"])

    def test_ignores_non_workout_objects(self):
        self.assertNotIn("notes", str(self.days()["dates"]))

    def test_no_workouts_is_not_an_error(self):
        self.bucket.objects = {}
        self.assertEqual(self.days()["dates"], [])


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
