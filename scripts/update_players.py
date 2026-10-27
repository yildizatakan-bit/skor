#!/usr/bin/env python3
"""Separate player-data collector; NEVER opens or changes match_archive.sqlite3.

Sources: ESPN public soccer team roster/injuries endpoints. All unavailable
values remain null. Market values require an independently licensed CSV.
Only a small batch of teams is requested per run to limit public API load.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import pathlib
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "player_data.sqlite3"
OUTPUT = ROOT / "player_features.json"
BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
UTC = dt.timezone.utc


def now():
    return dt.datetime.now(UTC).isoformat()


def get_json(url):
    request = urllib.request.Request(
        url, headers={"User-Agent": "MaçAnalizPro/1.0 (player-data; research)",
                      "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=18) as response:
        return json.load(response)


def num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        result = float(str(v).replace(",", "").strip())
        if result < 0 or result > 100000:
            return None
        return result
    except (TypeError, ValueError):
        return None


def stat_map(athlete):
    """Only trust explicitly named numeric statistics, never positional arrays."""
    aliases = {
        "goals": ("goals", "goalsscored"),
        "assists": ("assists",),
        "appearances": ("appearances", "gamesplayed", "matchesplayed"),
        "minutes": ("minutesplayed", "minutes"),
    }
    raw = athlete.get("statistics") or athlete.get("stats")
    pairs = {}
    if isinstance(raw, dict) and "$ref" not in raw:
        for k, v in raw.items():
            if isinstance(v, (int, float, str)):
                pairs["".join(c for c in str(k).lower() if c.isalnum())] = v
    elif isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            key = entry.get("name") or entry.get("displayName") or entry.get("label")
            value = entry.get("value")
            if key and value is not None:
                pairs["".join(c for c in str(key).lower() if c.isalnum())] = value
    result = {}
    for name, keys in aliases.items():
        result[name] = next((num(pairs[k]) for k in keys if k in pairs), None)
    return result


def athletes(payload):
    """Support both grouped {position,items} and flat ESPN roster shapes."""
    groups = payload.get("athletes", []) if isinstance(payload, dict) else []
    if isinstance(groups, dict):
        groups = list(groups.values())
    result = []
    if not isinstance(groups, list):
        return result
    for group in groups:
        if not isinstance(group, dict):
            continue
        items = group.get("items")
        if isinstance(items, list):
            result.extend(x for x in items if isinstance(x, dict))
        elif group.get("id") or group.get("athlete", {}).get("id"):
            result.append(group)
    return result


def player_row(athlete):
    obj = athlete.get("athlete", athlete)
    if not isinstance(obj, dict):
        return None
    ident = str(obj.get("id") or "")
    name = obj.get("fullName") or obj.get("displayName") or obj.get("name")
    if not ident or not name:
        return None
    position = obj.get("position") or athlete.get("position") or {}
    if isinstance(position, dict):
        position = position.get("displayName") or position.get("name") or position.get("abbreviation")
    s = stat_map(obj)
    return {
        "id": ident, "name": str(name), "position": str(position or ""),
        "age": int(obj["age"]) if str(obj.get("age", "")).isdigit() else None,
        "nationality": str(obj.get("citizenship") or obj.get("nationality") or ""),
        "goals": s["goals"], "assists": s["assists"],
        "appearances": s["appearances"], "minutes": s["minutes"],
        # A season total is not the player's recent 5-game form.
        "recentForm": None,
        "marketValueEur": None, "marketValueSource": None,
        "availability": "unknown",
    }


def injuries(payload):
    """No injury report does not prove players are fit; retain 'unknown'."""
    raw = payload.get("injuries", []) if isinstance(payload, dict) else []
    if not isinstance(raw, list):
        return {}
    result = {}
    for group in raw:
        if not isinstance(group, dict):
            continue
        entries = group.get("injuries") or group.get("items")
        entries = entries if isinstance(entries, list) else [group]
        for item in entries:
            if not isinstance(item, dict):
                continue
            athlete = item.get("athlete") or item.get("player") or {}
            if not isinstance(athlete, dict):
                continue
            ident = str(athlete.get("id") or "")
            status = item.get("status") or item.get("type") or ""
            if isinstance(status, dict):
                status = status.get("name") or status.get("description") or ""
            if ident and status:
                result[ident] = str(status)[:100]
    return result


def init(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS players (
      league TEXT NOT NULL, team_id TEXT NOT NULL, player_id TEXT NOT NULL,
      name TEXT NOT NULL, position TEXT, age INTEGER, nationality TEXT,
      goals REAL, assists REAL, appearances REAL, minutes REAL, recent_form REAL,
      market_value_eur REAL, market_value_source TEXT, market_value_asof TEXT,
      availability TEXT NOT NULL DEFAULT 'unknown', fetched_at TEXT NOT NULL,
      PRIMARY KEY (league,team_id,player_id)
    );
    CREATE TABLE IF NOT EXISTS team_snapshots (
      league TEXT NOT NULL, team_id TEXT NOT NULL, as_of TEXT NOT NULL,
      player_count INTEGER NOT NULL, goals_known INTEGER NOT NULL,
      assists_known INTEGER NOT NULL, injuries_reported INTEGER NOT NULL,
      payload TEXT NOT NULL,
      PRIMARY KEY (league,team_id,as_of)
    );
    CREATE TABLE IF NOT EXISTS refresh_status (
      league TEXT NOT NULL, team_id TEXT NOT NULL, checked_at TEXT NOT NULL,
      success INTEGER NOT NULL, error TEXT,
      PRIMARY KEY (league,team_id)
    );
    """)


