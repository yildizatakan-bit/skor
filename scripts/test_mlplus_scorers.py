import math
import unittest
from mlplus import scorer_predictions

class ScorerTests(unittest.TestCase):
    def test_season_performance_and_missing_players(self):
        players = [
            dict(id="1", name="Scorer", goals=10, appearances=20, minutes=1200, selected=True),
            dict(id="2", name="Zero goals", goals=0, appearances=20, selected=True),
            dict(id="3", name="Unknown", goals=None, appearances=20),
            dict(id="4", name="Bench", goals=10, appearances=20, selected=False),
            dict(id="5", name="Suspended", goals=10, appearances=20, availability="Suspended"),
        ]
        result = scorer_predictions([{"players":players, "selection":"minutes"}, {"players":[]}])
        rows = {p["id"]:p for p in result["teams"]["home"]}
        self.assertAlmostEqual(rows["1"]["probability"], 1-math.exp(-.5))
        self.assertEqual(rows["1"]["expectedMinutes"], 60)
        self.assertEqual(rows["1"]["goalsPer90"], .75)
        self.assertEqual(rows["2"]["probability"], 0)
        for key in ("3","4","5"):
            self.assertIsNone(rows[key]["probability"])
        self.assertEqual(result["teams"]["away"], [])

    def test_minutes_cap_and_invalid_values(self):
        result = scorer_predictions([{"players":[
            dict(id="1", goals=10, appearances=10, minutes=1200),
            dict(id="2", goals=float("nan"), appearances=10),
            dict(id="3", goals=10, appearances=0),
        ]}, {}])
        rows = {p["id"]:p for p in result["teams"]["home"]}
        self.assertEqual(rows["1"]["expectedMinutes"],90)
        self.assertAlmostEqual(rows["1"]["probability"],1-math.exp(-.75))
        self.assertIsNone(rows["2"]["probability"])
        self.assertIsNone(rows["3"]["probability"])

if __name__ == "__main__":
    unittest.main()
