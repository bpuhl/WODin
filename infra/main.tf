# ---------------------------------------------------------------------------
# Networking — OCI Functions and API Gateway both require a VCN/subnet.
# Public subnet + Internet Gateway rather than a NAT Gateway: an IGW has no
# hourly charge.
# ---------------------------------------------------------------------------

resource "oci_core_vcn" "wodin" {
  compartment_id = var.compartment_ocid
  display_name   = "wodin-vcn"
  cidr_blocks    = [var.vcn_cidr]
  dns_label      = "wodinvcn"
}

resource "oci_core_internet_gateway" "wodin" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.wodin.id
  display_name   = "wodin-igw"
  enabled        = true
}

resource "oci_core_route_table" "wodin" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.wodin.id
  display_name   = "wodin-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.wodin.id
  }
}

resource "oci_core_security_list" "wodin" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.wodin.id
  display_name   = "wodin-sl"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }

  # Load-bearing, not boilerplate. The PUBLIC API Gateway's VNIC lives in
  # this subnet, so this security list governs inbound internet traffic to
  # it. Pulse proved by outage that without this rule the site is
  # unreachable from outside the VCN entirely.
  ingress_security_rules {
    protocol = "6"
    source   = "0.0.0.0/0"

    tcp_options {
      min = 443
      max = 443
    }
  }
}

resource "oci_core_subnet" "wodin" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.wodin.id
  display_name               = "wodin-subnet"
  cidr_block                 = var.vcn_cidr
  dns_label                  = "wodinsub"
  route_table_id             = oci_core_route_table.wodin.id
  security_list_ids          = [oci_core_security_list.wodin.id]
  prohibit_public_ip_on_vnic = false
}

# ---------------------------------------------------------------------------
# Storage
#
# One private bucket holds three different things, which is deliberate --
# the function already has a Resource Principal for it, so a second bucket
# would buy a second IAM grant and nothing else:
#
#   (app assets)              the built site, public via the function
#   wods/<date>.json          published workouts   — written by the agent
#   results/<athlete>/…json   completed sessions   — written by the function
#
# Never public: the agent's credentials and the gate both assume the only
# way in is through the gateway.
# ---------------------------------------------------------------------------

data "oci_objectstorage_namespace" "ns" {
  compartment_id = var.compartment_ocid
}

resource "oci_objectstorage_bucket" "site" {
  compartment_id = var.compartment_ocid
  namespace      = data.oci_objectstorage_namespace.ns.namespace
  name           = var.site_bucket_name
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"

  # Results are the only thing here that cannot be rebuilt from the repo.
  versioning = "Enabled"
}

# The agent reads history and publishes workouts, so it needs a grant on
# whatever holds them. With a single bucket that grant necessarily covers
# auth.json -- the session signing secret -- and anything holding that can
# forge any athlete's session. Object-name conditions could express the
# distinction, but ListObjects is bucket-level and cannot be prefix-scoped,
# so the agent would still see every object name. A second bucket makes the
# boundary real rather than conventional.
resource "oci_objectstorage_bucket" "data" {
  compartment_id = var.compartment_ocid
  namespace      = data.oci_objectstorage_namespace.ns.namespace
  name           = var.data_bucket_name
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"

  # Results are the only thing across both buckets that cannot be rebuilt
  # from the repo, and this is also the bucket a third party writes to.
  versioning = "Enabled"
}

# ---------------------------------------------------------------------------
# NO IAM IN THIS FILE — deliberately.
#
# The dynamic groups and policy this stack needs are tenancy-level, and
# `manage policies` is privilege escalation by definition: anything holding
# it can grant itself anything. The deploy identity does not have it, so
# those live in scripts/bootstrap-iam.sh and are run once by a human.
#
# The cost is that a first apply produces a function that cannot read its
# own bucket until the bootstrap runs. The benefit is that the thing which
# deploys daily cannot rewrite the tenancy's permissions.
# ---------------------------------------------------------------------------

resource "oci_functions_application" "wodin" {
  compartment_id = var.compartment_ocid
  display_name   = "wodin"
  subnet_ids     = [oci_core_subnet.wodin.id]
}

