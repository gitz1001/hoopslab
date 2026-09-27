"""Analysis models that run on top of the SQLite tables: archetype clustering, similarity,
Marcel-style projections, aging curves, percentiles and team win models."""
import os
import warnings
from functools import lru_cache

import numpy as np
import pandas as pd
os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 4))
# joblib cannot count physical cores on some Windows setups and warns on every fit
warnings.filterwarnings("ignore", message="Could not find the number of physical cores")
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.linear_model import LinearRegression

from nbastats import db

# Style features: rate stats that describe *how* a player plays, mostly independent of minutes.
PROFILE = ["pts_p100", "usg_pct", "ast_pct", "oreb_pct", "dreb_pct", "stl_p100", "blk_p100",
           "tov_p100", "fg3a_rate", "fta_rate", "ts_pct"]
PROFILE_WORDS = {
    "pts_p100": ("Scorer", "Low-volume"),
    "usg_pct": ("High-usage", "Low-usage"),
    "ast_pct": ("Playmaker", "Off-ball"),
    "oreb_pct": ("Offensive glass", None),
    "dreb_pct": ("Rebounder", None),
    "stl_p100": ("Ball hawk", None),
    "blk_p100": ("Rim protector", None),
    "tov_p100": ("Turnover-prone", "Sure-handed"),
    "fg3a_rate": ("Floor spacer", "Interior"),
    "fta_rate": ("Foul drawer", None),
    "ts_pct": ("Efficient", "Inefficient"),
}
RADAR = ["pts_p100", "ts_pct", "ast_pct", "reb_pct", "stl_p100", "blk_p100", "usg_pct",
         "fg3a_rate", "bpm"]


def _con():
    return db.connect()


@lru_cache(maxsize=4)
def all_player_seasons() -> pd.DataFrame:
    con = _con()
    df = db.read_sql(con, "SELECT * FROM player_season_full")
    con.close()
    return df


def clear_cache():
    all_player_seasons.cache_clear()
    clusters.cache_clear()


def _zscore_within_season(df: pd.DataFrame, cols) -> pd.DataFrame:
    g = df.groupby("season")[cols]
    return (df[cols] - g.transform("mean")) / g.transform("std")


def qualified(df, min_minutes=500):
    return df[df["min"] >= min_minutes].copy()


# ------------------------------------------------------------------ archetypes

@lru_cache(maxsize=32)
def clusters(season: str, k: int = 8, min_minutes: int = 800):
    df = qualified(all_player_seasons().query("season == @season"), min_minutes)
    df = df.dropna(subset=PROFILE)
    if len(df) < k * 3:
        return {"players": [], "clusters": []}
    X = df[PROFILE].to_numpy()
    X = (X - X.mean(0)) / X.std(0)
    km = KMeans(n_clusters=k, n_init=20, random_state=7).fit(X)
    xy = PCA(n_components=2, random_state=7).fit_transform(X)
    df["cluster"] = km.labels_
    df["x"], df["y"] = xy[:, 0], xy[:, 1]

    out_clusters = []
    for c in range(k):
        center = km.cluster_centers_[c]
        order = np.argsort(-np.abs(center))
        words = []
        for i in order:
            hi, lo = PROFILE_WORDS[PROFILE[i]]
            w = hi if center[i] > 0 else lo
            if w and w not in words:
                words.append(w)
            if len(words) == 2:
                break
        members = df[df.cluster == c].sort_values("min", ascending=False)
        pos = members["pos"].mode().iloc[0] if members["pos"].notna().any() else ""
        out_clusters.append({
            "id": c,
            "label": " · ".join(words),
            "position": pos,
            "size": int(len(members)),
            "profile": {PROFILE[i]: round(float(center[i]), 2) for i in range(len(PROFILE))},
            "examples": members["player_name"].head(6).tolist(),
            "avg_bpm": None if members["bpm"].isna().all() else round(float(members["bpm"].mean()), 1),
        })
    players = df[["player_id", "player_name", "team_abbreviation", "pos", "cluster", "x", "y",
                  "min", "pts_pg", "bpm"]].round(3)
    return {"players": players.to_dict("records"), "clusters": out_clusters,
            "features": PROFILE}


