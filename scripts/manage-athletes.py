#!/usr/bin/env python3
"""
Issue, list and revoke WODin device keys.

    python3 scripts/manage-athletes.py list
    python3 scripts/manage-athletes.py add --id brian --name "Brian"
    python3 scripts/manage-athletes.py disable brian
    python3 scripts/manage-athletes.py enable brian
    python3 scripts/manage-athletes.py remove brian

A key identifies an ATHLETE, not merely "allowed". That is what lets
results be stored per athlete from the first session, so adding a second
person later is a new key rather than a migration.

The registry is auth.json in the private site bucket. The function reads
it per request (cached ~60s), so issuing or revoking a key takes effect
without a deploy. It is on the function's never-serve list: it holds the
session signing secret and every athlete's hash, so it must not be
fetchable even by an authenticated athlete.

THE KEY IS SHOWN ONCE, at creation. Only a salted PBKDF2 hash is stored.

Requires the OCI CLI authenticated against the WODin compartment.
"""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
from datetime import date

# auth.json stays in the site bucket, which the agent has no grant on.
BUCKET = os.environ.get("WODIN_BUCKET", "wodin-site")
OBJECT = "auth.json"

# roster.json goes in the DATA bucket, which is the only one the agent can
# read -- that is the whole point of the split.
DATA_BUCKET = os.environ.get("WODIN_DATA_BUCKET", "wodin-data")

# The agent needs to know who exists, in order to build a workout per
# athlete. It must never read auth.json -- that holds the session signing
# secret and every key hash -- so the public facts are mirrored here:
# ids, names, and where each athlete's workouts and results live.
ROSTER = "roster.json"
SITE = os.environ.get("WODIN_SITE", "https://wod.imav8n.com")
PBKDF2_ITERATIONS = 200000
KEY_BYTES = 24            # ~32 url-safe chars; typed once, then remembered


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def namespace():
    return run(["oci", "os", "ns", "get", "--query", "data",
                "--raw-output"]).stdout.strip()


def load(ns):
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        subprocess.run(["oci", "os", "object", "get", "--namespace", ns,
                        "--bucket-name", BUCKET, "--name", OBJECT,
                        "--file", tmp],
                       check=True, capture_output=True, text=True)
        with open(tmp, encoding="utf-8") as fh:
            return json.load(fh)
    except subprocess.CalledProcessError:
        print("No existing %s -- starting a new registry." % OBJECT)
        return {"version": 1, "session_secret": secrets.token_hex(32),
                "session_days": 365, "athletes": []}
    finally:
        os.path.exists(tmp) and os.unlink(tmp)


def write_roster(doc, ns):
    """Mirror the non-secret facts for the agent.

    Rewritten from auth.json on every change rather than edited in place,
    so it cannot drift into claiming an athlete who no longer exists.
    """
    roster = {
        "version": 1,
        "generated": date.today().isoformat(),
        "athletes": [
            {
                "id": a["id"],
                "name": a.get("name") or a["id"].title(),
                "disabled": bool(a.get("disabled")),
                "wods": "wods/%s/" % a["id"],
                "results": "results/%s/" % a["id"],
            }
            for a in sorted(doc.get("athletes", []), key=lambda x: x.get("id", ""))
        ],
    }
    fd, tmp = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(roster, fh, indent=2)
    try:
        run(["oci", "os", "object", "put", "--namespace", ns,
             "--bucket-name", DATA_BUCKET, "--name", ROSTER, "--file", tmp,
             "--force", "--content-type", "application/json"])
        print("Updated %s in %s (%d athlete(s))."
              % (ROSTER, DATA_BUCKET, len(roster["athletes"])))
    finally:
        os.unlink(tmp)


