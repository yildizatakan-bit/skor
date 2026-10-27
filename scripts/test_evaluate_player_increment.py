#!/usr/bin/env python3
"""Ensure prospective-only training and chronological holdout for player impact."""
from datetime import datetime, timedelta, timezone
import copy
import unittest

from evaluate_player_increment import evaluate, sample_rows


def ledger(n=260):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    snapshots, settlements = [], []
    for i in range(n):
        kickoff = start + timedelta(days=i)
        before = kickoff - timedelta(hours=6)
        home_goals = 5 + i % 17
        away_goals = 5 + (i * 3) % 13
        outcome = ("home", "draw", "away")[i % 3]
        snapshots.append({
            "matchId": str(i), "kickoff": kickoff.isoformat(),
            "frozenAt": before.isoformat(),
            "playerFeedGeneratedAt": (before - timedelta(hours=1)).isoformat(),
            "home": {"knownSeasonGoals": home_goals, "goalCoverage": .92, "rosterPlayers": 25},
            "away": {"knownSeasonGoals": away_goals, "goalCoverage": .88, "rosterPlayers": 24},
            "probabilities": {
                "classic": {"home": .40 + (i % 3)*.02, "draw": .27, "away": .33 - (i % 3)*.02},
                "ml": {"home": .39 + (i % 3)*.02, "draw": .28, "away": .33 - (i % 3)*.02},
            },
        })
        settlements.append({"matchId": str(i), "outcome": outcome})
    return {"snapshots": snapshots, "settlements": settlements, "modelSnapshots": []}


class PlayerIncrementTests(unittest.TestCase):
    def test_minimum_sample_and_no_leakage(self):
        data = ledger(20)
        self.assertEqual(evaluate(data, "classic")["ready"], False)
        data["snapshots"][0]["frozenAt"] = (
            datetime(2026, 1, 2, tzinfo=timezone.utc)).isoformat()
        self.assertEqual(len(sample_rows(data, "classic")), 19)

    def test_holdout_is_trained_only_on_prior_games(self):
        data = ledger()
        for model in ("classic", "ml"):
            report = evaluate(data, model)
            self.assertTrue(report["ready"], report)
            self.assertEqual(report["eligibleSettled"], 260)
            self.assertGreaterEqual(report["trainMatches"], 180)
            self.assertGreaterEqual(report["testMatches"], 60)
            self.assertIsInstance(report["candidateBetter"], bool)
            self.assertEqual(report["deployment"], "NOT AUTOMATIC; requires separate review and approval")

    def test_supplementary_ml_requires_pre_kickoff_proof(self):
        data = ledger(2)
        data["snapshots"][0]["probabilities"].pop("ml")
        p = {"home": .4, "draw": .3, "away": .3}
        kickoff = data["snapshots"][0]["kickoff"]
        data["modelSnapshots"] = [{
            "matchId": "0", "model": "ml", "p": p, "kickoff": kickoff,
            "frozenAt": data["snapshots"][0]["frozenAt"],
            "sourceRecordedAt": data["snapshots"][0]["playerFeedGeneratedAt"],
        }]
        self.assertEqual(len(sample_rows(data, "ml")), 2)
        late = copy.deepcopy(data)
        late["modelSnapshots"][0]["sourceRecordedAt"] = (
            datetime(2026, 1, 2, tzinfo=timezone.utc)).isoformat()
        self.assertEqual(len(sample_rows(late, "ml")), 1)


if __name__ == "__main__":
    unittest.main()
