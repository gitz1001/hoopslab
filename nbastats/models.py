"""Advanced models built on the stored tables (no network calls):

Team:   Elo ratings for every game, SRS / strength of schedule, rest and home-court splits.
Player: lineup-based RAPM (offense/defense), a box-score impact model trained on RAPM,
        prior-informed RAPM ("impact"), wins above replacement, shot-making vs expected,
        scoring value, game-to-game consistency.

    python -m nbastats models
"""
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from . import db

RS = "Regular Season"


def log(msg):
    import time
    print(f"[{time.strftime('%H:%M:%S')}] models: {msg}", flush=True)


# ====================================================================== team models

def _games(con) -> pd.DataFrame:
    """One row per game with home and away sides (regular season + playoffs)."""
    tg = db.read_sql(con, """SELECT season, season_type, team_id, team_abbreviation abbr,
                                    game_id, game_date, matchup, wl, pts, plus_minus
                             FROM team_game""")
    tg["home"] = tg["matchup"].str.contains(" vs. ")
    h = tg[tg.home].rename(columns=lambda c: f"h_{c}" if c not in ("game_id", "season", "season_type", "game_date") else c)
    a = tg[~tg.home].rename(columns=lambda c: f"a_{c}" if c not in ("game_id", "season", "season_type", "game_date") else c)
    g = h.merge(a[["game_id", "a_team_id", "a_abbr", "a_pts"]], on="game_id")
    g["margin"] = g["h_pts"] - g["a_pts"]
    return g.sort_values(["game_date", "game_id"]).reset_index(drop=True)


ELO_K, ELO_HOME, ELO_MEAN, ELO_CARRY = 20.0, 100.0, 1505.0, 0.75


def elo(games: pd.DataFrame) -> pd.DataFrame:
    """FiveThirtyEight-style NBA Elo: K=20, 100-point home edge, margin-of-victory
    multiplier with autocorrelation correction, 25% reversion to the mean between seasons."""
    rating, last_season = {}, {}
    rows = []
    for g in games.itertuples(index=False):
        for tid in (g.h_team_id, g.a_team_id):
            if tid not in rating:
                rating[tid] = ELO_MEAN
            elif last_season.get(tid) != g.season:
                rating[tid] = ELO_CARRY * rating[tid] + (1 - ELO_CARRY) * ELO_MEAN
            last_season[tid] = g.season
        rh, ra = rating[g.h_team_id], rating[g.a_team_id]
        diff = rh + ELO_HOME - ra
        p_home = 1 / (1 + 10 ** (-diff / 400))
        won = 1.0 if g.margin > 0 else 0.0
        elo_diff_winner = diff if won else -diff
        mult = ((abs(g.margin) + 3) ** 0.8) / (7.5 + 0.006 * elo_diff_winner)
        shift = ELO_K * mult * (won - p_home)
        rating[g.h_team_id] = rh + shift
        rating[g.a_team_id] = ra - shift
        rows.append((g.game_id, g.season, g.season_type, g.game_date, g.h_team_id, g.h_abbr,
                     g.a_team_id, g.a_abbr, g.h_pts, g.a_pts, rh, ra, p_home, rh + shift, ra - shift))
    return pd.DataFrame(rows, columns=[
        "game_id", "season", "season_type", "game_date", "home_id", "home", "away_id", "away",
        "home_pts", "away_pts", "home_elo_pre", "away_elo_pre", "home_win_prob",
        "home_elo_post", "away_elo_post"])


def srs(games: pd.DataFrame) -> pd.DataFrame:
    """Simple Rating System per season: least squares on game margins with a shared
    home-court term; SRS = average margin adjusted for opponent strength."""
    out = []
    for season, g in games[games.season_type == RS].groupby("season"):
        teams = sorted(set(g.h_team_id) | set(g.a_team_id))
        idx = {t: i for i, t in enumerate(teams)}
        n = len(g)
        X = np.zeros((n + 1, len(teams) + 1))
        X[np.arange(n), g.h_team_id.map(idx)] = 1
        X[np.arange(n), g.a_team_id.map(idx)] = -1
        X[:n, -1] = 1  # home court
        X[n, :-1] = 1  # ratings sum to zero
        y = np.append(g.margin.to_numpy(float), 0)
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        r = pd.Series(coef[:-1], index=teams)
        for t in teams:
            played = g[(g.h_team_id == t) | (g.a_team_id == t)]
            opp = np.where(played.h_team_id == t, played.a_team_id, played.h_team_id)
            mov = np.where(played.h_team_id == t, played.margin, -played.margin).mean()
            out.append({"season": season, "team_id": t, "srs": r[t], "mov": mov,
                        "sos": float(r[opp].mean()), "hca": coef[-1]})
    return pd.DataFrame(out)


