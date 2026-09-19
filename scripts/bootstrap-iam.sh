#!/usr/bin/env bash
# One-time IAM bootstrap for WODin. RUN THIS AS A TENANCY ADMIN.
#
#   scripts/bootstrap-iam.sh <wodin-compartment-ocid>
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
# quick; the alternative is a deploy credential that can rewrite the
# tenancy's permissions, which fails quietly and much later.
#
# Idempotent: skips anything that already exists, so it is safe to re-run.
set -euo pipefail
export SUPPRESS_LABEL_WARNING=True

COMPARTMENT="${1:?usage: bootstrap-iam.sh <wodin-compartment-ocid>}"
TENANCY="$(oci iam compartment get --compartment-id "$COMPARTMENT" --query 'data."compartment-id"' --raw-output)"
BUCKET="${BUCKET:-wodin-site}"

# Walk up to the tenancy root: the compartment's parent may itself be a
# child (WODin sits under Projects), and IAM must be created at the root.
while oci iam compartment get --compartment-id "$TENANCY" >/dev/null 2>&1; do
  PARENT="$(oci iam compartment get --compartment-id "$TENANCY" --query 'data."compartment-id"' --raw-output 2>/dev/null || true)"
  [ -z "$PARENT" ] && break
  TENANCY="$PARENT"
done
echo "tenancy root: $TENANCY"

ensure_dg() {
  local name="$1" desc="$2" rule="$3"
  if oci iam dynamic-group list --compartment-id "$TENANCY" --all \
       --query "data[?name=='$name'].id" --raw-output 2>/dev/null | grep -q ocid1; then
    echo "  dynamic-group $name already exists"
  else
    oci iam dynamic-group create --compartment-id "$TENANCY" \
      --name "$name" --description "$desc" --matching-rule "$rule" >/dev/null
    echo "  created dynamic-group $name"
  fi
}

# Scoped to this compartment, so these match WODin's resources and nothing
# else in the tenancy.
ensure_dg "wodin-functions" "WODin OCI Functions (server)" \
  "ALL {resource.type = 'fnfunc', resource.compartment.id = '$COMPARTMENT'}"
ensure_dg "wodin-gateway" "WODin API Gateway" \
  "ALL {resource.type = 'ApiGateway', resource.compartment.id = '$COMPARTMENT'}"

# manage, not read: the function writes results/<athlete>/<date>.json as
# well as serving the site.
STATEMENTS='[
  "Allow dynamic-group wodin-functions to manage objects in compartment id '"$COMPARTMENT"' where target.bucket.name = '"'$BUCKET'"'",
  "Allow dynamic-group wodin-functions to read buckets in compartment id '"$COMPARTMENT"'",
  "Allow dynamic-group wodin-gateway to use functions-family in compartment id '"$COMPARTMENT"'"
]'

if oci iam policy list --compartment-id "$TENANCY" --all \
     --query "data[?name=='wodin-policies'].id" --raw-output 2>/dev/null | grep -q ocid1; then
  echo "  policy wodin-policies already exists -- review it by hand if the statements above changed"
else
  oci iam policy create --compartment-id "$TENANCY" --name "wodin-policies" \
    --description "Access for WODin's dynamic groups" --statements "$STATEMENTS" >/dev/null
  echo "  created policy wodin-policies"
fi

echo
echo "Done. IAM propagation is not instant -- a deploy in the next minute or"
echo "two may still see 404s reading the bucket, which OCI returns for"
echo "'not authorized' as readily as for 'absent'."
