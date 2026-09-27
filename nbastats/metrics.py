"""Derived metrics. Everything here is computed from the raw tables so it can be rebuilt
for any season without new network calls."""
import numpy as np
import pandas as pd


def _div(a, b):
    a = pd.to_numeric(a, errors="coerce")
    b = pd.to_numeric(b, errors="coerce")
    return np.where((b == 0) | b.isna(), np.nan, a / b.replace(0, np.nan))


def game_score(df: pd.DataFrame) -> pd.Series:
    """John Hollinger's Game Score for a box score line."""
    return (df.pts + 0.4 * df.fgm - 0.7 * df.fga - 0.4 * (df.fta - df.ftm) + 0.7 * df.oreb
            + 0.3 * df.dreb + df.stl + 0.7 * df.ast + 0.7 * df.blk - 0.4 * df.pf - df.tov)


def league_totals(team_base: pd.DataFrame) -> dict:
    cols = ["fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb", "ast", "tov",
            "stl", "blk", "pf", "pts", "gp", "min"]
    return {c: float(team_base[c].sum()) for c in cols}


def hollinger_per(p: pd.DataFrame, team_base: pd.DataFrame, team_adv: pd.DataFrame) -> pd.Series:
    """Player Efficiency Rating computed from scratch (Hollinger's formula), scaled so the
    minutes-weighted league average is 15. p needs totals plus team_id."""
    lg = league_totals(team_base)
    factor = (2 / 3) - (0.5 * (lg["ast"] / lg["fgm"])) / (2 * (lg["fgm"] / lg["ftm"]))
    vop = lg["pts"] / (lg["fga"] - lg["oreb"] + lg["tov"] + 0.44 * lg["fta"])
    drbp = (lg["reb"] - lg["oreb"]) / lg["reb"]
    lg_pace = float(np.average(team_adv["pace"], weights=team_adv["min"]))

    tm = team_base.set_index("team_id")
    tm_ast_fg = p["team_id"].map(tm["ast"] / tm["fgm"])
    tm_pace = p["team_id"].map(team_adv.set_index("team_id")["pace"]).fillna(lg_pace)

    uper = (1 / p["min"].replace(0, np.nan)) * (
        p.fg3m
        + (2 / 3) * p.ast
        + (2 - factor * tm_ast_fg) * p.fgm
        + p.ftm * 0.5 * (1 + (1 - tm_ast_fg) + (2 / 3) * tm_ast_fg)
        - vop * p.tov
        - vop * drbp * (p.fga - p.fgm)
        - vop * 0.44 * (0.44 + 0.56 * drbp) * (p.fta - p.ftm)
        + vop * (1 - drbp) * (p.reb - p.oreb)
        + vop * drbp * p.oreb
        + vop * p.stl
        + vop * drbp * p.blk
        - p.pf * ((lg["ftm"] / lg["pf"]) - 0.44 * (lg["fta"] / lg["pf"]) * vop)
    )
    aper = (lg_pace / tm_pace) * uper
    ok = aper.notna() & (p["min"] > 0)
    lg_aper = np.average(aper[ok], weights=p.loc[ok, "min"])
    return aper * (15 / lg_aper)


RATE_STATS = ["pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "fgm", "fga", "fg3m",
              "fg3a", "ftm", "fta", "pf"]