def rest_splits(con) -> pd.DataFrame:
    """Days of rest before every regular-season team game, with result and margin."""
    tg = db.read_sql(con, """SELECT season, team_id, team_abbreviation abbr, game_id, game_date,
                                    matchup, wl, plus_minus FROM team_game
                             WHERE season_type = ? ORDER BY team_id, game_date""", (RS,))
    tg["game_date"] = pd.to_datetime(tg["game_date"])
    tg["rest"] = tg.groupby(["team_id", "season"])["game_date"].diff().dt.days - 1
    tg["home"] = tg["matchup"].str.contains(" vs. ").astype(int)
    tg["game_date"] = tg["game_date"].dt.strftime("%Y-%m-%d")
    return tg


def team_models(con):
    log("team games")
    g = _games(con)
    log(f"Elo over {len(g):,} games")
    e = elo(g)
    db.replace_rows(con, "game_elo", e)
    log("SRS")
    db.replace_rows(con, "team_srs", srs(g))
    log("rest days")
    r = rest_splits(con)
    # attach the opponent's rest so we can compare rest advantage
    opp = r[["game_id", "team_id", "rest"]].rename(columns={"team_id": "opp_id", "rest": "opp_rest"})
    r = r.merge(opp, on="game_id")
    r = r[r.team_id != r.opp_id]
    db.replace_rows(con, "team_rest", r)

    # fold the season-level results into team_season_full
    t = db.read_sql(con, "SELECT * FROM team_season_full")
    t = t.drop(columns=[c for c in ("srs", "mov", "sos", "elo_end", "elo_peak") if c in t])
    t = t.merge(db.read_sql(con, "SELECT season, team_id, srs, mov, sos FROM team_srs"),
                on=["season", "team_id"], how="left")
    rs = e[e.season_type == RS]
    sides = pd.concat([
        rs[["season", "game_date", "home_id", "home_elo_post"]].set_axis(["season", "game_date", "team_id", "elo"], axis=1),
        rs[["season", "game_date", "away_id", "away_elo_post"]].set_axis(["season", "game_date", "team_id", "elo"], axis=1)])
    sides = sides.sort_values("game_date")
    agg = sides.groupby(["season", "team_id"])["elo"].agg(elo_end="last", elo_peak="max").reset_index()
    t = t.merge(agg, on=["season", "team_id"], how="left")
    db.replace_rows(con, "team_season_full", t)


# ====================================================================== player models

def _lineup_matrix(lu: pd.DataFrame):
    """Sparse-ish design matrix: one column per player, 1 when on the floor."""
    ids = lu["group_id"].str.strip("-").str.split("-")
    players = sorted({int(p) for grp in ids for p in grp if p})
    col = {p: i for i, p in enumerate(players)}
    X = np.zeros((len(lu), len(players)), dtype=np.float32)
    for r, grp in enumerate(ids):
        for p in grp:
            if p:
                X[r, col[int(p)]] = 1
    return X, players


RAPM_ALPHA = 2000.0  # ridge penalty in possession-weighted units


def rapm_season(lu: pd.DataFrame, prior: pd.DataFrame | None = None) -> pd.DataFrame:
    """Ridge regression of each lineup's offensive and defensive rating (relative to
    league average, per 100 possessions) on who was on the floor, weighted by possessions.
    With a prior, the regression shrinks toward each player's box-score estimate instead of 0."""
    lu = lu[(lu.poss > 0) & lu.off_rating.notna() & lu.def_rating.notna()].reset_index(drop=True)
    X, players = _lineup_matrix(lu)
    w = lu["poss"].to_numpy(float)
    lg_o = np.average(lu.off_rating, weights=w)
    lg_d = np.average(lu.def_rating, weights=w)
    y_o = lu.off_rating.to_numpy(float) - lg_o
    y_d = lg_d - lu.def_rating.to_numpy(float)  # positive = good defense
    pri_o = pri_d = np.zeros(len(players))
    if prior is not None:
        p = prior.set_index("player_id")
        pri_o = np.array([p["o_box"].get(pid, 0.0) for pid in players])
        pri_d = np.array([p["d_box"].get(pid, 0.0) for pid in players])
        pri_o, pri_d = np.nan_to_num(pri_o), np.nan_to_num(pri_d)
    out = {"player_id": players}
    for name, y, pri in (("o", y_o, pri_o), ("d", y_d, pri_d)):
        resid = y - X @ pri
        m = Ridge(alpha=RAPM_ALPHA, fit_intercept=False).fit(X, resid, sample_weight=w)
        out[name] = m.coef_ + pri
    df = pd.DataFrame(out)
    df["total"] = df["o"] + df["d"]
    df["poss"] = X.T @ w
    return df


