"""Season predictions.

    python -m nbastats predict                   # next season after the latest stored one
    python -m nbastats predict --season 2026-27 --sims 10000

Steps
1. Player projections: 5/4/3 weighted history (by possessions or minutes), regressed toward a
   prior, aged with aging curves measured from our own data (delta method). Rookies get priors
   from how past rookies at the same draft slot played.
2. Minutes: projected games x minutes per game, then scaled so each roster fills exactly
   48 x 5 x 82 minutes.
3. Team strength: minutes-weighted sum of projected player impact, calibrated by backtesting the
   same procedure on every past season (actual net rating ~ a + b x projected).
4. Season simulation: every scheduled game, many times, with each team's true strength drawn
   from the backtest error distribution; then play-in and a full playoff bracket.
5. Awards: conditional-logit models of MVP and DPOY voting trained on past seasons.
"""
import json
import time
import numpy as np
import pandas as pd
from scipy.optimize import minimize

from . import db
from .config import current_season, season_in_progress, season_label, season_start
from .sources import nbacom

RS = "Regular Season"
TEAM_MIN = 48 * 5 * 82
GAME_SD = 12.5  # SD of NBA game margins around the expected spread
IN_SEASON_K = 25  # games of preseason projection blended with results so far
IMPACT_PRIOR, IMPACT_REG = -1.5, 2500.0  # prior for thin samples, possessions of prior
RATE_REG = 800.0  # minutes of league-average play mixed into rate stats
RATES = ["pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "oreb", "dreb"]
WEIGHTS = [6, 3, 1]  # chosen by backtest over 5/4/3, 8/3/1 and 10/3/0.5


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] predict: {msg}", flush=True)


# ====================================================================== aging

def aging_deltas(ps: pd.DataFrame, stats) -> dict:
    """Year-over-year change by age, minutes-weighted (harmonic mean), smoothed."""
    df = ps[ps["min"] >= 400][["player_id", "season", "age", "min"] + stats].copy()
    df["start"] = df.season.str[:4].astype(int)
    nxt = df.copy()
    nxt["start"] -= 1
    pairs = df.merge(nxt, on=["player_id", "start"], suffixes=("", "_n"))
    pairs["w"] = 2 / (1 / pairs["min"] + 1 / pairs["min_n"])
    pairs["age"] = pairs["age"].round().clip(19, 39).astype(int)
    out = {}
    for s in stats:
        p = pairs.dropna(subset=[s, f"{s}_n"])
        d = p.groupby("age").apply(lambda g: np.average(g[f"{s}_n"] - g[s], weights=g["w"]),
                                   include_groups=False)
        d = d.reindex(range(18, 43)).interpolate().bfill().ffill()
        out[s] = d.rolling(3, center=True, min_periods=1).mean()
    return out


# ====================================================================== rookie priors

def rookie_priors(con, ps: pd.DataFrame) -> pd.DataFrame:
    """How rookies played by draft slot bucket (first stored season within a year of the draft)."""
    dr = db.read_sql(con, """SELECT person_id player_id, CAST(season AS INTEGER) draft_year,
                                    overall_pick FROM draft_history""")
    first = ps.sort_values("season").drop_duplicates("player_id")
    first = first.assign(start=first.season.str[:4].astype(int))
    first = first[first.start > int(ps.season.min()[:4])]  # 1996-97 "debuts" are veterans
    first = first.drop(columns=[c for c in ("draft_year", "overall_pick") if c in first])
    r = first.merge(dr, on="player_id", how="left")
    r = r[(r.draft_year.isna()) | (r.start == r.draft_year)]
    r["bucket"] = r["overall_pick"].map(pick_bucket)
    agg = []
    for b, g in r.groupby("bucket"):
        w = g["min"].clip(lower=1)
        row = {"bucket": b, "n": len(g), "min": float(g["min"].mean()),
               "gp": float(g["gp"].mean()),
               "o_impact": float(np.average(g["o_impact"].fillna(g["o_impact"].mean()), weights=w)),
               "d_impact": float(np.average(g["d_impact"].fillna(g["d_impact"].mean()), weights=w)),
               "ts_pct": float(np.average(g["ts_pct"].fillna(0.52), weights=w))}
        for s in RATES:
            row[f"{s}_p36"] = float(g[s].sum() * 36 / max(g["min"].sum(), 1))
        agg.append(row)
    return pd.DataFrame(agg).set_index("bucket")


