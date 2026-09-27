# Refresh everything and rebuild the served database (Windows).
# Schedule it daily during the season with Task Scheduler, e.g.:
#   schtasks /Create /SC DAILY /ST 06:00 /TN "HoopsLab refresh" /TR "powershell -File E:\VIBE\NBA\scripts\refresh.ps1"
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONIOENCODING = "utf-8"
python -m nbastats update          # current season (fresh fetch while in progress) + models
python -m nbastats predict         # rest-of-season or next-season forecast
python -m nbastats export          # data/nba_serve.db for the website
Write-Host "Refreshed. Redeploy (docker compose up -d --build) or copy data/nba_serve.db to the server."