# Per-100 rates only (their percentage twins like AST% are near duplicates and make the
# coefficients cancel each other out), plus efficiency, height and team strength.
BOX_FEATURES = ["pts_p100", "fga_p100", "fta_p100", "fg3m_p100", "ast_p100", "tov_p100",
                "oreb_p100", "dreb_p100", "stl_p100", "blk_p100", "pf_p100", "ts_rel",
                "height_z", "team_net"]


def _box_frame(ps: pd.DataFrame) -> pd.DataFrame:
    df = ps.copy()
    for c in ["fga", "fta", "fg3m", "oreb", "dreb", "pf"]:
        if f"{c}_p100" not in df:
            df[f"{c}_p100"] = np.where(df["poss"] > 0, df[c] * 100 / df["poss"], np.nan)
    lg_ts = df.groupby("season").apply(lambda g: g.pts.sum() / (2 * (g.fga.sum() + 0.44 * g.fta.sum())),
                                       include_groups=False)
    df["ts_rel"] = df["ts_pct"] - df["season"].map(lg_ts)
    h = df["height_in"]
    df["height_z"] = ((h - h.mean()) / h.std()).fillna(0)
    return df


def box_model(ps: pd.DataFrame, rapm: pd.DataFrame):
    """Learn how box-score rates map to RAPM (offense and defense separately), weighted by
    possessions. This is the same idea as Basketball Reference's BPM, fit on our own data."""
    df = _box_frame(ps)
    train = df.merge(rapm[["player_id", "season", "o", "d", "poss"]].rename(columns={"poss": "rapm_poss"}),
                     on=["player_id", "season"])
    train = train[(train["min"] >= 250)].dropna(subset=BOX_FEATURES + ["o", "d"])
    Xtr = train[BOX_FEATURES].to_numpy(float)
    mu, sd = Xtr.mean(0), Xtr.std(0)
    Z = (Xtr - mu) / sd
    w = train["rapm_poss"].to_numpy(float)
    models = {k: Ridge(alpha=50.0).fit(Z, train[k], sample_weight=w) for k in ("o", "d")}

    full = df.dropna(subset=[c for c in BOX_FEATURES if c not in ("height_z", "team_net")]).copy()
    full[["height_z", "team_net"]] = full[["height_z", "team_net"]].fillna(0)
    Zf = (full[BOX_FEATURES].to_numpy(float) - mu) / sd
    full["o_box"] = models["o"].predict(Zf)
    full["d_box"] = models["d"].predict(Zf)
    # center so the minutes-weighted average player is 0 each season
    for k in ("o_box", "d_box"):
        full[k] -= full.groupby("season")[k].transform(lambda s: np.average(s, weights=full.loc[s.index, "min"]))
    full["box_impact"] = full["o_box"] + full["d_box"]
    coefs = pd.DataFrame({"feature": BOX_FEATURES,
                          "o_coef": models["o"].coef_, "d_coef": models["d"].coef_})
    fit = {k: float(np.corrcoef(models[k].predict(Z), train[k])[0, 1]) for k in ("o", "d")}
    return full[["player_id", "season", "o_box", "d_box", "box_impact"]], coefs, fit


def points_per_win(con) -> pd.Series:
    """Marginal wins per point of season point differential, fit per season."""
    t = db.read_sql(con, "SELECT season, w, gp, plus_minus FROM team_season_full")
    out = {}
    for s, g in t.groupby("season"):
        slope = np.polyfit(g["plus_minus"], g["w"], 1)[0]
        out[s] = 1 / slope
    return pd.Series(out)


REPLACEMENT = -2.0  # impact of a freely available player, points per 100 possessions