resource "oci_functions_function" "server" {
  application_id = oci_functions_application.wodin.id
  display_name   = "wodin-server"
  image          = var.server_image
  memory_in_mbs  = 256

  # 120s, not the 30s default. A cold start pulls this image and starts the
  # container; Perch measured ~46s on a fresh node, which the gateway
  # reported as a bare {"code":500} with no explanation. Warm requests are
  # sub-second, so the headroom costs nothing and only has to cover the
  # worst cold start.
  timeout_in_seconds = 120

  config = {
    BUCKET_NAME      = oci_objectstorage_bucket.site.name
    DATA_BUCKET_NAME = oci_objectstorage_bucket.data.name
    NAMESPACE        = data.oci_objectstorage_namespace.ns.namespace
  }
}

# ---------------------------------------------------------------------------
# API Gateway
#
# No authentication or authorization policy anywhere in here. The routes
# deliberately omit `authorization { type = "ANONYMOUS" }`: that is a real
# policy type, but OCI rejects it unless the deployment also declares an
# `authentication` policy for it to be the anonymous case OF. Confirmed by
# a live 400 on Perch: "Invalid specification.routes[0].requestPolicies.
# authorization.type: authentication policy not provided". With no
# authenticator, open is simply the default.
#
# The access gate is partial and lives inside the function, not here: the
# app shell stays public so shared #w= links keep working, while /wods/
# and history require a device key.
# ---------------------------------------------------------------------------

resource "oci_apigateway_gateway" "wodin" {
  compartment_id = var.compartment_ocid
  display_name   = "wodin-gw"
  endpoint_type  = "PUBLIC"
  subnet_id      = oci_core_subnet.wodin.id
}

resource "oci_apigateway_deployment" "wodin" {
  compartment_id = var.compartment_ocid
  gateway_id     = oci_apigateway_gateway.wodin.id
  display_name   = "wodin"
  path_prefix    = "/"

  specification {
    request_policies {
      # Not optional once a 6-digit PIN exists. A 32-character device key
      # is unguessable and this would be decoration; 1,000,000 PINs
      # against an unthrottled endpoint is a weekend's work. Combined with
      # the PBKDF2 cost per attempt, exhausting the space from one address
      # takes days against a single named athlete.
      #
      # Perch has carried this since its own build; WODin was simply never
      # given it, which did not matter until now.
      rate_limiting {
        rate_in_requests_per_second = 5
        rate_key                    = "CLIENT_IP"
      }
    }

    # "/" and "/{path*}" are separate routes because a path-parameter route
    # does not match the empty path -- with only the wildcard, the site root
    # 404s and only deep links work.
    routes {
      path    = "/"
      methods = ["GET", "HEAD"]

      backend {
        type        = "ORACLE_FUNCTIONS_BACKEND"
        function_id = oci_functions_function.server.id
      }
    }

    # POST is included from the start for the result-submission endpoint.
    # Adding a method later means another gateway deployment update, and
    # the route list is the one place that is cheap to get right now.
    routes {
      path    = "/{path*}"
      methods = ["GET", "HEAD", "POST"]

      backend {
        type        = "ORACLE_FUNCTIONS_BACKEND"
        function_id = oci_functions_function.server.id
      }
    }
  }
}

# ---------------------------------------------------------------------------
# Logging — configured from the start.
#
# Without it the API Gateway returns a bare 500 and the function's output
# goes nowhere, which on Perch turned one bug into an afternoon of guessing.
# ---------------------------------------------------------------------------

resource "oci_logging_log_group" "wodin" {
  compartment_id = var.compartment_ocid
  display_name   = "wodin-logs"
  description    = "Logs for the WODin app"
}

resource "oci_logging_log" "function_invoke" {
  display_name       = "wodin-server-invoke"
  log_group_id       = oci_logging_log_group.wodin.id
  log_type           = "SERVICE"
  is_enabled         = true
  retention_duration = 30

  configuration {
    compartment_id = var.compartment_ocid

    source {
      category    = "invoke"
      resource    = oci_functions_application.wodin.id
      service     = "functions"
      source_type = "OCISERVICE"
    }
  }
}
