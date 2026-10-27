#!/usr/bin/env python3
"""Diagnostic-only ESPN soccer statistics probe. Never modifies databases.

Checks two already-collected player IDs and prints only structural field names
and a few numeric statistic labels; this is not a production stats importer.
"""
import json
import sqlite3
import urllib.error
import urllib.parse

from update_players import ROOT, get_json

def shape(value, depth=0):
    if depth >= 3:
        return type(value).__name__
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in list(value.items())[:12]}
    if isinstance(value, list):
        return [shape(value[0], depth + 1)] if value else []
    if isinstance(value, (int, float, str)):
        return str(value)[:80]
    return type(value).__name__

def main():
    db = sqlite3.connect("file:" + str((ROOT / "player_data.sqlite3").resolve()) + "?mode=ro", uri=True)
    try:
        rows = db.execute(
            "SELECT league,player_id FROM players WHERE player_id <> '' ORDER BY league,player_id LIMIT 2"
        ).fetchall()
    finally:
        db.close()
    for league, athlete_id in rows:
        url = (
            "https://sports.core.api.espn.com/v2/sports/soccer/leagues/"
            + urllib.parse.quote(league, safe="")
            + "/athletes/" + urllib.parse.quote(athlete_id, safe="")
            + "/statistics"
        )
        try:
            result = get_json(url)
            categories = result.get("splits", {}).get("categories", [])
            for cat in categories[:6]:
                if isinstance(cat, dict):
                    print("STATS_CATEGORY " + json.dumps({
                        "name": cat.get("name"), "displayName": cat.get("displayName"),
                        "keys": list(cat),
                        "stats": cat.get("stats", [])[:18],
                        "names": cat.get("names", [])[:18],
                        "labels": cat.get("labels", [])[:18],
                    }, ensure_ascii=False)[:3500], flush=True)
            print("STATS_META " + json.dumps({
                "keys": list(result), "season": result.get("season"),
                "seasonType": result.get("seasonType"),
                "splitKeys": list(result.get("splits", {}))
            }, ensure_ascii=False)[:1400], flush=True)
            print(f"STATS_PROBE {league} athlete={athlete_id}: "
                  + json.dumps(shape(result), ensure_ascii=False)[:2200], flush=True)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            print(f"STATS_PROBE_UNAVAILABLE {league} athlete={athlete_id}: {exc}", flush=True)

if __name__ == "__main__":
    main()
