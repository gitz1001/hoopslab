"""Thin SQLite layer. Tables are created from DataFrames; every load is keyed by season
so re-running a season replaces its rows instead of duplicating them."""
import sqlite3

import pandas as pd

from .config import DB_PATH, DATA_DIR


def connect(path=DB_PATH) -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def table_exists(con, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)
    ).fetchone() is not None


def _sync_columns(con, table: str, df: pd.DataFrame):
    """Add any columns the DataFrame has that the existing table lacks."""
    have = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
    for col, dtype in df.dtypes.items():
        if col in have:
            continue
        sqltype = "REAL" if dtype.kind == "f" else "INTEGER" if dtype.kind in "iub" else "TEXT"
        con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {sqltype}')


def replace_rows(con, table: str, df: pd.DataFrame, where: dict | None = None):
    """Delete rows matching `where` (e.g. {'season': '2025-26'}) then append df.
    With where=None the whole table is replaced."""
    if df is None:
        return
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    if where is None or not table_exists(con, table):
        if where is None:
            con.execute(f'DROP TABLE IF EXISTS "{table}"')
        df.to_sql(table, con, if_exists="append", index=False)
    else:
        _sync_columns(con, table, df)
        clause = " AND ".join(f'"{k}"=?' for k in where)
        con.execute(f'DELETE FROM "{table}" WHERE {clause}', tuple(where.values()))
        df.to_sql(table, con, if_exists="append", index=False)
    con.commit()


def read_sql(con, sql: str, params=()) -> pd.DataFrame:
    return pd.read_sql_query(sql, con, params=params)


INDEXES = [
    ("player_game", "player_id, season"),
    ("player_game", "game_id"),
    ("team_game", "team_id, season"),
    ("player_season_full", "player_id"),
    ("player_season_full", "season"),
    ("team_season_full", "season"),
    ("player_onoff", "season, team_id"),
    ("bref_history", "bref_id"),
    ("bref_history", "bref_name"),
    ("draft_history", "person_id"),
    ("player_game", "triple_double, season_type"),
] + [("player_game", f"season_type, {c}") for c in
     ("pts", "reb", "ast", "stl", "blk", "fg3m", "ftm", "game_score", "plus_minus")] + [
    ("team_game", f"season_type, {c}") for c in ("pts", "plus_minus", "fg3m")]


def ensure_indexes(con):
    for table, cols in INDEXES:
        if table_exists(con, table):
            name = f"ix_{table}_{cols.replace(', ', '_')}"
            con.execute(f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}" ({cols})')
    con.commit()
