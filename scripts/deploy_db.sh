#!/usr/bin/env bash
# Push a fresh served database to a VPS running docker compose with the volume mount enabled.
# Usage: scripts/deploy_db.sh user@host /srv/hoopslab
set -euo pipefail
target="$1"; dir="${2:-/srv/hoopslab}"
scp data/nba_serve.db "$target:$dir/data/nba_serve.db.new"
ssh "$target" "cd $dir && mv data/nba_serve.db.new data/nba_serve.db && docker compose restart web"
