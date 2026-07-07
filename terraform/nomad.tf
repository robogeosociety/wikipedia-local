# The monthly refresh job (newest ZIM → rebuild wiki.db → r2_sync → prune). The
# jobspec lives next to the project; Terraform registers/updates it from the file,
# so the .nomad spec stays the single source. Already registered via `nomad job
# run`, so import it on first apply:
#   terraform import nomad_job.wikipedia_refresh wikipedia-refresh
resource "nomad_job" "wikipedia_refresh" {
  jobspec = file("${path.module}/../nomad/wikipedia-refresh.nomad")
}

# Custom-wiki build/deploy: a parameterized dispatch job (fired by the
# obsidian-automations lanes after they merge article markdown) + a daily cron
# backstop. Both run scripts/build_wikis.sh (DB + Quartz → Cloudflare Pages).
resource "nomad_job" "wiki_build" {
  jobspec = file("${path.module}/../nomad/wiki-build.nomad")
}

resource "nomad_job" "wiki_build_cron" {
  jobspec = file("${path.module}/../nomad/wiki-build-cron.nomad")
}
