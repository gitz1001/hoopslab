"""NBA.com stats via nba_api. Every raw response is cached as JSON under data/raw/nbacom
so re-running the pipeline costs no network calls unless --refresh is passed."""
import json
import time

import pandas as pd
from nba_api.stats.endpoints import (
    alltimeleadersgrids,
    commonallplayers,
    drafthistory,
    leaguedashplayerbiostats,
    leaguedashplayerclutch,
    leaguedashlineups,
    leaguedashplayershotlocations,
    leaguehustlestatsplayer,
    franchisehistory,
    leaguedashplayerstats,
    leaguedashteamstats,
    leaguegamelog,
    leaguestandingsv3,
    teamplayeronoffsummary,
)
from nba_api.stats.static import teams as static_teams

from ..config import RAW_DIR

CACHE = RAW_DIR / "nbacom"
PAUSE = 0.7  # seconds between live calls; stats.nba.com throttles bursts
REFRESH = False
_last_call = 0.0


def _headers(hdr) -> list[str]:
    """Most endpoints have flat headers; shot-location endpoints nest them by zone."""
    if not hdr or not isinstance(hdr[0], dict):
        return hdr
    cats = next(h for h in hdr if h["name"] == "SHOT_CATEGORY")
    cols = next(h for h in hdr if h["name"] == "columns")["columnNames"]
    skip, span = cats["columnsToSkip"], cats["columnSpan"]
    zone = {"Restricted Area": "ra", "In The Paint (Non-RA)": "paint", "Mid-Range": "mid",
            "Left Corner 3": "lc3", "Right Corner 3": "rc3", "Above the Break 3": "atb3",
            "Backcourt": "back", "Corner 3": "c3"}
    names = cols[:skip]
    for cat in cats["columnNames"]:
        names += [f"{zone.get(cat, cat)}_{c}" for c in cols[skip: skip + span]]
    return names


def _frames(payload: dict) -> list[pd.DataFrame]:
    sets = payload.get("resultSets") or payload.get("resultSet")
    if isinstance(sets, dict):
        sets = [sets]
    return [pd.DataFrame(s["rowSet"], columns=_headers(s["headers"])[: len(s["rowSet"][0])]
                         if s["rowSet"] else _headers(s["headers"])) for s in sets]


def fetch(key: str, endpoint, **params) -> list[pd.DataFrame]:
    """Call an nba_api endpoint class (cached by `key`) and return its result sets."""
    global _last_call
    path = CACHE / f"{key}.json"
    if path.exists() and not REFRESH:
        return _frames(json.loads(path.read_text(encoding="utf-8")))
    CACHE.mkdir(parents=True, exist_ok=True)
    err = None
    for attempt in range(4):
        wait = PAUSE - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            _last_call = time.time()
            payload = endpoint(timeout=60, **params).get_dict()
            path.write_text(json.dumps(payload), encoding="utf-8")
            return _frames(payload)
        except Exception as e:  # network hiccups and throttling are common
            err = e
            time.sleep(3 * 2**attempt)
    raise RuntimeError(f"nba_api call failed for {key}: {err}")


def _slug(season_type: str) -> str:
    return "po" if season_type == "Playoffs" else "rs"


# ---------------------------------------------------------------- reference data

def teams() -> pd.DataFrame:
    df = pd.DataFrame(static_teams.get_teams())
    return df.rename(columns={"id": "team_id", "abbreviation": "abbr", "full_name": "name"})


def all_players() -> pd.DataFrame:
    df = fetch("commonallplayers", commonallplayers.CommonAllPlayers, is_only_current_season=0)[0]
    df = df.rename(columns={"PERSON_ID": "player_id", "DISPLAY_FIRST_LAST": "name",
                            "ROSTERSTATUS": "is_active", "FROM_YEAR": "from_year",
                            "TO_YEAR": "to_year"})
    df = df[["player_id", "name", "is_active", "from_year", "to_year"]].copy()
    df["from_year"] = pd.to_numeric(df["from_year"], errors="coerce")
    df["to_year"] = pd.to_numeric(df["to_year"], errors="coerce")
    return df


def franchise_history() -> pd.DataFrame:
    df = fetch("franchisehistory", franchisehistory.FranchiseHistory)[0]
    return df


