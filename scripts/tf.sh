#!/usr/bin/env bash
# Run Terraform locally against the same state CI uses.
#
#   scripts/tf.sh plan
#   scripts/tf.sh apply
#
# State is a single object in Object Storage rather than a Terraform
# backend -- see infra/backend.tf for why. This pulls it before running and
# pushes it back after, so a deploy from here and a deploy from CI continue
# each other instead of forking into two divergent stacks.
#
# Credentials come from ~/.oci/config. The same variables are supplied by
# repository secrets in CI, so both paths exercise identical Terraform.
set -euo pipefail

STATE_BUCKET="${STATE_BUCKET:-wodin-tfstate}"
STATE_OBJECT="${STATE_OBJECT:-wodin/terraform.tfstate}"
PROFILE="${OCI_CLI_PROFILE:-DEFAULT}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export SUPPRESS_LABEL_WARNING=True

cfg() { awk -F= -v k="$1" '/^\[/{s=$0} s=="['"$PROFILE"']" && $1==k {sub(/^[^=]*=/,""); print; exit}' ~/.oci/config; }

TF_VAR_tenancy_ocid="$(cfg tenancy)";  export TF_VAR_tenancy_ocid
TF_VAR_user_ocid="$(cfg user)";        export TF_VAR_user_ocid
TF_VAR_fingerprint="$(cfg fingerprint)"; export TF_VAR_fingerprint
TF_VAR_region="$(cfg region)";         export TF_VAR_region
TF_VAR_private_key="$(cat "$(cfg key_file)")"; export TF_VAR_private_key

: "${TF_VAR_compartment_ocid:?set TF_VAR_compartment_ocid to the WODin compartment OCID}"
: "${TF_VAR_server_image:=placeholder}"; export TF_VAR_server_image

NS="$(oci os ns get --query data --raw-output)"

echo "==> pulling state ${STATE_BUCKET}/${STATE_OBJECT}"
oci os object get --namespace "$NS" --bucket-name "$STATE_BUCKET" \
  --name "$STATE_OBJECT" --file "$ROOT/infra/terraform.tfstate" 2>/dev/null \
  || echo "    no existing state -- first run"

terraform -chdir="$ROOT/infra" init -input=false >/dev/null
set +e
terraform -chdir="$ROOT/infra" "$@"
rc=$?
set -e

# Unconditional, like CI: Terraform writes state incrementally, so a failed
# apply still needs its progress recorded or the resources it created are
# orphaned with nothing tracking them.
if [ -f "$ROOT/infra/terraform.tfstate" ]; then
  echo "==> pushing state"
  oci os object put --namespace "$NS" --bucket-name "$STATE_BUCKET" \
    --name "$STATE_OBJECT" --file "$ROOT/infra/terraform.tfstate" --force >/dev/null
fi
exit $rc
