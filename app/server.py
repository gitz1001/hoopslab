"""Flask JSON API + static single-page site.  Run: python -m app  (http://127.0.0.1:8050)"""
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, jsonify, request, send_from_directory

from nbastats import db

from . import analysis
from .catalog import LEAGUE_STATS, PLAYER_STATS, TEAM_STATS, as_json

STATIC = Path(__file__).parent / "static"
app = Flask(__name__, static_folder=None)


def q(sql, params=()):
    con = db.connect()
    try:
        return db.read_sql(con, sql, params)
    finally:
        con.close()


def clean(obj):
    """Make pandas/numpy output JSON safe (NaN -> null)."""
    if isinstance(obj, pd.DataFrame):
        obj = obj.replace({np.nan: None}).to_dict("records")
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if math.isnan(obj) or math.isinf(obj) else float(obj)
    return obj


def ok(obj):
    return jsonify(clean(obj))


def arg_season():
    s = request.args.get("season")
    if s:
        return s
    return q("SELECT max(season) s FROM player_season_full")["s"].iloc[0]


def safe_col(col, allowed):
    if col not in allowed:
        raise ValueError(f"unknown stat {col}")
    return col


PLAYER_COLS = list(PLAYER_STATS)
ID_COLS = ["player_id", "player_name", "season", "team_id", "team_abbreviation", "age", "pos"]
EXTRA_COLS = ["college", "country", "draft_year"]


def player_cols():
    """Catalog columns that exist in the table (an older database may lack newer sources)."""
    con = db.connect()
    have = {r[1] for r in con.execute("PRAGMA table_info(player_season_full)")}
    con.close()
    return [c for c in ID_COLS + EXTRA_COLS + PLAYER_COLS if c in have]


# ------------------------------------------------------------------ meta

@app.get("/api/meta")
def meta():
    seasons = q("SELECT DISTINCT season FROM player_season_full ORDER BY season DESC")["season"]
    hist = []
    con = db.connect()
    if db.table_exists(con, "bref_history"):
        hist = db.read_sql(con, "SELECT DISTINCT season FROM bref_history ORDER BY season DESC")["season"].tolist()
    con.close()
    return ok({"seasons": seasons.tolist(), "history_seasons": hist,
               "player_stats": as_json(PLAYER_STATS), "team_stats": as_json(TEAM_STATS),
               "league_stats": {k: {"label": v[0], "fmt": v[1]} for k, v in LEAGUE_STATS.items()}})


@app.get("/api/search")
def search():
    term = f"%{request.args.get('q', '').strip()}%"
    df = q("""SELECT p.player_id, p.name, p.from_year, p.to_year,
                     (SELECT count(*) FROM player_season_full f WHERE f.player_id=p.player_id) n
              FROM players p WHERE p.name LIKE ? ORDER BY n DESC, p.to_year DESC LIMIT 12""",
           (term,))
    teams = q("SELECT team_id, abbr, name FROM teams WHERE name LIKE ? OR abbr LIKE ? LIMIT 5",
              (term, term))
    return ok({"players": df, "teams": teams})


# ------------------------------------------------------------------ dashboard

@app.get("/api/dashboard")
def dashboard():
    season = arg_season()
    lg = q("SELECT * FROM league_season WHERE season=?", (season,))
    prev = q("SELECT * FROM league_season WHERE season < ? ORDER BY season DESC LIMIT 1", (season,))
    leaders = {}
    for stat, min_gp in [("pts_pg", 58), ("reb_pg", 58), ("ast_pg", 58), ("per", 0),
                         ("bpm", 0), ("ws", 0), ("ts_pct", 0), ("net_diff", 0)]:
        where = "gp >= ?" if min_gp else "min >= ?"
        val = min_gp if min_gp else 1500
        leaders[stat] = q(f"""SELECT player_id, player_name, team_abbreviation, {stat} v
                               FROM player_season_full WHERE season=? AND {where}
                               AND {stat} IS NOT NULL ORDER BY {stat} DESC LIMIT 5""",
                          (season, val))
    teams = q("""SELECT team_id, team_abbr, team_name, w, l, off_rating, def_rating, net_rating,
                        pace, conference FROM team_season_full WHERE season=?""", (season,))
    return ok({"season": season, "league": lg, "prev": prev, "leaders": leaders, "teams": teams})


# ------------------------------------------------------------------ players

