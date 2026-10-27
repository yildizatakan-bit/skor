#!/usr/bin/env python3
"""Experimental 1X2 ML model. Reads the historical SQLite archive strictly read-only.

Outputs ONLY ml_predictions.json, ml_prediction_history.json and ml_metrics.json.
No historical database or existing Poisson predictions are ever written.
"""
from __future__ import annotations

import itertools
import json
import math
import pathlib
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from analysis_engine import team_key, when

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODEL_VERSION = "ml-v49-logistic-1x2"
LABELS = ("home", "draw", "away")
NOW = datetime.now(timezone.utc)


def read_json(name, fallback):
    try:
        return json.loads((ROOT / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def write_json(name, data):
    (ROOT / name).write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )


def finished(m):
    if not m.get("done") or not isinstance(m.get("home"), dict) or not isinstance(m.get("away"), dict):
        return False
    h, a = m["home"].get("score"), m["away"].get("score")
    return type(h) is int and type(a) is int and 0 <= h <= 30 and 0 <= a <= 30


def parsed_date(m):
    try:
        return when(m["date"])
    except (KeyError, TypeError, ValueError):
        return None


def load_archive():
    # SQLite mode=ro prevents writes to the historical database, even accidentally.
    db_path = ROOT / "match_archive.sqlite3"
    db = sqlite3.connect("file:" + str(db_path.resolve()) + "?mode=ro", uri=True)
    db.execute("PRAGMA query_only=ON")
    try:
        matches = [json.loads(payload) for (payload,) in db.execute(
            "SELECT payload FROM matches WHERE done=1 ORDER BY date, id"
        )]
    finally:
        db.close()
    return [m for m in matches if finished(m) and parsed_date(m) is not None]


def stats(history, key, venue_home, kickoff):
    rows = history.get(key, [])[-10:]
    if len(rows) < 5:
        return None
    venue = [r for r in rows if r["home"] == venue_home]
    if not venue:
        venue = rows
    mean = lambda seq, field: sum(x[field] for x in seq) / len(seq)
    rest = max(1, min(28, (kickoff - rows[-1]["date"]).total_seconds() / 86400))
    return [
        mean(rows, "gf"), mean(rows, "ga"), mean(rows, "points"),
        mean(venue, "gf"), mean(venue, "ga"), rest / 14,
        len(rows) / 10,
    ]


def features(m, history, kickoff):
    home_key = team_key(m["home"].get("name"))
    away_key = team_key(m["away"].get("name"))
    if not home_key or not away_key or home_key == away_key:
        return None
    h = stats(history, home_key, True, kickoff)
    a = stats(history, away_key, False, kickoff)
    if h is None or a is None:
        return None
    return h + a + [h[0] - a[1], a[0] - h[1], h[2] - a[2]]


def build_training(events):
    history = defaultdict(list)
    X, y, dates = [], [], []
    ordered = sorted(events, key=lambda m: (parsed_date(m), str(m.get("id", ""))))
    # Games with the exact same kickoff must not be visible to each other.
    for kickoff, group_iter in itertools.groupby(ordered, key=parsed_date):
        group = list(group_iter)
        for m in group:
            f = features(m, history, kickoff)
            if f is not None:
                h, a = m["home"]["score"], m["away"]["score"]
                X.append(f)
                y.append(0 if h > a else 1 if h == a else 2)
                dates.append(kickoff)
        for m in group:
            for side, opponent, is_home in (("home", "away", True), ("away", "home", False)):
                k = team_key(m[side].get("name"))
                if not k:
                    continue
                gf, ga = m[side]["score"], m[opponent]["score"]
                history[k].append({
                    "date": kickoff, "gf": gf, "ga": ga,
                    "points": 3 if gf > ga else 1 if gf == ga else 0,
                    "home": is_home,
                })
                if len(history[k]) > 10:
                    history[k] = history[k][-10:]
    return np.asarray(X, dtype=float), np.asarray(y, dtype=int), dates, history


def probabilities(model, f):
    result = {label: 0.0 for label in LABELS}
    for cls, p in zip(model.classes_, model.predict_proba([f])[0]):
        result[LABELS[int(cls)]] = float(p)
    return result


def evaluate(pairs):
    if not pairs:
        return {"matches": 0, "accuracy": None, "logLoss": None, "brier": None}
    hits, losses, briers = 0, [], []
    for probs, outcome in pairs:
        vals = [probs[k] for k in LABELS]
        hits += int(int(np.argmax(vals)) == outcome)
        losses.append(-math.log(max(1e-12, vals[outcome])))
        briers.append(sum((vals[i] - int(i == outcome)) ** 2 for i in range(3)))
    n = len(pairs)
    return {"matches": n, "accuracy": round(hits / n, 5),
            "logLoss": round(sum(losses) / n, 5),
            "brier": round(sum(briers) / n, 5)}


def valid_p(p):
    return isinstance(p, dict) and all(
        isinstance(p.get(k), (int, float)) and math.isfinite(p[k]) and 0 <= p[k] <= 1
        for k in LABELS
    ) and abs(sum(p[k] for k in LABELS) - 1) < 0.025


def main():
    events = load_archive()
    X, y, dates, history = build_training(events)
    if len(y) < 120 or len(set(y)) != 3:
        raise RuntimeError(f"Not enough eligible historical matches: {len(y)}")
    cut = int(len(y) * 0.8)
    if len(set(y[:cut])) != 3:
        raise RuntimeError("Chronological training partition is missing a class")

    make_model = lambda: make_pipeline(
        StandardScaler(), LogisticRegression(C=0.35, max_iter=1500, random_state=42)
    )
    test_model = make_model()
    test_model.fit(X[:cut], y[:cut])
    test_pairs = [(probabilities(test_model, f), int(actual))
                  for f, actual in zip(X[cut:], y[cut:])]
    holdout = evaluate(test_pairs)
    holdout.update({"trainMatches": cut, "testFrom": dates[cut].isoformat(),
                    "testTo": dates[-1].isoformat()})

    model = make_model()
    model.fit(X, y)
    snapshot = read_json("data.json", {})
    visible = snapshot.get("events", [])
    if not isinstance(visible, list):
        visible = []

    old_ledger = read_json("ml_prediction_history.json", {}).get("predictions", [])
    if not isinstance(old_ledger, list):
        old_ledger = []
    # Frozen entries are immutable: first prediction for each match ID wins.
    ledger = {}
    for row in old_ledger:
        if isinstance(row, dict) and row.get("matchId") and valid_p(row.get("p")):
            ledger.setdefault(row["matchId"], row)

    for m in visible:
        if not isinstance(m, dict) or not m.get("id") or m.get("done"):
            continue
        kickoff = parsed_date(m)
        if kickoff is None or kickoff <= NOW or m["id"] in ledger:
            continue
        f = features(m, history, kickoff)
        if f is None:
            continue
        p = probabilities(model, f)
        if valid_p(p):
            ledger[m["id"]] = {
                "matchId": m["id"], "kickoff": m["date"],
                "recordedAt": NOW.isoformat(), "model": MODEL_VERSION,
                "home": m["home"].get("name", ""), "away": m["away"].get("name", ""),
                "p": {k: round(p[k], 7) for k in LABELS},
            }

    current_ids = {m.get("id") for m in visible if isinstance(m, dict)}
    current = {mid: {"p": row["p"], "kickoff": row["kickoff"],
                     "recordedAt": row["recordedAt"], "model": row.get("model", MODEL_VERSION)}
               for mid, row in ledger.items() if mid in current_ids}

    write_json("ml_prediction_history.json", {
        "version": 1, "predictions": list(ledger.values())
    })
    write_json("ml_predictions.json", {
        "version": 49, "model": MODEL_VERSION,
        "generatedAt": NOW.isoformat(), "predictions": current
    })

    completed = {m["id"]: m for m in events}
    for m in visible:
        if isinstance(m, dict) and m.get("id") and finished(m):
            completed.setdefault(m["id"], m)

    classic_rows = read_json("prediction_history.json", {}).get("predictions", [])
    classic = {}
    if isinstance(classic_rows, list):
        for row in classic_rows:
            if isinstance(row, dict) and row.get("matchId") and valid_p(row.get("p")):
                classic.setdefault(row["matchId"], row["p"])

    ml_pairs, common_ml, common_classic = [], [], []
    for mid, row in ledger.items():
        m = completed.get(mid)
        if not m or not finished(m):
            continue
        if parsed_date(m) and parsed_date(m) < parsed_date({"date": row["recordedAt"]}):
            continue  # Guard against a prediction created after kickoff.
        h, a = m["home"]["score"], m["away"]["score"]
        outcome = 0 if h > a else 1 if h == a else 2
        ml_pairs.append((row["p"], outcome))
        if mid in classic:
            common_ml.append((row["p"], outcome))
            common_classic.append((classic[mid], outcome))

    write_json("ml_metrics.json", {
        "version": 49, "model": MODEL_VERSION, "generatedAt": NOW.isoformat(),
        "frozenPredictions": len(ledger),
        "holdout": holdout, "mlAllSettled": evaluate(ml_pairs),
        "comparison": {"sameMatches": len(common_ml),
                       "ml": evaluate(common_ml), "classic": evaluate(common_classic)},
        "note": "Both models compared on identical finished matches with frozen pre-match 1X2 probabilities. Historical database never modified.",
    })
    print(f"ML training={cut}, holdout={len(y)-cut}, frozen={len(ledger)}, matched={len(common_ml)}")


if __name__ == "__main__":
    main()
