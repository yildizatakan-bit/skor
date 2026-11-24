"""Train MLPlus and publish a separate prospective prediction ledger."""
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

LABELS = ("home", "draw", "away")
PLAYER_FIELDS = ("goals", "assists", "appearances", "minutes", "shots",
                 "shotsOnTarget", "interceptions", "tackles", "saves",
                 "yellowCards", "redCards", "age")

EXTRA_FIELDS = ()

def player_vector(side):
    players = [p for p in side.get("players", []) if p.get("selected", True)]
    values = []
    for field in PLAYER_FIELDS:
        known = [p[field] for p in players if type(p.get(field)) in (int, float)
                 and math.isfinite(p[field])]
        values.extend([sum(known)/len(known) if known else 0,
                       len(known)/max(1, len(players))])
    for field in EXTRA_FIELDS:
        known = [p.get("extraStats", {}).get(field) for p in players]
        known = [v for v in known if type(v) in (int, float) and math.isfinite(v)]
        values.extend([sum(known)/len(known) if known else 0,
                       len(known)/max(1, len(players))])
    for status in ("injur", "suspend", "out", "doubtful", "questionable"):
        values.append(sum(status in str(p.get("availability", "")).lower()
                          for p in players))
    values.append(sum(p.get("availability", "unknown") != "unknown"
                      for p in players)/max(1, len(players)))
    return values



def read(path):
    return json.loads(Path(path).read_text())


def stamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return math.inf


def features(p, home, away):
    if any(not isinstance(p.get(k), (float, int)) for k in LABELS):
        return None
    if any(not isinstance(x.get("knownSeasonGoals"), (int, float))
           or x.get("goalCoverage", 0) < .7 for x in (home, away)):
        return None
    h, a = (x["knownSeasonGoals"] for x in (home, away))
    return [p[k] for k in LABELS] + [(h-a)/(1+h+a), home["goalCoverage"]-away["goalCoverage"]] + player_vector(home) + player_vector(away)


def starting_ids(match):
    event = str(match["id"]).rsplit(":", 1)[-1]
    if not event.isdecimal():
        return {}
    url = f'https://site.api.espn.com/apis/site/v2/sports/soccer/{match["league"]}/summary?event={event}'
    try:
        with urlopen(url, timeout=6) as response:
            payload = json.load(response)
        status = payload["header"]["competitions"][0]["status"]["type"]["state"]
        if status != "pre":
            return {}
        return {str(row["team"]["id"]): {str(p["athlete"]["id"]) for p in row.get("roster", [])
                                          if p.get("starter") is True}
                for row in payload.get("rosters", [])}
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        print(f"Lineup unavailable for {event}: {exc}")
        return {}


def squad(team, minutes, starters, kickoff):
    players = [p for p in team.get("players", []) if stamp(p.get("rosterAsOf")) < kickoff]
    XI = [p for p in players if str(p["id"]) in starters] if len(starters) == 11 else []
    confirmed = len(XI) == 11
    minutes = {**minutes, **{str(p["id"]): p["extraStats"]["minutes"] for p in players
                            if type(p.get("extraStats", {}).get("minutes")) in (int, float)
                            and stamp(p.get("extraStatsAsOf")) < kickoff}}
    ranked = [p for p in players if minutes.get(str(p["id"]), 0) > 0]
    if not confirmed:
        XI = sorted(ranked, key=lambda p: (-minutes[str(p["id"])], str(p["id"])))[:11]
    selected = XI if len(XI) == 11 else players
    known = [p for p in selected if isinstance(p.get("goals"), (int, float))
             and stamp(p.get("seasonStatsAsOf")) < kickoff]
    # Use mean goals per observed player scaled to roster size to retain
    # the units of the historical roster feature used during training.
    captured_players = []
    for player in players:
        entry = dict(player)
        entry["selected"] = player in selected
        entry["minutes"] = player.get("extraStats", {}).get("minutes", minutes.get(str(player["id"])))
        if stamp(player.get("seasonStatsAsOf")) >= kickoff:
            for field in PLAYER_FIELDS:
                if field not in ("age", "minutes"):
                    entry[field] = None
        if stamp(player.get("extraStatsAsOf")) >= kickoff:
            entry["extraStats"] = {}
            entry["minutes"] = minutes.get(str(player["id"]))
        captured_players.append(entry)
    return {"players": captured_players, "goalCoverage": len(known)/max(1, len(selected)),
            "knownSeasonGoals": sum(p["goals"] for p in known)*len(players)/max(1,len(known)),
            "selection": "confirmed" if confirmed else "minutes" if len(XI)==11 else "roster"}


