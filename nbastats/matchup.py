"""Matchup predictor and lineup builder: the pure math, kept free of the database so it can be
unit tested on its own. app/server.py feeds it rows from the database.

Game predictor
    spread = average of the Elo spread and the SRS spread (both include home court)
             + rest adjustment (measured from every regular-season game we store)
             + availability adjustment (impact of players ruled out)
    win probability = P(margin > 0) with margins ~ Normal(spread, 12.5)
    score = league-relative offense x opponent defense x expected pace, split by the spread

Lineup builder
    lineup net rating = sum of the five players' offensive + defensive impact (prior-informed
    RAPM, points per 100 possessions relative to league average). Fit flags are heuristics and
    are shown beside the number, not folded into it, because they are not validated.
"""
from itertools import combinations
from statistics import NormalDist

GAME_SD = 12.5          # SD of NBA game margins around the expected spread
ELO_PER_POINT = 28.0    # FiveThirtyEight: ~28 Elo points per point of spread
ELO_HOME = 100.0        # home edge built into our Elo
REPLACEMENT = -2.0      # impact of a freely available player (matches models.REPLACEMENT)


# ---------------------------------------------------------------------------- game predictor

def rest_effects(rows):
    """rows: dicts with rest (days, 3 = 3+) and margin (avg plus-minus). Returns the effect of
    each rest bucket relative to one day off, in points."""
    m = {int(r["r"]): float(r["margin"]) for r in rows if r.get("r") is not None}
    base = m.get(1, 0.0)
    return {k: v - base for k, v in m.items()}


def missing_cost(players, pace=100.0):
    """Points per game a team loses when these players sit: each player's impact over
    replacement, times his share of the team's minutes on the floor, scaled to game pace."""
    cost = 0.0
    for p in players:
        imp, mpg = p.get("impact"), p.get("min_pg")
        if imp is None or mpg is None:
            continue
        cost += (imp - REPLACEMENT) * min(mpg, 48.0) / 48.0
    return cost * pace / 100.0


def predict_game(home, away, hca, lg_ortg, rest=None, rest_fx=None, home_out=(), away_out=()):
    """home/away: dicts with elo_end, srs, off_rating, def_rating, pace.
    rest: (home_days, away_days) or None. Returns spread, probability, score and factors."""
    elo_diff = home["elo_end"] - away["elo_end"]
    elo_strength = elo_diff / ELO_PER_POINT
    srs_strength = home["srs"] - away["srs"]
    strength = (elo_strength + srs_strength) / 2
    home_edge = (ELO_HOME / ELO_PER_POINT + hca) / 2

    rest_adj = 0.0
    if rest and rest_fx:
        cap = lambda d: max(0, min(3, int(d)))
        rest_adj = rest_fx.get(cap(rest[0]), 0.0) - rest_fx.get(cap(rest[1]), 0.0)

    pace = (home["pace"] + away["pace"]) / 2
    avail_adj = missing_cost(away_out, pace) - missing_cost(home_out, pace)

    spread = strength + home_edge + rest_adj + avail_adj
    p_home = NormalDist(0, GAME_SD).cdf(spread)

    h_ortg = home["off_rating"] + away["def_rating"] - lg_ortg
    a_ortg = away["off_rating"] + home["def_rating"] - lg_ortg
    total = pace * (h_ortg + a_ortg) / 100
    factors = [
        {"factor": "Team strength (Elo + SRS)", "points": strength},
        {"factor": "Home court", "points": home_edge},
        {"factor": "Rest", "points": rest_adj},
        {"factor": "Missing players", "points": avail_adj},
    ]
    return {
        "spread": spread, "p_home": p_home,
        "home_pts": total / 2 + spread / 2, "away_pts": total / 2 - spread / 2,
        "total": total, "pace": pace,
        "models": {"elo_spread": elo_strength + ELO_HOME / ELO_PER_POINT,
                   "srs_spread": srs_strength + hca},
        "factors": factors,
    }


# ---------------------------------------------------------------------------- lineup builder

def _num(p, k, default=0.0):
    v = p.get(k)
    return default if v is None else float(v)


def rate_lineup(players):
    """Sum of offensive and defensive impact, plus heuristic fit flags."""
    o = sum(_num(p, "o_impact", REPLACEMENT / 2) for p in players)
    d = sum(_num(p, "d_impact", REPLACEMENT / 2) for p in players)
    return {"o": o, "d": d, "net": o + d, "flags": fit_flags(players)}