def build_player_season(base, adv, team_base, team_adv, bref_adv, onoff, games) -> pd.DataFrame:
    """One wide row per player-season: box totals, per game, per 36, per 100 possessions,
    shooting, NBA.com advanced, Basketball Reference advanced, on/off and our own PER."""
    df = base.copy()
    df["gp"] = df["gp"].astype(float)
    for c in RATE_STATS:
        df[f"{c}_pg"] = _div(df[c], df["gp"])
        df[f"{c}_p36"] = _div(df[c] * 36, df["min"])
    df["min_pg"] = _div(df["min"], df["gp"])

    # Shooting
    df["efg_pct"] = _div(df.fgm + 0.5 * df.fg3m, df.fga)
    df["ts_pct"] = _div(df.pts, 2 * (df.fga + 0.44 * df.fta))
    df["fg3a_rate"] = _div(df.fg3a, df.fga)
    df["fta_rate"] = _div(df.fta, df.fga)
    df["fg2m"] = df.fgm - df.fg3m
    df["fg2a"] = df.fga - df.fg3a
    df["fg2_pct"] = _div(df.fg2m, df.fg2a)
    df["ast_tov"] = _div(df.ast, df.tov)

    a = adv[["player_id", "off_rating", "def_rating", "net_rating", "ast_pct", "ast_ratio",
             "oreb_pct", "dreb_pct", "reb_pct", "tm_tov_pct", "usg_pct", "pace", "pie", "poss",
             "e_off_rating", "e_def_rating", "e_net_rating"]]
    df = df.merge(a, on="player_id", how="left")
    for c in RATE_STATS:
        df[f"{c}_p100"] = _div(df[c] * 100, df["poss"])

    df["per_calc"] = hollinger_per(df, team_base, team_adv)

    if bref_adv is not None and not bref_adv.empty:
        b = bref_adv.dropna(subset=["player_id"]).drop_duplicates("player_id")
        b = b[["player_id", "bref_id", "pos", "games_started", "per", "stl_pct", "blk_pct",
               "ows", "dws", "ws", "ws_per_48", "obpm", "dbpm", "bpm", "vorp", "awards", "teams"]]
        df = df.merge(b.astype({"player_id": int}), on="player_id", how="left")

    if onoff is not None and not onoff.empty:
        # a traded player has one row per team: keep the one with the most on-court minutes
        o = (onoff.sort_values("on_min", ascending=False).drop_duplicates("player_id")
             [["player_id", "on_min", "on_net_rating", "off_net_rating", "net_diff",
               "ortg_diff", "drtg_diff"]])
        df = df.merge(o, on="player_id", how="left")

    if games is not None and not games.empty:
        g = games.groupby("player_id").agg(
            gmsc_avg=("game_score", "mean"), gmsc_max=("game_score", "max"),
            pts_max=("pts", "max"), reb_max=("reb", "max"), ast_max=("ast", "max"),
            games_40pt=("pts", lambda s: int((s >= 40).sum())),
        ).reset_index()
        df = df.merge(g, on="player_id", how="left")
    return df


ZONES = ["ra", "paint", "mid", "c3", "atb3"]


def add_extras(df, bio=None, shots=None, clutch=None, hustle=None) -> pd.DataFrame:
    """Bio/draft info, shot-zone profile, clutch and hustle numbers for each player-season."""
    if bio is not None and not bio.empty:
        b = bio.drop(columns=["season"]).drop_duplicates("player_id").copy()
        b = b.rename(columns={"player_height_inches": "height_in", "player_weight": "weight"})
        for c in ["weight", "draft_year", "draft_round", "draft_number"]:
            b[c] = pd.to_numeric(b[c], errors="coerce")
        df = df.merge(b[["player_id", "height_in", "weight", "college", "country", "draft_year",
                         "draft_round", "draft_number"]], on="player_id", how="left")
    if shots is not None and not shots.empty:
        z = shots.drop(columns=["season"]).drop_duplicates("player_id").copy()
        z = z.apply(lambda col: pd.to_numeric(col, errors="coerce"))
        tot = sum(z[f"{k}_fga"].fillna(0) for k in ZONES)
        out = pd.DataFrame({"player_id": z["player_id"]})
        for k in ZONES:
            out[f"{k}_fga"] = z[f"{k}_fga"]
            out[f"{k}_fgm"] = z[f"{k}_fgm"]
            out[f"{k}_share"] = _div(z[f"{k}_fga"], tot)
            out[f"{k}_fg_pct"] = _div(z[f"{k}_fgm"], z[f"{k}_fga"])
        df = df.merge(out, on="player_id", how="left")
    if clutch is not None and not clutch.empty:
        c = clutch.drop(columns=["season"]).drop_duplicates("player_id")
        c = c.assign(clutch_ts_pct=_div(c.clutch_pts, 2 * (c.clutch_fga + 0.44 * c.clutch_fta)),
                     clutch_pts_p36=_div(c.clutch_pts * 36, c.clutch_min))
        df = df.merge(c[["player_id", "clutch_gp", "clutch_min", "clutch_pts", "clutch_fga",
                         "clutch_ts_pct", "clutch_pts_p36", "clutch_plus_minus"]],
                      on="player_id", how="left")
    if hustle is not None and not hustle.empty:
        hdf = hustle.drop(columns=["season"]).drop_duplicates("player_id")
        df = df.merge(hdf, on="player_id", how="left")
        for c in ["contested_shots", "deflections", "screen_assists", "loose_balls_recovered",
                  "box_outs"]:
            df[f"{c}_p36"] = _div(df[c] * 36, df["min"])
    return df