def pick_bucket(p):
    if pd.isna(p) or p <= 0:
        return "undrafted"
    p = int(p)
    return ("1-3" if p <= 3 else "4-10" if p <= 10 else "11-20" if p <= 20
            else "21-30" if p <= 30 else "31-60")


# ====================================================================== player projections

def project_players(ps: pd.DataFrame, target: str, roster: pd.DataFrame, aging: dict,
                    priors: pd.DataFrame, draft: pd.DataFrame) -> pd.DataFrame:
    """Project every rostered player for `target` using only seasons before it."""
    t0 = season_start(target)
    window = [season_label(t0 - k) for k in (1, 2, 3)]
    hist = ps[ps.season.isin(window)].copy()
    hist["w"] = hist["season"].map(dict(zip(window, WEIGHTS)))
    lg = ps[ps.season == window[0]]
    lg_rate = {s: lg[s].sum() * 36 / lg["min"].sum() for s in RATES}
    lg_ts = lg["pts"].sum() / (2 * (lg["fga"].sum() + 0.44 * lg["fta"].sum()))

    g = hist.groupby("player_id")
    wposs = (hist["w"] * hist["poss"].fillna(hist["min"] * 2.05)).groupby(hist["player_id"]).sum()
    wmin = (hist["w"] * hist["min"]).groupby(hist["player_id"]).sum()

    def wavg_poss(col):
        v = (hist["w"] * hist["poss"].fillna(hist["min"] * 2.05) * hist[col].fillna(IMPACT_PRIOR))
        return v.groupby(hist["player_id"]).sum()

    proj = pd.DataFrame(index=roster["player_id"].unique())
    proj.index.name = "player_id"
    for side in ("o_impact", "d_impact"):
        prior = IMPACT_PRIOR / 2
        proj[side] = (wavg_poss(side).reindex(proj.index).fillna(0) + prior * IMPACT_REG) / (
            wposs.reindex(proj.index).fillna(0) + IMPACT_REG)
    for s in RATES:
        tot = (hist["w"] * hist[s]).groupby(hist["player_id"]).sum().reindex(proj.index).fillna(0)
        proj[f"{s}_p36"] = (tot * 36 + lg_rate[s] * RATE_REG) / (wmin.reindex(proj.index).fillna(0) + RATE_REG)
    pts = (hist["w"] * hist["pts"]).groupby(hist["player_id"]).sum().reindex(proj.index).fillna(0)
    tsa = (hist["w"] * 2 * (hist["fga"] + 0.44 * hist["fta"])).groupby(hist["player_id"]).sum().reindex(proj.index).fillna(0)
    proj["ts_pct"] = (pts + lg_ts * 300) / (tsa + 300)
    proj["reliability"] = (wposs.reindex(proj.index).fillna(0) / (wposs.reindex(proj.index).fillna(0) + IMPACT_REG))

    last = hist.sort_values("season").groupby("player_id").tail(1).set_index("player_id")
    prev = hist[hist.season == window[1]].set_index("player_id")
    proj["last_season"] = last["season"].reindex(proj.index)
    proj["last_impact"] = last["impact"].reindex(proj.index)
    proj["last_min"] = last["min"].reindex(proj.index)
    proj["last_pts_pg"] = last["pts_pg"].reindex(proj.index)
    proj["last_reb_pg"] = last["reb_pg"].reindex(proj.index)
    proj["last_ast_pg"] = last["ast_pg"].reindex(proj.index)
    proj["last_ts_pct"] = last["ts_pct"].reindex(proj.index)
    proj["last_shot_making"] = last["shot_making"].reindex(proj.index) if "shot_making" in last else np.nan
    proj["seasons_used"] = g.size().reindex(proj.index).fillna(0).astype(int)

    # minutes: games and minutes per game, Marcel style
    g1 = hist[hist.season == window[0]].set_index("player_id")
    gp1 = g1["gp"].reindex(proj.index)
    gp2 = prev["gp"].reindex(proj.index)
    mpg = (hist["w"] * hist["min"]).groupby(hist["player_id"]).sum() / (hist["w"] * hist["gp"]).groupby(hist["player_id"]).sum()
    proj["mpg_hist"] = mpg.reindex(proj.index)
    proj["gp"] = (0.6 * gp1.fillna(40) + 0.2 * gp2.fillna(gp1.fillna(40)) + 0.2 * 62).where(proj["seasons_used"] > 0)

    # roster info, age
    r = roster.drop_duplicates("player_id").set_index("player_id")
    proj["team_id"] = r["team_id"]
    proj["name"] = r["name"]
    proj["age"] = pd.to_numeric(r["age"], errors="coerce")
    age_i = proj["age"].round().clip(18, 42).fillna(27).astype(int) - 1  # age during last season

    # aging from last season's age to this one
    for side in ("o_impact", "d_impact"):
        proj[side] += age_i.map(aging[side]).fillna(0).to_numpy() * proj["reliability"].clip(0.3, 1)
    for s in RATES:
        k = f"{s}_p36"
        if k in aging:
            proj[k] = (proj[k] + age_i.map(aging[k]).fillna(0).to_numpy()).clip(lower=0)
    proj["mpg_hist"] = proj["mpg_hist"] + age_i.map(aging["min_pg"]).fillna(0).to_numpy()

    # rookies and players with no recent NBA stats
    dmap = draft.drop_duplicates("player_id", keep="last").set_index("player_id")["overall_pick"]
    new = proj["seasons_used"] == 0
    proj["rookie"] = new & proj.index.isin(draft[draft.draft_year == t0]["player_id"])
    proj["pick"] = pd.Series(proj.index, index=proj.index).map(dmap).where(proj["rookie"])
    for pid in proj.index[new]:
        b = pick_bucket(proj.at[pid, "pick"]) if proj.at[pid, "rookie"] else "undrafted"
        pr = priors.loc[b] if b in priors.index else priors.loc["undrafted"]
        proj.loc[pid, ["o_impact", "d_impact", "ts_pct"]] = [pr["o_impact"], pr["d_impact"], pr["ts_pct"]]
        for s in RATES:
            proj.at[pid, f"{s}_p36"] = pr[f"{s}_p36"]
        proj.at[pid, "gp"] = pr["gp"]
        # camp invites and two-way players rarely crack a rotation
        proj.at[pid, "mpg_hist"] = pr["min"] / max(pr["gp"], 1) * (1.0 if proj.at[pid, "rookie"] else 0.35)
        proj.at[pid, "reliability"] = 0.0
    proj["impact"] = proj["o_impact"] + proj["d_impact"]
    proj["raw_min"] = (proj["gp"].clip(0, 82) * proj["mpg_hist"].clip(0, 38)).fillna(0)
    return proj.reset_index()


