"""Tests for the game predictor and lineup builder: the pure math, plus the two API endpoints
run against a small synthetic database (no real data needed).

    python -m unittest tests.test_matchup -v
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from nbastats import matchup as m

TEAM = {"elo_end": 1505.0, "srs": 0.0, "off_rating": 114.0, "def_rating": 114.0, "pace": 99.0}


class GamePredictor(unittest.TestCase):
    def test_even_teams_only_home_court(self):
        r = m.predict_game(TEAM, TEAM, hca=2.5, lg_ortg=114.0)
        home_edge = (m.ELO_HOME / m.ELO_PER_POINT + 2.5) / 2
        self.assertAlmostEqual(r["spread"], home_edge)
        self.assertGreater(r["p_home"], 0.5)
        self.assertAlmostEqual(r["home_pts"] - r["away_pts"], r["spread"])
        self.assertAlmostEqual(r["total"], 99.0 * 228 / 100)
        self.assertAlmostEqual(sum(f["points"] for f in r["factors"]), r["spread"])

    def test_stronger_team_and_symmetry(self):
        good = {**TEAM, "elo_end": 1650.0, "srs": 6.0}
        a = m.predict_game(good, TEAM, 0.0, 114.0)
        b = m.predict_game(TEAM, good, 0.0, 114.0)
        strength = (145 / m.ELO_PER_POINT + 6) / 2
        self.assertAlmostEqual(a["factors"][0]["points"], strength)
        self.assertAlmostEqual(b["factors"][0]["points"], -strength)

    def test_rest_and_injuries_move_the_line(self):
        fx = m.rest_effects([{"r": 0, "margin": -1.8}, {"r": 1, "margin": 0.2}, {"r": 2, "margin": 0.6}])
        self.assertAlmostEqual(fx[0], -2.0)
        base = m.predict_game(TEAM, TEAM, 2.5, 114.0)
        tired = m.predict_game(TEAM, TEAM, 2.5, 114.0, rest=(0, 2), rest_fx=fx)
        self.assertAlmostEqual(tired["spread"] - base["spread"], -2.0 - (0.6 - 0.2))
        star = {"impact": 6.0, "min_pg": 36.0}
        hurt = m.predict_game(TEAM, TEAM, 2.5, 114.0, home_out=[star])
        cost = (6.0 + 2.0) * 36 / 48 * 99 / 100
        self.assertAlmostEqual(base["spread"] - hurt["spread"], cost)
        self.assertEqual(m.missing_cost([{"impact": None, "min_pg": 30}]), 0)


def player(pid, o, d, **kw):
    base = {"player_id": pid, "o_impact": o, "d_impact": d, "fg3_pct": 0.37, "fg3a_p100": 8,
            "blk_p100": 0.5, "height_in": 78, "ast_pct": 0.15, "usg_pct": 0.2, "dreb_pct": 0.15}
    return {**base, **kw}


class LineupBuilder(unittest.TestCase):
    def test_rating_and_flags(self):
        five = [player(i, 1.0, 0.5) for i in range(5)]
        r = m.rate_lineup(five)
        self.assertAlmostEqual(r["net"], 7.5)
        texts = " ".join(f["text"] for f in r["flags"])
        self.assertIn("Rim protection", texts)
        self.assertIn("Creation", texts)
        self.assertIn("5 volume 3-point", texts)
        five[0].update(blk_p100=3.0, ast_pct=0.3)
        self.assertNotIn("Rim protection", " ".join(f["text"] for f in m.rate_lineup(five)["flags"]))

    def test_optimizer(self):
        pool = [player(i, i * 0.5, -i * 0.2) for i in range(8)]
        best = m.best_lineups(pool, "offense", top=1)[0]
        self.assertEqual(sorted(best["players"]), [3, 4, 5, 6, 7])
        best_d = m.best_lineups(pool, "defense", top=1)[0]
        self.assertEqual(sorted(best_d["players"]), [0, 1, 2, 3, 4])
        forced = m.best_lineups(pool, "offense", top=3, must=[0])
        self.assertTrue(all(0 in b["players"] for b in forced))

    def test_backtest(self):
        impact, lineups = {}, []
        for g in range(40):
            ids = [g * 5 + k for k in range(5)]
            for i in ids:
                impact[(i, "2023-24")] = (g * 0.1, 0.0)
            lineups.append({"season": "2024-25", "group_id": "-" + "-".join(map(str, ids)) + "-",
                            "min": 300, "net_rating": g * 0.5})
        r = m.backtest_lineups(lineups, impact)
        self.assertEqual(r["n"], 40)
        self.assertAlmostEqual(r["r"], 1.0)
        self.assertAlmostEqual(r["slope"], 1.0)
        self.assertEqual(m.prev_season("2000-01"), "1999-00")


def build_fake_db(path):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE team_season_full (season, team_id, team_abbr, team_name, conference,
        elo_end, srs, net_rating, off_rating, def_rating, pace, w, l)""")
    con.executemany("INSERT INTO team_season_full VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("2025-26", 1, "AAA", "Alpha", "East", 1600, 5.0, 6.0, 118, 112, 99, 55, 27),
        ("2025-26", 2, "BBB", "Bravo", "West", 1450, -3.0, -3.5, 112, 115.5, 101, 35, 47)])
    con.execute("CREATE TABLE team_srs (season, team_id, srs, mov, sos, hca)")
    con.execute("INSERT INTO team_srs VALUES ('2025-26', 1, 5, 6, 0, 2.4)")
    cols = ["player_id", "player_name", "season", "team_id", "team_abbreviation", "age", "pos",
            "gp", "min", "min_pg", "impact", "o_impact", "d_impact", "fg3_pct", "fg3a_p100",
            "blk_p100", "height_in", "ast_pct", "usg_pct", "dreb_pct"]
    con.execute(f"CREATE TABLE player_season_full ({', '.join(cols)})")
    rows = []
    for season in ("2024-25", "2025-26"):
        for tid in (1, 2):
            for k in range(9):
                pid = tid * 100 + k
                o, d = (3 - k * 0.5) * (1 if tid == 1 else 0.5), 0.3 * (k % 3)
                rows.append((pid, f"P{pid}", season, tid, "X", 25, "G", 70, 2400 - k * 150,
                             34 - k * 2, o + d, o, d, 0.30 + k * 0.01, 4 + k, k * 0.4,
                             76 + k, 0.1 + 0.03 * (k % 4), 0.2, 0.12 + 0.01 * k))
    con.executemany(f"INSERT INTO player_season_full VALUES ({','.join('?' * len(cols))})", rows)
    con.execute("CREATE TABLE lineups (season, team_id, team_abbreviation, group_id, group_name, gp, min, poss, off_rating, def_rating, net_rating, pace, efg_pct, ts_pct)")
    lu = []
    for i in range(30):
        ids = [100 + (i + j) % 9 for j in range(5)]
        lu.append(("2025-26", 1, "AAA", "-" + "-".join(map(str, ids)) + "-", "x", 20, 200 + i, 400,
                   115, 110, (i % 7) - 2.0, 99, 0.55, 0.58))
    con.executemany("INSERT INTO lineups VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", lu)
    con.commit()
    con.close()