def player_models(con):
    ps = db.read_sql(con, "SELECT * FROM player_season_full")
    tfull = db.read_sql(con, "SELECT season, team_id, net_rating team_net FROM team_season_full")
    drop_cols = [c for c in ps.columns if c in (
        "team_net", "rapm_o", "rapm_d", "rapm", "rapm_poss", "o_box", "d_box", "box_impact",
        "o_impact", "d_impact", "impact", "war", "xefg_pct", "shot_making", "ts_rel",
        "scoring_value", "gmsc_sd", "gmsc_p10", "gmsc_p90", "pts_sd", "consistency")]
    ps = ps.drop(columns=drop_cols)
    ps = ps.merge(tfull, on=["season", "team_id"], how="left")

    lineup_seasons = []
    if db.table_exists(con, "lineups"):
        lu_all = db.read_sql(con, "SELECT * FROM lineups")
        lineup_seasons = sorted(lu_all.season.unique())
    # 1) pure RAPM per season
    rapm = []
    for s in lineup_seasons:
        r = rapm_season(lu_all[lu_all.season == s])
        r["season"] = s
        rapm.append(r)
    rapm = pd.concat(rapm, ignore_index=True) if rapm else pd.DataFrame(
        columns=["player_id", "o", "d", "total", "poss", "season"])
    log(f"RAPM for {len(lineup_seasons)} seasons, {len(rapm):,} player-seasons")

    # 2) box-score model trained on RAPM, applied to every season
    box, coefs, fit = box_model(ps, rapm) if len(rapm) else (None, None, None)
    if box is not None:
        db.replace_rows(con, "box_model_coefs", coefs.assign(fit_o=fit["o"], fit_d=fit["d"]))
        log(f"box model fit r: offense {fit['o']:.2f}, defense {fit['d']:.2f}")

    # 3) prior-informed RAPM where lineups exist, box impact elsewhere
    impact = []
    for s in lineup_seasons:
        prior = box[box.season == s]
        r = rapm_season(lu_all[lu_all.season == s], prior)
        r["season"] = s
        impact.append(r.rename(columns={"o": "o_impact", "d": "d_impact", "total": "impact"}))
    impact = pd.concat(impact, ignore_index=True) if impact else pd.DataFrame()

    ps = ps.merge(rapm.rename(columns={"o": "rapm_o", "d": "rapm_d", "total": "rapm", "poss": "rapm_poss"}),
                  on=["player_id", "season"], how="left")
    if box is not None:
        ps = ps.merge(box, on=["player_id", "season"], how="left")
    if len(impact):
        ps = ps.merge(impact.drop(columns=["poss"]), on=["player_id", "season"], how="left")
    else:
        ps["o_impact"] = ps["d_impact"] = ps["impact"] = np.nan
    for k, b in (("o_impact", "o_box"), ("d_impact", "d_box"), ("impact", "box_impact")):
        if b in ps:
            ps[k] = ps[k].fillna(ps[b])

    # 4) wins above replacement: impact over replacement, times possessions on the floor
    ppw = points_per_win(con)
    ps["war"] = (ps["impact"] - REPLACEMENT) * ps["poss"] / 100 / ps["season"].map(ppw)

    # 5) shooting: expected eFG from shot locations, shot-making, scoring value
    zones = ["ra", "paint", "mid", "c3", "atb3"]
    lg = ps.groupby("season")[[f"{z}_{k}" for z in zones for k in ("fgm", "fga")]].sum()
    tot = sum(ps[f"{z}_fga"].fillna(0) for z in zones)
    xefg = 0
    for z in zones:
        mult = 1.5 if z in ("c3", "atb3") else 1.0
        lg_z = ps["season"].map(lg[f"{z}_fgm"] / lg[f"{z}_fga"]) * mult
        xefg = xefg + ps[f"{z}_fga"].fillna(0) * lg_z
    ps["xefg_pct"] = np.where(tot > 0, xefg / tot.replace(0, np.nan), np.nan)
    ps["shot_making"] = ps["efg_pct"] - ps["xefg_pct"]
    lg_ts = ps.groupby("season").apply(lambda g: g.pts.sum() / (2 * (g.fga.sum() + 0.44 * g.fta.sum())),
                                       include_groups=False)
    ps["ts_rel"] = ps["ts_pct"] - ps["season"].map(lg_ts)
    ps["scoring_value"] = ps["ts_rel"] * 2 * (ps["fga"] + 0.44 * ps["fta"])

    # 6) consistency from game logs
    g = db.read_sql(con, """SELECT player_id, season, game_score, pts FROM player_game
                            WHERE season_type = ? AND min > 0""", (RS,))
    cons = g.groupby(["player_id", "season"]).agg(
        gmsc_sd=("game_score", "std"), gmsc_p10=("game_score", lambda s: s.quantile(0.1)),
        gmsc_p90=("game_score", lambda s: s.quantile(0.9)), pts_sd=("pts", "std")).reset_index()
    ps = ps.merge(cons, on=["player_id", "season"], how="left")
    ps["consistency"] = ps["gmsc_avg"] / ps["gmsc_sd"]

    db.replace_rows(con, "player_season_full", ps)
    log("player_season_full updated with impact, WAR, shooting and consistency")


def load_lineups(con, seasons):
    from .sources import nbacom
    teams = nbacom.teams()["team_id"].tolist()
    for s in seasons:
        frames = [f for f in (nbacom.lineups(s, t) for t in teams) if not f.empty]
        if frames:
            df = pd.concat(frames, ignore_index=True)
            df.columns = [c.lower() for c in df.columns]
            db.replace_rows(con, "lineups", df, {"season": s})


def run(con, seasons_for_lineups=None):
    if seasons_for_lineups:
        log("lineups")
        load_lineups(con, seasons_for_lineups)
    team_models(con)
    player_models(con)
    db.ensure_indexes(con)