def allocate_minutes(proj: pd.DataFrame) -> pd.DataFrame:
    """Scale each team's raw minutes to fill 19,680 minutes, capped at 3,000 per player.
    Better players get a nudge up so rotations lean on them, like real coaches do."""
    proj = proj.copy()
    proj["min"] = 0.0
    for tid, g in proj.groupby("team_id"):
        raw = g["raw_min"] * (1 + 0.04 * (g["impact"] - g["impact"].median())).clip(0.7, 1.3)
        cap = (g["gp"].clip(upper=82).fillna(60) * 37).clip(upper=2900)
        m = raw.copy()
        for _ in range(30):
            scale = TEAM_MIN / max(m.sum(), 1)
            m = np.minimum(m * scale, cap)
            if abs(m.sum() - TEAM_MIN) < 1:
                break
        proj.loc[g.index, "min"] = m
    proj["mpg"] = proj["min"] / proj["gp"].clip(lower=1).where(proj["min"] > 0, 1)
    proj["mpg"] = proj["mpg"].clip(upper=37)
    for s in RATES:
        proj[f"{s}_pg"] = proj[f"{s}_p36"] * proj["mpg"] / 36
    return proj


def team_strength(proj: pd.DataFrame) -> pd.DataFrame:
    t = proj.groupby("team_id").apply(lambda g: pd.Series({
        "raw_net": 5 * (g["impact"] * g["min"]).sum() / g["min"].sum(),
        "raw_o": 5 * (g["o_impact"] * g["min"]).sum() / g["min"].sum(),
        "raw_d": 5 * (g["d_impact"] * g["min"]).sum() / g["min"].sum()}), include_groups=False)
    # center: the league as a whole has a net rating of zero
    for c in ("raw_net", "raw_o", "raw_d"):
        t[c] -= t[c].mean()
    return t.reset_index()