def scorer_predictions(sides):
    """Season-rate Poisson estimates conditional on selected lineup/minutes.

    This is a separate scoring estimator, not the MLPlus 1X2 classifier.
    Unknown participation is never presented as a zero scoring chance.
    """
    result = {}
    for side_name, side in zip(("home", "away"), sides):
        rows = []
        for player in side.get("players", []):
            goals, apps, minutes = (player.get(k) for k in ("goals", "appearances", "minutes"))
            valid = lambda v: type(v) in (int, float) and math.isfinite(v) and v >= 0
            selected = player.get("selected", True)
            unavailable = any(word in str(player.get("availability", "")).lower()
                              for word in ("injur", "suspend", "out"))
            rate = goals/apps if valid(goals) and valid(apps) and apps > 0 else None
            expected = min(90., minutes/apps) if valid(minutes) and valid(apps) and apps > 0 and minutes > 0 else None
            per90 = 90*goals/minutes if valid(goals) and valid(minutes) and minutes > 0 else None
            # Prefer observed season minutes, otherwise use goals/appearance.
            lam = per90*expected/90 if per90 is not None and expected is not None else rate
            probability = -math.expm1(-lam) if selected and not unavailable and lam is not None else None
            rows.append({"id": player["id"], "name": player.get("name", player["id"]),
                         "seasonYear": player.get("seasonYear"),
                         "seasonFallback": player.get("seasonFallback", False),
                         "goals": goals if valid(goals) else None,
                         "appearances": apps if valid(apps) else None,
                         "minutes": minutes if valid(minutes) else None,
                         "goalsPer90": per90, "goalsPerAppearance": rate,
                         "expectedMinutes": expected, "probability": probability,
                         "selected": selected, "availability": player.get("availability", "unknown"),
                         "statsAsOf": player.get("seasonStatsAsOf"),
                         "selection": side.get("selection", "roster")})
        result[side_name] = sorted(rows, key=lambda row: (
            row["probability"] is None, -(row["probability"] or 0), str(row["name"])))
    return {"method": "season_rate_poisson_v1", "conditionalOnSelection": True,
            "teams": result}


