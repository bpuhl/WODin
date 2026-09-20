#!/usr/bin/env bash
# Push this machine's working OCI credentials into a repository's Actions
# secrets.
#
#   scripts/sync-secrets.sh --repo bpuhl/WODin --compartment <ocid>
#   scripts/sync-secrets.sh --repo bpuhl/WODin --compartment <ocid> --dry-run
#   scripts/sync-secrets.sh --repo bpuhl/Perch --compartment <ocid> \
#       --token-secret ocid1.vaultsecret...        # auth token from Vault
#
# WHY
# ---
# GitHub secrets are write-only: once set, nobody can read them back, so a
# typo is invisible until a deploy fails on it twenty steps in. Every value
# here is instead taken from a source that is demonstrably working -- the
# ~/.oci/config that is authenticating right now, and the key file it
# points at -- rather than retyped.
#
# Most of these are not secrets at all. Tenancy, user, fingerprint, region
# and compartment are identifiers; they are secrets only because Actions
# has no other place to put configuration. The genuinely sensitive values
# are the private key and the OCIR auth token.
#
# THE AUTH TOKEN is the one value with no local source: OCI displays it
# once, at creation, and never again. Supply it with --token-secret (read
# from Vault), the OCIR_AUTH_TOKEN environment variable, or the prompt.
set -euo pipefail
export SUPPRESS_LABEL_WARNING=True

REPO="" PROFILE="DEFAULT" COMPARTMENT="" TOKEN_SECRET="" OCIR_USER="claude-aws"
DRY_RUN=false VERIFY_ONLY=false

while [ $# -gt 0 ]; do
  case "$1" in
    --repo)         REPO="$2"; shift 2 ;;
    --profile)      PROFILE="$2"; shift 2 ;;
    --compartment)  COMPARTMENT="$2"; shift 2 ;;
    --token-secret) TOKEN_SECRET="$2"; shift 2 ;;
    --ocir-user)    OCIR_USER="$2"; shift 2 ;;
    --dry-run)      DRY_RUN=true; shift ;;
    --verify-only)  VERIFY_ONLY=true; shift ;;
    -h|--help)      sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$REPO" ]        || { echo "--repo is required" >&2; exit 2; }
[ -n "$COMPARTMENT" ] || { echo "--compartment is required" >&2; exit 2; }

CFG="$HOME/.oci/config"
[ -f "$CFG" ] || { echo "no $CFG on this machine" >&2; exit 1; }

# Read one key from the named profile only -- a bare $1==key match would
# pick up whichever profile happened to come first in the file.
cfg() {
  awk -F= -v want="[$PROFILE]" -v k="$1" '
    /^\[/ { sect=$0 }
    sect==want && $1==k { sub(/^[^=]*=/,""); print; exit }' "$CFG"
}

TENANCY="$(cfg tenancy)"; USER_OCID="$(cfg user)"
FINGERPRINT="$(cfg fingerprint)"; REGION="$(cfg region)"
KEY_FILE="$(cfg key_file)"
for v in TENANCY USER_OCID FINGERPRINT REGION KEY_FILE; do
  [ -n "${!v}" ] || { echo "profile [$PROFILE] is missing ${v,,}" >&2; exit 1; }
done
[ -f "$KEY_FILE" ] || { echo "key file not found: $KEY_FILE" >&2; exit 1; }

# PEM only. The OCI CLI appends an "OCI_API_KEY" label line by convention
# and understands it; Terraform's provider does not expect it.
KEY_CONTENT="$(sed -n '/-----BEGIN/,/-----END/p' "$KEY_FILE")"
echo "$KEY_CONTENT" | openssl rsa -noout -check >/dev/null 2>&1 \
  || { echo "key file does not parse as a private key: $KEY_FILE" >&2; exit 1; }

NAMESPACE="$(oci os ns get --query data --raw-output)"
OCIR_USERNAME="${NAMESPACE}/${OCIR_USER}"

# --- the auth token, in order of preference -------------------------------
TOKEN="${OCIR_AUTH_TOKEN:-}"
if [ -n "$TOKEN_SECRET" ]; then
  echo "reading the auth token from Vault"
  TOKEN="$(oci secrets secret-bundle get --secret-id "$TOKEN_SECRET" \
            --query 'data."secret-bundle-content".content' --raw-output | base64 -d)"
fi
if [ -z "$TOKEN" ] && ! $VERIFY_ONLY; then
  read -r -s -p "OCIR auth token (not echoed): " TOKEN; echo
fi
[ -n "$TOKEN" ] || { echo "no auth token supplied" >&2; exit 1; }

# --- verify BEFORE writing anything ---------------------------------------
# A wrong OCIR username survives every other check and then fails at docker
# login, forty seconds into a deploy. This is the same bearer handshake
# docker performs, so a pass here means a pass there.
echo
echo "verifying OCIR credentials as ${OCIR_USERNAME}"
REG="${REGION}.ocir.io"
WWW="$(curl -s -D - -o /dev/null --max-time 45 "https://${REG}/v2/" | tr -d '\r' | grep -i '^www-authenticate:')"
REALM="$(echo "$WWW"    | sed -n 's/.*realm="\([^"]*\)".*/\1/p')"
SERVICE="$(echo "$WWW"  | sed -n 's/.*service="\([^"]*\)".*/\1/p')"
if [ -z "$REALM" ]; then
  echo "  could not read the registry's auth realm -- skipping verification" >&2
else
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 45 -u "${OCIR_USERNAME}:${TOKEN}" \
            "${REALM}?service=${SERVICE}&scope=repository:${NAMESPACE}/placeholder:pull")"
  if [ "$CODE" = "200" ]; then
    echo "  OK — registry issued a token"
  else
    echo "  FAILED (HTTP $CODE). Check --ocir-user and the auth token." >&2
    echo "  Nothing was written." >&2
    exit 1
  fi
fi
$VERIFY_ONLY && { echo "verify-only: nothing written."; exit 0; }

# --- write ----------------------------------------------------------------
set_secret() {
  if $DRY_RUN; then
    printf '  would set %-22s (%d chars)\n' "$1" "${#2}"
  else
    printf '%s' "$2" | gh secret set "$1" --repo "$REPO" >/dev/null
    printf '  set %-22s (%d chars)\n' "$1" "${#2}"
  fi
}

echo
echo "repo        : $REPO"
echo "profile     : $PROFILE"
echo "namespace   : $NAMESPACE"
$DRY_RUN && echo "MODE        : dry run"
echo
set_secret OCI_CLI_TENANCY      "$TENANCY"
set_secret OCI_CLI_USER         "$USER_OCID"
set_secret OCI_CLI_FINGERPRINT  "$FINGERPRINT"
set_secret OCI_CLI_REGION       "$REGION"
set_secret OCI_CLI_KEY_CONTENT  "$KEY_CONTENT"
set_secret OCI_COMPARTMENT_OCID "$COMPARTMENT"
set_secret OCIR_USERNAME        "$OCIR_USERNAME"
set_secret OCIR_AUTH_TOKEN      "$TOKEN"

echo
$DRY_RUN || echo "Done. Values are unreadable from here on -- re-run this rather than"
$DRY_RUN || echo "trying to recover them."
