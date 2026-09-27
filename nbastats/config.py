"""Paths and season helpers shared by the pipeline and the web app."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "nba.db"

# NBA.com stats (nba_api) have full player/team/game-log coverage from 1996-97.
FIRST_NBACOM_SEASON = 1996
# Basketball Reference advanced tables go back much further; we stop at the 3-point era.
FIRST_BREF_SEASON = 1980
# Latest completed regular season as of the project start (Sept 2026).
LATEST_SEASON = "2025-26"


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
