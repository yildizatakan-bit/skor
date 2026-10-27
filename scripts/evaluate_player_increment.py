#!/usr/bin/env python3
"""Prospective test: does frozen player data improve 1X2 probabilities?

Train TWO separate experimental calibrators for each frozen base model:
  baseline-only: 1X2 probabilities
  player-aware: same probabilities + pre-kickoff roster statistics
Compare on the SAME chronological holdout. No historical roster reconstruction.
No auto-deployment or edits to existing models/predictions.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
import json
import math

from track_player_signals import ROOT, LABELS, instant, valid_probabilities

OUTPUT = ROOT / "player_increment_validation.json"
MIN_TOTAL = 240
MIN_TRAIN = 180
MIN_TEST = 60
MIN_IMPROVEMENT = 0.005


def sample_rows(ledger, model):
    settlements = {r["matchId"]: r for r in ledger.get("settlements", [])}
    extra = {(r["matchId"], r.get("model")): r
             for r in ledger.get("modelSnapshots", [])}
    result = []
    for s in ledger.get("snapshots", []):
        kickoff = instant(s.get("kickoff"))
        frozen = instant(s.get("frozenAt"))
        feed = instant(s.get("playerFeedGeneratedAt"))
        if not kickoff or not frozen or not feed or not (feed <= frozen < kickoff):
            continue
        outcome = settlements.get(s.get("matchId"), {}).get("outcome")
        if outcome not in LABELS:
            continue
        p = (s.get("probabilities") or {}).get(model)
        if not valid_probabilities(p):
            later = extra.get((s.get("matchId"), model))
            if later:
                at = instant(later.get("frozenAt"))
                source = instant(later.get("sourceRecordedAt"))
                if (at and source and source <= at < kickoff
                        and instant(later.get("kickoff")) == kickoff):
                    p = later.get("p")
        if not valid_probabilities(p):
            continue
        h, a = s.get("home") or {}, s.get("away") or {}
        hg, ag = h.get("knownSeasonGoals"), a.get("knownSeasonGoals")
        hc, ac = h.get("goalCoverage"), a.get("goalCoverage")
        hn, an = h.get("rosterPlayers"), a.get("rosterPlayers")
        values = (hg, ag, hc, ac, hn, an)
        if not all(type(x) in (int, float) and math.isfinite(x) for x in values):
            continue
        if min(hg, ag) < 0 or not (0.7 <= hc <= 1 and 0.7 <= ac <= 1):
            continue
        baseline = [p[k] for k in LABELS]
        player = [
            (hg - ag) / (1 + hg + ag),
            hc - ac,
            (hn - an) / max(1, hn + an),
        ]
        result.append((kickoff, str(s["matchId"]), baseline, baseline + player,
                       LABELS.index(outcome)))
    return sorted(result, key=lambda x: (x[0], x[1]))


def scores(model, X, y):
    import numpy as np
    if not len(y):
        return {"matches": 0, "brier": None, "logLoss": None, "accuracy": None}
    probs = model.predict_proba(X)
    labels = list(model.classes_)
    n = len(y)
    brier, loss, hits = 0.0, 0.0, 0
    for i, truth in enumerate(y):
        p = [float(probs[i, labels.index(j)]) if j in labels else 0.0 for j in range(3)]
        brier += sum((p[j] - int(j == truth))**2 for j in range(3))
        loss -= math.log(max(p[int(truth)], 1e-12))
        hits += int(max(range(3), key=lambda j: p[j]) == truth)
    return {"matches": n, "brier": round(brier/n, 5),
            "logLoss": round(loss/n, 5), "accuracy": round(hits/n, 5)}


def evaluate(ledger, model):
    rows = sample_rows(ledger, model)
    status = {
        "model": model, "eligibleSettled": len(rows),
        "minimumRequired": MIN_TOTAL, "ready": False,
        "reason": "Insufficient prospective settled matches with frozen model and player data",
    }
    if len(rows) < MIN_TOTAL:
        return status
    # Split by unique kickoff, never let simultaneous games cross partitions.
    dates = sorted(set(r[0] for r in rows))
    if len(dates) < 10:
        status["reason"] = "Insufficient distinct kickoff times"
        return status
    cut = dates[int(len(dates) * .75)]
    train = [r for r in rows if r[0] < cut]
    test = [r for r in rows if r[0] >= cut]
    if len(train) < MIN_TRAIN or len(test) < MIN_TEST:
        status["reason"] = "Chronological holdout too small"
        return status
    if len(set(r[4] for r in train)) != 3:
        status["reason"] = "Training split missing an outcome class"
        return status
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    ytrain = np.asarray([r[4] for r in train])
    ytest = np.asarray([r[4] for r in test])
    make_model = lambda: make_pipeline(
        StandardScaler(), LogisticRegression(C=.12, max_iter=1200, random_state=42))
    base = make_model()
    aware = make_model()
    base.fit(np.asarray([r[2] for r in train]), ytrain)
    aware.fit(np.asarray([r[3] for r in train]), ytrain)
    baseline_metrics = scores(base, np.asarray([r[2] for r in test]), ytest)
    player_metrics = scores(aware, np.asarray([r[3] for r in test]), ytest)
    delta_brier = round(baseline_metrics["brier"] - player_metrics["brier"], 5)
    delta_logloss = round(baseline_metrics["logLoss"] - player_metrics["logLoss"], 5)
    status.update({
        "ready": True, "reason": "Chronological holdout evaluated",
        "trainMatches": len(train), "testMatches": len(test),
        "testFrom": cut.isoformat(), "testTo": test[-1][0].isoformat(),
        "calibratedBaseline": baseline_metrics,
        "playerAwareCandidate": player_metrics,
        "brierImprovement": delta_brier,
        "logLossImprovement": delta_logloss,
        "candidateBetter": delta_brier >= MIN_IMPROVEMENT and
                           delta_logloss >= MIN_IMPROVEMENT,
        "deployment": "NOT AUTOMATIC; requires separate review and approval",
    })
    return status


def main():
    path = ROOT / "player_signal_history.json"
    ledger = json.loads(path.read_text(encoding="utf-8"))
    report = {
        "version": 1, "generatedAt": datetime.now(timezone.utc).isoformat(),
        "method": "Chronological 75/25 split, separate baseline-only and player-aware logistic calibration, identical frozen matches",
        "minimumTotal": MIN_TOTAL, "minimumTrain": MIN_TRAIN,
        "minimumTest": MIN_TEST, "minBrierAndLogLossGain": MIN_IMPROVEMENT,
        "models": {model: evaluate(ledger, model) for model in ("classic", "ml")},
        "limitations": [
            "Player stats are latest available season aggregates, not verified lineups",
            "Only prospective pre-kickoff snapshots may enter training",
            "No retrospective player feature backfill; no automatic model activation",
        ],
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False,
                                 separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print("PLAYER_INCREMENT " + json.dumps({
        model: {"eligible": value["eligibleSettled"], "ready": value["ready"]}
        for model, value in report["models"].items()
    }), flush=True)


if __name__ == "__main__":
    main()