@app.get("/api/players")
def players():
    season = arg_season()
    min_gp = int(request.args.get("min_gp", 0))
    min_min = int(request.args.get("min_min", 0))
    df = q(f"""SELECT {', '.join(player_cols())} FROM player_season_full
               WHERE season=? AND gp >= ? AND min >= ?""", (season, min_gp, min_min))
    return ok({"season": season, "rows": df})


@app.get("/api/player/<int:pid>")
def player(pid):
    bio = q("SELECT * FROM players WHERE player_id=?", (pid,))
    seasons = q("SELECT * FROM player_season_full WHERE player_id=? ORDER BY season", (pid,))
    po = q("""SELECT season, team_abbreviation, gp, min, pts, reb, ast, stl, blk, fgm, fga,
                     fg3m, fg3a, ftm, fta, plus_minus
              FROM player_season_playoffs WHERE player_id=? ORDER BY season""", (pid,))
    if seasons.empty and bio.empty:
        return ok({"error": "not found"}), 404
    season = request.args.get("season") or (seasons.season.iloc[-1] if len(seasons) else None)
    games = q("""SELECT game_id, game_date, matchup, wl, season_type, min, pts, reb, ast, stl,
                        blk, tov, fgm, fga, fg3m, fg3a, ftm, fta, plus_minus, game_score
                 FROM player_game WHERE player_id=? AND season=? ORDER BY game_date""",
              (pid, season))
    career = None
    if len(seasons):
        tot = seasons[["gp", "min", "pts", "reb", "ast", "stl", "blk", "tov", "fgm", "fga",
                       "fg3m", "fg3a", "ftm", "fta"]].sum()
        career = {k: float(v) for k, v in tot.items()}
        for k in ["pts", "reb", "ast", "stl", "blk"]:
            career[f"{k}_pg"] = career[k] / career["gp"] if career["gp"] else None
        career["ts_pct"] = career["pts"] / (2 * (career["fga"] + 0.44 * career["fta"]))
        for k in ["ws", "vorp"]:
            career[k] = float(seasons[k].sum()) if k in seasons else None
    bref_hist = pd.DataFrame()
    con = db.connect()
    if db.table_exists(con, "bref_history"):
        cols = """season, teams, age, pos, games g, mp, pts, trb, ast, stl, blk, fg, fga,
                  fg3, fg3a, ft, fta, per, ws, bpm, vorp"""
        if "bref_id" in seasons and seasons["bref_id"].notna().any():
            bref_hist = db.read_sql(con, f"SELECT {cols} FROM bref_history WHERE bref_id=? ORDER BY season",
                                    (seasons["bref_id"].dropna().iloc[0],))
        elif len(bio):
            # retired before 1996-97: match Basketball Reference history by name
            bref_hist = db.read_sql(con, f"SELECT {cols} FROM bref_history WHERE bref_name=? ORDER BY season",
                                    (bio["name"].iloc[0],))
    con.close()
    pct = analysis.percentiles(pid, season) if season else None
    sim = analysis.similar(pid, season) if season else []
    return ok({"bio": bio, "seasons": seasons, "playoffs": po, "season": season,
               "games": games, "career": career, "percentiles": pct, "radar": analysis.RADAR,
               "similar": sim, "history": bref_hist})


# ------------------------------------------------------------------ teams

@app.get("/api/teams")
def teams():
    season = arg_season()
    df = q("SELECT * FROM team_season_full WHERE season=? ORDER BY w_pct DESC", (season,))
    return ok({"season": season, "rows": df})


@app.get("/api/team/<int:tid>")
def team(tid):
    season = arg_season()
    info = q("SELECT * FROM teams WHERE team_id=?", (tid,))
    row = q("SELECT * FROM team_season_full WHERE team_id=? AND season=?", (tid, season))
    history = q("""SELECT season, w, l, w_pct, off_rating, def_rating, net_rating, pace,
                          playoffrank, conference FROM team_season_full WHERE team_id=?
                   ORDER BY season""", (tid,))
    roster = q(f"""SELECT {', '.join(player_cols())} FROM player_season_full
                   WHERE season=? AND team_id=? ORDER BY min DESC""", (season, tid))
    onoff = q("""SELECT o.*, p.name player_name FROM player_onoff o
                 LEFT JOIN players p ON p.player_id=o.player_id
                 WHERE o.season=? AND o.team_id=? ORDER BY o.on_min DESC""", (season, tid))
    games = q("""SELECT game_id, game_date, matchup, wl, season_type, pts, plus_minus, fgm, fga,
                        fg3m, fg3a, reb, ast, tov FROM team_game WHERE team_id=? AND season=?
                 ORDER BY game_date""", (tid, season))
    franchise = q("SELECT * FROM franchise_history WHERE team_id=?", (tid,))
    return ok({"season": season, "info": info, "row": row, "history": history,
               "roster": roster, "onoff": onoff, "games": games, "franchise": franchise})


