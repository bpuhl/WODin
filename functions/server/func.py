"""
WODin static-asset server: reads objects out of a private Object Storage
bucket and serves them through the API Gateway.

BINARY IS THE POINT OF THIS FILE
--------------------------------
The sibling Perch project's equivalent does `content.decode("utf-8")` on
every object, which is fine for a site of HTML and CSS and fatal here:
WODin ships eight .woff2 faces and a set of PNG app icons, and decoding a
font as UTF-8 either throws or silently corrupts it.

Returning bytes is correct and needs no base64 dance. Confirmed by reading
the FDK rather than by guessing:

  fdk/response.py   Response(response_data: Union[str, bytes]); body_bytes()
                    returns bytes unchanged and only str()-coerces non-bytes.
  fdk/event_handler.py  passes body_bytes() straight to the transport.

So the rule is simply: hand the FDK `bytes` for binary and `str` for text,
and set an honest Content-Type. What this file cannot prove on its own is
whether the API Gateway relays those bytes intact -- that is the one hop
with no local test, and scripts/verify-assets.py checks it against a
deployed site by comparing SHA-256 with the source files.

THE GATE IS PARTIAL, ON PURPOSE
------------------------------
WODin's sharing model puts the workout in the URL fragment, so a #w= link
opens for anyone. Gating the app shell would break that for everyone not
enrolled. So the shell, its assets and the protocol schemas stay public,
while the things that are actually private -- the published daily workout
and the stored history -- require a device key.

Anything NOT recognised as public is gated. A new asset added to the app
and forgotten here fails closed, which is the safe direction.

WHERE THE SIGNING SECRET LIVES
------------------------------
In auth.json, in this bucket, rather than in Vault. Vault was the first
choice and it does not earn the dependency here: the function's grant is
`manage objects` on this bucket, so anything that can read the signing
secret can already read the athlete hashes and every stored result. Vault
would add an audit trail and KMS encryption, not a boundary. It also needs
a second IAM grant for the KMS key, which the deploy identity does not
have. If the audit trail is wanted later, only _load_auth() changes.
"""

import base64
import hashlib
import hmac
import io
import json
import logging
import os
import time
from urllib.parse import urlsplit, parse_qs, quote

import oci
from fdk import response

log = logging.getLogger()

# Served as text: decoded to str, charset declared where it matters.
_TEXT_TYPES = {
    "html": "text/html; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "js": "application/javascript; charset=utf-8",
    "mjs": "application/javascript; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "webmanifest": "application/manifest+json; charset=utf-8",
    "svg": "image/svg+xml; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "map": "application/json; charset=utf-8",
}

# Served as raw bytes. Never decoded, never re-encoded.
_BINARY_TYPES = {
    "woff2": "font/woff2",
    "woff": "font/woff",
    "ttf": "font/ttf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "ico": "image/x-icon",
}

# Fingerprinted or effectively immutable content can be cached hard. The
# app shell cannot: the service worker is how updates reach an installed
# PWA, and a stale sw.js or index.html at the CDN pins users to an old
# build for as long as the TTL.
_IMMUTABLE_PREFIXES = ("fonts/", "icons/")
_NEVER_CACHE = ("index.html", "sw.js", "manifest.webmanifest")


# Reachable without a key: the shell, its assets, and the protocol
# documents an agent needs to resolve $id.
_PUBLIC_OBJECTS = frozenset([
    "index.html", "sw.js", "manifest.webmanifest", "favicon.ico", "robots.txt",
])
_PUBLIC_PREFIXES = ("src/", "styles/", "fonts/", "icons/", "schema/", "examples/")

# Never served to anyone at any auth level. auth.json holds the signing
# secret and every athlete's hash; results are read through /api, never as
# raw objects, so that one athlete cannot fetch another's sessions by
# guessing a path.
_NEVER_SERVE = frozenset(["auth.json"])
_NEVER_SERVE_PREFIXES = ("results/",)

_AUTH_OBJECT = "auth.json"
_COOKIE_NAME = "wodin_session"
_PBKDF2_ITERATIONS = 200000

# auth.json is read per request, so a short cache keeps browsing the app
# from re-fetching it every time. The cost is that revoking an athlete
# takes up to this long to take effect.
_CACHE_TTL_SECONDS = 60
_cache = {"at": 0.0, "doc": None}