def build_team_season(team_base, team_adv, team_ff, standings, team_opp=None) -> pd.DataFrame:
    df = team_base.copy()
    for c in ["pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fg3a", "fga", "fta"]:
        df[f"{c}_pg"] = _div(df[c], df["gp"])
    df["ts_pct"] = _div(df.pts, 2 * (df.fga + 0.44 * df.fta))
    df["fg3a_rate"] = _div(df.fg3a, df.fga)
    a = team_adv[["team_id", "off_rating", "def_rating", "net_rating", "pace", "poss",
                  "ast_pct", "oreb_pct", "dreb_pct", "tm_tov_pct", "efg_pct", "pie"]]
    df = df.merge(a, on="team_id", how="left")
    if team_ff is not None and not team_ff.empty:
        ff = team_ff[["team_id", "fta_rate", "opp_efg_pct", "opp_fta_rate", "opp_tov_pct",
                      "opp_oreb_pct"]]
        df = df.merge(ff, on="team_id", how="left")
    if standings is not None and not standings.empty:
        s = standings.drop(columns=["season"]).rename(columns=str.lower)
        s = s.rename(columns={"wins": "st_wins", "losses": "st_losses"})
        df = df.merge(s, on="team_id", how="left")
    # Pythagorean expectation (Morey's exponent 13.91 for the NBA)
    if team_opp is not None and not team_opp.empty:
        opp = team_opp.set_index("team_id")["opp_pts"]
        df["opp_pts"] = df["team_id"].map(opp)
        df["opp_pts_pg"] = _div(df["opp_pts"], df["gp"])
        exp = 13.91
        df["pyth_wpct"] = df.pts**exp / (df.pts**exp + df.opp_pts**exp)
        df["pyth_w"] = df["pyth_wpct"] * df["gp"]
        df["luck"] = df["w"] - df["pyth_w"]
    return df


def build_league_season(team_full: pd.DataFrame, season: str) -> pd.DataFrame:
    t = team_full
    lg = {c: t[c].sum() for c in ["pts", "fga", "fta", "fg3a", "fg3m", "fgm", "ast", "tov",
                                   "oreb", "reb", "gp"]}
    games = lg["gp"] / 2
    return pd.DataFrame([{
        "season": season,
        "teams": len(t),
        "pts_pg": lg["pts"] / lg["gp"],
        "pace": float(np.average(t["pace"], weights=t["min"])),
        "ortg": float(np.average(t["off_rating"], weights=t["min"])),
        "ts_pct": lg["pts"] / (2 * (lg["fga"] + 0.44 * lg["fta"])),
        "efg_pct": (lg["fgm"] + 0.5 * lg["fg3m"]) / lg["fga"],
        "fg3a_rate": lg["fg3a"] / lg["fga"],
        "fg3a_pg": lg["fg3a"] / lg["gp"],
        "fg3_pct": lg["fg3m"] / lg["fg3a"],
        "fta_rate": lg["fta"] / lg["fga"],
        "ast_pg": lg["ast"] / lg["gp"],
        "tov_pg": lg["tov"] / lg["gp"],
        "oreb_pg": lg["oreb"] / lg["gp"],
        "games": games,
    }])
