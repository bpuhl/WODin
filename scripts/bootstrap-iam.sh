#!/usr/bin/env bash
# One-time IAM bootstrap for WODin. RUN THIS AS A TENANCY ADMIN.
#
#   scripts/bootstrap-iam.sh <wodin-compartment-ocid> [tenancy-ocid]
#   scripts/bootstrap-iam.sh <wodin-compartment-ocid> --dry-run
#
# Designed to be pasted into OCI Cloud Shell, which is already
# authenticated as your console user and needs no key on disk.
#
# WHY THIS IS NOT IN TERRAFORM
# ----------------------------
# Everything here is tenancy-level IAM. `manage policies` is privilege
# escalation by definition -- anything holding it can write itself a policy
# granting anything else -- so the identity that deploys daily deliberately
# does not have it. Keeping these three resources out of the stack is what
# lets that identity be meaningfully less than an administrator.
#
# The trade is that a fresh `terraform apply` produces a function which
# cannot read its own bucket until this has run. That failure is loud and
# immediate; the alternative is a deploy credential that can rewrite the
# tenancy's permissions, which fails quietly and much later.
#
# Idempotent: skips anything that already exists, so re-running is safe.
set -euo pipefail
export SUPPRESS_LABEL_WARNING=True

COMPARTMENT="${1:-}"
DRY_RUN=false
TENANCY="${2:-}"
[ "${2:-}" = "--dry-run" ] && { DRY_RUN=true; TENANCY=""; }
[ "${3:-}" = "--dry-run" ] && DRY_RUN=true

if [ -z "$COMPARTMENT" ]; then
  echo "usage: $0 <wodin-compartment-ocid> [tenancy-ocid] [--dry-run]" >&2
  exit 2
fi

# The tenancy OCID is taken, never derived. Walking up the compartment tree
# looks tidier and is a trap: OCI's root compartment reports ITSELF as its
# own parent, so the obvious loop never terminates.
#   1. an explicit argument
#   2. $OCI_TENANCY  -- Cloud Shell sets this for you
#   3. the CLI config
if [ -z "$TENANCY" ]; then
  TENANCY="${OCI_TENANCY:-}"
fi
if [ -z "$TENANCY" ] && [ -f "$HOME/.oci/config" ]; then
  TENANCY="$(awk -F= '/^tenancy=/{print $2; exit}' "$HOME/.oci/config")"
fi
if [ -z "$TENANCY" ]; then
  echo "Could not determine the tenancy OCID. Pass it as the second argument." >&2
  exit 2
fi

BUCKET="${BUCKET:-wodin-site}"

echo "compartment : $COMPARTMENT"
echo "tenancy     : $TENANCY"
echo "bucket      : $BUCKET"
$DRY_RUN && echo "MODE        : dry run, nothing will be created"
echo

run() {
  if $DRY_RUN; then
    printf '  would run:'; printf ' %q' "$@"; printf '\n'
  else
    "$@" >/dev/null
  fi
}

exists() {
  # $1 = resource ("dynamic-group" | "policy"), $2 = name
  oci iam "$1" list --compartment-id "$TENANCY" --all \
    --query "data[?name=='$2'].id" --raw-output 2>/dev/null | grep -q ocid1
}

ensure_dg() {
  local name="$1" desc="$2" rule="$3"
  if exists dynamic-group "$name"; then
    echo "  dynamic-group $name already exists"
  else
    run oci iam dynamic-group create --compartment-id "$TENANCY" \
      --name "$name" --description "$desc" --matching-rule "$rule"
    echo "  created dynamic-group $name"
  fi
}

# Scoped to this compartment, so these match WODin's resources and nothing
# else in the tenancy.
ensure_dg "wodin-functions" "WODin OCI Functions (server)" \
  "ALL {resource.type = 'fnfunc', resource.compartment.id = '$COMPARTMENT'}"
ensure_dg "wodin-gateway" "WODin API Gateway" \
  "ALL {resource.type = 'ApiGateway', resource.compartment.id = '$COMPARTMENT'}"

# Unquoted heredoc so the OCIDs expand, while the single quotes around the
# bucket name stay literal -- the policy language wants them, and building
# this by string-concatenation in shell is how the quoting goes wrong.
#
# manage, not read: the function writes results/<athlete>/<date>.json as
# well as serving the site.
STATEMENTS=$(cat <<EOF
[
  "Allow dynamic-group wodin-functions to manage objects in compartment id $COMPARTMENT where target.bucket.name = '$BUCKET'",
  "Allow dynamic-group wodin-functions to read buckets in compartment id $COMPARTMENT",
  "Allow dynamic-group wodin-gateway to use functions-family in compartment id $COMPARTMENT"
]
EOF
)

if exists policy "wodin-policies"; then
  echo "  policy wodin-policies already exists -- review it by hand if these statements changed:"
  echo "$STATEMENTS" | sed 's/^/    /'
else
  run oci iam policy create --compartment-id "$TENANCY" --name "wodin-policies" \
    --description "Access for WODin's dynamic groups" --statements "$STATEMENTS"
  echo "  created policy wodin-policies"
fi

echo
if $DRY_RUN; then
  echo "Dry run only. Re-run without --dry-run to apply."
else
  echo "Done. IAM propagation is not instant -- a deploy in the next minute or"
  echo "two may still see 404s reading the bucket, which OCI returns for"
  echo "'not authorized' as readily as for 'absent'."
fi
