"""Regression checks for player-stat features and kickoff cutoffs."""
from mlplus import player_vector, squad, stamp
import mlplus

mlplus.EXTRA_FIELDS = ("accuratePasses", "duelsWon", "penaltyKicksSaved")
side = {"players": [{"extraStats": {"accuratePasses": 200, "duelsWon": 30, "penaltyKicksSaved": 2}}]}
offset = len(mlplus.PLAYER_FIELDS)*2
assert player_vector(side)[offset:offset+6] == [200, 1, 30, 1, 2, 1]
assert player_vector({"players":[{}]})[offset:offset+6] == [0, 0, 0, 0, 0, 0]
player = {"id":"1", "rosterAsOf":"2026-01-01T00:00:00Z",
          "seasonStatsAsOf":"2026-01-01T00:00:00Z",
          "extraStatsAsOf":"2027-01-01T00:00:00Z",
          "extraStats":{"accuratePasses":50, "minutes":500}, "goals":3}
captured = squad({"players":[player]}, {}, set(), stamp("2026-12-01T00:00:00Z"))
assert captured["players"][0]["extraStats"] == {}
assert captured["players"][0]["minutes"] is None
print("MLPlus extra-field and cutoff checks passed")
