# Hoops Lab

A local NBA stats system: a data pipeline that pulls free public data into SQLite, and a
website/dashboard on top of it for browsing players, teams, standings, leaderboards and
records, plus an analysis section that goes from simple comparisons to models.

## Deploying

See [DEPLOY.md](DEPLOY.md): build the data locally, `python -m nbastats export`, then
`docker compose up -d --build` (or Fly.io / Render / waitress on Windows). Refresh with
`scripts/refresh.ps1` or `scripts/refresh.sh`. Tests: `python -m unittest discover -s tests`.

## Quick start

Needs Python 3.10+.

```bash
pip install -r requirements.txt
python -m nbastats update                       # latest season (2025-26), ~1 minute
python -m app                                   # http://127.0.0.1:8050
```

Load more seasons whenever you like; everything is cached, so re-runs are free:

```bash
python -m nbastats update --from 1996-97        # every season NBA.com covers, ~30 minutes
python -m nbastats history                      # 1979-80 to 1995-96 from Basketball Reference
python -m nbastats update --seasons 2026-27     # a new season once it's played (add --refresh mid-season)
```

`update` flags: `--no-bref`, `--no-onoff` (skips 30 calls per season), `--no-playoffs`,
`--refresh` (ignore the raw-response cache).

## Data sources

| Source | How | What it gives |
| --- | --- | --- |
| NBA.com stats | [`nba_api`](https://github.com/swar/nba_api), 0.7 s between calls | player and team totals, NBA.com advanced (ratings, USG%, AST%, rebound %, PIE, pace), four factors, opponent stats, standings, every player and team box score (regular season + playoffs), on/off court splits (2007-08 on), shot zones, clutch stats, hustle stats (2015-16 on), height/weight/college/country, draft history, all-time career leaders, franchise history, player index |
| Basketball Reference | scraped, one page per 4 s (their limit is 20/min) | PER, Win Shares, BPM/OBPM/DBPM, VORP, STL%/BLK%, positions, awards; pre-1996 season totals |

Raw responses are cached in `data/raw/` (JSON for NBA.com, HTML for Basketball Reference),
so the database can be rebuilt without touching the network. Basketball Reference players
are matched to NBA.com IDs by normalized name (accents, punctuation and Jr./III removed),
falling back to last name + first initial.

## Database (`data/nba.db`)

| Table | Grain |
| --- | --- |
| `player_season_full` | one wide row per player-season: totals, per game, per 36, per 100 possessions, shooting, NBA.com advanced, Basketball Reference advanced, on/off, our own PER and Game Score |
| `team_season_full` | team-season: record, ratings, four factors, standings splits, Pythagorean wins and luck |
| `league_season` | league averages per season (pace, ORtg, TS%, 3PA rate, …) |
| `player_game`, `team_game` | every box score line, with Game Score |
| `player_onoff` | on-court vs off-court team ratings per player-team-season |
| `player_bio`, `player_shot_zones`, `player_clutch`, `player_hustle`, `draft_history` | raw extra sources, also merged into `player_season_full` |
| `player_season_base`, `player_season_adv`, `player_season_playoffs`, `team_season_base`, `team_season_adv`, `standings`, `bref_advanced` | raw source tables |
| `bref_history` | pre-1996 Basketball Reference totals + advanced |
| `players`, `teams`, `franchise_history`, `alltime_leaders` | reference data |

## Metrics we compute ourselves

- **TS%, eFG%, 3PA rate, FTA rate, per 36, per 100 possessions** from box totals.
- **PER** from scratch with Hollinger's formula (league factor, VOP, DRB%, pace adjustment,
  normalized to 15). It matches Basketball Reference's PER at r = 0.999.
- **Game Score** for every box score line.
- **Pythagorean wins** (exponent 13.91) and luck = actual − expected wins.

## What's loaded

After the full load: 30 NBA.com seasons (1996-97 to 2025-26) with about 787,000 player box
score lines, plus 17 Basketball Reference seasons (1979-80 to 1995-96). 99.6% of Basketball
Reference player-seasons match an NBA.com player. The database is about 430 MB.

## Advanced models (`python -m nbastats models`)

Runs automatically at the end of every `update`; rerun it alone any time (no network needed
except lineups missing from the cache; add `--lineups` to (re)load lineups for all seasons).

| Model | What it does |
| --- | --- |
| **Elo** | Every game since 1996-97: K = 20, 100-point home edge, margin-of-victory multiplier, 25% regression to 1505 between seasons. Calls about two thirds of regular-season games correctly. |
| **SRS** | Least-squares team ratings from game margins with a home-court term, giving margin adjusted for strength of schedule. |
| **Rest and home court** | Days of rest before each game, back-to-back effects, rest-vs-rest matchups, home win % over time. |
| **RAPM** | Ridge regression of every five-man lineup's offensive and defensive rating (NBA.com lineups, 2007-08 on, possession weighted) on who was on the floor. |
| **Box impact** | Ridge model that learns which per-100 box-score stats predict RAPM, so seasons without lineup data (1996-2007) still get an estimate. Its coefficients are shown on the Impact page. |
| **Impact** | RAPM re-fit with the box estimate as its prior (the approach behind modern metrics like EPM), split into offense and defense. |
| **WAR** | (Impact − replacement level of −2) × possessions ÷ 100 ÷ that season's points per win (fit from team point differential vs wins). |
| **Shot quality** | Expected eFG% from each player's shot-zone mix at league-average accuracy; shot-making = eFG% − xeFG%; scoring value = points added vs a league-average true shooter on the same attempts. |
| **Consistency** | Game Score standard deviation, floor (10th percentile), ceiling (90th percentile), mean ÷ SD. |
| **Signal vs noise** | Season-to-season correlation of 30+ stats, showing which ones reflect stable skill. |

