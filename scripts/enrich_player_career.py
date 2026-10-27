#!/usr/bin/env python3
"""Fetch explicitly labeled ESPN CAREER soccer statistics into separate tables.

Never alters match_archive.sqlite3, past predictions or roster identities.
Career totals must NOT be interpreted as current-season performance or used
for backtesting historical games.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
import urllib.parse
import urllib.error

from update_players import ROOT, DB_PATH, get_json, now, num

BATCH = max(1, min(5000, int(os.getenv("CAREER_STATS_BATCH", "1200"))))
WORKERS = max(1, min(8, int(os.getenv("CAREER_STATS_WORKERS", "6"))))
UTC = timezone.utc

FIELD_NAMES = {
    "goals": ("goals", "totalGoals"),
    "assists": ("assists", "totalAssists"),
    "appearances": ("appearances",),
    "shots": ("totalShots", "shots"),
    "shotsOnTarget": ("shotsOnTarget",),
    "yellowCards": ("yellowCards",),
    "redCards": ("redCards",),
    "interceptions": ("interceptions",),
    "tackles": ("totalTackles",),
    "saves": ("saves", "totalSaves"),
}


def parse_stats(payload):
    if not isinstance(payload, dict):
        return {}
    splits = payload.get("splits")
    if not isinstance(splits, dict) or splits.get("type") != "total":
        return {}
    rows = {}
    for category in splits.get("categories", []):
        if not isinstance(category, dict):
            continue
        for item in category.get("stats", []):
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if name in rows:
                continue
            value = item.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100000:
                rows[name] = float(value)
    return {key: next((rows[name] for name in aliases if name in rows), None)
            for key, aliases in FIELD_NAMES.items()}


def get_one(league, athlete_id):
    url = (
        "https://sports.core.api.espn.com/v2/sports/soccer/leagues/"
        + urllib.parse.quote(league, safe="")
        + "/athletes/" + urllib.parse.quote(athlete_id, safe="")
        + "/statistics"
    )
    try:
        result = parse_stats(get_json(url))
        if not any(value is not None for value in result.values()):
            return league, athlete_id, None, "no verified numeric stats"
        return league, athlete_id, result, None
    except (OSError, ValueError, TypeError, urllib.error.URLError) as exc:
        return league, athlete_id, None, str(exc)[:220]


def init(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS player_career_stats (
        league TEXT NOT NULL,
        player_id TEXT NOT NULL,
        goals REAL, assists REAL, appearances REAL, shots REAL,
        shots_on_target REAL, yellow_cards REAL, red_cards REAL,
        interceptions REAL, tackles REAL, saves REAL,
        source TEXT NOT NULL, scope TEXT NOT NULL, fetched_at TEXT NOT NULL,
        PRIMARY KEY (league,player_id)
    );
    CREATE TABLE IF NOT EXISTS career_stats_status (
        league TEXT NOT NULL, player_id TEXT NOT NULL,
        checked_at TEXT NOT NULL, success INTEGER NOT NULL, error TEXT,
        PRIMARY KEY (league,player_id)
    );
    """)


def main():
    db = sqlite3.connect(DB_PATH)
    try:
        init(db)
        db.commit()
        status = {(league, pid): (stamp, success)
                  for league, pid, stamp, success in db.execute(
                      "SELECT league,player_id,checked_at,success FROM career_stats_status")}
        now_dt = datetime.now(UTC)
        pending = []
        for league, pid in db.execute(
                "SELECT DISTINCT league,player_id FROM players ORDER BY league,player_id"):
            prior = status.get((league, pid))
            if prior:
                try:
                    age = now_dt - datetime.fromisoformat(prior[0])
                    if age < timedelta(days=30 if prior[1] else 3):
                        continue
                except (ValueError, TypeError):
                    pass
            pending.append((league, pid))
            if len(pending) >= BATCH:
                break
        print(f"Career stats due: {len(pending)}; max batch {BATCH}", flush=True)
        successful = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(get_one, league, pid) for league, pid in pending]
            for future in as_completed(futures):
                league, pid, fields, error = future.result()
                stamp = now()
                success = int(fields is not None)
                if fields is not None:
                    db.execute("""INSERT INTO player_career_stats (
                      league,player_id,goals,assists,appearances,shots,shots_on_target,
                      yellow_cards,red_cards,interceptions,tackles,saves,source,scope,fetched_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(league,player_id) DO UPDATE SET
                      goals=excluded.goals,assists=excluded.assists,
                      appearances=excluded.appearances,shots=excluded.shots,
                      shots_on_target=excluded.shots_on_target,
                      yellow_cards=excluded.yellow_cards,red_cards=excluded.red_cards,
                      interceptions=excluded.interceptions,tackles=excluded.tackles,
                      saves=excluded.saves,source=excluded.source,
                      scope=excluded.scope,fetched_at=excluded.fetched_at""",
                      (league,pid,fields["goals"],fields["assists"],fields["appearances"],
                       fields["shots"],fields["shotsOnTarget"],fields["yellowCards"],
                       fields["redCards"],fields["interceptions"],fields["tackles"],
                       fields["saves"],"ESPN core soccer athlete statistics",
                       "career",stamp))
                    successful += 1
                db.execute("""INSERT INTO career_stats_status VALUES(?,?,?,?,?)
                  ON CONFLICT(league,player_id) DO UPDATE SET
                    checked_at=excluded.checked_at,success=excluded.success,
                    error=excluded.error""",
                    (league,pid,stamp,success,error))
                if (successful + (len(pending) - successful)) % 200 == 0:
                    db.commit()
        db.commit()
        row = db.execute("""SELECT COUNT(*),SUM(goals IS NOT NULL),SUM(assists IS NOT NULL),
                          SUM(appearances IS NOT NULL) FROM player_career_stats""").fetchone()
        count = db.execute("SELECT COUNT(*) FROM (SELECT DISTINCT league,player_id FROM players)").fetchone()[0]
        checked = db.execute("SELECT COUNT(*) FROM career_stats_status").fetchone()[0]
        summary = {
            "updatedAt": now(), "scope": "career", "source": "ESPN core soccer athlete statistics",
            "playerLeagueEntries": count, "checkedEntries": checked,
            "successfulEntries": row[0], "withGoals": row[1] or 0,
            "withAssists": row[2] or 0, "withAppearances": row[3] or 0,
            "attemptedThisRun": len(pending), "successfulThisRun": successful,
            "warning": "Career aggregates, NOT current-season or last-five-match form. Never use these values for historical backtesting.",
        }
        (ROOT / "player_career_coverage.json").write_text(
            json.dumps(summary, ensure_ascii=False, separators=(",", ":")), "utf-8")
        print("CAREER_COVERAGE " + json.dumps(summary, ensure_ascii=False), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
