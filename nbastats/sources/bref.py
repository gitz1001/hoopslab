"""Basketball Reference scraping. Pages are cached as HTML under data/raw/bref and fetched
no faster than one every 4 seconds (their published limit is 20 requests/minute)."""
import re
import time
import unicodedata

import pandas as pd
import requests
from bs4 import BeautifulSoup, Comment

from ..config import RAW_DIR, bref_year

CACHE = RAW_DIR / "bref"
BASE = "https://www.basketball-reference.com"
PAUSE = 4.0
REFRESH = False
_last_call = 0.0
_session = requests.Session()
_session.headers["User-Agent"] = "Mozilla/5.0 (personal NBA stats research project)"


def _get(path: str) -> str:
    global _last_call
    local = CACHE / path.strip("/").replace("/", "_")
    if local.exists() and not REFRESH:
        return local.read_text(encoding="utf-8")
    CACHE.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        wait = PAUSE - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
        r = _session.get(BASE + path, timeout=60)
        if r.status_code == 200:
            text = r.content.decode("utf-8", errors="replace")
            local.write_text(text, encoding="utf-8")
            return text
        if r.status_code == 404:
            return ""
        # 429 means we were rate limited: back off hard
        time.sleep(60 if r.status_code == 429 else 5 * 2**attempt)
    raise RuntimeError(f"Basketball Reference fetch failed: {path}")


def _table(html: str, table_id: str) -> pd.DataFrame:
    """Parse a stats table by id, including ones hidden inside HTML comments."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id=table_id)
    if table is None:
        for c in soup.find_all(string=lambda s: isinstance(s, Comment) and table_id in s):
            table = BeautifulSoup(c, "lxml").find("table", id=table_id)
            if table is not None:
                break
    if table is None:
        return pd.DataFrame()
    rows = []
    for tr in table.find("tbody").find_all("tr"):
        if "thead" in (tr.get("class") or []):
            continue
        row = {}
        for cell in tr.find_all(["th", "td"]):
            stat = cell.get("data-stat")
            row[stat] = cell.get_text(strip=True)
            if stat in ("name_display", "player") and cell.get("data-append-csv"):
                row["bref_id"] = cell["data-append-csv"]
        if row.get("bref_id"):
            rows.append(row)
    return pd.DataFrame(rows)


def _numeric(df: pd.DataFrame, skip=("bref_id", "name_display", "team_name_abbr", "pos", "awards")):
    for c in df.columns:
        if c not in skip:
            df[c] = pd.to_numeric(df[c].replace("", None), errors="coerce")
    return df


def _one_row_per_player(df: pd.DataFrame) -> pd.DataFrame:
    """Traded players get a combined row (2TM/3TM/TOT) first, then one per team.
    Keep the combined row and remember the teams they played for."""
    teams = (df[~df["team_name_abbr"].str.match(r"^(\dTM|TOT)$")]
             .groupby("bref_id")["team_name_abbr"].agg(lambda s: ",".join(s)))
    df = df.drop_duplicates("bref_id", keep="first").copy()
    df["teams"] = df["bref_id"].map(teams).fillna(df["team_name_abbr"])
    return df


def advanced(season: str) -> pd.DataFrame:
    html = _get(f"/leagues/NBA_{bref_year(season)}_advanced.html")
    df = _table(html, "advanced")
    if df.empty:
        return df
    df = _one_row_per_player(_numeric(df))
    keep = ["bref_id", "name_display", "pos", "age", "teams", "games", "games_started", "mp",
            "per", "stl_pct", "blk_pct", "ows", "dws", "ws", "ws_per_48", "obpm", "dbpm",
            "bpm", "vorp", "awards"]
    df = df[[c for c in keep if c in df.columns]].rename(columns={"name_display": "bref_name"})
    df.insert(0, "season", season)
    return df


def totals(season: str) -> pd.DataFrame:
    """Season totals; used for seasons before NBA.com coverage."""
    html = _get(f"/leagues/NBA_{bref_year(season)}_totals.html")
    df = _table(html, "totals_stats")
    if df.empty:
        return df
    df = _one_row_per_player(_numeric(df))
    df = df.drop(columns=[c for c in ("ranker", "awards") if c in df.columns])
    df = df.rename(columns={"name_display": "bref_name"})
    df.insert(0, "season", season)
    return df


# ---------------------------------------------------------------- name matching

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z ]", "", s.lower().replace("-", " "))
    s = _SUFFIX.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def match_ids(bref: pd.DataFrame, nba: pd.DataFrame) -> pd.DataFrame:
    """Attach NBA.com player_id to Basketball Reference rows for the same season.
    nba needs columns player_id, name. Tries exact normalized name, then last name + first
    initial, which handles nicknames like 'Nic' vs 'Nicolas'."""
    nba = nba.copy()
    nba["key"] = nba["name"].map(norm_name)
    exact = dict(zip(nba["key"], nba["player_id"]))
    nba["loose"] = nba["key"].map(lambda k: (k.split(" ")[-1], k[:1]))
    loose = nba.groupby("loose")["player_id"].agg(list)

    def find(n):
        k = norm_name(n)
        if k in exact:
            return exact[k]
        cands = loose.get((k.split(" ")[-1], k[:1]), [])
        return cands[0] if len(cands) == 1 else None

    out = bref.copy()
    out["player_id"] = out["bref_name"].map(find)
    return out