# ====================================================================== backtest

def backtest(con, ps, aging, priors, draft, first=2000):
    """Run the whole projection on past seasons with their real rosters (players who
    appeared for each team), using only data from before that season."""
    teams = db.read_sql(con, "SELECT season, team_id, net_rating, w, gp FROM team_season_full")
    rows, players = [], []
    last = int(ps.season.max()[:4])
    for y in range(first, last + 1):
        if season_in_progress(season_label(y)):
            continue  # an unfinished season has no final result to test against
        tgt = season_label(y)
        roster = ps[ps.season == tgt][["team_id", "player_id", "player_name", "age"]].rename(
            columns={"player_name": "name"})
        roster = roster.assign(age=roster["age"] + 0)
        pj = allocate_minutes(project_players(ps, tgt, roster, aging, priors, draft))
        ts = team_strength(pj).merge(teams[teams.season == tgt], on="team_id")
        ts["season"] = tgt
        rows.append(ts)
        pj["season"] = tgt
        players.append(pj)
    bt = pd.concat(rows, ignore_index=True)
    b, a = np.polyfit(bt["raw_net"], bt["net_rating"], 1)
    bt["pred_net"] = a + b * bt["raw_net"]
    resid_sd = float((bt["net_rating"] - bt["pred_net"]).std())
    bt_players = pd.concat(players, ignore_index=True)
    return bt, bt_players, {"a": float(a), "b": float(b), "sd": resid_sd,
                            "r": float(bt["raw_net"].corr(bt["net_rating"]))}


# ====================================================================== simulation

def _series_win_prob(p_high_home, p_high_road):
    """Vectorized best-of-7 (2-2-1-1-1) win probability for the higher seed."""
    home = [1, 1, 0, 0, 1, 0, 1]
    state = {(0, 0): np.ones_like(p_high_home)}
    win = np.zeros_like(p_high_home)
    for gi in range(7):
        nxt = {}
        p = p_high_home if home[gi] else p_high_road
        for (w, l), pr in state.items():
            for k, pp in (((w + 1, l), p), ((w, l + 1), 1 - p)):
                if k[0] == 4:
                    win = win + pr * pp
                elif k[1] < 4:
                    nxt[k] = nxt.get(k, 0) + pr * pp
        state = nxt
    return win


