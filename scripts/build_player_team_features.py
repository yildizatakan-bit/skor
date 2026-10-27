#!/usr/bin/env python3
"""Publish current, verified player snapshots for upcoming fixtures only.

This is a data-quality and feature feed, NOT a trained player-impact model.
Never backfills historical lineups, never changes past predictions or
match_archive.sqlite3, and never converts missing data to zero.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import sqlite3

from update_players import ROOT, DB_PATH, now

OUTPUT = ROOT / "player_team_features.json"
UTC = timezone.utc
FIELDS = ("goals", "assists", "appearances", "shots", "shots_on_target",
          "yellow_cards", "red_cards", "interceptions", "tackles", "saves")


def future_team_ids(data, instant):
    teams = {}
    for match in data.get("events", []):
        if not isinstance(match, dict) or match.get("done") or not match.get("date"):
            continue
        try:
            kickoff = datetime.fromisoformat(match["date"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if kickoff.tzinfo is None or kickoff <= instant:
            continue
        league = match.get("league")
        if not isinstance(league, str) or not league:
            continue
        for side in ("home", "away"):
            team = match.get(side) or {}
            ident = str(team.get("id") or "")
            if ident.isdigit():
                teams[(league, ident)] = team.get("name") or ident
    return teams


def numeric(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return None


def build(db, data, instant):
    teams = future_team_ids(data, instant)
    if not teams:
        return {"version": 1, "generatedAt": now(), "teams": {},
                "coverage": {"upcomingTeams": 0, "teamsWithRosters": 0,
                             "teamsWithSeasonGoals": 0},
                "warning": "No upcoming fixture teams found. Not historical player data."}
    league_years = dict(db.execute(
        "SELECT league,MAX(season_year) FROM player_season_stats GROUP BY league"))
    by_team = defaultdict(list)
    for row in db.execute("""
      SELECT p.league,p.team_id,p.player_id,p.name,p.position,p.age,p.nationality,
             p.availability,p.market_value_eur,p.market_value_source,
             p.market_value_asof,p.fetched_at,
             s.season_year,s.goals,s.assists,s.appearances,s.shots,
             s.shots_on_target,s.yellow_cards,s.red_cards,
             s.interceptions,s.tackles,s.saves,s.fetched_at
      FROM players p
      LEFT JOIN player_season_stats s
        ON s.league=p.league AND s.player_id=p.player_id
       AND s.season_year=(SELECT MAX(ss.season_year) FROM player_season_stats ss
                          WHERE ss.league=p.league AND ss.player_id=p.player_id)
    """):
        league, tid, pid, name, position, age, nation, avail, market, market_source, market_asof, roster_at, year, *values = row
        if (league, tid) not in teams:
            continue
        stats_values = values[:len(FIELDS)]
        stat_at = values[len(FIELDS)]
        # Do not confuse last season's totals with the current competition season.
        current = year is not None and year == league_years.get(league)
        stats = {field: numeric(value) if current else None
                 for field, value in zip(FIELDS, stats_values)}
        by_team[(league, tid)].append({
            "id": pid, "name": name, "position": position, "age": age,
            "nationality": nation, "availability": avail or "unknown",
            "seasonYear": year if current else None,
            "seasonStatsAsOf": stat_at if current else None,
            "goals": stats["goals"], "assists": stats["assists"],
            "appearances": stats["appearances"], "shots": stats["shots"],
            "shotsOnTarget": stats["shots_on_target"],
            "yellowCards": stats["yellow_cards"], "redCards": stats["red_cards"],
            "interceptions": stats["interceptions"], "tackles": stats["tackles"],
            "saves": stats["saves"], "marketValueEur": numeric(market),
            "marketValueSource": market_source if market is not None else None,
            "marketValueAsOf": market_asof if market is not None else None,
            "rosterAsOf": roster_at,
        })
    output = {}
    with_roster = with_goals = 0
    for (league, tid), name in sorted(teams.items()):
        players = by_team.get((league, tid), [])
        n = len(players)
        with_roster += bool(n)
        goals = [p["goals"] for p in players if p["goals"] is not None]
        apps = [p["appearances"] for p in players if p["appearances"] is not None]
        known_market = [p["marketValueEur"] for p in players if p["marketValueEur"] is not None]
        with_goals += bool(goals)
        output[f"{league}:{tid}"] = {
            "name": name, "league": league, "teamId": tid,
            "seasonYear": league_years.get(league),
            "rosterPlayers": n,
            "verifiedSeasonGoalPlayers": len(goals),
            "verifiedSeasonAppearancePlayers": len(apps),
            "verifiedSeasonGoalCoverage": round(len(goals)/n, 4) if n else None,
            "verifiedSeasonGoalsSum": round(sum(goals), 2) if goals else None,
            "verifiedSeasonAppearancesSum": round(sum(apps), 2) if apps else None,
            "marketValuePlayers": len(known_market),
            "knownMarketValueEurSum": round(sum(known_market), 2) if known_market else None,
            "availabilityReportedPlayers": sum(p["availability"] != "unknown" for p in players),
            "rosterAsOf": max((p["rosterAsOf"] or "" for p in players), default=None),
            "players": sorted(players, key=lambda p: p["name"].casefold()),
        }
    return {
        "version": 1, "generatedAt": now(), "source": "ESPN rosters and season-scoped core statistics",
        "scope": "Upcoming fixture teams only; current snapshot, not historical",
        "teams": output,
        "coverage": {"upcomingTeams": len(teams), "teamsWithRosters": with_roster,
                     "teamsWithSeasonGoals": with_goals},
        "limitations": {
            "assists": "Unavailable in verified ESPN soccer fields; NULL is not zero",
            "recentForm": "Not available until verified match-by-match history is collected",
            "marketValue": "Only values from licensed and attributed imports",
            "lineup": "Roster membership does not establish match starting XI",
            "model": "Descriptive only; not yet used to change Poisson or ML probabilities",
            "backtesting": "Never use a current player snapshot for past fixtures",
        }
    }


def main():
    data = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))
    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)
    db = sqlite3.connect(f"file:{DB_PATH.resolve()}?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        result = build(db, data, datetime.now(UTC))
    finally:
        db.close()
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False,
                                 separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print("PLAYER_TEAM_FEATURES " + json.dumps(result["coverage"]), flush=True)


if __name__ == "__main__":
    main()
