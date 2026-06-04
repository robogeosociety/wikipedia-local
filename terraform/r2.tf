# The wikipedia-snapshots bucket already exists (created via the Cloudflare MCP),
# so it is imported into state rather than recreated. Uploads use wrangler's OAuth,
# so — unlike observability/influxdb-backups — NO scoped S3-API token is needed here.
# Remove the import block after the first successful apply.
resource "cloudflare_r2_bucket" "wikipedia_snapshots" {
  account_id = var.cloudflare_account_id
  name       = var.bucket_name
}

import {
  to = cloudflare_r2_bucket.wikipedia_snapshots
  id = "${var.cloudflare_account_id}/${var.bucket_name}"
}
