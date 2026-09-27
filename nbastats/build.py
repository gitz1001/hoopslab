"""Pipeline orchestration: fetch -> store raw -> compute derived tables."""
import time

import pandas as pd

from . import db, metrics
from .config import season_range
from .sources import bref, nbacom

RS, PO = "Regular Season", "Playoffs"


def _lower(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    return df


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def update_reference(con):
    log("reference: teams, players, franchise history, all-time leaders")
    db.replace_rows(con, "teams", nbacom.teams())
    db.replace_rows(con, "players", nbacom.all_players())
    db.replace_rows(con, "franchise_history", _lower(nbacom.franchise_history()))
    db.replace_rows(con, "alltime_leaders", nbacom.alltime_leaders(50))


def update_season(con, season: str, use_bref=True, use_onoff=True, playoffs=True):
    where = {"season": season}
    log(f"{season}: NBA.com season tables")
    base = _lower(nbacom.player_season(season, "Base"))
    adv = _lower(nbacom.player_season(season, "Advanced"))
    t_base = _lower(nbacom.team_season(season, "Base"))
    t_adv = _lower(nbacom.team_season(season, "Advanced"))
    t_ff = _lower(nbacom.team_season(season, "Four Factors"))
    t_opp = _lower(nbacom.team_season(season, "Opponent"))
    stand = _lower(nbacom.standings(season))

    log(f"{season}: game logs")
    pg = _lower(nbacom.game_logs(season, "P"))
    tg = _lower(nbacom.game_logs(season, "T"))
    if playoffs:
        pg = pd.concat([pg, _lower(nbacom.game_logs(season, "P", PO))], ignore_index=True)
        tg = pd.concat([tg, _lower(nbacom.game_logs(season, "T", PO))], ignore_index=True)
        base_po = _lower(nbacom.player_season(season, "Base", PO))
    pg["game_score"] = metrics.game_score(pg).round(1)
    tg["game_score"] = None

    onoff = pd.DataFrame()
    if use_onoff:
        log(f"{season}: on/off for {len(t_base)} teams")
        frames = [nbacom.onoff(season, int(tid)) for tid in t_base["team_id"]]
        onoff = _lower(pd.concat([f for f in frames if not f.empty], ignore_index=True))

    b_adv = pd.DataFrame()
    if use_bref:
        log(f"{season}: Basketball Reference advanced")
        b_adv = bref.advanced(season)
        if not b_adv.empty:
            b_adv = bref.match_ids(b_adv, base.rename(columns={"player_name": "name"}))
            unmatched = b_adv["player_id"].isna().sum()
            if unmatched:
                log(f"  {unmatched} Basketball Reference rows had no NBA.com match")

    log(f"{season}: storing raw tables")
    db.replace_rows(con, "player_season_base", base, where)
    db.replace_rows(con, "player_season_adv", adv, where)
    if playoffs:
        db.replace_rows(con, "player_season_playoffs", base_po, where)
    db.replace_rows(con, "team_season_base", t_base, where)
    db.replace_rows(con, "team_season_adv", t_adv, where)
    db.replace_rows(con, "standings", stand, where)
    db.replace_rows(con, "player_game", pg, where)
    db.replace_rows(con, "team_game", tg.drop(columns=["game_score"]), where)
    if not onoff.empty:
        db.replace_rows(con, "player_onoff", onoff, where)
    if not b_adv.empty:
        db.replace_rows(con, "bref_advanced", b_adv, where)

    log(f"{season}: computing derived tables")
    rs_games = pg[pg.season_type == RS]
    full = metrics.build_player_season(base, adv, t_base, t_adv, b_adv, onoff, rs_games)
    tfull = metrics.build_team_season(t_base, t_adv, t_ff, stand, t_opp)
    abbr = nbacom.teams().set_index("team_id")["abbr"]
    tfull.insert(3, "team_abbr", tfull["team_id"].map(abbr))
    league = metrics.build_league_season(tfull, season)
    db.replace_rows(con, "player_season_full", full, where)
    db.replace_rows(con, "team_season_full", tfull, where)
    db.replace_rows(con, "league_season", league, where)
    log(f"{season}: done ({len(full)} players, {len(pg)} player-games)")


def update_history(con, seasons: list[str]):
    """Pre-1996 seasons from Basketball Reference only (totals + advanced)."""
    for s in seasons:
        log(f"{s}: Basketball Reference history")
        tot, adv = bref.totals(s), bref.advanced(s)
        if tot.empty:
            continue
        keep = [c for c in adv.columns if c not in tot.columns or c == "bref_id"]
        df = tot.merge(adv[keep], on="bref_id", how="left")
        db.replace_rows(con, "bref_history", df, {"season": s})


def run(seasons, use_bref=True, use_onoff=True, playoffs=True, reference=True):
    con = db.connect()
    if reference:
        update_reference(con)
    for s in seasons:
        update_season(con, s, use_bref, use_onoff, playoffs)
    db.ensure_indexes(con)
    con.close()


def seasons_from_args(first, last):
    return season_range(first, last)
