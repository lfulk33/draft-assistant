"""
daily_reports.py — generates the waiver-wire scouting report across every
league the user is in, and caches the result to disk.

The manual on-demand path already exists (the "Scout Waivers" mode in the
app, backed by /api/waiver-report) — pick leagues, click Run Report, wait
for live web research. This script covers the different need: show up
each morning to a report that's already there, for every league, with no
button to click. Meant to run once a day via cron; see the /reports/waivers
route in server.py for how the cached output gets served.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from config import SLEEPER_USERNAME
from sleeper_league import get_user, get_leagues, get_league, get_rosters, load_players
import waiver_scout
import algorithmic_waiver_scan

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
WAIVER_REPORT_PATH = os.path.join(REPORTS_DIR, "waiver_report_latest.json")


def _generate_one(league, user_id, players):
    league_id = league["league_id"]
    league_name = league.get("name", league_id)
    league_detail = get_league(league_id)
    rosters = get_rosters(league_id)
    my_roster = next((r for r in rosters if r.get("owner_id") == user_id), None)
    if not my_roster:
        return {"league_id": league_id, "league_name": league_name, "error": "Roster not found for this user in this league."}

    is_dynasty = league_detail.get("settings", {}).get("type") == 2
    roster_summary = waiver_scout.build_roster_summary(my_roster, players, league_detail)
    available_summary = waiver_scout.build_available_summary(rosters, players)
    # Free, instant, zero-API-cost pass first — real value gaps and
    # depth-chart mismatches Sleeper + FantasyCalc data already supports
    # directly. Claude's job below is the situational judgment neither of
    # those numbers can carry (see algorithmic_waiver_scan.py docstring).
    algorithmic = algorithmic_waiver_scan.run_algorithmic_scan(roster_summary, available_summary, is_dynasty)
    report_text = waiver_scout.generate_waiver_report(league_name, is_dynasty, roster_summary, available_summary)
    return {"league_id": league_id, "league_name": league_name, "algorithmic": algorithmic, "report": report_text}


def run_waiver_reports(username=None):
    """
    Runs the waiver report for every league the user is currently in,
    writes the combined result to WAIVER_REPORT_PATH, and returns the
    payload. Leagues run in parallel — each involves several real web
    searches per report.
    """
    username = username or SLEEPER_USERNAME
    user = get_user(username)
    if not user:
        raise ValueError(f"Sleeper user '{username}' not found.")
    user_id = user["user_id"]

    leagues = get_leagues(user_id)
    players = {str(k): v for k, v in load_players().items()}

    results = []
    with ThreadPoolExecutor(max_workers=max(1, len(leagues))) as executor:
        futures = {
            executor.submit(_generate_one, league, user_id, players): league["league_id"]
            for league in leagues
        }
        for future in as_completed(futures):
            lid = futures[future]
            try:
                results.append(future.result())
            except Exception as e:
                results.append({"league_id": lid, "league_name": lid, "error": str(e)})

    order = [league["league_id"] for league in leagues]
    results.sort(key=lambda r: order.index(r["league_id"]))

    os.makedirs(REPORTS_DIR, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reports": results,
    }
    with open(WAIVER_REPORT_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    return payload


if __name__ == "__main__":
    payload = run_waiver_reports()
    print(f"Wrote {len(payload['reports'])} league reports to {WAIVER_REPORT_PATH}")
