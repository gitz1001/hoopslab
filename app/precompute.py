"""Precompute the heavy analysis results into a `precomputed` table of the served database.

Called by `python -m nbastats export`. The website then answers records, similar players,
clusters, projections, aging curves and model summaries with a single indexed read, which
keeps a tiny host (Render free: 0.1 CPU, 512 MB) fast and within memory.
"""
import json
import sqlite3
import time

from . import analysis
from .catalog import PLAYER_STATS
from .server import (_draft, _impact_corr, _records, _situational, _stickiness, clean, q)

# counting stats have no meaningful aging curve (the UI hides them too)
NO_AGING = {"gp", "min", "dd2", "td3", "pts", "reb", "ast", "stl", "blk", "fg3m", "ftm"}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] precompute: {msg}", flush=True)


def build(out_path):
    seasons = q("SELECT DISTINCT season FROM player_season_full ORDER BY season")["season"].tolist()
    rows = []

    def put(key, value):
        rows.append((key, json.dumps(clean(value), separators=(",", ":"))))

    log("records")
    for s in ["all"] + seasons:
        for stype in ("Regular Season", "Playoffs"):
            put(f"records:{s}:{stype}", _records(s, stype))
    log("model summaries")
    put("stickiness:1000", _stickiness(1000))
    put("winmodel", analysis.win_model())
    put("draft", _draft())
    put("situational", _situational())
    put("impact_corr", _impact_corr())
    log("aging curves")
    for stat in PLAYER_STATS:
        if stat not in NO_AGING:
            put(f"aging:{stat}", analysis.aging_curve(stat))
    log("clusters and projections per season")
    for s in seasons:
        put(f"clusters:{s}:8", analysis.clusters(s, 8))
        put(f"projections:{s}", analysis.projections(s))
    log("similar players")
    for (pid, s), sim in analysis.similar_all().items():
        put(f"similar:{pid}:{s}", sim)

    con = sqlite3.connect(out_path)
    con.execute("DROP TABLE IF EXISTS precomputed")
    con.execute("CREATE TABLE precomputed (key TEXT PRIMARY KEY, json TEXT NOT NULL)")
    con.executemany("INSERT INTO precomputed VALUES (?, ?)", rows)
    con.commit()
    con.close()
    log(f"{len(rows):,} results stored")