def alltime_leaders(topx: int = 50) -> pd.DataFrame:
    """Career leaders for every counting stat, in one long table."""
    frames = fetch(f"alltimeleaders_{topx}", alltimeleadersgrids.AllTimeLeadersGrids, topx=topx)
    out = []
    for df in frames:
        rank_col = next(c for c in df.columns if c.endswith("_RANK"))
        stat = rank_col[: -len("_RANK")]
        val_col = stat if stat in df.columns else df.columns[2]
        out.append(pd.DataFrame({
            "stat": stat.lower(),
            "rank": df[rank_col],
            "player_id": df["PLAYER_ID"],
            "name": df["PLAYER_NAME"],
            "value": df[val_col],
            "is_active": df.get("IS_ACTIVE_FLAG"),
        }))
    return pd.concat(out, ignore_index=True)


# ---------------------------------------------------------------- season data

def player_season(season: str, measure: str = "Base", season_type="Regular Season") -> pd.DataFrame:
    df = fetch(f"player_{measure.lower()}_{season}_{_slug(season_type)}",
               leaguedashplayerstats.LeagueDashPlayerStats,
               season=season, season_type_all_star=season_type,
               measure_type_detailed_defense=measure, per_mode_detailed="Totals")[0]
    df = df[[c for c in df.columns if not c.endswith("_RANK") and not c.startswith("sp_work")]]
    df.insert(0, "season", season)
    df.insert(1, "season_type", season_type)
    return df


def team_season(season: str, measure: str = "Base", season_type="Regular Season") -> pd.DataFrame:
    df = fetch(f"team_{measure.lower().replace(' ', '')}_{season}_{_slug(season_type)}",
               leaguedashteamstats.LeagueDashTeamStats,
               season=season, season_type_all_star=season_type,
               measure_type_detailed_defense=measure, per_mode_detailed="Totals")[0]
    df = df[[c for c in df.columns if not c.endswith("_RANK")]]
    df.insert(0, "season", season)
    df.insert(1, "season_type", season_type)
    return df


def standings(season: str) -> pd.DataFrame:
    df = fetch(f"standings_{season}", leaguestandingsv3.LeagueStandingsV3, season=season)[0]
    keep = ["TeamID", "TeamCity", "TeamName", "Conference", "Division", "PlayoffRank",
            "DivisionRank", "WINS", "LOSSES", "WinPCT", "LeagueRank", "Record", "HOME", "ROAD",
            "L10", "OT", "ThreePTSOrLess", "TenPTSOrMore", "LongWinStreak", "LongLossStreak",
            "strCurrentStreak", "ConferenceGamesBack", "ConferenceRecord", "DivisionRecord",
            "PointsPG", "OppPointsPG", "DiffPointsPG", "OppOver500", "vsEast", "vsWest",
            "ClinchIndicator"]
    df = df[[c for c in keep if c in df.columns]].rename(columns={"TeamID": "team_id"})
    df.insert(0, "season", season)
    return df


def game_logs(season: str, who: str = "P", season_type="Regular Season") -> pd.DataFrame:
    """Every player (who='P') or team (who='T') box score line for a season, in one call."""
    df = fetch(f"gamelog_{who}_{season}_{_slug(season_type)}", leaguegamelog.LeagueGameLog,
               season=season, season_type_all_star=season_type,
               player_or_team_abbreviation=who)[0]
    df = df.drop(columns=[c for c in ("VIDEO_AVAILABLE", "SEASON_ID", "FANTASY_PTS") if c in df])
    df.insert(0, "season", season)
    df.insert(1, "season_type", season_type)
    return df


def onoff(season: str, team_id: int) -> pd.DataFrame:
    """On/off court ratings for every player on one team."""
    frames = fetch(f"onoff_{season}_{team_id}", teamplayeronoffsummary.TeamPlayerOnOffSummary,
                   team_id=team_id, season=season)
    if len(frames) < 3 or frames[1].empty:
        return pd.DataFrame()
    cols = ["VS_PLAYER_ID", "GP", "MIN", "PLUS_MINUS", "OFF_RATING", "DEF_RATING", "NET_RATING"]
    on = frames[1][cols].rename(columns=lambda c: c if c == "VS_PLAYER_ID" else f"on_{c}")
    off = frames[2][cols].rename(columns=lambda c: c if c == "VS_PLAYER_ID" else f"off_{c}")
    df = on.merge(off, on="VS_PLAYER_ID", how="outer").rename(columns={"VS_PLAYER_ID": "player_id"})
    df["net_diff"] = df["on_NET_RATING"] - df["off_NET_RATING"]
    df["ortg_diff"] = df["on_OFF_RATING"] - df["off_OFF_RATING"]
    df["drtg_diff"] = df["on_DEF_RATING"] - df["off_DEF_RATING"]
    df.insert(0, "season", season)
    df.insert(1, "team_id", team_id)
    return df