def fit_flags(players):
    flags = []
    spacers = [p for p in players if _num(p, "fg3_pct") >= 0.35 and _num(p, "fg3a_p100") >= 5]
    if len(spacers) < 2:
        flags.append({"level": "warn", "text": f"Spacing: only {len(spacers)} volume 3-point shooter(s) (35%+ on 5+ attempts per 100)"})
    elif len(spacers) >= 4:
        flags.append({"level": "good", "text": f"Spacing: {len(spacers)} volume 3-point shooters"})
    if max(_num(p, "blk_p100") for p in players) < 2.0 and max(_num(p, "height_in") for p in players) < 82:
        flags.append({"level": "warn", "text": "Rim protection: no one blocks 2+ shots per 100 or stands 6'10\"+"})
    if max(_num(p, "ast_pct") for p in players) < 0.20:
        flags.append({"level": "warn", "text": "Creation: no one assists on 20%+ of teammates' baskets"})
    usg = sum(_num(p, "usg_pct", 0.2) for p in players)
    if usg > 1.15:
        flags.append({"level": "warn", "text": f"Shots: combined usage {usg:.0%}, more than one ball to go around"})
    elif usg < 0.85:
        flags.append({"level": "warn", "text": f"Shots: combined usage {usg:.0%}, someone has to take shots they don't usually take"})
    if sum(_num(p, "dreb_pct") for p in players) < 0.65:
        flags.append({"level": "warn", "text": "Rebounding: low combined defensive rebound rate"})
    return flags


OBJECTIVES = {"net": lambda r: r["net"], "offense": lambda r: r["o"], "defense": lambda r: r["d"],
              "balanced": lambda r: r["net"] - 0.5 * sum(f["level"] == "warn" for f in r["flags"])}


def best_lineups(pool, objective="net", top=5, must=()):
    """Brute force every five-man group from the pool (pool ~13 players -> 1,287 groups).
    must: player ids that have to be in the lineup."""
    score = OBJECTIVES.get(objective, OBJECTIVES["net"])
    must = set(must)
    out = []
    for group in combinations(pool, 5):
        if must and not must <= {p["player_id"] for p in group}:
            continue
        r = rate_lineup(group)
        out.append((score(r), [p["player_id"] for p in group], r))
    out.sort(key=lambda x: -x[0])
    return [{"players": ids, **r} for _, ids, r in out[:top]]


# ---------------------------------------------------------------------------- validation

def lineup_ids(group_id):
    return [int(p) for p in str(group_id).strip("-").split("-") if p]


def prev_season(season):
    y = int(season[:4]) - 1
    return f"{y}-{str(y + 1)[2:]}"


def backtest_lineups(lineups, impact):
    """Out-of-sample check: predict each lineup's net rating from its players' PREVIOUS-season
    impact and compare with what it actually did. lineups: dicts with season, group_id, min,
    net_rating. impact: {(player_id, season): (o_impact, d_impact)}.
    Returns correlation, error vs a predict-zero baseline, and binned predicted vs actual."""
    pts = []
    for lu in lineups:
        ps = prev_season(lu["season"])
        vals = [impact.get((pid, ps)) for pid in lineup_ids(lu["group_id"])]
        if len(vals) != 5 or any(v is None for v in vals):
            continue
        pred = sum(o + d for o, d in vals)
        pts.append((pred, float(lu["net_rating"]), float(lu["min"])))
    n = len(pts)
    if n < 20:
        return {"n": n}
    w = [m for *_, m in pts]
    sw = sum(w)
    mp = sum(p * m for p, _, m in pts) / sw
    ma = sum(a * m for _, a, m in pts) / sw
    cov = sum(m * (p - mp) * (a - ma) for p, a, m in pts) / sw
    vp = sum(m * (p - mp) ** 2 for p, _, m in pts) / sw
    va = sum(m * (a - ma) ** 2 for _, a, m in pts) / sw
    rmse = (sum(m * (a - p) ** 2 for p, a, m in pts) / sw) ** 0.5
    rmse0 = (sum(m * a ** 2 for _, a, m in pts) / sw) ** 0.5
    bins = {}
    for p, a, m in pts:
        b = max(-10, min(10, round(p / 2.5) * 2.5))
        s = bins.setdefault(b, [0.0, 0.0, 0.0, 0])
        s[0] += p * m; s[1] += a * m; s[2] += m; s[3] += 1
    binned = [{"pred": s[0] / s[2], "actual": s[1] / s[2], "n": s[3]} for _, s in sorted(bins.items())]
    return {"n": n, "r": cov / (vp * va) ** 0.5 if vp and va else None, "rmse": rmse,
            "rmse_zero": rmse0, "slope": cov / vp if vp else None, "bins": binned}