@app.get("/api/standings")
def standings():
    season = arg_season()
    df = q("""SELECT team_id, team_abbr, team_name, conference, division, playoffrank, w, l,
                     w_pct, conferencegamesback, home, road, l10, strcurrentstreak,
                     pts_pg, opp_pts_pg, net_rating, pyth_w, luck, clinchindicator
              FROM team_season_full WHERE season=? ORDER BY conference, playoffrank""",
           (season,))
    return ok({"season": season, "rows": df})


# ------------------------------------------------------------------ leaders & records

@app.get("/api/leaders")
def leaders():
    season = request.args.get("season", "all")
    stat = safe_col(request.args.get("stat", "pts_pg"), PLAYER_STATS)
    min_gp = int(request.args.get("min_gp", 0))
    min_min = int(request.args.get("min_min", 0))
    limit = min(int(request.args.get("limit", 25)), 200)
    better = PLAYER_STATS[stat][3]
    order = "ASC" if better is False else "DESC"
    where, params = ["gp >= ?", "min >= ?", f"{stat} IS NOT NULL"], [min_gp, min_min]
    if season != "all":
        where.append("season = ?")
        params.append(season)
    df = q(f"""SELECT player_id, player_name, season, team_abbreviation, gp, min, {stat} v
               FROM player_season_full WHERE {' AND '.join(where)}
               ORDER BY {stat} {order} LIMIT ?""", (*params, limit))
    return ok({"stat": stat, "rows": df})


GAME_RECORD_STATS = {"pts": "Points", "reb": "Rebounds", "ast": "Assists", "stl": "Steals",
                     "blk": "Blocks", "fg3m": "3-pointers", "ftm": "Free throws",
                     "game_score": "Game Score", "plus_minus": "Plus/minus"}


@app.get("/api/records")
def records():
    return ok(_records(request.args.get("season", "all"),
                       request.args.get("season_type", "Regular Season")))


@lru_cache(maxsize=64)
def _records(season, season_type):
    filt, params = "season_type = ?", [season_type]
    if season != "all":
        filt += " AND season = ?"
        params.append(season)
    single = {}
    for stat, label in GAME_RECORD_STATS.items():
        single[stat] = q(f"""SELECT player_id, player_name, team_abbreviation, game_date,
                                    matchup, wl, {stat} v, pts, reb, ast
                             FROM player_game WHERE {filt} AND {stat} IS NOT NULL
                             ORDER BY {stat} DESC LIMIT 10""", params)
    triple = q(f"""SELECT player_id, player_name, count(*) n FROM player_game WHERE {filt}
                   AND ((pts>=10)+(reb>=10)+(ast>=10)+(stl>=10)+(blk>=10)) >= 3
                   GROUP BY player_id ORDER BY n DESC LIMIT 15""", params)
    team_games = {
        "Most points": q(f"""SELECT team_abbreviation, game_date, matchup, wl, pts v
                             FROM team_game WHERE {filt} ORDER BY pts DESC LIMIT 10""", params),
        "Biggest wins": q(f"""SELECT team_abbreviation, game_date, matchup, wl, plus_minus v
                              FROM team_game WHERE {filt} ORDER BY plus_minus DESC LIMIT 10""",
                          params),
        "Most 3-pointers": q(f"""SELECT team_abbreviation, game_date, matchup, wl, fg3m v
                                 FROM team_game WHERE {filt} ORDER BY fg3m DESC LIMIT 10""",
                             params),
    }
    best_teams = q("""SELECT season, team_abbr, w, l, net_rating, off_rating, def_rating
                      FROM team_season_full ORDER BY net_rating DESC LIMIT 10""")
    alltime = q("SELECT * FROM alltime_leaders ORDER BY stat, rank")
    return clean({"single_game": single, "labels": GAME_RECORD_STATS, "triple_doubles": triple,
                  "team_games": team_games, "best_teams": best_teams, "alltime": alltime})


