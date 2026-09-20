# State lives in Object Storage, moved by the workflow rather than by a
# Terraform backend block.
#
# The "s3" backend against OCI's S3-compatible endpoint fails with
# "501 NotImplemented: AWS chunked encoding not supported" on PutObject --
# established the hard way on the Pulse project, and not worth rediscovering.
#
# deploy.yml downloads the state object before `terraform init` and uploads
# it afterwards unconditionally, so a partial apply is recorded rather than
# lost. Safe without locking because the workflow's concurrency group
# serialises runs.
#
# Local runs use the same bucket and object name, so a deploy from a laptop
# and a deploy from CI continue each other rather than fork. scripts/tf.sh
# handles the download/upload for local use.
