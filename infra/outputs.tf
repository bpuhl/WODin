output "gateway_hostname" {
  description = "CNAME target for wod.imav8n.com"
  value       = oci_apigateway_gateway.wodin.hostname
}

output "bucket_name" {
  value = oci_objectstorage_bucket.site.name
}

output "data_bucket_name" {
  value = oci_objectstorage_bucket.data.name
}

output "namespace" {
  value = data.oci_objectstorage_namespace.ns.namespace
}

output "server_function_id" {
  description = "Invoke directly to separate a broken function from a gateway that cannot reach a working one"
  value       = oci_functions_function.server.id
}

output "functions_app_id" {
  description = "Needed by the IAM bootstrap and by log queries"
  value       = oci_functions_application.wodin.id
}
