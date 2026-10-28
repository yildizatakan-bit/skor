#!/usr/bin/env python3
"""Train an isolated 1X2 model from the published JSON snapshot.

Reads data.json and writes only ml_predictions.json. It never opens or writes
match_archive.sqlite3, data.json, or frozen prediction history.
"""
import datetime as dt
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def side_name(match, side):
    return str((match.get(side) or {}).get("name", "")).strip().casefold()


def features(match, history):
    home, away = side_name(match, "home"), side_name(match, "away")
    if not home or not away:
        return None

    def recent(name):
        rows = []
        for past in reversed(history):
            home_side = side_name(past, "home") == name
            away_side = side_name(past, "away") == name
            if not home_side and not away_side:
                continue
            hs = (past.get("home") or {}).get("score")
            aws = (past.get("away") or {}).get("score")
            if not isinstance(hs, int) or not isinstance(aws, int):
                continue
            gf, ga = (hs, aws) if home_side else (aws, hs)
            rows.append((gf, ga, int(gf > ga), int(gf == ga)))
            if len(rows) == 10:
                break
        return rows

    home_rows, away_rows = recent(home), recent(away)
    if len(home_rows) < 5 or len(away_rows) < 5:
        return None

    def means(rows):
        n = len(rows)
        return [sum(row[i] for row in rows) / n for i in range(4)]

    h, a = means(home_rows), means(away_rows)
    return [1.0, (h[0] - a[0]) / 3, (h[1] - a[1]) / 3,
            h[2] - a[2], h[3] - a[3], (h[0] + a[1]) / 5,
            (a[0] + h[1]) / 5, (len(home_rows) - len(away_rows)) / 10]


def probabilities(weights, x):
    logits = [sum(wi * xi for wi, xi in zip(row, x)) for row in weights]
    peak = max(logits)
    exps = [math.exp(value - peak) for value in logits]
    total = sum(exps)
    return [value / total for value in exps]


def fit(rows, epochs=180, rate=0.12, regularization=0.012):
    weights = [[0.0] * 8 for _ in range(3)]
    for _ in range(epochs):
        gradient = [[0.0] * 8 for _ in range(3)]
        for x, label in rows:
            p = probabilities(weights, x)
            for k in range(3):
                error = p[k] - int(k == label)
                for j in range(8):
                    gradient[k][j] += error * x[j]
        count = len(rows)
        for k in range(3):
            for j in range(8):
                weights[k][j] -= rate * (gradient[k][j] / count + regularization * weights[k][j])
    return weights


def main():
    source = ROOT / "data.json"
    if not source.exists():
        raise SystemExit("Published data.json snapshot is missing")
    payload = json.loads(source.read_text(encoding="utf-8"))
    events = sorted((m for m in payload.get("events", [])
                     if m.get("date") and m.get("home") and m.get("away")),
                    key=lambda m: (m.get("date", ""), m.get("id", "")))

    history, rows = [], []
    for match in events:
        x = features(match, history)
        hs = (match.get("home") or {}).get("score")
        aws = (match.get("away") or {}).get("score")
        if match.get("done") and isinstance(hs, int) and isinstance(aws, int):
            if x is not None:
                rows.append((x, 0 if hs > aws else 1 if hs == aws else 2))
            history.append(match)

    now = dt.datetime.now(dt.timezone.utc)
    target = ROOT / "ml_predictions.json"
    try:
        previous = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except (OSError, json.JSONDecodeError):
        previous = {}
    history_rows = [row for row in previous.get("history", [])
                    if isinstance(row, dict) and row.get("id")]
    history_by_id = {str(row["id"]): row for row in history_rows}
    for match in events:
        row = history_by_id.get(str(match.get("id", "")))
        hs = (match.get("home") or {}).get("score")
        aws = (match.get("away") or {}).get("score")
        if row and match.get("done") and isinstance(hs, int) and isinstance(aws, int):
            row["settled"] = True
            row["score"] = [hs, aws]
            row["outcome"] = 0 if hs > aws else 1 if hs == aws else 2

    output = {
        "model": "multinomial-logistic-v1-experimental",
        "method": "chronological 80/20 holdout; only earlier match results used as features",
        "trainingMatches": 0, "validationMatches": 0, "metrics": None,
        "predictions": {}, "history": history_rows, "performance": None,
        "generatedAt": now.isoformat(),
        "warning": "Experimental 1X2 research output; not evidence of better betting performance.",
    }

    if len(rows) >= 100:
        split = int(len(rows) * 0.8)
        train_rows, holdout = rows[:split], rows[split:]
        weights = fit(train_rows)
        correct, log_loss, brier = 0, 0.0, 0.0
        for x, actual in holdout:
            p = probabilities(weights, x)
            correct += int(max(range(3), key=lambda k: p[k]) == actual)
            log_loss -= math.log(max(1e-12, p[actual]))
            brier += sum((p[k] - int(k == actual)) ** 2 for k in range(3))
        n = len(holdout)
        output.update(trainingMatches=split, validationMatches=n, metrics={
            "accuracy": round(correct / n, 4),
            "logLoss": round(log_loss / n, 4),
            "brier": round(brier / n, 4),
        })

        weights = fit(rows)
        history = []
        for match in events:
            x = features(match, history)
            if match.get("done"):
                history.append(match)
                continue
            try:
                kickoff = dt.datetime.fromisoformat(match["date"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if kickoff <= now or match.get("live") or x is None:
                continue
            p = probabilities(weights, x)
            match_id = str(match["id"])
            record = history_by_id.get(match_id)
            frozen = record.get("p") if record else None
            prediction = frozen or {"home": round(p[0], 6), "draw": round(p[1], 6), "away": round(p[2], 6)}
            output["predictions"][match_id] = prediction
            if record is None:
                record = {"id": match_id, "date": match.get("date"),
                          "league": match.get("league"),
                          "home": side_name(match, "home"), "away": side_name(match, "away"),
                          "p": prediction, "settled": False}
                history_rows.append(record)
                history_by_id[match_id] = record

    settled = [row for row in history_rows if row.get("settled") and
               isinstance(row.get("outcome"), int) and isinstance(row.get("p"), dict)]
    if settled:
        correct = 0
        brier = 0.0
        for row in settled:
            try:
                p = [float(row["p"][key]) for key in ("home", "draw", "away")]
                actual = row["outcome"]
            except (KeyError, TypeError, ValueError):
                continue
            correct += int(max(range(3), key=lambda k: p[k]) == actual)
            brier += sum((p[k] - int(k == actual)) ** 2 for k in range(3))
        n = len(settled)
        if n:
            output["performance"] = {"settledMatches": n, "accuracy": round(correct / n, 4),
                                     "brier": round(brier / n, 4)}

    target.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n",
                       encoding="utf-8")
    print("ML snapshot:", output["trainingMatches"], "train,",
          output["validationMatches"], "holdout,", len(output["predictions"]), "upcoming")


if __name__ == "__main__":
    main()