SCRIPT = r"""
import json, sys
from app.server import app
c = app.test_client()
out = {}
for k, url in json.loads(sys.argv[1]).items():
    r = c.get(url)
    out[k] = [r.status_code, json.loads(r.data)]
print(json.dumps(out))
"""


class Endpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import flask  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("Flask not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        db = Path(cls.tmp.name) / "fake.db"
        build_fake_db(db)
        urls = {
            "predict": "/api/analysis/predict?season=2025-26&home=1&away=2",
            "predict_out": "/api/analysis/predict?season=2025-26&home=1&away=2&out=100,101&home_rest=0&away_rest=2",
            "builder": "/api/analysis/lineup_builder?season=2025-26&team=1&objective=offense&players=100,101,102,103,104",
            "backtest": "/api/analysis/lineup_backtest",
        }
        env = {**os.environ, "NBA_DB_PATH": str(db)}
        root = Path(__file__).resolve().parents[1]
        res = subprocess.run([sys.executable, "-c", SCRIPT, json.dumps(urls)], cwd=root, env=env,
                             capture_output=True, text=True, check=True)
        cls.r = json.loads(res.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_predict(self):
        code, d = self.r["predict"]
        self.assertEqual(code, 200, d)
        b = d["blend"]
        self.assertGreater(b["p_home"], 0.7)
        self.assertGreater(b["home_pts"], b["away_pts"])
        self.assertTrue(0 < b["series"] < 1)
        self.assertEqual(len(d["roster"]), 18)

    def test_predict_with_injuries_and_rest(self):
        base = self.r["predict"][1]["blend"]["spread"]
        code, d = self.r["predict_out"]
        self.assertEqual(code, 200, d)
        self.assertLess(d["blend"]["spread"], base)
        self.assertEqual(d["out"], [100, 101])

    def test_builder(self):
        code, d = self.r["builder"]
        self.assertEqual(code, 200, d)
        self.assertEqual(len(d["pool"]), 9)
        self.assertIsNotNone(d["rating"])
        self.assertEqual(sorted(d["best"][0]["players"]), [100, 101, 102, 103, 104])
        self.assertEqual(len(d["best"]), 5)

    def test_backtest_endpoint(self):
        code, d = self.r["backtest"]
        self.assertEqual(code, 200, d)
        self.assertEqual(d["n"], 30)


if __name__ == "__main__":
    unittest.main()
