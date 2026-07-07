# Build + deploy the custom wikis (dev/atlas/campsites): per wiki, build its
# SQLite DB (for MCP) and its Quartz site → Cloudflare Pages. Parameterized +
# dispatchable — obsidian-automations' lanes fire it after merging article markdown:
#
#   nomad job dispatch -meta wiki=dev wiki-build      # one wiki
#   nomad job dispatch wiki-build                     # all wikis
#
# The daily cron backstop is a sibling jobspec (wiki-build-cron.nomad) because
# Nomad forbids periodic + parameterized on one job.
job "wiki-build" {
  type        = "batch"
  datacenters = ["*"]

  parameterized {
    meta_optional = ["wiki"]
  }

  group "build" {
    task "run" {
      driver = "raw_exec"

      config {
        command = "/bin/bash"
        args    = ["-c", "/Volumes/dev/data/wikipedia/scripts/build_wikis.sh ${NOMAD_META_wiki}"]
      }

      env {
        PATH = "/Users/tommydoerr/.volta/bin:/opt/homebrew/bin:/Users/tommydoerr/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        HOME = "/Users/tommydoerr"
      }

      resources {
        cpu    = 2000
        memory = 2048
      }
    }
  }
}
