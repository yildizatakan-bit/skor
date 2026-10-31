#!/usr/bin/env python3
"""Prospectively snapshot measurable fixture context without editing model history.

Only completed-fixture rest/workload proxies are recorded. League position,
competition stakes, and psychological motivation are explicitly left unknown
until verified, timestamped source data is available.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data.json"
OUTPUT = ROOT / "match_context_history.json"
UTC = timezone.utc


def parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(UTC)


def team_id(match, side):
    team = match.get(side) or {}
    ident = str(team.get("id") or "")
    return ident if ident else None


def team_schedule(events, ident, now):
    completed = []
    future = []
    for event in events:
        if not isinstance(event, dict):
            continue
        kickoff = parse_time(event.get("date"))
        if not kickoff:
            continue
        sides = (team_id(event, "home"), team_id(event, "away"))
        if ident not in sides:
            continue
        if event.get("done") is True and kickoff <= now:
            completed.append(kickoff)
        elif event.get("done") is not True and kickoff > now:
            future.append(kickoff)
    previous = max((d for d in completed if d <= now), default=None)
    days_since = round((now - previous).total_seconds() / 86400, 2) if previous else None
    return {
        "restDays": days_since,
        "completedMatchesLast14Days": sum(now.timestamp() - 14*86400 <= d.timestamp() <= now.timestamp() for d in completed),
        "upcomingMatchesNext14Days": sum(0 < (d-now).total_seconds() <= 14*86400 for d in future),
        "completedMatchesLast7Days": sum(now.timestamp() - 7*86400 <= d.timestamp() <= now.timestamp() for d in completed),
    }


def read_ledger():
    try:
        value = json.loads(OUTPUT.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("snapshots"), list):
            value.setdefault("settlements", [])
            return value
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "snapshots": [], "settlements": []}


def write_atomic(value):
    fd, name = tempfile.mkstemp(prefix=OUTPUT.name, suffix=".tmp", dir=ROOT)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
        os.replace(name, OUTPUT)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    now = datetime.now(UTC)
    data = json.loads(DATA.read_text(encoding="utf-8"))
    events = data.get("events", [])
    ledger = read_ledger()
    known_snapshots = {
        (row.get("matchId"), row.get("capturedAt"))
        for row in ledger["snapshots"] if isinstance(row, dict)
    }
    new_count = 0
    for event in events:
        if not isinstance(event, dict) or event.get("done") is True:
            continue
        kickoff = parse_time(event.get("date"))
        match_id = str(event.get("id") or "")
        if not kickoff or not match_id or kickoff <= now:
            continue
        home_id, away_id = team_id(event, "home"), team_id(event, "away")
        if not home_id or not away_id:
            continue
        captured = now.isoformat()
        if (match_id, captured) in known_snapshots:
            continue
        ledger["snapshots"].append({
            "matchId": match_id,
            "kickoff": kickoff.isoformat(),
            "capturedAt": captured,
            "sourceUpdatedAt": data.get("updatedAt"),
            "competition": event.get("league"),
            "home": {"id": home_id, "name": (event.get("home") or {}).get("name"),
                     **team_schedule(events, home_id, now)},
            "away": {"id": away_id, "name": (event.get("away") or {}).get("name"),
                     **team_schedule(events, away_id, now)},
            "standings": {"status": "unavailable", "source": None},
            "stakes": {"status": "unverified", "signals": []},
            "motivation": {"status": "not_measured", "score": None},
            "modelProbabilitiesModified": False,
        })
        new_count += 1
    completed = {}
    for event in events:
        if not isinstance(event, dict) or event.get("done") is not True:
            continue
        match_id = str(event.get("id") or "")
        kickoff = parse_time(event.get("date"))
        home = event.get("home") or {}
        away = event.get("away") or {}
        try:
            score = [int(home["score"]), int(away["score"])]
        except (KeyError, TypeError, ValueError):
            continue
        if match_id and kickoff and kickoff <= now:
            completed[match_id] = {"matchId": match_id, "kickoff": kickoff.isoformat(),
                                   "score": score, "settledAt": now.isoformat()}
    settled_ids = {x.get("matchId") for x in ledger["settlements"] if isinstance(x, dict)}
    for match_id, result in completed.items():
        if match_id not in settled_ids and any(s.get("matchId") == match_id for s in ledger["snapshots"]):
            ledger["settlements"].append(result)
    ledger["version"] = 1
    ledger["generatedAt"] = now.isoformat()
    ledger["method"] = "Timestamped fixture schedule snapshots; rest and workload proxies only"
    ledger["limitations"] = [
        "Historical match archive and prediction history are never modified",
        "League standings and qualification stakes require a verified dated standings feed",
        "Psychological motivation is not inferred or converted into a probability adjustment",
        "Fixture schedule is limited to events present in the current data.json snapshot",
        "Latest pre-kickoff observation must be selected for any later evaluation",
    ]
    write_atomic(ledger)
    print(f"MATCH_CONTEXT snapshots_added={new_count} total={len(ledger['snapshots'])} settlements={len(ledger['settlements'])}")


if __name__ == "__main__":
    main()
