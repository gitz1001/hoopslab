"""Command line entry point.

    python -m nbastats update                      # latest season
    python -m nbastats update --from 2015-16       # a range, through the latest season
    python -m nbastats update --seasons 2023-24 2024-25
    python -m nbastats history --from 1979-80 --to 1995-96   # Basketball Reference only
    python -m nbastats models --lineups            # Elo, SRS, RAPM, impact, WAR
    python -m nbastats predict --sims 10000        # next-season projections and simulation
"""
import argparse

from . import build, db
from .config import LATEST_SEASON, season_range
from .sources import bref, nbacom


def main():
    ap = argparse.ArgumentParser(prog="nbastats")
    sub = ap.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("update", help="fetch NBA.com + Basketball Reference data for seasons")
    up.add_argument("--seasons", nargs="*", help="explicit seasons like 2024-25")
    up.add_argument("--from", dest="first", help="first season of a range")
    up.add_argument("--to", dest="last", default=LATEST_SEASON, help="last season of a range")
    up.add_argument("--no-bref", action="store_true", help="skip Basketball Reference")
    up.add_argument("--no-onoff", action="store_true", help="skip on/off (30 calls per season)")
    up.add_argument("--no-playoffs", action="store_true")
    up.add_argument("--refresh", action="store_true", help="ignore cached raw responses")

    hi = sub.add_parser("history", help="pre-1996 seasons from Basketball Reference")
    hi.add_argument("--from", dest="first", default="1979-80")
    hi.add_argument("--to", dest="last", default="1995-96")

    sub.add_parser("reference", help="teams, players, franchise history, all-time leaders")

    mo = sub.add_parser("models", help="rebuild Elo, SRS, RAPM, impact, WAR (no fetching "
                                       "except lineups missing from the cache)")
    mo.add_argument("--lineups", action="store_true", help="(re)load lineups for all stored seasons")

    pr = sub.add_parser("predict", help="project players, simulate the season, predict awards")
    pr.add_argument("--season", help="target season, default the one after the latest stored")
    pr.add_argument("--sims", type=int, default=10000)

    a = ap.parse_args()
    if getattr(a, "refresh", False):
        nbacom.REFRESH = bref.REFRESH = True

    if a.cmd == "update":
        seasons = a.seasons or (season_range(a.first, a.last) if a.first else [a.last])
        build.run(seasons, use_bref=not a.no_bref, use_onoff=not a.no_onoff,
                  playoffs=not a.no_playoffs)
    elif a.cmd == "history":
        con = db.connect()
        build.update_history(con, season_range(a.first, a.last))
    elif a.cmd == "models":
        from . import models
        con = db.connect()
        seasons = None
        if a.lineups:
            seasons = db.read_sql(con, "SELECT DISTINCT season FROM player_season_full")["season"].tolist()
        models.run(con, seasons)
    elif a.cmd == "predict":
        from . import predict
        predict.run(a.season, a.sims)
    elif a.cmd == "reference":
        con = db.connect()
        build.update_reference(con)


if __name__ == "__main__":
    main()
