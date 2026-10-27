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
            for cat in categories:
                if isinstance(cat, dict) and cat.get("name") == "offensive":
                    print("OFFENSIVE_KEYS " + json.dumps(
                        [x.get("name") for x in cat.get("stats", []) if isinstance(x, dict)],
                        ensure_ascii=False), flush=True)
            try:
                logurl = url.replace("/statistics", "/statisticslog")
                game_log = get_json(logurl)
                first = (game_log.get("entries") or [{}])[0]
                print("GAMELOG_FIRST " + json.dumps(
                    first, ensure_ascii=False)[:3200], flush=True)
                total = next((x for x in first.get("statistics", [])
                              if x.get("type") == "total"), {})
                ref = total.get("statistics", {}).get("$ref", "")
                if ref.startswith("http://sports.core.api.espn.com/"):
                    ref = "https://" + ref[len("http://"):]
                if ref.startswith("https://sports.core.api.espn.com/"):
                    season_stats = get_json(ref)
                    print("SEASON_STATS " + json.dumps({
                        "keys": list(season_stats),
                        "categories": [
                            {"name": cat.get("name"),
                             "stats": {stat.get("name"): stat.get("value")
                                       for stat in cat.get("stats", [])
                                       if stat.get("name") in
                                       ("totalGoals","goals","assists","goalAssists",
                                        "appearances","gameWinningAssists")}}
                            for cat in season_stats.get("splits", {}).get("categories", [])
                            if isinstance(cat, dict)]
                    }, ensure_ascii=False)[:1700], flush=True)
                print("GAMELOG_SHAPE " + json.dumps({
                    "keys": list(game_log),
                    "sample": shape(game_log, depth=1)
                }, ensure_ascii=False)[:2800], flush=True)
            except (OSError, ValueError, urllib.error.URLError) as exc:
                print("GAMELOG_UNAVAILABLE " + str(exc), flush=True)
            for label, alternative in [
                ("SEASON_QUERY", url + "?season=2026"),
                ("SEASON_PATH", "https://sports.core.api.espn.com/v2/sports/soccer/leagues/"
                 + urllib.parse.quote(league, safe="")
                 + "/seasons/2026/athletes/" + urllib.parse.quote(athlete_id, safe="")
                 + "/statistics")
            ]:
                try:
                    variant = get_json(alternative)
                    print(label + " " + json.dumps({
                        "keys": list(variant), "season": variant.get("season"),
                        "categoryNames": [x.get("name") for x in
                            variant.get("splits", {}).get("categories", [])
                            if isinstance(x, dict)],
                        "firstGeneralStat": next((
                            x.get("value") for cat in
                            variant.get("splits", {}).get("categories", [])
                            if isinstance(cat, dict)
                            for x in cat.get("stats", [])
                            if isinstance(x, dict) and x.get("name") == "appearances"), None)
                    }, ensure_ascii=False)[:1200], flush=True)
                except (OSError, ValueError, urllib.error.URLError) as exc:
                    print(label + "_UNAVAILABLE " + str(exc), flush=True)
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