def simulate(teams: pd.DataFrame, sched: pd.DataFrame, cal: dict, hca: float, sims: int,
             seed: int = 7, base_wins=None, base_games=None):
    """sched holds the games still to play; base_wins/base_games are results so far
    (in-season mode), otherwise every team starts 0-0."""
    rng = np.random.default_rng(seed)
    ids = teams["team_id"].to_numpy()
    idx = {t: i for i, t in enumerate(ids)}
    n = len(ids)
    conf = teams["conference"].to_numpy()
    true = teams["pred_net"].to_numpy()[None, :] + rng.normal(0, cal["sd"], (sims, n))

    s = sched[(sched.home_id.isin(idx)) & (sched.away_id.isin(idx))]
    h = s.home_id.map(idx).to_numpy()
    a = s.away_id.map(idx).to_numpy()
    edge = np.where(s.neutral.to_numpy() == 1, 0.0, hca)
    wins = np.zeros((sims, n)) + (0 if base_wins is None else np.asarray(base_wins, float)[None, :])
    chunk = 1000
    for c0 in range(0, sims, chunk):
        tr = true[c0:c0 + chunk]
        z = (tr[:, h] - tr[:, a] + edge) / GAME_SD
        p = _phi(z)
        hw = rng.random(p.shape) < p
        wins[c0:c0 + chunk] += hw @ _onehot(h, n) + (~hw) @ _onehot(a, n)
    # games not yet scheduled (NBA Cup knockout slots): play vs an average team, neutral site
    scheduled = np.bincount(h, minlength=n) + np.bincount(a, minlength=n)
    done = np.zeros(n) if base_games is None else np.asarray(base_games, float)
    extra = np.clip(82 - scheduled - done, 0, None).astype(int)
    wins += rng.binomial(extra[None, :].repeat(sims, 0), _phi(true / GAME_SD))
    tieb = rng.random((sims, n)) * 0.01

    res = {k: np.zeros(n) for k in ("playoffs", "playin", "top6", "seed1", "r2", "cf", "finals", "title")}
    seed_counts = np.zeros((n, 15))
    for cf in ("East", "West"):
        members = np.where(conf == cf)[0]
        score = wins[:, members] + tieb[:, members]
        order = members[np.argsort(-score, axis=1)]  # sims x 15, best first
        for k in range(order.shape[1]):
            np.add.at(seed_counts, (order[:, k], k), 1)
        for k in range(6):
            np.add.at(res["top6"], order[:, k], 1)
        np.add.at(res["seed1"], order[:, 0], 1)
        for k in range(6, 10):
            np.add.at(res["playin"], order[:, k], 1)
        # play-in: 7 v 8 at 7, 9 v 10 at 9, loser(7/8) v winner(9/10) at loser's home
        rows = np.arange(sims)

        def game(home_t, away_t, neutral=False):
            z = (true[rows, home_t] - true[rows, away_t] + (0 if neutral else hca)) / GAME_SD
            return np.where(rng.random(sims) < _phi(z), home_t, away_t)

        s7, s8, s9, s10 = order[:, 6], order[:, 7], order[:, 8], order[:, 9]
        w78 = game(s7, s8)
        l78 = np.where(w78 == s7, s8, s7)
        w910 = game(s9, s10)
        seed8 = game(l78, w910)
        bracket = np.column_stack([order[:, 0], seed8, order[:, 3], order[:, 4],
                                   order[:, 2], order[:, 5], order[:, 1], w78])
        for k in range(8):
            np.add.at(res["playoffs"], bracket[:, k], 1)

        def series(hi, lo):
            ph = _phi((true[rows, hi] - true[rows, lo] + hca) / GAME_SD)
            pr = _phi((true[rows, hi] - true[rows, lo] - hca) / GAME_SD)
            return np.where(rng.random(sims) < _series_win_prob(ph, pr), hi, lo)

        r1 = [series(bracket[:, 2 * i], bracket[:, 2 * i + 1]) for i in range(4)]
        for t in r1:
            np.add.at(res["r2"], t, 1)
        # higher seed (more wins) hosts from round 2 on
        def better(x, y):
            return np.where(wins[rows, x] + tieb[rows, x] >= wins[rows, y] + tieb[rows, y], x, y)

        def series_hc(x, y):
            hi = better(x, y)
            lo = np.where(hi == x, y, x)
            return series(hi, lo)

        r2 = [series_hc(r1[0], r1[1]), series_hc(r1[2], r1[3])]
        for t in r2:
            np.add.at(res["cf"], t, 1)
        champ = series_hc(r2[0], r2[1])
        np.add.at(res["finals"], champ, 1)
        if cf == "East":
            east = champ
        else:
            west = champ
    title = series_hc_global(east, west, true, wins, tieb, hca, rng)
    np.add.at(res["title"], title, 1)

    out = teams[["team_id"]].copy()
    out["wins_mean"] = wins.mean(0)
    out["wins_p10"] = np.percentile(wins, 10, axis=0)
    out["wins_p90"] = np.percentile(wins, 90, axis=0)
    for k, v in res.items():
        out[f"p_{k}"] = v / sims
    out["seed_dist"] = [json.dumps((seed_counts[i] / sims).round(4).tolist()) for i in range(n)]
    hist = [json.dumps(np.bincount(wins[:, i].astype(int), minlength=83)[:83].tolist()) for i in range(n)]
    out["wins_hist"] = hist
    return out


