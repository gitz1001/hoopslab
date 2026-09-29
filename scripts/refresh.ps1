# Refresh everything and rebuild the served database (Windows).
# Schedule it daily during the season with Task Scheduler, e.g.:
#   schtasks /Create /SC DAILY /ST 06:00 /TN "HoopsLab refresh" /TR "powershell -File E:\VIBE\NBA\scripts\refresh.ps1"
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONIOENCODING = "utf-8"
python -m nbastats update          # current season (fresh fetch while in progress) + models
python -m nbastats predict         # rest-of-season or next-season forecast
python -m nbastats export --gz     # data/nba_serve.db (+ .gz for Render)
git add data/nba_serve.db.gz
git commit -m "Data refresh $(Get-Date -Format yyyy-MM-dd)"
git push                           # Render redeploys automatically
Write-Host "Refreshed and pushed. Render is redeploying."