## Season predictions (`python -m nbastats predict`)

Forecasts the next season (2026-27 by default) from the stored history plus NBA.com's current
rosters and schedule. Rerun it after trades and signings, or mid-season after an `update`.

1. **Player projections**: offensive and defensive impact from the last three seasons weighted
   6/3/1 by possessions (weights chosen by backtest), shrunk toward a prior when the sample is
   thin, and aged with aging curves measured from this database. Per-game stats and TS% are
   projected the same way. Rookies start from how past picks in the same draft range played.
2. **Minutes**: recent games × minutes per game, nudged toward better players, scaled so each
   roster fills 48 × 5 × 82 minutes.
3. **Team strength**: minutes-weighted player impact, calibrated on a backtest of every season
   since 2000-01 with only earlier data (r = 0.70 vs actual net rating, typical miss ≈ 8.5 wins).
4. **Simulation**: 10,000 seasons over the real schedule, each drawing team strength from the
   backtest error; play-in and a full best-of-7 bracket give playoff, round-by-round and title odds.
5. **Awards**: conditional-logit MVP and DPOY models trained on past preseason projections vs
   actual voting (the preseason favourite historically wins about a quarter to a third of the time).
6. **Breakouts and regression**: projected impact changes, shooting-luck candidates, team win swings.

## Website

- **Dashboard**: league KPIs vs last season, leaders, team ORtg/DRtg map, net rating ranking.
- **Players**: sortable, filterable table with Basic / Shooting / Advanced / Per 36 / Per 100 / Impact / Totals / Shot zones / Clutch / Hustle views.
- **Player page**: bio and draft info, shot profile by zone, clutch and hustle numbers, game log chart with rolling average, percentile profile, career arc of any stat, most similar player-seasons, season-by-season, playoffs, pre-1996 history, full game log.
- **Teams, team page**: ratings, four factors, season flow, franchise rating history, roster, on/off.
- **Standings** with Pythagorean wins and luck.
- **Leaders**: any stat, one season or all seasons.
- **Records**: single-game highs, triple-doubles, best teams, team game records, all-time career leaders.
- **Trends**: where shots come from (rim, paint, mid-range, corner 3, above-the-break 3) and how pace, 3-point volume, efficiency and more changed since 1979-80.
- **Predictions**: standings forecast with 80% win ranges and win distributions, playoff/seed/title odds, player projections (with rookies), MVP and DPOY odds, breakouts and regression candidates, and the backtest.
- **Analysis**
  - *Impact & WAR*: offense vs defense impact map, what the box model learned, full leaderboard.
  - *Shot quality*: shot difficulty vs shot-making, usage vs efficiency.
  - *Signal vs noise*: year-to-year reliability of each stat, most consistent players.
  - *Power ratings*: Elo through the season, SRS and schedule strength, calibration chart, best teams ever by Elo.
  - *Game predictor*: win probability, spread and best-of-7 odds for any two teams.
  - *Lineups*: every five-man unit, minutes vs net rating.
  - *Rest and home court*: back-to-backs, rest advantage, home-court decline.
  - *Compare*: up to four player-seasons, radar of percentiles plus a stat table.
  - *Stat explorer*: any stat vs any stat for players or teams, with correlation.
  - *Archetypes*: k-means clustering on eleven style features, PCA map, auto-named clusters.
  - *Projections*: Marcel-style next-season forecasts (5/4/3 weights, regression to the mean, age adjustment).
  - *Aging curves*: delta-method curves for any rate stat.
  - *What wins*: four-factor regression on win% (R² 0.93 over 892 team-seasons) and a Pythagorean luck chart.
  - *Draft value*: average career win shares by pick, biggest steals, every draft class.
  - *Origins*: international share of minutes, height over time, countries and colleges.

## Layout

```
nbastats/            data pipeline (python -m nbastats)
  sources/nbacom.py  NBA.com via nba_api, cached
  sources/bref.py    Basketball Reference scraper, cached and rate limited
  metrics.py         derived metrics (PER, per-100, Game Score, Pythagorean, league averages)
  build.py           fetch -> store raw -> build derived tables
app/                 Flask API + static single-page site (python -m app)
  server.py          JSON endpoints under /api
  analysis.py        clustering, similarity, projections, aging curves, win model
  catalog.py         stat labels and formats
  static/            index.html, app.js, style.css (Chart.js from a CDN)
data/                database and raw cache (not committed)
```

Please respect the sources: keep the built-in delays, and don't hammer Basketball Reference.
