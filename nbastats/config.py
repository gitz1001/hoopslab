"""Paths and season helpers shared by the pipeline and the web app."""
import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("NBA_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
# The pipeline writes nba.db; the website can serve a slim read-only copy (see `export`).
DB_PATH = Path(os.environ.get("NBA_DB_PATH", DATA_DIR / "nba.db"))
SERVE_DB_PATH = DATA_DIR / "nba_serve.db"
# NBA_READONLY=1 opens the database read-only and immutable (safe for production serving).
READONLY = os.environ.get("NBA_READONLY", "0") == "1"

# NBA.com stats (nba_api) have full player/team/game-log coverage from 1996-97.
FIRST_NBACOM_SEASON = 1996
# Basketball Reference advanced tables go back much further; we stop at the 3-point era.
FIRST_BREF_SEASON = 1980


def current_season(today: date | None = None) -> str:
    """The season in progress (or most recently finished before October's tip-off)."""
    today = today or date.today()
    start = today.year if today.month >= 10 else today.year - 1
    return season_label(start)


def season_in_progress(label: str, today: date | None = None) -> bool:
    """True from October of the season's first year through June of its second."""
    today = today or date.today()
    y = season_start(label)
    return date(y, 10, 1) <= today <= date(y + 1, 6, 30)


def season_label(start_year: int) -> str:
    """1996 -> '1996-97'."""
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def season_start(label: str) -> int:
    """'1996-97' -> 1996."""
    return int(label[:4])


def bref_year(label: str) -> int:
    """Basketball Reference names seasons by the year they end: '2025-26' -> 2026."""
    return season_start(label) + 1


def season_range(first: str, last: str) -> list[str]:
    return [season_label(y) for y in range(season_start(first), season_start(last) + 1)]


# The season `update` targets by default: the one in progress, or the last one played.
LATEST_SEASON = current_season()