# ------------------------------------------------------------------ similarity

def similar(player_id: int, season: str, n: int = 10, min_minutes: int = 800):
    df = qualified(all_player_seasons(), min_minutes).dropna(subset=PROFILE).reset_index(drop=True)
    target = df[(df.player_id == player_id) & (df.season == season)]
    if target.empty:
        return []
    z = _zscore_within_season(df, PROFILE).fillna(0).to_numpy()
    t = z[target.index[0]]
    df["distance"] = np.sqrt(((z - t) ** 2).sum(1))
    df = df[df.player_id != player_id].sort_values("distance")
    df = df.drop_duplicates("player_id").head(n)
    df["similarity"] = (100 * np.exp(-df["distance"] / 4)).round(0)
    return df[["player_id", "player_name", "season", "team_abbreviation", "age", "pts_pg",
               "reb_pg", "ast_pg", "ts_pct", "bpm", "similarity"]].round(3).to_dict("records")


# ------------------------------------------------------------------ percentiles / compare

def percentiles(player_id: int, season: str, cols=RADAR, min_minutes=500):
    df = qualified(all_player_seasons().query("season == @season"), min_minutes)
    row = all_player_seasons().query("player_id == @player_id and season == @season")
    if row.empty:
        return None
    out = {}
    for c in cols:
        v = row[c].iloc[0]
        s = df[c].dropna()
        out[c] = None if pd.isna(v) or s.empty else round(float((s < v).mean() * 100), 0)
    return out


def compare(pairs):
    rows = []
    allp = all_player_seasons()
    for pid, season in pairs:
        r = allp[(allp.player_id == pid) & (allp.season == season)]
        if r.empty:
            continue
        rec = r.iloc[0].replace({np.nan: None}).to_dict()
        rec["percentiles"] = percentiles(pid, season)
        rows.append(rec)
    return {"players": rows, "radar": RADAR}


# ------------------------------------------------------------------ projections

PROJ_STATS = ["pts", "reb", "ast", "stl", "blk", "tov", "fg3m"]
WEIGHTS = [5, 4, 3]
REGRESS_MIN = 1000  # minutes of league-average play mixed into every projection


def projections(base_season: str, min_minutes: int = 500):
    """Marcel-the-monkey projections for the season after base_season: a 5/4/3 weighted
    average of the last three seasons, regressed toward league average, age adjusted."""
    allp = all_player_seasons()
    seasons = sorted(allp.season.unique())
    if base_season not in seasons:
        return []
    i = seasons.index(base_season)
    window = seasons[max(0, i - 2): i + 1][::-1]
    lg = allp[allp.season == base_season]
    lg_rate = {s: lg[s].sum() / lg["min"].sum() for s in PROJ_STATS}
    lg_ts = lg["pts"].sum() / (2 * (lg["fga"].sum() + 0.44 * lg["fta"].sum()))

    cur = allp[(allp.season == base_season) & (allp["min"] >= min_minutes)]
    hist = allp[allp.season.isin(window)].set_index(["player_id", "season"])
    out = []
    for _, p in cur.iterrows():
        wmin = 0.0
        wsum = {s: 0.0 for s in PROJ_STATS + ["fga", "fta"]}
        mins = []
        for w, s in zip(WEIGHTS, window):
            if (p.player_id, s) in hist.index:
                h = hist.loc[(p.player_id, s)]
                wmin += w * h["min"]
                for st in wsum:
                    wsum[st] += w * h[st]
                mins.append(h["min"])
            else:
                mins.append(0.0)
        rel = wmin / (wmin + REGRESS_MIN)
        age = (27 if pd.isna(p.age) else p.age) + 1
        age_adj = 1 + (0.006 * (28 - age) if age < 28 else -0.004 * (age - 28))
        proj = {}
        for st in PROJ_STATS:
            rate = (wsum[st] + lg_rate[st] * REGRESS_MIN) / (wmin + REGRESS_MIN)
            if st != "tov":
                rate *= age_adj
            proj[f"{st}_p36"] = rate * 36
        ts_att = 2 * (wsum["fga"] + 0.44 * wsum["fta"])
        prior_att = 400  # shooting attempts of league-average true shooting
        proj["ts_pct"] = (wsum["pts"] + lg_ts * prior_att) / (ts_att + prior_att)
        m1 = mins[0] if mins else 0
        m2 = mins[1] if len(mins) > 1 else 0
        proj_min = (0.5 * m1 + 0.1 * m2 + 400) * (1 if age < 32 else 0.9)
        mpg = 0 if pd.isna(p.min_pg) else p.min_pg
        proj["min_pg"] = mpg * (1.02 if age < 25 else 0.97 if age > 31 else 1.0)
        for st in PROJ_STATS:
            proj[f"{st}_pg"] = proj[f"{st}_p36"] * proj["min_pg"] / 36
        out.append({
            "player_id": int(p.player_id), "player_name": p.player_name,
            "team_abbreviation": p.team_abbreviation, "age": age,
            "seasons_used": int(sum(1 for m in mins if m > 0)),
            "reliability": round(rel, 2), "proj_min": round(proj_min),
            "last_pts_pg": p.pts_pg, "last_reb_pg": p.reb_pg, "last_ast_pg": p.ast_pg,
            "last_ts_pct": p.ts_pct,
            **{k: round(float(v), 3) for k, v in proj.items()},
        })
    out.sort(key=lambda r: -r["pts_pg"])
    return out


