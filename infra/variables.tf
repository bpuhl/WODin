# Nothing in this file may carry a default that identifies the tenancy.
# This repository is public: OCIDs are not credentials, but they are free
# reconnaissance, and a committed fallback is how one ends up published.

variable "tenancy_ocid" {
  description = "OCID of the tenancy"
  type        = string
}

variable "user_ocid" {
  description = "OCID of the API user"
  type        = string
}

variable "fingerprint" {
  description = "Fingerprint of the API signing key"
  type        = string
}

variable "private_key" {
  description = "PEM contents of the API signing key"
  type        = string
  sensitive   = true
}

variable "region" {
  description = "OCI region identifier, e.g. us-sanjose-1"
  type        = string
}

variable "compartment_ocid" {
  description = "Compartment holding the WODin resources"
  type        = string
}

variable "server_image" {
  description = "Fully qualified OCIR image for the server function"
  type        = string
}

variable "site_bucket_name" {
  description = "Object Storage bucket holding the app, published workouts and results"
  type        = string
  default     = "wodin-site"
}

variable "vcn_cidr" {
  description = "CIDR for the WODin VCN. Distinct from Pulse (10.20) and Perch (10.30) so the three could be peered later without renumbering."
  type        = string
  default     = "10.40.0.0/24"
}
