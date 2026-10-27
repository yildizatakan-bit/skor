#!/usr/bin/env python3
"""Freeze player-data signals strictly before kickoff for future evaluation.

This is NOT a player-adjusted prediction model. Historical player data do not
exist at the same time resolution as historical match results, so training a
retrospective player model now would leak future information.

Read-only: match_archive.sqlite3, data.json, existing prediction histories.
Writes ONLY player_signal_history.json and player_signal_metrics.json.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / "player_signal_history.json"
METRICS_PATH = ROOT / "player_signal_metrics.json"
UTC = timezone.utc
LABELS = ("home", "draw", "away")


def instant(value):
    if not isinstance(value, str):
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone(UTC) if d.tzinfo is not None else None


def read_json(name, default):
    try:
        return json.loads((ROOT / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def valid_probabilities(p):
    return isinstance(p, dict) and all(
        type(p.get(k)) in (int, float) and math.isfinite(p[k]) and 0 <= p[k] <= 1
        for k in LABELS
    ) and abs(sum(p[k] for k in LABELS) - 1) <= 0.025


def score(m):
    if not isinstance(m, dict) or m.get("done") is not True:
        return None
    h, a = (m.get("home") or {}).get("score"), (m.get("away") or {}).get("score")
    if type(h) is not int or type(a) is not int or not (0 <= h <= 30 and 0 <= a <= 30):
        return None
    return h, a


def side_features(team, kickoff, fetched):
    if not isinstance(team, dict):
        return None
    roster_at = instant(team.get("rosterAsOf"))
    if roster_at is None or roster_at > fetched or roster_at >= kickoff:
        return None
    n, known, goals = (team.get("rosterPlayers"), team.get("verifiedSeasonGoalPlayers"),
                       team.get("verifiedSeasonGoalsSum"))
    if type(n) is not int or type(known) is not int or n < 5 or known < 0 or known > n:
        return None
    if type(goals) not in (int, float) or not math.isfinite(goals) or goals < 0:
        return None
    # Avoid snapshots where most of the roster is missing from the provider.
    if known / n < 0.70:
        return None
    players = team.get("players")
    if not isinstance(players, list) or len(players) != n:
        return None
    # Every included statistic must have been retrieved before this snapshot.
    counted = 0
    verified_goals = 0.0
    for player in players:
        if not isinstance(player, dict):
            return None
        value = player.get("goals")
        if value is None:
            continue
        fetched_at = instant(player.get("seasonStatsAsOf"))
        if fetched_at is None or fetched_at > fetched or fetched_at >= kickoff:
            return None
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            return None
        counted += 1
        verified_goals += value
    if counted != known or abs(verified_goals - goals) > 0.01:
        return None
    return {
        "seasonYear": team.get("seasonYear"),
        "seasonStatus": team.get("seasonStatus", "latest_available_unverified"),
        "rosterPlayers": n,
        "goalKnownPlayers": known,
        "goalCoverage": round(known / n, 4),
        "knownSeasonGoals": round(goals, 3),
        "rosterAsOf": team.get("rosterAsOf"),
    }


def freeze(event, feature_feed, classic, ml, now):
    if not isinstance(event, dict) or event.get("done") or event.get("live"):
        return None
    kickoff = instant(event.get("date"))
    fetched = instant(feature_feed.get("generatedAt"))
    if kickoff is None or fetched is None or not (fetched <= now < kickoff):
        return None
    league, mid = event.get("league"), event.get("id")
    if not isinstance(league, str) or not mid:
        return None
    teams = feature_feed.get("teams") or {}
    sides = {}
    for side in ("home", "away"):
        info = event.get(side) or {}
        tid = str(info.get("id") or "")
        if not tid.isdigit():
            return None
        source = teams.get(f"{league}:{tid}")
        if not isinstance(source, dict) or source.get("teamId") != tid:
            return None
        f = side_features(source, kickoff, fetched)
        if f is None:
            return None
        sides[side] = {"id": tid, "name": info.get("name") or "", **f}
    probabilities = {}
    if valid_probabilities(classic):
        probabilities["classic"] = {k: round(classic[k], 7) for k in LABELS}
    if valid_probabilities(ml):
        probabilities["ml"] = {k: round(ml[k], 7) for k in LABELS}
    return {
        "matchId": str(mid), "league": league, "kickoff": kickoff.isoformat(),
        "frozenAt": now.isoformat(), "playerFeedGeneratedAt": fetched.isoformat(),
        "home": sides["home"], "away": sides["away"],
        "knownSeasonGoalEdge": round(
            sides["home"]["knownSeasonGoals"] - sides["away"]["knownSeasonGoals"], 3),
        "probabilities": probabilities,
        "warning": "Roster aggregates are not a starting XI, active-season guarantee, or calibrated prediction.",
    }


def finished_archive():
    path = ROOT / "match_archive.sqlite3"
    if not path.is_file():
        return {}
    db = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        result = {}
        for (payload,) in db.execute("SELECT payload FROM matches WHERE done=1"):
            try:
                event = json.loads(payload)
            except (ValueError, TypeError):
                continue
            if score(event) is not None and event.get("id"):
                result[str(event["id"])] = event
        return result
    finally:
        db.close()


def settle(snapshot, event):
    result = score(event)
    if result is None:
        return None
    if snapshot["league"] != event.get("league"):
        return None
    if instant(snapshot["kickoff"]) != instant(event.get("date")):
        return None
    for side in ("home", "away"):
        if str((event.get(side) or {}).get("id") or "") != snapshot[side]["id"]:
            return None
    h, a = result
    return {
        "matchId": snapshot["matchId"], "homeScore": h, "awayScore": a,
        "outcome": "home" if h > a else "away" if a > h else "draw",
    }


def probability_metrics(pairs):
    if not pairs:
        return {"matches": 0, "accuracy": None, "brier": None, "logLoss": None}
    n = len(pairs)
    hits = 0
    brier = 0.0
    logloss = 0.0
    for p, outcome in pairs:
        hits += int(max(LABELS, key=lambda k: p[k]) == outcome)
        brier += sum((p[k] - int(k == outcome)) ** 2 for k in LABELS)
        logloss -= math.log(max(p[outcome], 1e-12))
    return {"matches": n, "accuracy": round(hits/n, 5),
            "brier": round(brier/n, 5), "logLoss": round(logloss/n, 5)}


def build_report(snapshots, settlements, now):
    completed = {x["matchId"]: x for x in settlements}
    classic, ml, common_classic, common_ml = [], [], [], []
    directional = []
    for snap in snapshots:
        result = completed.get(snap["matchId"])
        if result is None:
            continue
        probs = snap.get("probabilities") or {}
        if valid_probabilities(probs.get("classic")):
            classic.append((probs["classic"], result["outcome"]))
        if valid_probabilities(probs.get("ml")):
            ml.append((probs["ml"], result["outcome"]))
        if valid_probabilities(probs.get("classic")) and valid_probabilities(probs.get("ml")):
            common_classic.append((probs["classic"], result["outcome"]))
            common_ml.append((probs["ml"], result["outcome"]))
        edge = snap.get("knownSeasonGoalEdge")
        if type(edge) in (int, float) and edge != 0 and result["outcome"] != "draw":
            directional.append((edge > 0 and result["outcome"] == "home") or
                               (edge < 0 and result["outcome"] == "away"))
    return {
        "version": 1, "generatedAt": now.isoformat(),
        "frozenMatches": len(snapshots), "settledMatches": len(settlements),
        "classicFrozen": sum("classic" in (s.get("probabilities") or {}) for s in snapshots),
        "mlFrozen": sum("ml" in (s.get("probabilities") or {}) for s in snapshots),
        "classic": probability_metrics(classic), "ml": probability_metrics(ml),
        "pairedComparison": {
            "sameMatches": len(common_ml),
            "classic": probability_metrics(common_classic),
            "ml": probability_metrics(common_ml),
            "minimumSampleMet": len(common_ml) >= 30,
        },
        "descriptiveGoalEdge": {
            "nonDrawMatches": len(directional),
            "sameDirectionRate": round(sum(directional)/len(directional), 5) if directional else None,
            "minimumSampleMet": len(directional) >= 30,
            "note": "Exploratory association only, not model accuracy or evidence of incremental predictive value.",
        },
        "note": "Frozen pre-kickoff player snapshots are separate from historical match/prediction archives. No player adjustment is made to either model.",
    }


def process(data, feed, old, archive, ml_predictions, now):
    if not isinstance(old, dict):
        old = {}
    snapshots = list(old.get("snapshots") or [])
    settlements = list(old.get("settlements") or [])
    ids = {s["matchId"] for s in snapshots}
    settled = {s["matchId"] for s in settlements}
    events = data.get("events") or []
    analyses = data.get("analyses") or {}
    for event in events:
        if not isinstance(event, dict) or not event.get("id"):
            continue
        mid = str(event["id"])
        if mid in ids:
            continue
        analysis = analyses.get(mid) or {}
        ml_entry = ml_predictions.get(mid) or {}
        candidate = freeze(event, feed, analysis.get("p"), ml_entry.get("p"), now)
        if candidate is not None:
            snapshots.append(candidate)
            ids.add(mid)
    finished = {str(m["id"]): m for m in events
                if isinstance(m, dict) and m.get("id") and score(m) is not None}
    finished.update(archive)
    for snap in snapshots:
        mid = snap["matchId"]
        if mid in settled:
            continue
        result = settle(snap, finished.get(mid))
        if result is not None:
            settlements.append(result)
            settled.add(mid)
    ledger = {"version": 1, "snapshots": snapshots, "settlements": settlements}
    return ledger, build_report(snapshots, settlements, now)


def main():
    now = datetime.now(UTC)
    data = read_json("data.json", {})
    feed = read_json("player_team_features.json", {})
    old = read_json(HISTORY_PATH.name, {})
    ml = read_json("ml_predictions.json", {}).get("predictions", {})
    if not isinstance(data.get("events"), list) or not isinstance(feed.get("teams"), dict):
        raise RuntimeError("Missing match or player snapshot input; refusing to overwrite ledger")
    ledger, report = process(data, feed, old, finished_archive(), ml, now)
    HISTORY_PATH.write_text(json.dumps(ledger, ensure_ascii=False, separators=(",", ":"),
                                       allow_nan=False), encoding="utf-8")
    METRICS_PATH.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":"),
                                       allow_nan=False), encoding="utf-8")
    print("PLAYER_SIGNAL_LEDGER " + json.dumps({
        "frozen": report["frozenMatches"], "settled": report["settledMatches"],
        "paired": report["pairedComparison"]["sameMatches"],
    }), flush=True)


if __name__ == "__main__":
    main()