def series_hc_global(x, y, true, wins, tieb, hca, rng):
    rows = np.arange(len(x))
    hi = np.where(wins[rows, x] + tieb[rows, x] >= wins[rows, y] + tieb[rows, y], x, y)
    lo = np.where(hi == x, y, x)
    ph = _phi((true[rows, hi] - true[rows, lo] + hca) / GAME_SD)
    pr = _phi((true[rows, hi] - true[rows, lo] - hca) / GAME_SD)
    return np.where(rng.random(len(x)) < _series_win_prob(ph, pr), hi, lo)


def _phi(z):
    from scipy.special import ndtr
    return ndtr(z)


def _onehot(idx, n):
    m = np.zeros((len(idx), n))
    m[np.arange(len(idx)), idx] = 1
    return m


# ====================================================================== awards

AWARD_FEATURES = {
    "MVP": ["pts_pg", "ast_pg", "reb_pg", "war", "impact", "team_wpct"],
    "DPOY": ["d_impact", "blk_pg", "stl_pg", "dreb_pg", "min_pg", "team_wpct"],
}


def _award_rank(awards: pd.Series, award: str) -> pd.Series:
    r = awards.fillna("").str.extract(rf"(?:^|,){award}-(\d+)")[0]
    return pd.to_numeric(r, errors="coerce")


def _zs(df, cols):
    g = df.groupby("season")[cols]
    return ((df[cols] - g.transform("mean")) / g.transform("std")).fillna(0)


def fit_award(ps: pd.DataFrame, award: str):
    """Conditional logit: within each season, P(winner) = softmax(X beta). Trained on
    PRESEASON projections (from the backtest) against how voting actually went, so the
    probabilities carry real preseason uncertainty. Finishers 2-5 get partial credit."""
    cols = AWARD_FEATURES[award]
    df = ps[(ps["min"] >= 1200)].copy()
    df["rank"] = _award_rank(df["awards"], award)
    seasons = [s for s, g in df.groupby("season") if (g["rank"] == 1).any()]
    df = df[df.season.isin(seasons)].reset_index(drop=True)
    X = _zs(df, cols).to_numpy()
    target = np.where(df["rank"] == 1, 1.0, np.where(df["rank"] <= 5, 0.15, 0.0))
    groups = [np.where(df.season == s)[0] for s in seasons]

    def nll(beta, groups=groups):
        total = 0.0
        for gi in groups:
            sc = X[gi] @ beta
            sc = sc - sc.max()
            logp = sc - np.log(np.exp(sc).sum())
            t = target[gi] / target[gi].sum()
            total -= (t * logp).sum()
        return total + 0.05 * (beta ** 2).sum()

    beta = minimize(nll, np.zeros(len(cols)), method="L-BFGS-B").x
    # leave-one-season-out: how often the model's favourite actually won
    hits = 0
    for k, gi in enumerate(groups):
        rest = groups[:k] + groups[k + 1:]
        b = minimize(lambda bb: nll(bb, rest), beta, method="L-BFGS-B").x
        pick = gi[np.argmax(X[gi] @ b)]
        hits += int(df.loc[pick, "rank"] == 1)
    return beta, {"seasons": len(groups), "top_pick_accuracy": hits / len(groups)}


def predict_award(frame: pd.DataFrame, beta, award: str):
    cols = AWARD_FEATURES[award]
    f = frame.assign(season="target")
    X = _zs(f, cols).to_numpy()
    sc = X @ beta
    p = np.exp(sc - sc.max())
    f["prob"] = p / p.sum()
    return f.sort_values("prob", ascending=False)


