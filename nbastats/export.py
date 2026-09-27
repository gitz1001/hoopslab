"""Write a slim copy of the database with only what the website reads.

The pipeline database keeps raw source tables (about 490 MB); the served copy drops them,
switches off WAL so it is a single self-contained file, rebuilds indexes and vacuums.
The website opens it read-only and immutable (NBA_READONLY=1).
"""
import sqlite3
import time
from pathlib import Path

from . import db
from .config import DB_PATH, SERVE_DB_PATH

SERVED_TABLES = [
    "players", "teams", "franchise_history", "alltime_leaders", "draft_history",
    "player_season_full", "team_season_full", "league_season", "player_season_playoffs",
    "player_game", "team_game", "player_onoff", "bref_history", "lineups", "box_model_coefs",
    "game_elo", "team_srs", "team_rest",
    "pred_players", "pred_teams", "pred_awards", "pred_meta", "pred_backtest", "pred_backtest_teams",
]

RECORD_STATS = ["pts", "reb", "ast", "stl", "blk", "fg3m", "ftm", "game_score", "plus_minus"]

# Raw lineup rows are only needed for the Lineups pages, which show units with 100+ minutes.
FILTERS = {"lineups": "min >= 50"}


def run(out: str | None = None):
    out = Path(out) if out else SERVE_DB_PATH
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(DB_PATH)
    src.execute(f"ATTACH DATABASE '{tmp.as_posix()}' AS dst")
    have = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in SERVED_TABLES:
        if t not in have:
            print(f"  skipping {t} (not built yet)")
            continue
        where = f" WHERE {FILTERS[t]}" if t in FILTERS else ""
        src.execute(f'CREATE TABLE dst."{t}" AS SELECT * FROM main."{t}"{where}')
    src.commit()
    src.execute("DETACH DATABASE dst")
    src.close()

    con = sqlite3.connect(tmp)
    con.execute("PRAGMA journal_mode=DELETE")
    # Records pages only ever need the top games per stat, so precompute the top 25 for every
    # season and game type instead of carrying ~180 MB of per-stat indexes.
    parts = [f"""SELECT * FROM (SELECT * FROM player_game WHERE season=? AND season_type=?
                  ORDER BY {c} DESC LIMIT 25)""" for c in RECORD_STATS]
    keys = con.execute("SELECT DISTINCT season, season_type FROM player_game").fetchall()
    con.execute("CREATE TABLE player_game_top AS SELECT * FROM player_game WHERE 0")
    for season, stype in keys:
        con.execute("INSERT INTO player_game_top SELECT DISTINCT * FROM (" + " UNION ".join(parts) + ")",
                    [v for _ in RECORD_STATS for v in (season, stype)])
    skip = {(t, c) for t, c in db.INDEXES if t == "player_game" and c.startswith("season_type,")}
    db.ensure_indexes(con, skip=skip)
    con.execute("ANALYZE")
    con.commit()
    con.execute("VACUUM")
    con.close()
    out.unlink(missing_ok=True)
    tmp.rename(out)
    mb = out.stat().st_size / 1e6
    print(f"[{time.strftime('%H:%M:%S')}] wrote {out} ({mb:.0f} MB, {len(SERVED_TABLES)} tables)")