def teams_from_snapshot():
    try:
        data = json.loads((ROOT / "data.json").read_text("utf-8"))
    except (OSError, ValueError):
        return []
    # Current/upcoming teams only. Historic matches never trigger roster scraping.
    result = {}
    for m in data.get("events", []):
        if not isinstance(m, dict) or m.get("done"):
            continue
        league = m.get("league")
        if not league:
            continue
        for side in ("home", "away"):
            team = m.get(side) or {}
            ident = str(team.get("id") or "")
            if ident.isdigit():
                result[(league, ident)] = team.get("name") or ident
    return [(league, ident, name) for (league, ident), name in sorted(result.items())]


def licensed_market_values():
    """Optional opt-in import, NOT Transfermarkt scraping.
    CSV: league,team_id,player_id,market_value_eur,source,as_of
    """
    filename = os.getenv("LICENSED_PLAYER_VALUES_CSV", "").strip()
    if not filename:
        return {}
    path = pathlib.Path(filename).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    result = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            value = num(row.get("market_value_eur"))
            source = (row.get("source") or "").strip()
            as_of = (row.get("as_of") or "").strip()
            if value is None or not source or not as_of:
                continue
            result[(row["league"], row["team_id"], row["player_id"])] = (value, source, as_of)
    return result


def update_one(db, league, team_id, market):
    base = f"{BASE}/{urllib.parse.quote(league)}/teams/{urllib.parse.quote(team_id)}"
    roster = get_json(base + "/roster")
    players = [row for athlete in athletes(roster) if (row := player_row(athlete))]
    if not players:
        raise ValueError("roster returned no identifiable players")
    try:
        statuses = injuries(get_json(base + "/injuries"))
    except (OSError, urllib.error.URLError, ValueError, KeyError):
        statuses = {}
    timestamp = now()
    for p in players:
        p["availability"] = statuses.get(p["id"], "unknown")
        licensed = market.get((league, team_id, p["id"]))
        if licensed:
            p["marketValueEur"], p["marketValueSource"], p["marketValueAsOf"] = licensed
        else:
            p["marketValueAsOf"] = None
        db.execute("""
          INSERT INTO players (
            league,team_id,player_id,name,position,age,nationality,
            goals,assists,appearances,minutes,recent_form,
            market_value_eur,market_value_source,market_value_asof,availability,fetched_at
          ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(league,team_id,player_id) DO UPDATE SET
            name=excluded.name,position=excluded.position,age=excluded.age,
            nationality=excluded.nationality,goals=excluded.goals,assists=excluded.assists,
            appearances=excluded.appearances,minutes=excluded.minutes,
            recent_form=excluded.recent_form,
            market_value_eur=COALESCE(excluded.market_value_eur,players.market_value_eur),
            market_value_source=COALESCE(excluded.market_value_source,players.market_value_source),
            market_value_asof=COALESCE(excluded.market_value_asof,players.market_value_asof),
            availability=excluded.availability,fetched_at=excluded.fetched_at
        """, (league,team_id,p["id"],p["name"],p["position"],p["age"],p["nationality"],
              p["goals"],p["assists"],p["appearances"],p["minutes"],p["recentForm"],
              p["marketValueEur"],p["marketValueSource"],p["marketValueAsOf"],
              p["availability"],timestamp))
    known_goals = sum(p["goals"] is not None for p in players)
    known_assists = sum(p["assists"] is not None for p in players)
    # Store point-in-time snapshots for future leakage-free backtesting.
    db.execute("""INSERT INTO team_snapshots
      (league,team_id,as_of,player_count,goals_known,assists_known,injuries_reported,payload)
      VALUES (?,?,?,?,?,?,?,?)""",
      (league,team_id,timestamp,len(players),known_goals,known_assists,
       sum(p["availability"] != "unknown" for p in players),
       json.dumps(players,ensure_ascii=False,separators=(",",":"))))
    return len(players)