def _extract_path(ctx):
    try:
        request_url = ctx.RequestURL()
    except Exception as exc:
        log.error("wodin-server: ctx.RequestURL() failed: %s", exc)
        return "/", ""
    if not request_url:
        return "/", ""
    # RequestURL() may be a full URL or a bare path depending on invocation
    # context; urlsplit handles both.
    parts = urlsplit(request_url)
    return (parts.path or "/"), (parts.query or "")


def _resolve_object_name(path):
    """Map a request path to a bucket object, or None to refuse it.

    No pretty-URL suffixing here, unlike Perch: WODin is a single-page app
    that routes on the fragment and ?d=, so every real request is for a
    literal file.
    """
    name = path.lstrip("/").strip()
    if not name:
        return "index.html"
    if ".." in name or name.startswith("/"):
        return None
    return name


def _extension(object_name):
    tail = object_name.rsplit("/", 1)[-1]
    return tail.rsplit(".", 1)[-1].lower() if "." in tail else ""


def _cache_control(object_name):
    if object_name in _NEVER_CACHE:
        return "no-cache"
    if object_name.startswith(_IMMUTABLE_PREFIXES):
        return "public, max-age=31536000, immutable"
    return "public, max-age=300"


def _not_found(ctx):
    return response.Response(
        ctx,
        status_code=404,
        response_data="Not found.",
        headers={"Content-Type": "text/plain; charset=utf-8",
                 "Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def _headers(ctx):
    """Header names case-folded, values flattened to a single string."""
    try:
        raw = ctx.HTTPHeaders() or {}
    except Exception:
        return {}
    out = {}
    for key, value in raw.items():
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        out[str(key).lower()] = str(value)
    return out


def _cookie(ctx, name):
    for part in _headers(ctx).get("cookie", "").split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            if k.strip() == name:
                return v.strip()
    return None


def _load_auth(os_client, namespace, bucket):
    now = time.time()
    if _cache["doc"] is not None and (now - _cache["at"]) < _CACHE_TTL_SECONDS:
        return _cache["doc"]
    try:
        obj = os_client.get_object(namespace, bucket, _AUTH_OBJECT)
        doc = json.loads(obj.data.content.decode("utf-8"))
    except oci.exceptions.ServiceError as exc:
        if exc.status == 404:
            # No registry yet: fail CLOSED. The app stays reachable and
            # every gated path rejects, which is the safe direction to be
            # broken in.
            log.error("wodin-server: %s missing from the bucket", _AUTH_OBJECT)
            doc = {"session_secret": "", "athletes": []}
        else:
            raise
    except (ValueError, UnicodeDecodeError) as exc:
        log.error("wodin-server: %s is not valid JSON: %s", _AUTH_OBJECT, exc)
        doc = {"session_secret": "", "athletes": []}
    _cache["doc"] = doc
    _cache["at"] = now
    return doc


def _verify_key(doc, key):
    """Return the athlete this device key belongs to, or None."""
    if not key:
        return None
    match = None
    for athlete in doc.get("athletes", []):
        salt, expected = athlete.get("salt", ""), athlete.get("hash", "")
        if not salt or not expected or athlete.get("disabled"):
            continue
        derived = hashlib.pbkdf2_hmac(
            "sha256", key.encode("utf-8"), bytes.fromhex(salt),
            int(athlete.get("iterations", _PBKDF2_ITERATIONS))).hex()
        # Every candidate is compared even after a match, so response time
        # does not reveal which athlete a key belongs to.
        if hmac.compare_digest(derived, expected) and match is None:
            match = athlete
    return match


def _b64e(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret, payload):
    return _b64e(hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest())


def _make_session(secret, athlete_id, expires_at):
    payload = json.dumps({"a": athlete_id, "exp": expires_at},
                         separators=(",", ":")).encode("utf-8")
    return _b64e(payload) + "." + _sign(secret, payload)


def _verify_session(secret, token):
    """Return the athlete id carried by a valid session, else None."""
    if not secret or not token or "." not in token:
        return None
    body, _, signature = token.rpartition(".")
    try:
        payload = _b64d(body)
    except Exception:
        return None
    if not hmac.compare_digest(_sign(secret, payload), signature):
        return None
    try:
        claims = json.loads(payload.decode("utf-8"))
    except ValueError:
        return None
    if float(claims.get("exp", 0)) < time.time():
        return None
    return claims.get("a")


def _is_public(object_name):
    return (object_name in _PUBLIC_OBJECTS
            or object_name.startswith(_PUBLIC_PREFIXES))


def _is_never_served(object_name):
    return (object_name in _NEVER_SERVE
            or object_name.startswith(_NEVER_SERVE_PREFIXES))


def _redirect(ctx, location, cookie=None):
    headers = {"Location": location, "Cache-Control": "no-store"}
    if cookie:
        headers["Set-Cookie"] = cookie
    return response.Response(ctx, status_code=303, response_data="", headers=headers)


def _unauthorized(ctx):
    return response.Response(
        ctx, status_code=401,
        response_data="A device key is required for this. Open the site with "
                      "?key=<your key> once and it will be remembered.",
        headers={"Content-Type": "text/plain; charset=utf-8",
                 "Cache-Control": "no-store"})


def serve(ctx, os_client, namespace, bucket, object_name, protected=False):
    try:
        obj = os_client.get_object(namespace, bucket, object_name)
        content = obj.data.content
    except oci.exceptions.ServiceError as exc:
        if exc.status == 404:
            log.info("wodin-server: 404 for object '%s'", object_name)
            return _not_found(ctx)
        raise

    ext = _extension(object_name)
    # Cloudflare proxies this domain. A gated response cached at the edge
    # and handed to someone with no session would defeat the gate silently,
    # so protected content is never cacheable.
    headers = {"Cache-Control": "private, no-store" if protected
               else _cache_control(object_name)}

    if ext in _BINARY_TYPES:
        headers["Content-Type"] = _BINARY_TYPES[ext]
        # Raw bytes. Decoding here is the bug this whole file exists to
        # avoid; body_bytes() will pass these through untouched.
        return response.Response(ctx, response_data=content, headers=headers)

    headers["Content-Type"] = _TEXT_TYPES.get(ext, "application/octet-stream")
    if ext not in _TEXT_TYPES:
        # Unknown extension: treat as opaque bytes rather than guessing at
        # an encoding and corrupting it.
        return response.Response(ctx, response_data=content, headers=headers)
    return response.Response(ctx, response_data=content.decode("utf-8"), headers=headers)


def handler(ctx, data: io.BytesIO = None):
    path, query = _extract_path(ctx)

    signer = oci.auth.signers.get_resource_principals_signer()
    os_client = oci.object_storage.ObjectStorageClient(config={}, signer=signer)
    namespace = os.environ["NAMESPACE"]
    bucket = os.environ["BUCKET_NAME"]

    object_name = _resolve_object_name(path)
    if object_name is None or _is_never_served(object_name):
        return _not_found(ctx)

    # Public paths skip the registry read entirely -- the common case (an
    # athlete loading the app) costs no extra bucket fetch.
    if _is_public(object_name):
        return serve(ctx, os_client, namespace, bucket, object_name)

    doc = _load_auth(os_client, namespace, bucket)
    secret = doc.get("session_secret", "")

    # Enrolment: ?key=<device key> on any path. Validated here rather than
    # in the app so the key never has to reach JavaScript, and the session
    # cookie can be HttpOnly.
    key = (parse_qs(query).get("key", [""])[0] or "").strip()
    if key:
        athlete = _verify_key(doc, key)
        if not athlete:
            log.info("wodin-server: rejected enrolment attempt")
            return _unauthorized(ctx)
        days = int(doc.get("session_days", 365))
        expires_at = time.time() + days * 86400
        token = _make_session(secret, athlete.get("id", "athlete"), expires_at)
        cookie = ("%s=%s; Path=/; Max-Age=%d; HttpOnly; Secure; SameSite=Lax"
                  % (_COOKIE_NAME, token, int(days * 86400)))
        log.info("wodin-server: enrolled '%s'", athlete.get("id"))
        # Redirect to the same path without the key, so it stops appearing
        # in history, referrers and any link the athlete shares.
        return _redirect(ctx, path if path.startswith("/") else "/" + path,
                         cookie=cookie)

    if not _verify_session(secret, _cookie(ctx, _COOKIE_NAME)):
        return _unauthorized(ctx)

    return serve(ctx, os_client, namespace, bucket, object_name, protected=True)
