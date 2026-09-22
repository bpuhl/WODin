#!/usr/bin/env python3
"""
Verify a deployed WODin serves every asset byte-for-byte.

    python3 scripts/verify-assets.py https://wod.imav8n.com

The function's binary handling is covered by unit tests. What those cannot
reach is the hop they don't own: OCI API Gateway relaying the function's
response. A gateway that re-encodes a body corrupts fonts and icons while
still returning HTTP 200, so the failure is invisible without comparing
bytes. This fetches each built asset and compares SHA-256 against the file
on disk.

Exit status is 0 only if every asset matches, so CI can gate on it.
"""

import hashlib
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (path on the site, file on disk). Binary first: those are what this
# script exists for, and a failure there is the expected one.
ASSETS = [
    ("fonts/barlow-400.woff2", "public/fonts/barlow-400.woff2"),
    ("fonts/barlow-500.woff2", "public/fonts/barlow-500.woff2"),
    ("fonts/barlow-600.woff2", "public/fonts/barlow-600.woff2"),
    ("fonts/barlow-condensed-500.woff2", "public/fonts/barlow-condensed-500.woff2"),
    ("fonts/barlow-condensed-600.woff2", "public/fonts/barlow-condensed-600.woff2"),
    ("fonts/barlow-condensed-700.woff2", "public/fonts/barlow-condensed-700.woff2"),
    ("fonts/jetbrains-mono-400.woff2", "public/fonts/jetbrains-mono-400.woff2"),
    ("fonts/jetbrains-mono-500.woff2", "public/fonts/jetbrains-mono-500.woff2"),
    ("manifest.webmanifest", "public/manifest.webmanifest"),
    ("styles/tokens.css", "styles/tokens.css"),
    ("src/icons.js", "src/icons.js"),
]

# Cloudflare's managed bot rules reject urllib's default User-Agent
# ("Python-urllib/3.x") with a 403 before the request ever reaches OCI.
# CI verifies against the gateway hostname directly and so never saw it;
# pointed at the public domain, every asset "failed" while the site was
# perfectly healthy. Identify the script honestly instead.
USER_AGENT = "wodin-verify-assets/1.0 (+https://github.com/bpuhl/WODin)"

# The gateway rate-limits to 5 requests/second per client IP, which exists
# so a 6-digit sign-in PIN cannot be brute-forced. This script fetches
# every asset from one address as fast as it can, and promptly throttled
# itself into a failed deploy: 429s reported as corrupted assets.
#
# The limit is the point, so the client paces itself rather than the
# gateway being loosened. 4/s leaves headroom for anything else sharing
# the address.
REQUEST_INTERVAL = 0.25
MAX_RETRIES = 4

EXPECTED_TYPE = {"woff2": "font/woff2", "png": "image/png", "css": "text/css",
                 "js": "application/javascript", "webmanifest": "application/manifest+json"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: verify-assets.py <base-url>")
    base = sys.argv[1].rstrip("/")

    # Icons are generated rather than committed, so only check the ones
    # actually present.
    assets = list(ASSETS)
    icons = os.path.join(ROOT, "public", "icons")
    if os.path.isdir(icons):
        for name in sorted(os.listdir(icons)):
            if name.endswith(".png"):
                assets.append(("icons/" + name, "public/icons/" + name))

    failures = 0
    checked = 0
    for path, local in assets:
        disk = os.path.join(ROOT, local)
        if not os.path.exists(disk):
            print("  SKIP  %-38s (not in the repo)" % path)
            continue
        with open(disk, "rb") as fh:
            want = fh.read()
        url = base + "/" + path
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        got = ctype = None
        last = None
        for attempt in range(MAX_RETRIES):
            time.sleep(REQUEST_INTERVAL)
            try:
                with urllib.request.urlopen(req, timeout=60) as res:
                    got = res.read()
                    ctype = res.headers.get("Content-Type", "")
                break
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code == 429:
                    # Throttled rather than broken. Back off and retry --
                    # reporting this as a corrupted asset, which is what it
                    # used to do, sends the reader hunting for a bug that
                    # is not there.
                    time.sleep(1.0 * (2 ** attempt))
                    continue
                break
            except Exception as exc:
                last = exc
                break

        if got is None:
            code = getattr(last, "code", None)
            hint = ""
            if code == 403:
                hint = "  (403 from a CDN, not the origin? check bot rules)"
            elif code == 429:
                hint = "  (still rate-limited after %d tries)" % MAX_RETRIES
            print("  FAIL  %-38s %s%s" % (path, "HTTP %s" % code if code else last, hint))
            failures += 1
            continue

        checked += 1
        if sha(got) != sha(want):
            print("  FAIL  %-38s %d bytes on disk, %d served -- CORRUPTED"
                  % (path, len(want), len(got)))
            failures += 1
            continue

        ext = path.rsplit(".", 1)[-1]
        expected = EXPECTED_TYPE.get(ext)
        if expected and expected not in ctype:
            print("  WARN  %-38s bytes ok, Content-Type is %r" % (path, ctype))
        else:
            print("  ok    %-38s %d bytes" % (path, len(got)))

    print("\n%d checked, %d failed" % (checked, failures))
    if failures:
        print("\nA byte mismatch on a font or icon means the gateway is not "
              "relaying binary intact. The function's own handling is unit "
              "tested, so look at the gateway, not func.py.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
