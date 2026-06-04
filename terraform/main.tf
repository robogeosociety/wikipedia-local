# Terraform model of the local-Wikipedia project's cloud/infra footprint.
#
# The DATA PLANE (wiki.db, the ZIM, the R2 snapshot objects) is intentionally NOT
# modeled here — those are local artifacts + wrangler-driven objects, not infra.
# What IS infra, and is captured below:
#   • the R2 bucket that holds monthly DB snapshots (created out-of-band, imported)
#   • the Nomad periodic job that runs the monthly refresh
#
# Terraform is the declarative source of truth (plan / drift). The Cloudflare MCP +
# wrangler remain the imperative/runtime path for the data plane and ad-hoc ops —
# complementary, not a TF backend. The bucket was created via the MCP, so it is
# imported into state rather than recreated (see r2.tf).
#
# Apply (token supplied at apply time, never committed):
#   export TF_VAR_cloudflare_api_token=<CF API token, R2 read perms>
#   export TF_VAR_cloudflare_account_id=<account id>
#   terraform init && terraform plan && terraform apply
terraform {
  required_version = ">= 1.5"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
    nomad = {
      source  = "hashicorp/nomad"
      version = "~> 2.0"
    }
  }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

provider "nomad" {
  address = var.nomad_address
}
