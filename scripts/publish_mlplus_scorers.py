"""Publish pre-match scorer estimates independently of MLPlus training."""
import importlib.util
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from mlplus import squad, scorer_predictions, starting_ids, stamp

def retain_confirmed(sides, old, kickoff):
    if old.get("kickoff") != kickoff:
        return sides
    previous = old.get("sides", [])
    return [previous[i] if i < len(previous) and previous[i].get("selection") == "confirmed"
            and side.get("selection") != "confirmed" else side
            for i, side in enumerate(sides)]

def publish(data, feed, minutes, previous, instant, lineup_lookup=starting_ids):
    predictions = dict(previous.get("predictions", {}))
    for match in data.get("events", []):
        cutoff = stamp(match.get("date"))
        if match.get("done") or match.get("live") or not instant.timestamp() < cutoff <= instant.timestamp()+7*86400:
            continue
        # Lineups are checked more frequently near kickoff, without delaying
        # initial estimates for the rest of the fixture list.
        lineup = lineup_lookup(match) if cutoff-instant.timestamp() < 2*3600 else {}
        sides = [squad(feed.get("teams", {}).get(f'{match["league"]}:{match[side]["id"]}', {}),
                       minutes.get(f'{match["league"]}:{match[side]["id"]}', {}),
                       lineup.get(str(match[side]["id"]), set()), cutoff)
                 for side in ("home", "away")]
        old = predictions.get(match["id"], {})
        sides = retain_confirmed(sides, old, match["date"])
        if not any(side["players"] for side in sides):
            continue
        captured = datetime.now(timezone.utc)
        if captured.timestamp() >= cutoff:
            continue
        predictions[match["id"]] = {"kickoff": match["date"], "recordedAt": captured.isoformat(),
                                    "selection": [side["selection"] for side in sides],
                                    "scorers": scorer_predictions(sides), "sides": sides}
    return {"generatedAt":instant.isoformat(), "predictions":predictions}

def main():
    root = Path("player-source")
    data = json.loads(Path("data.json").read_text())
    instant = datetime.now(timezone.utc)
    # Build from the last successful DB values for ALL upcoming fixtures,
    # even when today's refresh has failed or the exported feed was empty.
    spec = importlib.util.spec_from_file_location("build_player_team_features", root/"scripts/build_player_team_features.py")
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.path.insert(0, str(root/"scripts"))
    spec.loader.exec_module(module)
    with sqlite3.connect(f"file:{root}/player_data.sqlite3?mode=ro", uri=True) as db:
        feed = module.build(db, data, instant)
        minutes = {}
        for league, team, player, value, observed in db.execute(
                "SELECT league,team_id,player_id,minutes,fetched_at FROM players WHERE minutes > 0"):
            if stamp(observed) <= instant.timestamp():
                minutes.setdefault(f"{league}:{team}", {})[str(player)] = value
    path = Path("mlplus_scorers.json")
    previous = json.loads(path.read_text()) if path.exists() else {}
    result = publish(data, feed, minutes, previous, instant)
    path.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))+"\n")
    print("Scorer forecasts:", len(result["predictions"]))

if __name__ == "__main__":
    main()