@app.get("/api/league")
def league():
    df = q("SELECT * FROM league_season ORDER BY season")
    con = db.connect()
    hist = pd.DataFrame()
    if db.table_exists(con, "bref_history"):
        # league-wide shooting trends before NBA.com coverage, from Basketball Reference totals
        hist = db.read_sql(con, """SELECT season, sum(pts) pts, sum(fga) fga, sum(fta) fta,
                                          sum(fg3a) fg3a, sum(fg3) fg3m, sum(fg) fgm
                                   FROM bref_history GROUP BY season ORDER BY season""")
        if not hist.empty:
            hist["ts_pct"] = hist.pts / (2 * (hist.fga + 0.44 * hist.fta))
            hist["efg_pct"] = (hist.fgm + 0.5 * hist.fg3m) / hist.fga
            hist["fg3a_rate"] = hist.fg3a / hist.fga
            hist["fg3_pct"] = hist.fg3m / hist.fg3a
            hist["fta_rate"] = hist.fta / hist.fga
    zones = pd.DataFrame()
    have = {r[1] for r in con.execute("PRAGMA table_info(player_season_full)")}
    if "ra_fga" in have:
        zones = db.read_sql(con, """SELECT season, sum(ra_fga) ra, sum(paint_fga) paint,
                                    sum(mid_fga) mid, sum(c3_fga) c3, sum(atb3_fga) atb3,
                                    sum(ra_fgm) ra_m, sum(paint_fgm) paint_m, sum(mid_fgm) mid_m,
                                    sum(c3_fgm) c3_m, sum(atb3_fgm) atb3_m
                                    FROM player_season_full GROUP BY season ORDER BY season""")
        zones = zones[zones.ra.notna() & (zones.ra > 0)]
    con.close()
    return ok({"rows": df, "history": hist, "zones": zones})


# ------------------------------------------------------------------ analysis

@app.get("/api/analysis/clusters")
def a_clusters():
    return ok(analysis.clusters(arg_season(), int(request.args.get("k", 8))))


@app.get("/api/analysis/projections")
def a_projections():
    return ok({"base_season": arg_season(), "rows": analysis.projections(arg_season())})


@app.get("/api/analysis/compare")
def a_compare():
    pairs = []
    for item in request.args.get("ids", "").split(","):
        if ":" in item:
            pid, season = item.split(":")
            pairs.append((int(pid), season))
    return ok(analysis.compare(pairs))


@app.get("/api/analysis/aging")
def a_aging():
    stat = safe_col(request.args.get("stat", "bpm"), PLAYER_STATS)
    return ok({"stat": stat, "rows": analysis.aging_curve(stat)})


@app.get("/api/analysis/scatter")
def a_scatter():
    kind = request.args.get("kind", "players")
    season = arg_season()
    catalog = PLAYER_STATS if kind == "players" else TEAM_STATS
    x = safe_col(request.args.get("x"), catalog)
    y = safe_col(request.args.get("y"), catalog)
    if kind == "players":
        min_min = int(request.args.get("min_min", 1000))
        df = q(f"""SELECT player_id id, player_name name, team_abbreviation team, {x} x, {y} y
                   FROM player_season_full WHERE season=? AND min>=? AND {x} IS NOT NULL
                   AND {y} IS NOT NULL""", (season, min_min))
    else:
        df = q(f"""SELECT team_id id, team_abbr name, team_abbr team, {x} x, {y} y
                   FROM team_season_full WHERE season=?""", (season,))
    r = float(df.x.corr(df.y)) if len(df) > 2 else None
    return ok({"rows": df, "r": r})


@app.get("/api/analysis/winmodel")
def a_winmodel():
    return ok(analysis.win_model())


@app.get("/api/analysis/team_trend")
def a_team_trend():
    stat = safe_col(request.args.get("stat", "net_rating"), TEAM_STATS)
    df = q(f"SELECT season, team_abbr, {stat} v FROM team_season_full ORDER BY season")
    return ok({"stat": stat, "rows": df})