# ------------------------------------------------------------------ aging curves

def aging_curve(stat: str, min_minutes: int = 500):
    """Delta method: average year-over-year change at each age, weighted by the harmonic
    mean of minutes in the two seasons, chained into a cumulative curve anchored at 0."""
    df = qualified(all_player_seasons(), min_minutes)[["player_id", "season", "age", "min", stat]]
    df = df.dropna()
    df["start"] = df.season.str[:4].astype(int)
    nxt = df.copy()
    nxt["start"] -= 1
    pairs = df.merge(nxt, on=["player_id", "start"], suffixes=("", "_next"))
    if pairs.empty:
        return []
    pairs["delta"] = pairs[f"{stat}_next"] - pairs[stat]
    pairs["w"] = 2 / (1 / pairs["min"] + 1 / pairs["min_next"])
    pairs["age"] = pairs["age"].round().astype(int)
    rows = []
    for age, g in pairs.groupby("age"):
        if len(g) < 15:
            continue
        rows.append({"age": int(age), "to_age": int(age) + 1, "n": int(len(g)),
                     "delta": float(np.average(g["delta"], weights=g["w"]))})
    cum = 0.0
    for r in rows:
        r["cumulative_start"] = cum
        cum += r["delta"]
        r["cumulative"] = cum
    return rows


# ------------------------------------------------------------------ team models

FOUR_FACTORS = ["efg_pct", "tm_tov_pct", "oreb_pct", "fta_rate",
                "opp_efg_pct", "opp_tov_pct", "opp_oreb_pct", "opp_fta_rate"]


def win_model():
    """Regress team win% on the four factors (offense and defense) across every stored
    team-season, on standardized inputs so the coefficients are comparable."""
    con = _con()
    t = db.read_sql(con, "SELECT season, team_abbr, w_pct, net_rating, "
                         + ", ".join(FOUR_FACTORS) + " FROM team_season_full").dropna()
    con.close()
    if len(t) < 30:
        return None
    X = t[FOUR_FACTORS]
    Xz = (X - X.mean()) / X.std()
    model = LinearRegression().fit(Xz, t["w_pct"])
    pred = model.predict(Xz)
    ss_res = ((t["w_pct"] - pred) ** 2).sum()
    ss_tot = ((t["w_pct"] - t["w_pct"].mean()) ** 2).sum()
    corr = {c: float(t[c].corr(t["w_pct"])) for c in FOUR_FACTORS + ["net_rating"]}
    return {
        "n": int(len(t)), "r2": float(1 - ss_res / ss_tot),
        "coefficients": {c: float(v) for c, v in zip(FOUR_FACTORS, model.coef_)},
        "correlations": corr,
        "net_rating_fit": np.polyfit(t["net_rating"], t["w_pct"] * 82, 1).tolist(),
    }
