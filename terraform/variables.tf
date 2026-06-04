variable "cloudflare_api_token" {
  description = "Cloudflare API token with R2 read perms. Supply via TF_VAR_cloudflare_api_token; never commit it."
  type        = string
  sensitive   = true
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID (not secret — it's in the public R2 endpoint URL)."
  type        = string
}

variable "bucket_name" {
  description = "R2 bucket holding Wikipedia DB snapshots."
  type        = string
  default     = "wikipedia-snapshots"
}

variable "nomad_address" {
  description = "Nomad HTTP API address."
  type        = string
  default     = "http://127.0.0.1:4646"
}