# ====================================================================== orchestration

def run(target: str | None = None, sims: int = 10000):
    con = db.connect()
    ps = db.read_sql(con, "SELECT * FROM player_season_full")
    ps["min_pg"] = ps["min"] / ps["gp"]
    cur = current_season()
    if target is None:
        live = season_in_progress(cur) and cur in set(ps.season)
        target = cur if live else season_label(season_start(ps.season.max()) + 1)
    in_season = target in set(ps.season)
    ps = ps[ps.season < target].copy()  # projections only ever see earlier seasons
    last = ps.season.max()
    log(f"target season {target} (history through {last}{', in-season mode' if in_season else ''})")

    teams_ref = nbacom.teams()
    roster = pd.concat([nbacom.roster(target, t) for t in teams_ref.team_id], ignore_index=True)
    sched = nbacom.schedule(target)
    db.replace_rows(con, "rosters", roster, {"season": target})
    db.replace_rows(con, "schedule", sched, {"season": target})
    log(f"{len(roster)} rostered players, {len(sched)} scheduled games")

    draft = db.read_sql(con, """SELECT person_id player_id, CAST(season AS INTEGER) draft_year,
                                       overall_pick FROM draft_history""")
    stats = ["o_impact", "d_impact", "min_pg"] + [f"{s}_p36" for s in RATES]
    for s in RATES:
        ps[f"{s}_p36"] = ps[s] * 36 / ps["min"].replace(0, np.nan)
    aging = aging_deltas(ps, stats)
    priors = rookie_priors(con, ps)

    log("backtesting team projections")
    bt, bt_players, cal = backtest(con, ps, aging, priors, draft)
    log(f"backtest r={cal['r']:.2f}, calibrated slope {cal['b']:.2f}, residual SD {cal['sd']:.2f}")
    bt_summary = bt.groupby("season").apply(lambda g: pd.Series({
        "r": g.pred_net.corr(g.net_rating),
        "rmse_net": float(np.sqrt(((g.pred_net - g.net_rating) ** 2).mean()))}), include_groups=False).reset_index()
    wins_per_net = float(np.polyfit(bt["net_rating"], bt["w"] / bt["gp"] * 82, 1)[0])
    bt_summary["rmse_wins"] = bt_summary["rmse_net"] * wins_per_net
    db.replace_rows(con, "pred_backtest", bt_summary)
    db.replace_rows(con, "pred_backtest_teams", bt[["season", "team_id", "raw_net", "pred_net", "net_rating", "w"]])

    log("projecting players")
    proj = allocate_minutes(project_players(ps, target, roster, aging, priors, draft))
    ts = team_strength(proj)
    ts["pred_net"] = cal["a"] + cal["b"] * ts["raw_net"]
    ts["pred_o"] = cal["b"] * ts["raw_o"]
    ts["pred_d"] = cal["b"] * ts["raw_d"]
    info = db.read_sql(con, f"""SELECT team_id, team_abbr, team_name, conference, w last_w,
                                       net_rating last_net, luck last_luck
                                FROM team_season_full WHERE season = '{last}'""")
    ts = ts.merge(info, on="team_id")
    hca = float(db.read_sql(con, "SELECT avg(hca) h FROM team_srs WHERE season >= ?",
                            (season_label(season_start(last) - 2),))["h"].iloc[0])

    base_wins = base_games = None
    to_play = sched
    if in_season:
        # blend the preseason projection with results so far (weight grows with games played)
        now = db.read_sql(con, """SELECT team_id, gp, w, net_rating FROM team_season_full
                                  WHERE season = ?""", (target,))
        ts = ts.merge(now.rename(columns={"gp": "cur_gp", "w": "cur_w", "net_rating": "cur_net"}),
                      on="team_id", how="left").fillna({"cur_gp": 0, "cur_w": 0, "cur_net": 0})
        ts["pre_net"] = ts["pred_net"]
        ts["pred_net"] = (ts["pre_net"] * IN_SEASON_K + ts["cur_net"] * ts["cur_gp"]) / (IN_SEASON_K + ts["cur_gp"])
        played = set(db.read_sql(con, "SELECT DISTINCT game_id FROM team_game WHERE season=? AND season_type=?",
                                 (target, RS))["game_id"])
        to_play = sched[~sched.game_id.isin(played)]
        base_wins, base_games = ts["cur_w"].to_numpy(), ts["cur_gp"].to_numpy()
        log(f"{len(played)} games played, {len(to_play)} left to simulate")

    log(f"simulating {sims:,} seasons")
    sim = simulate(ts, to_play, cal, hca, sims, base_wins=base_wins, base_games=base_games)
    ts = ts.merge(sim, on="team_id")
    ts["season"] = target

    # projected wins above replacement: one point of net rating over a season is worth
    # wins_per_net wins, and a season is ~82 x 100 possessions, so a win costs 82 / wins_per_net
    # points of differential. Players see about 2.08 possessions per minute.
    points_per_win = 82 / wins_per_net
    proj["war"] = (proj["impact"] + 2.0) * (proj["min"] * 2.08) / 100 / points_per_win
    proj = proj.merge(ts[["team_id", "team_abbr", "wins_mean"]], on="team_id", how="left")
    proj["team_wpct"] = proj["wins_mean"] / 82
    proj["season"] = target
    proj["impact_change"] = proj["impact"] - proj["last_impact"]

    # awards
    log("award models")
    awards = []
    award_meta = {}
    cand = proj[(proj["min"] >= 1200)].copy()
    cand["min_pg"] = cand["mpg"]
    # training rows: backtest projections + projected team win% + the real voting results
    tr = bt_players.copy()
    tr["min_pg"] = tr["mpg"]
    tr["war"] = (tr["impact"] + 2.0) * (tr["min"] * 2.08) / 100 / (82 / wins_per_net)
    tw = bt[["season", "team_id", "pred_net"]].assign(team_wpct=lambda d: (41 + wins_per_net * d.pred_net) / 82)
    tr = tr.merge(tw[["season", "team_id", "team_wpct"]], on=["season", "team_id"], how="left")
    tr = tr.merge(ps[["player_id", "season", "awards"]], on=["player_id", "season"], how="left")
    for award in AWARD_FEATURES:
        beta, meta = fit_award(tr, award)
        award_meta[award] = {**meta, "coefficients": dict(zip(AWARD_FEATURES[award], beta.round(3).tolist()))}
        pr = predict_award(cand, beta, award).head(15)
        awards.append(pr[["player_id", "name", "team_abbr", "prob"] + AWARD_FEATURES[award]].assign(award=award))
        log(f"{award}: backtest top pick won {meta['top_pick_accuracy']:.0%} of {meta['seasons']} seasons")
    awards = pd.concat(awards, ignore_index=True)
    awards["season"] = target

    keep = ["season", "player_id", "name", "team_id", "team_abbr", "age", "rookie", "pick", "seasons_used",
            "reliability", "o_impact", "d_impact", "impact", "war", "min", "gp", "mpg", "ts_pct",
            "last_season", "last_impact", "last_min", "last_pts_pg", "last_reb_pg", "last_ast_pg",
            "last_ts_pct", "last_shot_making", "impact_change"] + [f"{s}_pg" for s in RATES]
    db.replace_rows(con, "pred_players", proj[keep], {"season": target})
    db.replace_rows(con, "pred_teams", ts, {"season": target})
    db.replace_rows(con, "pred_awards", awards, {"season": target})
    meta = pd.DataFrame([{"season": target, "history_through": last, "sims": sims,
                          "created": time.strftime("%Y-%m-%d %H:%M"), "hca": hca,
                          "calibration": json.dumps(cal), "wins_per_net": wins_per_net,
                          "awards": json.dumps(award_meta), "games": len(sched),
                          "mode": "in-season" if in_season else "preseason",
                          "games_left": len(to_play)}])
    db.replace_rows(con, "pred_meta", meta, {"season": target})
    log("done")
    con.close()
