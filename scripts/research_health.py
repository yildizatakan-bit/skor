#!/usr/bin/env python3
"""Compact research health report, strictly descriptive and read-only inputs."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "research_health.json"
UTC = timezone.utc


def read(name):
    try:
        return json.loads((ROOT / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def instant(raw):
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return value.astimezone(UTC) if value.tzinfo else None
    except ValueError:
        return None


def summarize(data, coverage, season, signals, incremental, ml, now):
    events = data.get("events") or []
    if not isinstance(events, list):
        events = []
    future = []
    for e in events:
        if not isinstance(e, dict) or e.get("done"):
            continue
        kickoff = instant(e.get("date"))
        if kickoff and kickoff > now:
            future.append(e)
    total = int(coverage.get("targetTeamEntries") or 0)
    successful = int(coverage.get("successfulTeamEntries") or 0)
    entries = int(season.get("playerLeagueEntries") or 0)
    stats = int(season.get("successfulSeasonEntries") or 0)
    checked = int(season.get("checkedEntries") or 0)
    frozen = int(signals.get("frozenMatches") or 0)
    settled = int(signals.get("settledMatches") or 0)
    classic = (incremental.get("models") or {}).get("classic") or {}
    machine = (incremental.get("models") or {}).get("ml") or {}
    flags = []
    if not future:
        flags.append("NO_FUTURE_FIXTURES_IN_MAIN_DATA")
    if not total:
        flags.append("NO_ARCHIVED_TEAM_COVERAGE")
    elif successful < total:
        flags.append("INCOMPLETE_ROSTER_COVERAGE")
    if checked < entries:
        flags.append("PLAYER_SEASON_STATS_STILL_IN_PROGRESS")
    if not settled:
        flags.append("NO_SETTLED_PROSPECTIVE_MATCHES_YET")
    if not classic.get("ready") or not machine.get("ready"):
        flags.append("PLAYER_INCREMENT_NOT_READY")
    return {
        "version": 1, "generatedAt": now.isoformat(),
        "source": "Read-only health summary of experimental outputs and current production fixtures",
        "productionUpcomingMatches": len(future),
        "nextKickoff": min((instant(x["date"]).isoformat() for x in future), default=None),
        "rosters": {
            "targetTeamEntries": total, "successfulTeamEntries": successful,
            "coveragePct": round(100*successful/total, 2) if total else None,
            "failedTeamEntries": coverage.get("failedTeamEntries"),
        },
        "playerSeasonStats": {
            "playerLeagueEntries": entries, "checkedEntries": checked,
            "successfulEntries": stats,
            "successPctOfChecked": round(100*stats/checked, 2) if checked else None,
            "unsupportedLeagues": season.get("unsupportedLeagues") or [],
        },
        "prospective": {
            "frozenMatches": frozen, "settledMatches": settled,
            "classicFrozen": signals.get("classicFrozen"),
            "mlFrozen": signals.get("mlFrozen"),
            "sameMatchModelComparison": (signals.get("pairedComparison") or {}).get("sameMatches"),
        },
        "incrementalPlayerImpact": {
            "classic": {"eligibleSettled": classic.get("eligibleSettled", 0),
                        "ready": bool(classic.get("ready"))},
            "ml": {"eligibleSettled": machine.get("eligibleSettled", 0),
                   "ready": bool(machine.get("ready"))},
            "minimumProspectiveSettled": incremental.get("minimumTotal", 240),
        },
        "mlHistoricalHoldout": ml.get("holdout") or {},
        "warnings": flags,
        "productionFilesModified": False,
        "notes": [
            "Competition-scoped roster coverage is not historical lineup coverage",
            "No player model is activated; all predictions remain experimental",
            "Scheduled research only sees fixtures available in main data.json",
        ],
    }


def main():
    report = summarize(
        read("data.json"), read("player_coverage.json"),
        read("player_season_coverage.json"), read("player_signal_metrics.json"),
        read("player_increment_validation.json"), read("ml_metrics.json"),
        datetime.now(UTC),
    )
    OUT.write_text(json.dumps(report, ensure_ascii=False,
                              separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print("RESEARCH_HEALTH " + json.dumps({
        "upcoming": report["productionUpcomingMatches"],
        "rosterCoveragePct": report["rosters"]["coveragePct"],
        "frozen": report["prospective"]["frozenMatches"],
        "settled": report["prospective"]["settledMatches"],
        "warnings": report["warnings"],
    }), flush=True)


if __name__ == "__main__":
    main()
