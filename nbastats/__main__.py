"""Command line entry point.

    python -m nbastats update                      # latest season
    python -m nbastats update --from 2015-16       # a range, through the latest season
    python -m nbastats update --seasons 2023-24 2024-25
    python -m nbastats history --from 1979-80 --to 1995-96   # Basketball Reference only
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
    elif a.cmd == "reference":
        con = db.connect()
        build.update_reference(con)


if __name__ == "__main__":
    main()
