#!/usr/bin/env python3
"""Collect current rosters for ALL competition-scoped teams in historical matches.

The historical match_archive.sqlite3 is opened STRICTLY READ-ONLY. The separate
player_data.sqlite3 stores verified roster fields, snapshot timestamps and errors.
ESPN roster data describes CURRENT players, not lineups of past matches.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
import sys
import threading

import update_players as p

ROOT = p.ROOT
WORKERS = max(1, min(8, int(os.getenv("PLAYER_WORKERS", "5"))))
LIMIT = max(1, int(os.getenv("PLAYER_TOTAL_LIMIT", "2000")))
RETRY_HOURS = max(1, int(os.getenv("PLAYER_RETRY_HOURS", "48")))
REFRESH_DAYS = max(1, int(os.getenv("PLAYER_REFRESH_DAYS", "7")))


def all_teams():
    teams = {}
    def add(m, priority):
        if not isinstance(m, dict):
            return
        league = m.get("league")
        if not isinstance(league, str) or not league:
            return
        for side in ("home", "away"):
            team = m.get(side)
            if not isinstance(team, dict):
                continue
            tid = str(team.get("id") or "")
            if not tid.isdecimal():
                continue
            key = (league, tid)
            name = str(team.get("name") or tid)
            if key not in teams or priority < teams[key][0]:
                teams[key] = (priority, name)

    # The published JSON is supplementary; the SQLite archive is authoritative.
    try:
        published = json.loads((ROOT / "data.json").read_text("utf-8"))
        for m in published.get("events", []):
            add(m, 0 if not m.get("done") else 1)
    except (OSError, ValueError, TypeError) as exc:
        print(f"Published snapshot not usable: {exc}", flush=True)

    archive = ROOT / "match_archive.sqlite3"
    if not archive.is_file():
        raise FileNotFoundError(f"Missing match archive: {archive}")
    db = sqlite3.connect(f"file:{archive.resolve()}?mode=ro", uri=True)
    db.execute("PRAGMA query_only=ON")
    try:
        for (payload,) in db.execute("SELECT payload FROM matches"):
            try:
                add(json.loads(payload), 2)
            except (ValueError, TypeError):
                continue
    finally:
        db.close()
    return [(league, tid, name, priority)
            for (league, tid), (priority, name) in
            sorted(teams.items(), key=lambda x: (x[1][0], x[0][0], x[0][1]))]


def work(item, market):
    league, tid, name, _ = item
    db = sqlite3.connect(p.DB_PATH, timeout=90)
    try:
        db.execute("PRAGMA busy_timeout=90000")
        try:
            count = p.update_one(db, league, tid, market)
            success, error = 1, None
            print(f"OK {league}/{name}: {count} players", flush=True)
        except Exception as exc:
            success, error = 0, str(exc)[:300]
            print(f"UNAVAILABLE {league}/{name}: {error}", flush=True)
        db.execute("""INSERT INTO refresh_status
            (league,team_id,checked_at,success,error) VALUES (?,?,?,?,?)
            ON CONFLICT(league,team_id) DO UPDATE SET
              checked_at=excluded.checked_at,success=excluded.success,
              error=excluded.error""",
            (league, tid, p.now(), success, error))
        db.commit()
        return success
    finally:
        db.close()


def report(db, teams, attempts, successes):
    checked = {(league, tid): (stamp, ok, err) for league, tid, stamp, ok, err
               in db.execute("SELECT league,team_id,checked_at,success,error FROM refresh_status")}
    eligible = {(league, tid) for league, tid, _, _ in teams}
    successful = sum(checked.get(key, ("", 0, ""))[1] == 1 for key in eligible)
    failed = [{"league": league, "teamId": tid, "error": checked[(league,tid)][2]}
              for league,tid in sorted(eligible) if (league,tid) in checked
              and checked[(league,tid)][1] == 0]
    counts = db.execute("""SELECT COUNT(*),COUNT(DISTINCT player_id),
        SUM(goals IS NOT NULL),SUM(assists IS NOT NULL),
        SUM(recent_form IS NOT NULL),SUM(market_value_eur IS NOT NULL)
        FROM players""").fetchone()
    result = {
        "updatedAt": p.now(), "targetTeamEntries": len(eligible),
        "successfulTeamEntries": successful,
        "unprocessedTeamEntries": len(eligible) - len(eligible.intersection(checked)),
        "failedTeamEntries": len(failed),
        "playerRows": counts[0], "distinctPlayers": counts[1],
        "playersWithGoals": counts[2] or 0,
        "playersWithAssists": counts[3] or 0,
        "playersWithRecentForm": counts[4] or 0,
        "playersWithMarketValue": counts[5] or 0,
        "attemptedThisRun": attempts, "successfulThisRun": successes,
        "failedTeams": failed,
        "note": "Competition-scoped team IDs can refer to the same club; rosters are current, not historical.",
    }
    (ROOT / "player_coverage.json").write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")), "utf-8")
    print("COVERAGE " + json.dumps({k:v for k,v in result.items() if k != "failedTeams"}), flush=True)
    return result


def main():
    targets = all_teams()
    print(f"Archived competition-scoped team entries: {len(targets)}", flush=True)
    if not targets:
        raise RuntimeError("No teams found in archive; refusing empty export")
    db = sqlite3.connect(p.DB_PATH, timeout=90)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        p.init(db)
        db.commit()
        existing = {(league, tid): (stamp, success)
                    for league, tid, stamp, success in db.execute(
                        "SELECT league,team_id,checked_at,success FROM refresh_status")}
        now = datetime.now(timezone.utc)
        pending = []
        for item in targets:
            previous = existing.get((item[0], item[1]))
            if previous:
                try:
                    last = datetime.fromisoformat(previous[0])
                    age = now - last
                    if age < timedelta(days=REFRESH_DAYS if previous[1] else 0,
                                       hours=0 if previous[1] else RETRY_HOURS):
                        continue
                except (ValueError, TypeError):
                    pass
            pending.append(item)
        pending = pending[:LIMIT]
        print(f"Due for fetch: {len(pending)} / {len(targets)}", flush=True)
        market = p.licensed_market_values()
        successes = 0
        if pending:
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                futures = [pool.submit(work, item, market) for item in pending]
                for future in as_completed(futures):
                    successes += future.result()
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        p.export(db)
        results = report(db, targets, len(pending), successes)
        if results["playerRows"] == 0:
            print("WARNING: no roster data was obtained; inspect provider responses", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
