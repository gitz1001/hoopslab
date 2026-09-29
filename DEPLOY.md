# Deploying Hoops Lab

## How it fits together

```
 your PC (NBA.com reachable)                      any host (Docker)
 ─────────────────────────────                    ──────────────────────────────
 python -m nbastats update   ─┐
 python -m nbastats predict   ├─► data/nba.db ─►  python -m nbastats export
 (models run inside update)  ─┘                    └─► data/nba_serve.db (≈240 MB)
                                                         │ baked into the image
                                                         │ or copied to a volume
                                                         ▼
                                                   gunicorn app.wsgi:app
                                                   (read-only, immutable SQLite)
```

**Why the data is built locally:** stats.nba.com refuses or times out requests from most cloud
provider IP ranges (AWS, GCP, Azure, Render, Fly…). The pipeline therefore runs on a machine
that can reach it (your PC), and the website only ever reads the exported file. The website
has no NBA.com or Basketball Reference dependency at runtime.

**What gets served:** `python -m nbastats export` copies only the tables the site reads, drops
raw source tables, replaces ~180 MB of record indexes with a precomputed top-games table,
switches off WAL and vacuums. The site opens it with `NBA_READONLY=1` (read-only + immutable),
so concurrent workers never lock.

## 1. Build the data (once, then on every refresh)

```bash
pip install -r requirements.txt
python -m nbastats update --from 1996-97      # first time: all seasons (~40 min, cached after)
python -m nbastats history                    # 1979-80 to 1995-96 (Basketball Reference)
python -m nbastats predict                    # next-season / rest-of-season forecast
python -m nbastats export --gz                # -> data/nba_serve.db (+ .gz for Render)
python -m unittest discover -s tests          # smoke tests against the database
```

Refreshing later is one script: `scripts/refresh.ps1` (Windows) or `scripts/refresh.sh`.
While a season is in progress, `update` automatically ignores the cache for that season, and
`predict` switches to rest-of-season mode (results so far + simulations of the games left).

## 2. Run it in production

### Option A: Docker anywhere (VPS, home server, any container host)

```bash
docker compose up -d --build        # http://localhost:8050, health at /healthz
```

To refresh data without rebuilding the image, uncomment the volume in `docker-compose.yml`,
then push a new file with `scripts/deploy_db.sh user@host /srv/hoopslab` (copies and restarts).

Put a reverse proxy with HTTPS in front on a VPS (Caddy is the least work):

```
stats.example.com {
    reverse_proxy localhost:8050
}
```

### Option B: Fly.io

```bash
fly launch --no-deploy      # accept the existing fly.toml, pick an app name
fly deploy                  # builds the Dockerfile, including data/nba_serve.db
```

`fly.toml` sets a `/healthz` check, HTTPS, 1 GB RAM and scale-to-zero. Redeploy after each refresh.

### Option C: Render (set up for this repo)

The repo carries the served database gzipped (≈60 MB) through **Git LFS** at
`data/nba_serve.db.gz`. Render pulls LFS files when it clones, the Docker build unpacks it
and checks it is a real SQLite file (the build fails with a clear message if it only got an
LFS pointer).

First deploy:

1. Render dashboard → **New → Blueprint** → connect GitHub and pick `gitz1001/hoopslab`.
2. Render reads `render.yaml`: one Docker web service on the **free** plan, health check
   `/healthz`, auto-deploy on every push to `main`. Click **Apply**.
3. The first build takes a few minutes; the site then lives at `https://hoopslab.onrender.com`
   (or the name Render assigns). Free instances sleep after 15 idle minutes and take about a
   minute to wake; switch `plan: starter` in `render.yaml` (and `WEB_WORKERS` to 2) to keep it awake.

Refreshing data: run `scripts/refresh.ps1` (or `.sh`). It updates, predicts, exports
`--gz`, commits `data/nba_serve.db.gz` and pushes; Render redeploys automatically.

**Git LFS quota:** GitHub's free LFS allowance is 1 GB of storage and 1 GB of download
bandwidth per month. Each refresh stores a new ≈60 MB version, and each Render build downloads
one, so budget roughly a dozen refreshes a month on the free allowance (buy a data pack or
prune old LFS versions if you need more).

### Option D: Windows without Docker

```powershell
pip install -r requirements-serve.txt
$env:NBA_DB_PATH = "E:\VIBE\NBA\data\nba_serve.db"; $env:NBA_READONLY = "1"
python -m app --prod --host 0.0.0.0 --port 8050     # waitress
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | 8050 | Listening port |
| `WEB_WORKERS` | 2 | Gunicorn worker processes (each ~250 MB with caches) |
| `NBA_DB_PATH` | `data/nba.db` (image: `/data/nba_serve.db`) | Database the site reads |
| `NBA_READONLY` | 0 (image: 1) | Open the database read-only and immutable |
| `NBA_DATA_DIR` | `data/` | Where the pipeline keeps the database and raw cache |
| `NBA_API_CACHE_SECONDS` | 600 | `Cache-Control` max-age on API responses |

See `.env.example`.

## Production behaviour already built in

- Gunicorn (Linux) or waitress (Windows) instead of the Flask dev server, 120 s timeout.
- gzip for JSON/HTML/CSS responses (the players table drops from ~900 KB to ~120 KB).
- `Cache-Control` on API and static files; data only changes when you redeploy.
- Security headers: Content-Security-Policy (only jsdelivr for Chart.js), nosniff, frame and referrer policies.
- `/healthz` readiness probe used by Docker, Fly and Render.
- JSON errors for the API, SPA fallback for unknown pages, unhandled errors logged, not leaked.
- Every stat name is checked against a whitelist before it reaches SQL; all values are bound parameters.
- The container runs as a non-root user.
- Records are precomputed at startup so the heaviest page is instant.

## Sizing

One shared CPU and 1 GB RAM handles light traffic comfortably. The first request for a
season's clustering or projections takes about a second, then it's cached per worker.

## Checklist before going public

- [ ] `python -m unittest discover -s tests` passes against the exported file
      (`NBA_DB_PATH=data/nba_serve.db NBA_READONLY=1`).
- [ ] `docker compose up --build` and open `/healthz`, then click through Dashboard, a player, Predictions.
- [ ] Decide on a domain and HTTPS (Fly and Render give you one; on a VPS use Caddy).
- [ ] Data attribution: the footer credits NBA.com and Basketball Reference. Their data is for
      personal, non-commercial use; check their terms before any commercial use or heavy traffic.
- [ ] Schedule `scripts/refresh.*` during the season and redeploy after it runs.
