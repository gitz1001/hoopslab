"""Smoke tests for the website API. They run against the database the app is configured to
use (data/nba.db, or NBA_DB_PATH) and are skipped when no database has been built.

    python -m unittest discover -s tests -v
"""
import json
import os
import unittest
from pathlib import Path

from nbastats.config import DB_PATH

HAVE_DB = Path(os.environ.get("NBA_DB_PATH", DB_PATH)).exists()


@unittest.skipUnless(HAVE_DB, "no database built yet (run python -m nbastats update)")
class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.server import app
        cls.c = app.test_client()
        cls.meta = cls.get(cls, "/api/meta")
        cls.season = cls.meta["seasons"][0]

    def get(self, url, status=200):
        r = self.c.get(url)
        assert r.status_code == status, f"{url} -> {r.status_code}: {r.data[:200]}"
        return json.loads(r.data)

    def test_health(self):
        self.assertEqual(self.get("/healthz")["status"], "ok")

    def test_pages_have_data(self):
        s = self.season
        self.assertGreater(len(self.get(f"/api/players?season={s}")["rows"]), 300)
        self.assertEqual(len(self.get(f"/api/teams?season={s}")["rows"]), 30)
        self.assertEqual(len(self.get(f"/api/standings?season={s}")["rows"]), 30)
        self.assertTrue(self.get(f"/api/dashboard?season={s}")["leaders"]["pts_pg"])
        self.assertTrue(self.get("/api/records")["single_game"]["pts"])
        self.assertTrue(self.get("/api/league")["rows"])

    def test_player_and_team_pages(self):
        p = self.get(f"/api/players?season={self.season}&min_gp=50")["rows"][0]
        d = self.get(f"/api/player/{p['player_id']}")
        self.assertTrue(d["seasons"])
        self.assertTrue(d["games"])
        x = self.get(f"/api/player/{p['player_id']}/extras?season={self.season}")
        self.assertTrue(x["similar"])
        self.assertIsNotNone(x["percentiles"])
        t = self.get(f"/api/teams?season={self.season}")["rows"][0]
        self.assertTrue(self.get(f"/api/team/{t['team_id']}?season={self.season}")["roster"])

    def test_analysis_endpoints(self):
        s = self.season
        for url in [f"/api/analysis/clusters?season={s}", f"/api/analysis/projections?season={s}",
                    "/api/analysis/aging?stat=bpm", "/api/analysis/winmodel",
                    f"/api/analysis/scatter?season={s}&x=usg_pct&y=ts_pct",
                    f"/api/analysis/impact?season={s}", f"/api/analysis/power?season={s}",
                    "/api/analysis/situational", "/api/analysis/stickiness",
                    f"/api/analysis/lineups?season={s}", "/api/analysis/draft",
                    f"/api/analysis/origins?season={s}"]:
            with self.subTest(url=url):
                self.get(url)

    def test_predictions(self):
        m = self.get("/api/predict/meta")
        if not m["available"]:
            self.skipTest("no predictions built")
        teams = self.get("/api/predict/teams")["rows"]
        self.assertEqual(len(teams), 30)
        self.assertAlmostEqual(sum(t["p_title"] for t in teams), 1.0, places=2)
        self.assertAlmostEqual(sum(t["wins_mean"] for t in teams), 1230, delta=2)

    def test_bad_input_is_rejected(self):
        self.get("/api/leaders?stat=drop_table", status=400)
        self.get("/api/nope", status=404)

    def test_compression_and_headers(self):
        r = self.c.get(f"/api/players?season={self.season}", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r.headers.get("Content-Encoding"), "gzip")
        self.assertIn("max-age", r.headers.get("Cache-Control", ""))
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")


class UnitTests(unittest.TestCase):
    def test_season_helpers(self):
        from datetime import date
        from nbastats.config import current_season, season_in_progress, season_label
        self.assertEqual(season_label(1996), "1996-97")
        self.assertEqual(current_season(date(2026, 9, 27)), "2025-26")
        self.assertEqual(current_season(date(2026, 11, 1)), "2026-27")
        self.assertTrue(season_in_progress("2026-27", date(2027, 3, 1)))
        self.assertFalse(season_in_progress("2025-26", date(2026, 9, 27)))

    def test_series_probability(self):
        import numpy as np
        from nbastats.predict import _series_win_prob
        even = _series_win_prob(np.array([0.5]), np.array([0.5]))[0]
        self.assertAlmostEqual(even, 0.5, places=6)
        self.assertGreater(_series_win_prob(np.array([0.6]), np.array([0.5]))[0], 0.5)

    def test_game_score(self):
        import pandas as pd
        from nbastats.metrics import game_score
        row = pd.DataFrame([dict(pts=30, fgm=10, fga=20, fta=10, ftm=8, oreb=2, dreb=8, stl=2,
                                 ast=5, blk=1, pf=3, tov=3)])
        self.assertAlmostEqual(float(game_score(row).iloc[0]), 25.0, places=1)  # hand-computed


if __name__ == "__main__":
    unittest.main()