def save(doc, ns):
    # Rotating the signing secret logs every athlete out, so it is created
    # once and then left alone.
    if not doc.get("session_secret"):
        doc["session_secret"] = secrets.token_hex(32)
    doc.setdefault("session_days", 365)
    fd, tmp = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    try:
        run(["oci", "os", "object", "put", "--namespace", ns,
             "--bucket-name", BUCKET, "--name", OBJECT, "--file", tmp,
             "--force", "--content-type", "application/json"])
        print("Updated %s." % OBJECT)
    finally:
        os.unlink(tmp)
    write_roster(doc, ns)


def find(doc, athlete_id):
    for a in doc.get("athletes", []):
        if a.get("id") == athlete_id:
            return a
    return None


def cmd_add(args, doc):
    if find(doc, args.id):
        sys.exit("An athlete with id %r already exists. Remove it first, or "
                 "pick another id." % args.id)
    key = secrets.token_urlsafe(KEY_BYTES)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"), salt,
                                 PBKDF2_ITERATIONS).hex()
    doc.setdefault("athletes", []).append({
        "id": args.id,
        "name": args.name or args.id.title(),
        "salt": salt.hex(),
        "iterations": PBKDF2_ITERATIONS,
        "hash": digest,
        "issued": date.today().isoformat(),
    })
    print()
    print("=" * 72)
    print("  Athlete : %s (%s)" % (args.name or args.id.title(), args.id))
    print()
    print("  Open this once on the device; the key is then remembered:")
    print()
    print("    %s/?key=%s" % (SITE, key))
    print()
    print("  Their workouts go to wods/%s/<date>.json" % args.id)
    print()
    print("  Shown once and not recoverable -- only a hash is stored.")
    print("=" * 72)
    return True


def cmd_list(args, doc):
    athletes = doc.get("athletes", [])
    if not athletes:
        print("No athletes. Nothing can reach a gated path.")
        return False
    print("%-14s %-20s %-12s %s" % ("ID", "NAME", "ISSUED", "STATUS"))
    for a in sorted(athletes, key=lambda x: x.get("id", "")):
        print("%-14s %-20s %-12s %s" % (
            a.get("id", "?"), (a.get("name") or "")[:20],
            a.get("issued", "?"),
            "disabled" if a.get("disabled") else "active"))
    return False


def _set_disabled(doc, athlete_id, value):
    a = find(doc, athlete_id)
    if not a:
        sys.exit("No athlete with id %r. Run `list` to see them." % athlete_id)
    a["disabled"] = value
    if not value:
        a.pop("disabled", None)
    return a


def cmd_disable(args, doc):
    _set_disabled(doc, args.id, True)
    print("Disabled %s. Their key stops working within ~60s (the function "
          "caches the registry)." % args.id)
    print("Existing sessions also stop: the cookie is checked against this "
          "registry on every request.")
    return True


def cmd_enable(args, doc):
    _set_disabled(doc, args.id, False)
    print("Enabled %s. Their existing key works again." % args.id)
    return True


def cmd_remove(args, doc):
    before = len(doc.get("athletes", []))
    doc["athletes"] = [a for a in doc.get("athletes", [])
                       if a.get("id") != args.id]
    if len(doc["athletes"]) == before:
        sys.exit("No athlete with id %r." % args.id)
    print("Removed %s. Their stored results are NOT deleted -- results/%s/ "
          "stays in the bucket." % (args.id, args.id))
    return True


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="issue a device key for an athlete")
    a.add_argument("--id", required=True,
                   help="short identifier; also the results/<id>/ prefix")
    a.add_argument("--name", help="display name (defaults from --id)")
    a.set_defaults(fn=cmd_add)

    sub.add_parser("list", help="show athletes").set_defaults(fn=cmd_list)

    for name, fn, helptext in (
            ("disable", cmd_disable, "revoke access, keeping the entry"),
            ("enable", cmd_enable, "restore a disabled athlete"),
            ("remove", cmd_remove, "delete the entry entirely")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("id")
        p.set_defaults(fn=fn)

    args = ap.parse_args()
    ns = namespace()
    doc = load(ns)
    if args.fn(args, doc):
        save(doc, ns)


if __name__ == "__main__":
    main()
