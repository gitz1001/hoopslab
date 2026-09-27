#!/usr/bin/env bash
# Refresh everything and rebuild the served database (macOS/Linux). Cron example:
#   0 6 * * * cd /path/to/NBA && ./scripts/refresh.sh >> data/refresh.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
python -m nbastats update
python -m nbastats predict
python -m nbastats export
echo "Refreshed. Redeploy or copy data/nba_serve.db to the server."
