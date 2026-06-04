# Monthly refresh of the local Wikipedia SQLite DB + R2 snapshot.
#
#   nomad job run nomad/wikipedia-refresh.nomad
#
# Runs scripts/refresh.sh on the 5th of each month (by then the prior month's
# Kiwix dump is published). raw_exec inherits a minimal env, so PATH/HOME are set
# explicitly (uv, homebrew tools, wrangler auth under HOME).
#
# Requirements:
#   - The Nomad agent's shell binary needs macOS Full Disk Access to read/write
#     /Volumes (launchd is TCC-blocked from /Volumes otherwise) — this also lets
#     r2_sync.sh read the write-only INFLUX_OPS_TOKEN from observability/influxdb/.env
#     at runtime (for the Grafana "Backups" dashboard metric).
#   - Runs at 04:00 (off-peak); the R2 upload is bandwidth-capped via R2_BWLIMIT.

job "wikipedia-refresh" {
  type        = "batch"
  datacenters = ["*"]

  periodic {
    crons            = ["0 4 5 * *"]
    prohibit_overlap = true
    time_zone        = "America/Denver"
  }

  group "refresh" {
    task "run" {
      driver = "raw_exec"

      config {
        command = "/Volumes/dev/data/wikipedia/scripts/refresh.sh"
      }

      env {
        PATH = "/opt/homebrew/bin:/Users/tommydoerr/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        HOME = "/Users/tommydoerr"
        R2_BWLIMIT        = "10m"   # pace the off-peak upload so it doesn't saturate the link
        INFLUX_URL        = "http://localhost:8086"
        INFLUX_ORG        = "home"
        INFLUX_OPS_BUCKET = "ops"
      }

      resources {
        cpu    = 4000
        memory = 4096
      }
    }
  }
}
