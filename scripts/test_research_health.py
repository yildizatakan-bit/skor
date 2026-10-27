#!/usr/bin/env python3
"""No-network research health regression tests."""
from datetime import datetime, timedelta, timezone
import unittest

from research_health import summarize


class ResearchHealthTests(unittest.TestCase):
    def test_flags_missing_future_matches_and_incomplete_sources(self):
        now = datetime(2026, 10, 28, tzinfo=timezone.utc)
        coverage = {"targetTeamEntries": 100, "successfulTeamEntries": 96,
                    "failedTeamEntries": 4}
        season = {"playerLeagueEntries": 500, "checkedEntries": 300,
                  "successfulSeasonEntries": 210}
        signals = {"frozenMatches": 13, "settledMatches": 0,
                   "classicFrozen": 12, "mlFrozen": 13}
        incremental = {"minimumTotal": 240,
                       "models": {"classic": {"eligibleSettled": 0, "ready": False},
                                  "ml": {"eligibleSettled": 0, "ready": False}}}
        report = summarize({"events": []}, coverage, season, signals,
                           incremental, {"holdout": {"matches": 438}}, now)
        self.assertEqual(report["rosters"]["coveragePct"], 96.0)
        self.assertEqual(report["playerSeasonStats"]["successPctOfChecked"], 70.0)
        self.assertIn("NO_FUTURE_FIXTURES_IN_MAIN_DATA", report["warnings"])
        self.assertIn("PLAYER_INCREMENT_NOT_READY", report["warnings"])
        self.assertEqual(report["prospective"]["frozenMatches"], 13)
        self.assertEqual(report["mlHistoricalHoldout"]["matches"], 438)
        future = {"events": [{"date": (now+timedelta(days=1)).isoformat(), "done": False}]}
        newer = summarize(future, coverage, season, signals, incremental, {}, now)
        self.assertEqual(newer["productionUpcomingMatches"], 1)
        self.assertNotIn("NO_FUTURE_FIXTURES_IN_MAIN_DATA", newer["warnings"])


if __name__ == "__main__":
    unittest.main()
