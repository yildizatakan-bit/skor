#!/usr/bin/env python3
"""Latest-available ESPN season player statistics from soccer statisticslog.

The latest season is resolved from ESPN's own per-player statisticslog rather
than guessed from calendar year. Never modifies historical match archive.
Missing goal assists and match-by-match form remain NULL, never fabricated.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import json
import os
import re
import sqlite3

from update_players import ROOT, DB_PATH, get_json, now, teams_from_snapshot
from enrich_player_career import parse_stats

BATCH = max(1, min(3000, int(os.getenv("SEASON_STATS_BATCH", "500"))))
WORKERS = max(1, min(6, int(os.getenv("SEASON_STATS_WORKERS", "4"))))
UTC = timezone.utc


def allowed_ref(url):
    if not isinstance(url, str):
        return None
    url = url.replace("http://sports.core.api.espn.com/",
                      "https://sports.core.api.espn.com/", 1)
    parts = urlparse(url)
    if parts.scheme != "https" or parts.hostname != "sports.core.api.espn.com":
        return None
    if not re.fullmatch(r"/v2/sports/soccer/leagues/[a-zA-Z0-9_.-]+/seasons/\d{4}/types/\d+/athletes/\d+/statistics/\d+", parts.path):
        return None
    return url


def current_season_ref(log):
    entries = log.get("entries", []) if isinstance(log, dict) else []
    now_year = datetime.now(UTC).year
    candidates = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        season_ref = (entry.get("season") or {}).get("$ref", "")
        match = re.search(r"/seasons/(\d{4})(?:[?/]|$)", season_ref)
        if not match:
            continue
        year = int(match.group(1))
        if year > now_year + 1 or year < now_year - 2:
            continue
        for row in entry.get("statistics", []):
            if isinstance(row, dict) and row.get("type") == "total":
                url = allowed_ref((row.get("statistics") or {}).get("$ref"))
                if url:
                    candidates.append((year, url))
    return max(candidates, default=(None, None), key=lambda x: x[0])


def fetch(league, player_id):
    from urllib.parse import quote
    url = ("https://sports.core.api.espn.com/v2/sports/soccer/leagues/"
           + quote(league, safe="") + "/athletes/" + quote(player_id, safe="")
           + "/statisticslog")
    try:
        year, stats_url = current_season_ref(get_json(url))
        if not year:
            return league, player_id, None, "no current-season statistics link"
        stats = parse_stats(get_json(stats_url))
        if not any(v is not None for v in stats.values()):
            return league, player_id, None, "no numeric current-season stats"
        return league, player_id, (year, stats), None
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return league, player_id, None, str(exc)[:240]


def init(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS player_season_stats (
      league TEXT NOT NULL, player_id TEXT NOT NULL, season_year INTEGER NOT NULL,
      goals REAL, assists REAL, appearances REAL, shots REAL, shots_on_target REAL,
      yellow_cards REAL, red_cards REAL, interceptions REAL, tackles REAL, saves REAL,
      source TEXT NOT NULL, fetched_at TEXT NOT NULL,
      PRIMARY KEY (league,player_id,season_year)
    );
    CREATE TABLE IF NOT EXISTS season_stats_status (
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
        status = {(league, pid): (stamp, success) for league, pid, stamp, success
                  in db.execute("SELECT league,player_id,checked_at,success FROM season_stats_status")}
        now_dt = datetime.now(UTC)
        # Fetch upcoming-match players first; the archive's historical teams
        # must not monopolize the queue in alphabetical league order.
        priority_teams = {(league, tid) for league, tid, _ in teams_from_snapshot()}
        candidates = {}
        for league, team_id, pid in db.execute(
                "SELECT league,team_id,player_id FROM players"):
            previous = status.get((league, pid))
            if previous:
                try:
                    if now_dt - datetime.fromisoformat(previous[0]) < timedelta(
                            days=7 if previous[1] else 2):
                        continue
                except (ValueError, TypeError):
                    pass
            key = (league, pid)
            is_priority = (league, team_id) in priority_teams
            candidates[key] = candidates.get(key, False) or is_priority
        pending = sorted(candidates, key=lambda key: (
            not candidates[key], key[0], key[1]))[:BATCH]
        priority_count = sum(bool(candidates[key]) for key in pending)
        print(f"Season stats due: {len(pending)}; upcoming-team priority: {priority_count}; "
              f"priority teams: {len(priority_teams)}", flush=True)
        if not pending and (ROOT / "player_season_coverage.json").is_file():
            print("No season stats refresh due", flush=True)
            return
        succeeded = 0
        processed = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(fetch, league, pid) for league, pid in pending]
            for future in as_completed(futures):
                league, pid, result, error = future.result()
                stamp = now()
                success = int(result is not None)
                if result:
                    year, stats = result
                    db.execute("""INSERT INTO player_season_stats (
                      league,player_id,season_year,goals,assists,appearances,
                      shots,shots_on_target,yellow_cards,red_cards,
                      interceptions,tackles,saves,source,fetched_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(league,player_id,season_year) DO UPDATE SET
                      goals=excluded.goals,assists=excluded.assists,
                      appearances=excluded.appearances,shots=excluded.shots,
                      shots_on_target=excluded.shots_on_target,
                      yellow_cards=excluded.yellow_cards,red_cards=excluded.red_cards,
                      interceptions=excluded.interceptions,tackles=excluded.tackles,
                      saves=excluded.saves,fetched_at=excluded.fetched_at""",
                      (league,pid,year,stats["goals"],stats["assists"],
                       stats["appearances"],stats["shots"],stats["shotsOnTarget"],
                       stats["yellowCards"],stats["redCards"],stats["interceptions"],
                       stats["tackles"],stats["saves"],
                       "ESPN core soccer season-specific total",stamp))
                    succeeded += 1
                db.execute("""INSERT INTO season_stats_status VALUES(?,?,?,?,?)
                  ON CONFLICT(league,player_id) DO UPDATE SET
                  checked_at=excluded.checked_at,success=excluded.success,error=excluded.error""",
                  (league,pid,stamp,success,error))
                processed += 1
                if processed % 150 == 0:
                    db.commit()
        db.commit()
        row = db.execute("""SELECT COUNT(*),SUM(goals IS NOT NULL),
                   SUM(assists IS NOT NULL),SUM(appearances IS NOT NULL)
                   FROM player_season_stats""").fetchone()
        total = db.execute("SELECT COUNT(*) FROM (SELECT DISTINCT league,player_id FROM players)").fetchone()[0]
        checked = db.execute("SELECT COUNT(*) FROM season_stats_status").fetchone()[0]
        report = {
            "updatedAt": now(), "source": "ESPN core soccer statisticslog season links",
            "playerLeagueEntries": total, "checkedEntries": checked,
            "successfulSeasonEntries": row[0], "withGoals": row[1] or 0,
            "withAssists": row[2] or 0, "withAppearances": row[3] or 0,
            "attemptedThisRun": len(pending), "successfulThisRun": succeeded,
            "upcomingPriorityThisRun": priority_count,
            "warning": "These are ESPN season-year totals, NOT verified currently active season or last-five-match form; do not use snapshots for matches predating the fetch timestamp.",
        }
        (ROOT / "player_season_coverage.json").write_text(
            json.dumps(report, ensure_ascii=False, separators=(",", ":")), "utf-8")
        print("SEASON_COVERAGE " + json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
