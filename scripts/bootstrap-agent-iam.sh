#!/usr/bin/env bash
# Grant the OpenClaw agent access to WODin's data bucket.
# RUN THIS AS A TENANCY ADMIN. Safe to paste into OCI Cloud Shell.
#
#   scripts/bootstrap-agent-iam.sh <wodin-compartment> <agent-compartment> [tenancy] [--dry-run]
#
# WHY IT IS SEPARATE FROM bootstrap-iam.sh
# ----------------------------------------
# That one grants WODin's own components access to WODin. This is a
# CROSS-COMPARTMENT grant handing a third party -- an agent living in
# another compartment -- access to this project's data. Different blast
# radius, different review, so it is a deliberate second decision rather
# than a line buried in the first.
#
# WHAT IT DELIBERATELY DOES NOT GRANT
# -----------------------------------
# Nothing on the site bucket. That holds auth.json: the session signing
# secret and every athlete's key hash. Anything holding the signing secret
# can forge any athlete's session, and the agent has no need of it. The
# two-bucket split exists precisely so this grant can be bucket-level and
# still exclude it -- object-name conditions cannot, because ListObjects is
# bucket-level and would expose every object name regardless.
set -euo pipefail
export SUPPRESS_LABEL_WARNING=True

WODIN_COMPARTMENT="${1:-}"
AGENT_COMPARTMENT="${2:-}"
TENANCY="${3:-}"
DRY_RUN=false
for a in "$@"; do [ "$a" = "--dry-run" ] && DRY_RUN=true; done
[ "$TENANCY" = "--dry-run" ] && TENANCY=""

if [ -z "$WODIN_COMPARTMENT" ] || [ -z "$AGENT_COMPARTMENT" ]; then
  echo "usage: $0 <wodin-compartment-ocid> <agent-compartment-ocid> [tenancy-ocid] [--dry-run]" >&2
  exit 2
fi

# Taken, never derived: OCI's root compartment reports itself as its own
# parent, so walking up never terminates.
[ -z "$TENANCY" ] && TENANCY="${OCI_TENANCY:-}"
if [ -z "$TENANCY" ] && [ -f "$HOME/.oci/config" ]; then
  TENANCY="$(awk -F= '/^tenancy=/{print $2; exit}' "$HOME/.oci/config")"
fi
[ -n "$TENANCY" ] || { echo "Could not determine the tenancy OCID. Pass it as the third argument." >&2; exit 2; }

DATA_BUCKET="${DATA_BUCKET:-wodin-data}"
DG_NAME="${DG_NAME:-claudbot-agents}"
POLICY_NAME="${POLICY_NAME:-wodin-agent-access}"

echo "wodin compartment : $WODIN_COMPARTMENT"
echo "agent compartment : $AGENT_COMPARTMENT"
echo "tenancy           : $TENANCY"
echo "data bucket       : $DATA_BUCKET  (site bucket is NOT granted)"
$DRY_RUN && echo "MODE              : dry run"
echo

run() {
  if $DRY_RUN; then printf '  would run:'; printf ' %q' "$@"; printf '\n'
  else "$@" >/dev/null; fi
}

exists() {
  oci iam "$1" list --compartment-id "$TENANCY" --all \
    --query "data[?name=='$2'].id" --raw-output 2>/dev/null | grep -q ocid1
}

# Matches every instance in the agent's compartment. If the agent is ever
# one instance among several there, narrow this to instance.id.
if exists dynamic-group "$DG_NAME"; then
  echo "  dynamic-group $DG_NAME already exists -- check its rule covers the agent"
else
  run oci iam dynamic-group create --compartment-id "$TENANCY" --name "$DG_NAME" \
    --description "OpenClaw agents permitted to publish WODin workouts" \
    --matching-rule "ALL {instance.compartment.id = '$AGENT_COMPARTMENT'}"
  echo "  created dynamic-group $DG_NAME"
fi

# manage, not read: the agent publishes workouts as well as reading
# history. Scoped to one bucket, and that bucket holds no credentials.
STATEMENTS=$(cat <<EOF
[
  "Allow dynamic-group $DG_NAME to manage objects in compartment id $WODIN_COMPARTMENT where target.bucket.name = '$DATA_BUCKET'",
  "Allow dynamic-group $DG_NAME to read buckets in compartment id $WODIN_COMPARTMENT"
]
EOF
)

if exists policy "$POLICY_NAME"; then
  echo "  policy $POLICY_NAME already exists. Intended statements:"
  echo "$STATEMENTS" | sed 's/^/    /'
else
  run oci iam policy create --compartment-id "$TENANCY" --name "$POLICY_NAME" \
    --description "Cross-compartment: OpenClaw agents read WODin history and publish workouts" \
    --statements "$STATEMENTS"
  echo "  created policy $POLICY_NAME"
fi

echo
$DRY_RUN && echo "Dry run only." || cat <<'NOTE'
Done. The agent can now, in the data bucket only:
  read  roster.json                      who to build for
  read  results/<athlete>/               history, including _index.json
  write wods/<athlete>/<date>.json       the next session

It has no grant on the site bucket, so auth.json -- the signing secret and
every key hash -- is out of reach.
NOTE