def export(db):
    teams = {}
    for league, team_id, as_of, count, goals, assists, injuries_count, payload in db.execute("""
      SELECT s.league,s.team_id,s.as_of,s.player_count,s.goals_known,
             s.assists_known,s.injuries_reported,s.payload
      FROM team_snapshots s
      JOIN (SELECT league,team_id,MAX(as_of) AS latest FROM team_snapshots
            GROUP BY league,team_id) x
      ON s.league=x.league AND s.team_id=x.team_id AND s.as_of=x.latest
    """):
        players = json.loads(payload)
        teams[f"{league}:{team_id}"] = {
            "asOf":as_of,"playerCount":count,"goalsKnown":goals,
            "assistsKnown":assists,"injuriesReported":injuries_count,
            "players":players,
        }
    OUTPUT.write_text(json.dumps({
        "version":1,"updatedAt":now(),"source":"ESPN soccer roster/injuries; optional licensed CSV",
        "teams":teams,
        "limitations":{
            "recentForm":"unknown unless verified match-by-match stats are ingested",
            "marketValue":"null unless a licensed external dataset is provided",
            "availability":"unknown when injury feed is missing; no false healthy assumptions",
        },
    },ensure_ascii=False,separators=(",",":")),encoding="utf-8")


def main():
    db = sqlite3.connect(str(DB_PATH))
    try:
        init(db)
        targets = teams_from_snapshot()
        batch = max(1,min(40,int(os.getenv("PLAYER_TEAM_BATCH","12"))))
        last = {(league,ident): (checked,success) for league,ident,checked,success
                in db.execute("SELECT league,team_id,checked_at,success FROM refresh_status")}
        targets.sort(key=lambda t:last.get((t[0],t[1]),("",0))[0])
        market = licensed_market_values()
        completed = 0
        for league,ident,name in targets[:batch]:
            timestamp = now()
            try:
                count = update_one(db,league,ident,market)
                db.execute("""INSERT INTO refresh_status VALUES(?,?,?,1,NULL)
                  ON CONFLICT(league,team_id) DO UPDATE SET
                    checked_at=excluded.checked_at,success=1,error=NULL""",
                    (league,ident,timestamp))
                completed += 1
                print(f"OK {league}/{name}: {count} players",flush=True)
            except (OSError,ValueError,KeyError,TypeError,urllib.error.URLError) as exc:
                db.execute("""INSERT INTO refresh_status VALUES(?,?,?,0,?)
                  ON CONFLICT(league,team_id) DO UPDATE SET
                    checked_at=excluded.checked_at,success=0,error=excluded.error""",
                    (league,ident,timestamp,str(exc)[:250]))
                print(f"UNAVAILABLE {league}/{name}: {exc}",flush=True)
            db.commit()
            time.sleep(0.4)
        export(db)
        print(f"Player team targets: {len(targets)}; updated: {completed}",flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
