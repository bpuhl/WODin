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
import re
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
PIN_DIGITS = 6            # typed at a gym, so it has to be memorable

# An athlete id is a URL segment (/brian), an object prefix
# (results/brian/) and a login path all at once. Refuse anything that
# collides with a real route -- after results exist under a prefix,
# renaming is a migration rather than an edit.
RESERVED_IDS = {
    "api", "src", "styles", "fonts", "icons", "schema", "examples",
    "wods", "results", "login", "auth", "roster", "sw", "index",
    "manifest", "favicon", "robots", "admin", "static", "assets",
}


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
                "role": a.get("role") or "athlete",
                "coaches": list(a.get("athletes") or []) if a.get("role") == "coach" else [],
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


def valid_id(text):
    text = text.strip().lower()
    if text in RESERVED_IDS:
        raise argparse.ArgumentTypeError(
            "%r collides with a route on the site. Reserved: %s"
            % (text, ", ".join(sorted(RESERVED_IDS))))
    if not re.match(r"^[a-z0-9][a-z0-9_-]{0,31}$", text):
        raise argparse.ArgumentTypeError(
            "ids are lower-case letters, digits, - and _, starting with a "
            "letter or digit, up to 32 characters -- it becomes a URL "
            "segment and an object prefix")
    return text


def make_pin(pin=None):
    """Return (pin, salt_hex, hash_hex, iterations)."""
    if pin is None:
        pin = "".join(secrets.choice("0123456789") for _ in range(PIN_DIGITS))
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt,
                                 PBKDF2_ITERATIONS).hex()
    return pin, salt.hex(), digest, PBKDF2_ITERATIONS


def cmd_set_pin(args, doc):
    a = find(doc, args.id)
    if not a:
        sys.exit("No athlete with id %r. Run `list` to see them." % args.id)
    pin, salt, digest, iters = make_pin(args.pin)
    a["pin_salt"], a["pin_hash"], a["pin_iterations"] = salt, digest, iters
    print()
    print("=" * 72)
    print("  %s can now sign in by typing:" % (a.get("name") or a["id"]))
    print()
    print("    %s/%s" % (SITE, a["id"]))
    print("    PIN: %s%s" % (pin, "  (chosen)" if args.pin else ""))
    print()
    print("  %d digits, so it is only safe because the gateway rate-limits"
          % len(pin))
    print("  and each attempt costs the server real work. Do not reuse it.")
    print("=" * 72)
    return True


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
    print("  Or set a PIN so they can just type the address:")
    print("    python3 scripts/manage-athletes.py set-pin %s" % args.id)
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
    print("%-14s %-18s %-9s %-8s %-22s %s" % ("ID", "NAME", "STATUS", "SIGN-IN", "ROLE", "ISSUED"))
    for a in sorted(athletes, key=lambda x: x.get("id", "")):
        state = "disabled" if a.get("disabled") else "active"
        role = a.get("role") or "athlete"
        if role == "coach":
            role += " (%s)" % ",".join(a.get("athletes") or [])
        print("%-14s %-18s %-9s %-8s %-22s %s" % (
            a.get("id", "?"), (a.get("name") or "")[:18],
            state, "PIN" if a.get("pin_hash") else "-",
            role[:22], a.get("issued", "?")))
    return False


def _set_disabled(doc, athlete_id, value):
    a = find(doc, athlete_id)
    if not a:
        sys.exit("No athlete with id %r. Run `list` to see them." % athlete_id)
    a["disabled"] = value
    if not value:
        a.pop("disabled", None)
    return a


def cmd_set_role(args, doc):
    a = find(doc, args.id)
    if not a:
        sys.exit("No athlete with id %r." % args.id)
    if args.role == "athlete":
        a.pop("role", None)
        a.pop("athletes", None)
        print("%s is now a plain athlete (sees only their own sessions)." % args.id)
        return True
    if args.role == "coach":
        if not args.athletes:
            sys.exit("A coach needs --athletes: a coach with nobody assigned "
                     "can do nothing a plain athlete cannot.")
        missing = [x for x in args.athletes if not find(doc, x)]
        if missing:
            sys.exit("No such athlete(s): %s" % ", ".join(missing))
        a["role"] = "coach"
        a["athletes"] = list(args.athletes)
        print("%s now coaches: %s" % (args.id, ", ".join(args.athletes)))
        print("They can read those athletes' history and log sessions for them.")
        print("Every result records who submitted it, so a coach-logged "
              "session is distinguishable from a self-logged one.")
        return True
    a["role"] = "admin"
    a.pop("athletes", None)
    print("%s is now an admin (may act for every athlete)." % args.id)
    return True


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
    a.add_argument("--id", required=True, type=valid_id,
                   help="short identifier; also the URL segment and the "
                        "results/<id>/ prefix")
    a.add_argument("--name", help="display name (defaults from --id)")
    a.set_defaults(fn=cmd_add)

    sub.add_parser("list", help="show athletes").set_defaults(fn=cmd_list)

    r = sub.add_parser("set-role", help="make someone a coach or admin")
    r.add_argument("id")
    r.add_argument("role", choices=["athlete", "coach", "admin"])
    r.add_argument("--athletes", nargs="+", metavar="ID",
                   help="for a coach: exactly whose sessions they may see "
                        "and log. Scoped deliberately -- a coach is not an "
                        "admin.")
    r.set_defaults(fn=cmd_set_role)

    sp = sub.add_parser("set-pin", help="set or regenerate a sign-in PIN")
    sp.add_argument("id")
    sp.add_argument("--pin", help="use this PIN instead of a random one "
                                  "(%d digits recommended)" % PIN_DIGITS)
    sp.set_defaults(fn=cmd_set_pin)

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
