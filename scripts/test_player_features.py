#!/usr/bin/env python3
"""Regression tests: never treat historical player stats as current or unknown as zero."""
from datetime import datetime, timedelta, timezone
import sqlite3
import unittest

from build_player_team_features import build, future_team_ids


class PlayerFeatureTests(unittest.TestCase):
    def test_current_season_and_missing_data(self):
        db = sqlite3.connect(":memory:")
        db.executescript("""
        CREATE TABLE players (
          league TEXT,team_id TEXT,player_id TEXT,name TEXT,position TEXT,
          age INTEGER,nationality TEXT,availability TEXT,market_value_eur REAL,
          market_value_source TEXT,market_value_asof TEXT,fetched_at TEXT);
        CREATE TABLE player_season_stats (
          league TEXT,player_id TEXT,season_year INTEGER,goals REAL,
          assists REAL,appearances REAL,shots REAL,shots_on_target REAL,
          yellow_cards REAL,red_cards REAL,interceptions REAL,
          tackles REAL,saves REAL,fetched_at TEXT);
        """)
        stamp = "2026-10-27T00:00:00+00:00"
        for pid in ("9", "10"):
            db.execute("INSERT INTO players VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("eng.1", "1", pid, "Player " + pid, "Forward", 25,
                        "England", "unknown", None, None, None, stamp))
        db.execute("INSERT INTO player_season_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("eng.1","9",2026,0,None,4,7,2,0,0,0,1,0,stamp))
        db.execute("INSERT INTO player_season_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("eng.1","10",2025,10,None,20,40,20,0,0,0,0,0,stamp))
        instant = datetime(2026,10,28,tzinfo=timezone.utc)
        data = {"events": [
            {"id": "past", "league": "eng.1", "date": (instant-timedelta(days=2)).isoformat(),
             "home": {"id":"3","name":"Old"}, "away":{"id":"4","name":"Old2"}},
            {"id": "future", "league": "eng.1", "date": (instant+timedelta(days=2)).isoformat(),
             "home": {"id":"1","name":"Home"}, "away":{"id":"2","name":"Away"}},
        ]}
        self.assertEqual(set(future_team_ids(data,instant)), {("eng.1","1"),("eng.1","2")})
        result = build(db,data,instant)
        home = result["teams"]["eng.1:1"]
        self.assertEqual(home["verifiedSeasonGoalPlayers"],1)
        self.assertEqual(home["verifiedSeasonGoalCoverage"],0.5)
        self.assertEqual(home["verifiedSeasonGoalsSum"],0)
        self.assertIsNone(home["knownMarketValueEurSum"])
        self.assertIsNone(home["players"][0]["assists"])
        older = next(p for p in home["players"] if p["id"]=="10")
        self.assertIsNone(older["goals"])
        self.assertIsNone(older["seasonYear"])
        self.assertNotIn("eng.1:3",result["teams"])
        self.assertEqual(result["coverage"]["teamsWithSeasonGoals"],1)
        db.close()


if __name__ == "__main__":
    unittest.main()
