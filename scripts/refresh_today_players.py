#!/usr/bin/env python3
"""Refresh ESPN squad data for today's upcoming fixtures only.

The workflow runs this script in the isolated player-data branch. The
production match archive and published fixture snapshot are read-only inputs;
no prediction history or match archive data is written by this script.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo

# The workflow executes this application-side script in the isolated player
# branch so its database and helper modules stay on that branch.
sys.path.insert(0, str(Path.cwd() / "scripts"))

from update_players import DB_PATH, ROOT, export, get_json, init, now, update_one
import enrich_player_season
import build_player_team_features

UTC = timezone.utc
LOCAL = ZoneInfo("Europe/Istanbul")
WORKERS = 4


def today_fixture_teams(data: dict, instant: datetime):
    """Return unique teams with a not-yet-started kickoff on the Istanbul date."""
    today = instant.astimezone(LOCAL).date()
    teams = {}
    for match in data.get("events", []):
        if not isinstance(match, dict) or match.get("done") or not match.get("date"):
            continue
        try:
            kickoff = datetime.fromisoformat(match["date"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if kickoff.tzinfo is None:
            continue
        kickoff = kickoff.astimezone(UTC)
        if kickoff <= instant or kickoff.astimezone(LOCAL).date() != today:
            continue
        league = match.get("league")
        if not isinstance(league, str) or not league:
            continue
        for side in ("home", "away"):
            team = match.get(side)
            if not isinstance(team, dict):
                continue
            team_id = str(team.get("id") or "")
            if team_id.isdecimal():
                teams[(league, team_id)] = str(team.get("name") or team_id)
    return [(league, team_id, name)
            for (league, team_id), name in sorted(teams.items())]


def refresh(item):
    league, team_id, name = item
    db = sqlite3.connect(DB_PATH, timeout=90)
    try:
        db.execute("PRAGMA busy_timeout=90000")
        try:
            count = update_one(db, league, team_id, {})
            success, error = 1, None
            print(f"UPDATED {league}/{name}: {count} players", flush=True)
        except Exception as exc:  # Preserve last known roster if the feed fails.
            success, error = 0, str(exc)[:300]
            print(f"UNAVAILABLE {league}/{name}: {error}", flush=True)
        db.execute("""INSERT INTO refresh_status
            (league,team_id,checked_at,success,error) VALUES (?,?,?,?,?)
            ON CONFLICT(league,team_id) DO UPDATE SET
              checked_at=excluded.checked_at,success=excluded.success,
              error=excluded.error""",
            (league, team_id, now(), success, error))
        db.commit()
        return success
    finally:
        db.close()


def main():
    instant = datetime.now(UTC)
    data = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))
    targets = today_fixture_teams(data, instant)
    print(f"Today's upcoming fixture teams ({instant.astimezone(LOCAL).date()}): {len(targets)}",
          flush=True)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=90)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=90000")
        init(db)
        enrich_player_season.init(db)
        db.commit()
    finally:
        db.close()

    successes = 0
    if targets:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(refresh, item) for item in targets]
            for future in as_completed(futures):
                successes += future.result()

    db = sqlite3.connect(DB_PATH, timeout=90)
    try:
        db.execute("PRAGMA busy_timeout=90000")
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        export(db)
        report = {
            "updatedAt": now(), "targetTeamEntries": len(targets),
            "successfulTeamEntries": successes,
            "failedTeamEntries": len(targets) - successes,
            "scope": "today's not-yet-started fixtures in Europe/Istanbul",
        }
        (ROOT / "player_coverage.json").write_text(
            json.dumps(report, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8")
        player_features = build_player_team_features.build(db, data, instant)
        (ROOT / "player_team_features.json").write_text(
            json.dumps(player_features, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False), encoding="utf-8")
    finally:
        db.close()
    print("PLAYER_REFRESH " + json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
