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

Auth is deliberately absent. The gate lands in a later phase, and it is
partial: the app shell and its assets stay public so shared #w= links keep
working, while /wods/ and history require a device key.
"""

import io
import logging
import os
from urllib.parse import urlsplit

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


def _extract_path(ctx):
    try:
        request_url = ctx.RequestURL()
    except Exception as exc:
        log.error("wodin-server: ctx.RequestURL() failed: %s", exc)
        return "/"
    if not request_url:
        return "/"
    # RequestURL() may be a full URL or a bare path depending on invocation
    # context; urlsplit handles both.
    return urlsplit(request_url).path or "/"


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


def serve(ctx, os_client, namespace, bucket, object_name):
    try:
        obj = os_client.get_object(namespace, bucket, object_name)
        content = obj.data.content
    except oci.exceptions.ServiceError as exc:
        if exc.status == 404:
            log.info("wodin-server: 404 for object '%s'", object_name)
            return _not_found(ctx)
        raise

    ext = _extension(object_name)
    headers = {"Cache-Control": _cache_control(object_name)}

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
    object_name = _resolve_object_name(_extract_path(ctx))
    if object_name is None:
        return _not_found(ctx)

    signer = oci.auth.signers.get_resource_principals_signer()
    os_client = oci.object_storage.ObjectStorageClient(config={}, signer=signer)
    return serve(ctx, os_client, os.environ["NAMESPACE"],
                 os.environ["BUCKET_NAME"], object_name)