@app.get("/api/analysis/draft")
def a_draft():
    """Career value by draft slot, for drafts whose careers fall inside the stored seasons."""
    con = db.connect()
    if not db.table_exists(con, "draft_history"):
        con.close()
        return ok({"picks": [], "classes": [], "steals": [], "draft": []})
    last = int(db.read_sql(con, "SELECT max(season) s FROM player_season_full")["s"].iloc[0][:4])
    first = int(db.read_sql(con, "SELECT min(season) s FROM player_season_full")["s"].iloc[0][:4])
    df = db.read_sql(con, """
        SELECT d.person_id player_id, d.player_name, CAST(d.season AS INTEGER) draft_year,
               d.overall_pick, d.team_abbreviation, d.organization,
               count(f.season) seasons, coalesce(sum(f.min), 0) minutes,
               coalesce(sum(f.ws), 0) ws, coalesce(sum(f.vorp), 0) vorp, max(f.bpm) best_bpm
        FROM draft_history d LEFT JOIN player_season_full f ON f.player_id = d.person_id
        WHERE CAST(d.season AS INTEGER) BETWEEN ? AND ? AND d.overall_pick > 0
        GROUP BY d.person_id, d.season""", (first, last))
    con.close()
    mature_through = last - 6  # give careers at least six seasons to play out
    mature = df[df.draft_year <= mature_through]
    picks = mature.groupby("overall_pick").agg(
        n=("player_id", "size"), avg_ws=("ws", "mean"), median_ws=("ws", "median"),
        avg_seasons=("seasons", "mean"),
        hit_rate=("seasons", lambda s: float((s >= 5).mean()))).reset_index()
    best = (mature.sort_values("ws", ascending=False).groupby("overall_pick").head(1)
            [["overall_pick", "player_id", "player_name", "draft_year", "ws"]])
    picks = picks.merge(best.rename(columns={"player_id": "best_id", "player_name": "best_name",
                                             "draft_year": "best_year", "ws": "best_ws"}),
                        on="overall_pick", how="left")
    picks = picks[picks.overall_pick <= 60]
    classes = (df.groupby("draft_year").agg(total_ws=("ws", "sum"), players=("player_id", "size"))
               .reset_index())
    steals = (df[df.overall_pick >= 15].sort_values("ws", ascending=False).head(15)
              [["player_id", "player_name", "draft_year", "overall_pick", "team_abbreviation", "ws"]])
    return ok({"picks": picks, "classes": classes, "steals": steals, "draft": df,
               "mature_through": mature_through})


@app.get("/api/analysis/origins")
def a_origins():
    season = arg_season()
    con = db.connect()
    have = {r[1] for r in con.execute("PRAGMA table_info(player_season_full)")}
    if "country" not in have:
        con.close()
        return ok({"trend": [], "countries": [], "colleges": []})
    trend = db.read_sql(con, """
        SELECT season,
               sum(CASE WHEN country IS NOT NULL AND country <> 'USA' THEN min ELSE 0 END) * 1.0
                 / sum(min) intl_min_share,
               count(DISTINCT CASE WHEN country <> 'USA' THEN player_id END) intl_players,
               count(DISTINCT country) countries,
               sum(height_in * min) / sum(CASE WHEN height_in IS NOT NULL THEN min END) avg_height,
               sum(age * min) / sum(min) avg_age
        FROM player_season_full GROUP BY season ORDER BY season""")
    countries = db.read_sql(con, """SELECT country, count(*) players, sum(min) minutes,
                                    group_concat(player_name, ', ') names
                                    FROM (SELECT * FROM player_season_full WHERE season=?
                                          ORDER BY min DESC)
                                    WHERE country IS NOT NULL GROUP BY country
                                    ORDER BY minutes DESC LIMIT 25""", (season,))
    colleges = db.read_sql(con, """SELECT college, count(*) players, sum(min) minutes,
                                   group_concat(player_name, ', ') names
                                   FROM (SELECT * FROM player_season_full WHERE season=?
                                         ORDER BY min DESC)
                                   WHERE college IS NOT NULL AND college NOT IN ('None', '')
                                   GROUP BY college ORDER BY minutes DESC LIMIT 25""", (season,))
    con.close()
    for d in (countries, colleges):
        d["names"] = d["names"].map(lambda s: ", ".join(str(s).split(", ")[:4]))
    return ok({"trend": trend, "countries": countries, "colleges": colleges})


# ------------------------------------------------------------------ static site

@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/<path:path>")
def static_files(path):
    return send_from_directory(STATIC, path)


@app.errorhandler(ValueError)
def bad_request(e):
    return jsonify({"error": str(e)}), 400
