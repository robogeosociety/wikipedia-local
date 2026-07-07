# Daily backstop for the custom-wiki build. Runs the same script with no wiki arg
# (all wikis); the per-wiki git-tree-oid gate makes it a cheap no-op when nothing
# changed. Separate from wiki-build.nomad because Nomad forbids periodic +
# parameterized on one job. Coexists with wikipedia-refresh (monthly, different DB).
job "wiki-build-cron" {
  type        = "batch"
  datacenters = ["*"]

  periodic {
    crons            = ["0 5 * * *"]
    prohibit_overlap = true
    time_zone        = "America/Denver"
  }

  group "build" {
    task "run" {
      driver = "raw_exec"

      config {
        command = "/Volumes/dev/data/wikipedia/scripts/build_wikis.sh"
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