def main():
    global EXTRA_FIELDS
    data, ml = read("data.json"), read("ml_predictions.json")
    ledger = read("player-source/player_signal_history.json")
    feed = read("player-source/player_team_features.json")
    player_history_path = Path("mlplus_player_history.json")
    player_history = read(player_history_path) if player_history_path.exists() else []
    ledger["snapshots"] = player_history + ledger["snapshots"]
    # Stable schema includes every provider numeric field observed in saved
    # pre-match records or the current feed. Missing historical fields retain
    # zero coverage; current values are never backfilled into past matches.
    sides = [side for row in ledger["snapshots"] for side in (row["home"], row["away"])]
    sides.extend(feed["teams"].values())
    EXTRA_FIELDS = tuple(sorted({key for side in sides for player in side.get("players", [])
                                for key, value in player.get("extraStats", {}).items()
                                if type(value) in (int, float) and math.isfinite(value)}))
    outcomes = {x["matchId"]: x["outcome"] for x in ledger["settlements"]}
    models = {x["matchId"]: x for x in ledger.get("modelSnapshots",[]) if x["model"] == "ml"}
    rows = {}
    for row in ledger["snapshots"]:
        key = row["matchId"]
        cutoff = stamp(row["kickoff"])
        if not stamp(row["playerFeedGeneratedAt"]) <= stamp(row["frozenAt"]) < cutoff:
            continue
        p = row.get("probabilities", {}).get("ml") or {}
        saved = models.get(key, {})
        if not p and stamp(saved.get("sourceRecordedAt")) <= stamp(saved.get("frozenAt")) < cutoff:
            p = saved["p"]
        x = features(p, row["home"], row["away"])
        if x and outcomes.get(key) in LABELS:
            rows.setdefault(key, (x, outcomes[key]))
    if len(rows) < 60 or len({y for _,y in rows.values()}) != 3:
        raise RuntimeError(f"Insufficient MLPlus training rows: {len(rows)}")
    x, y = zip(*rows.values())
    model = make_pipeline(StandardScaler(), LogisticRegression(C=.12, max_iter=1200))
    model.fit(x,y)
    path = Path("mlplus_predictions.json")
    previous = read(path) if path.exists() else {}
    predictions = previous.get("predictions", {})
    now = datetime.now(timezone.utc)
    local = ZoneInfo("Europe/Istanbul")
    with sqlite3.connect("file:player-source/player_data.sqlite3?mode=ro", uri=True) as db:
        minutes = {}
        for league, team, player, played, observed in db.execute(
                "SELECT league,team_id,player_id,minutes,fetched_at FROM players WHERE minutes > 0"):
            if stamp(observed) <= now.timestamp():
                minutes.setdefault(f"{league}:{team}", {})[str(player)] = played
    for m in data["events"]:
        cutoff = stamp(m["date"])
        if not now.timestamp() < cutoff <= now.timestamp()+7*86400 or m.get("done") or m.get("live"):
            continue
        if not stamp(feed.get("generatedAt")) <= now.timestamp() or not stamp(ml.get("generatedAt")) <= now.timestamp():
            continue
        today = datetime.fromtimestamp(cutoff,local).date() == now.astimezone(local).date()
        lineup = starting_ids(m) if today else {}
        sides = []
        for side in ("home","away"):
            ident = str(m[side]["id"])
            team_key = f'{m["league"]}:{ident}'
            sides.append(squad(feed["teams"].get(team_key,{}), minutes.get(team_key,{}),
                               lineup.get(ident,set()), cutoff))
        p = ml["predictions"].get(m["id"],{})
        vector = features(p.get("p",p),*sides)
        captured = datetime.now(timezone.utc)
        if vector is None or captured.timestamp() >= cutoff:
            continue
        player_history.append({"matchId":m["id"], "kickoff":m["date"],
                               "frozenAt":captured.isoformat(),
                               "playerFeedGeneratedAt":feed["generatedAt"],
                               "probabilities":{"ml":p.get("p",p)},
                               "home":sides[0], "away":sides[1]})
        old = predictions.get(m["id"],{})
        selection = [s["selection"] for s in sides]
        if old.get("kickoff") == m["date"] and old.get("selection", []).count("confirmed") > selection.count("confirmed"):
            continue
        probabilities = dict(zip(model.classes_, map(float,model.predict_proba([vector])[0])))
        predictions[m["id"]] = {"p": probabilities, "kickoff":m["date"],
                                "recordedAt":captured.isoformat(), "selection":selection,
                                "scorers":scorer_predictions(sides)}
    player_history_path.write_text(json.dumps(player_history, allow_nan=False, separators=(",", ":")))
    history = {r["id"]:r for r in previous.get("history",[])}
    for m in data["events"]:
        p = predictions.get(m["id"])
        if not p or not m.get("done") or p["kickoff"] != m["date"]:
            continue
        score = [m[s].get("score") for s in ("home","away")]
        if all(type(v) is int for v in score):
            history.setdefault(m["id"], {"id":m["id"],"p":p["p"],"settled":True,"score":score})
            del predictions[m["id"]]
    path.write_text(json.dumps({"trainingMatches":len(rows),"extraPlayerFields":list(EXTRA_FIELDS),"inputFeatures":len(x[0]),"generatedAt":now.isoformat(),
                               "predictions":predictions,"history":list(history.values())},
                              allow_nan=False,separators=(",",":"))+"\n")


if __name__ == "__main__":
    main()