def draft_history() -> pd.DataFrame:
    return fetch("drafthistory", drafthistory.DraftHistory)[0]


def player_bio(season: str) -> pd.DataFrame:
    df = fetch(f"bio_{season}", leaguedashplayerbiostats.LeagueDashPlayerBioStats, season=season)[0]
    keep = ["PLAYER_ID", "PLAYER_HEIGHT", "PLAYER_HEIGHT_INCHES", "PLAYER_WEIGHT", "COLLEGE",
            "COUNTRY", "DRAFT_YEAR", "DRAFT_ROUND", "DRAFT_NUMBER"]
    df = df[keep].copy()
    df.insert(0, "season", season)
    return df


def shot_locations(season: str) -> pd.DataFrame:
    """FGM/FGA by court zone (restricted area, paint, mid-range, corner 3, above-the-break 3)."""
    df = fetch(f"shotloc_{season}", leaguedashplayershotlocations.LeagueDashPlayerShotLocations,
               season=season, per_mode_detailed="Totals")[0]
    df = df[["PLAYER_ID"] + [c for c in df.columns if c[:3] in ("ra_", "pai", "mid", "c3_", "atb", "lc3", "rc3")]]
    df.insert(0, "season", season)
    return df


def clutch(season: str) -> pd.DataFrame:
    """Last 5 minutes, score within 5 points."""
    df = fetch(f"clutch_{season}", leaguedashplayerclutch.LeagueDashPlayerClutch, season=season,
               per_mode_detailed="Totals")[0]
    keep = ["PLAYER_ID", "GP", "MIN", "PTS", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "AST",
            "TOV", "PLUS_MINUS", "W", "L"]
    df = df[keep].rename(columns=lambda c: c if c == "PLAYER_ID" else f"CLUTCH_{c}")
    df.insert(0, "season", season)
    return df


def hustle(season: str) -> pd.DataFrame:
    """Hustle stats exist from 2015-16 on."""
    if int(season[:4]) < 2015:
        return pd.DataFrame()
    df = fetch(f"hustle_{season}", leaguehustlestatsplayer.LeagueHustleStatsPlayer, season=season,
               per_mode_time="Totals")[0]
    keep = ["PLAYER_ID", "CONTESTED_SHOTS", "CONTESTED_SHOTS_3PT", "DEFLECTIONS", "CHARGES_DRAWN",
            "SCREEN_ASSISTS", "SCREEN_AST_PTS", "LOOSE_BALLS_RECOVERED", "BOX_OUTS"]
    df = df[[c for c in keep if c in df.columns]]
    df.insert(0, "season", season)
    return df


def lineups(season: str, team_id: int) -> pd.DataFrame:
    """Every 5-man unit a team used (NBA.com caps league-wide queries at 2,000, so we ask
    team by team). Lineup data exists from 2007-08 on."""
    if int(season[:4]) < 2007:
        return pd.DataFrame()
    df = fetch(f"lineups_{season}_{team_id}", leaguedashlineups.LeagueDashLineups,
               season=season, team_id_nullable=team_id, group_quantity=5,
               measure_type_detailed_defense="Advanced", per_mode_detailed="Totals")[0]
    keep = ["GROUP_ID", "GROUP_NAME", "TEAM_ID", "TEAM_ABBREVIATION", "GP", "MIN", "OFF_RATING",
            "DEF_RATING", "NET_RATING", "PACE", "POSS", "EFG_PCT", "TS_PCT", "TM_TOV_PCT",
            "OREB_PCT", "AST_PCT"]
    df = df[[c for c in keep if c in df.columns]].copy()
    df.insert(0, "season", season)
    return df
