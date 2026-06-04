output "r2_bucket" {
  description = "R2 bucket holding the snapshots."
  value       = cloudflare_r2_bucket.wikipedia_snapshots.name
}

output "r2_endpoint" {
  description = "S3-compatible endpoint (for reference; uploads use wrangler OAuth)."
  value       = "https://${var.cloudflare_account_id}.r2.cloudflarestorage.com"
}

output "nomad_job" {
  description = "Registered monthly-refresh job id."
  value       = nomad_job.wikipedia_refresh.id
}
