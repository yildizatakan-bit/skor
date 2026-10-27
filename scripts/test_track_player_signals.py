#!/usr/bin/env python3
"""Prospective player-signal regression tests, no network or external database."""
from datetime import datetime, timedelta, timezone
import copy
import unittest

from track_player_signals import freeze, process, settle, valid_probabilities

NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
STAMP = (NOW - timedelta(hours=1)).isoformat()
KICKOFF = (NOW + timedelta(hours=12)).isoformat()
CLASSIC = {"home": .45, "draw": .25, "away": .30}


def team(tid, goals):
    return {
        "teamId": tid, "rosterAsOf": STAMP, "seasonYear": 2026,
        "rosterPlayers": 5, "verifiedSeasonGoalPlayers": 4,
        "verifiedSeasonGoalsSum": goals,
        "players": [
            {"id": str(i), "goals": goals / 4, "seasonStatsAsOf": STAMP}
            for i in range(4)
        ] + [{"id": "unknown", "goals": None, "seasonStatsAsOf": None}],
    }


def inputs():
    event = {
        "id": "future123", "league": "eng.1", "date": KICKOFF,
        "done": False, "home": {"id": "1", "name": "Home"},
        "away": {"id": "2", "name": "Away"},
    }
    feed = {"generatedAt": STAMP,
            "teams": {"eng.1:1": team("1", 12), "eng.1:2": team("2", 6)}}
    data = {"events": [event], "analyses": {"future123": {"p": CLASSIC}}}
    return data, feed


class ProspectiveSignalsTests(unittest.TestCase):
    def test_freeze_only_before_kickoff_and_only_verified(self):
        data, feed = inputs()
        s = freeze(data["events"][0], feed, CLASSIC, None, NOW)
        self.assertEqual(s["knownSeasonGoalEdge"], 6)
        self.assertEqual(s["home"]["goalCoverage"], .8)
        self.assertEqual(s["probabilities"]["classic"], CLASSIC)
        self.assertNotIn("ml", s["probabilities"])
        self.assertIsNone(freeze(data["events"][0], feed, CLASSIC, None,
                                 NOW + timedelta(days=1)))
        bad = copy.deepcopy(feed)
        bad["teams"]["eng.1:1"]["players"][0]["seasonStatsAsOf"] = (
            NOW + timedelta(hours=1)).isoformat()
        self.assertIsNone(freeze(data["events"][0], bad, CLASSIC, None, NOW))
        bad = copy.deepcopy(feed)
        bad["teams"]["eng.1:1"]["verifiedSeasonGoalPlayers"] = 2
        self.assertIsNone(freeze(data["events"][0], bad, CLASSIC, None, NOW))
        bad = copy.deepcopy(feed)
        bad["teams"]["eng.1:1"]["verifiedSeasonGoalsSum"] = None
        self.assertIsNone(freeze(data["events"][0], bad, CLASSIC, None, NOW))

    def test_first_snapshot_immutable_and_settlement_separate(self):
        data, feed = inputs()
        ledger, report = process(data, feed, {}, {}, {}, NOW)
        self.assertEqual(report["frozenMatches"], 1)
        self.assertEqual(report["settledMatches"], 0)
        frozen = copy.deepcopy(ledger["snapshots"][0])
        # Later provider corrections must NOT change the original snapshot.
        changed = copy.deepcopy(feed)
        changed["teams"]["eng.1:1"]["verifiedSeasonGoalsSum"] = 100
        changed["teams"]["eng.1:1"]["players"] = [
            {"id": str(i), "goals": 25, "seasonStatsAsOf": STAMP}
            for i in range(4)
        ] + [{"id": "unknown", "goals": None, "seasonStatsAsOf": None}]
        finished = copy.deepcopy(data["events"][0])
        finished["done"] = True
        finished["home"]["score"] = 2
        finished["away"]["score"] = 1
        ledger2, report2 = process({"events": [finished]}, changed, ledger,
                                   {"future123": finished}, {}, NOW + timedelta(days=2))
        self.assertEqual(ledger2["snapshots"][0], frozen)
        self.assertEqual(ledger2["settlements"][0]["outcome"], "home")
        self.assertEqual(report2["settledMatches"], 1)
        ledger3, _ = process({"events": [finished]}, changed, ledger2,
                             {"future123": finished}, {}, NOW + timedelta(days=3))
        self.assertEqual(ledger3["settlements"], ledger2["settlements"])
        wrong = copy.deepcopy(finished)
        wrong["away"]["id"] = "999"
        self.assertIsNone(settle(frozen, wrong))

    def test_later_ml_forecast_is_separately_frozen_before_kickoff(self):
        data, feed = inputs()
        ledger, _ = process(data, feed, {}, {}, {}, NOW)
        first_snapshot = copy.deepcopy(ledger["snapshots"][0])
        forecast = {"future123": {
            "p": {"home": .51, "draw": .24, "away": .25},
            "recordedAt": (NOW + timedelta(hours=1)).isoformat(),
            "kickoff": KICKOFF,
        }}
        ledger2, report = process(data, feed, ledger, {}, forecast,
                                  NOW + timedelta(hours=2))
        self.assertEqual(ledger2["snapshots"][0], first_snapshot)
        self.assertEqual(len(ledger2["modelSnapshots"]), 1)
        self.assertEqual(report["mlFrozen"], 1)
        self.assertEqual(report["supplementalMLFrozen"], 1)
        ledger3, _ = process(data, feed, ledger2, {}, forecast,
                             NOW + timedelta(hours=3))
        self.assertEqual(ledger3["modelSnapshots"], ledger2["modelSnapshots"])
        finished = copy.deepcopy(data["events"][0])
        finished["done"] = True
        finished["home"]["score"] = 2
        finished["away"]["score"] = 1
        _, settled = process({"events": [finished]}, feed, ledger3,
                             {"future123": finished}, forecast,
                             NOW + timedelta(days=2))
        self.assertEqual(settled["pairedComparison"]["sameMatches"], 1)
        # A new forecast discovered after kickoff must never be frozen.
        old, _ = process(data, feed, {}, {}, {}, NOW)
        late, _ = process(data, feed, old, {}, forecast,
                          NOW + timedelta(days=2))
        self.assertEqual(late["modelSnapshots"], [])
        bad = copy.deepcopy(forecast)
        bad["future123"]["recordedAt"] = (
            NOW + timedelta(days=2)).isoformat()
        denied, _ = process(data, feed, old, {}, bad,
                            NOW + timedelta(hours=2))
        self.assertEqual(denied["modelSnapshots"], [])

    def test_probabilities_must_be_normalized(self):
        self.assertTrue(valid_probabilities(CLASSIC))
        self.assertFalse(valid_probabilities({"home": 1, "draw": 1, "away": 1}))
        self.assertFalse(valid_probabilities({"home": .6, "draw": .4, "away": None}))


if __name__ == "__main__":
    unittest.main()
