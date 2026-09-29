#!/usr/bin/env bash
# Refresh everything and rebuild the served database (macOS/Linux). Cron example:
#   0 6 * * * cd /path/to/NBA && ./scripts/refresh.sh >> data/refresh.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
python -m nbastats update
python -m nbastats predict
python -m nbastats export --gz
git add data/nba_serve.db.gz
git commit -m "Data refresh $(date +%F)"
git push   # Render redeploys automatically
echo "Refreshed and pushed. Render is redeploying."
