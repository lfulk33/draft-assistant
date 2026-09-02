"""
adp_client.py — real, current Average Draft Position from
FantasyFootballCalculator's public API.

Used to drive the opportunity-cost simulation's "who gets drafted in the
next N picks" step (see draft_advisor.py's _calculate_urgency) with real
human drafting behavior instead of assuming everyone drafts in pure VORP
order. That false assumption is exactly why a hand-tuned modifier
(dampening TE/backup-QB urgency early) existed in the first place — real
ADP bakes in genuine pacing (including real TE/QB conservatism) directly,
rather than asserting it via an exponent we picked by feel.

FantasyFootballCalculator only supports team counts {8, 10, 12, 14} and a
fixed set of format strings (standard, ppr, half-ppr, 2qb) — no combined
PPR+2QB format exists, so a Superflex league always uses "2qb" regardless
of its PPR settings, and team count is rounded to the nearest supported
size. This is an approximation, not an exact match to any given league,
but it's real behavioral data instead of an assumption.
"""

import json
import os
import time

import requests

from salary_cap import _normalize_name, _build_name_index, _resolve_match

_CACHE_DIR = os.path.dirname(os.path.abspath(__file__))
_SUPPORTED_TEAM_SIZES = [8, 10, 12, 14]
_CACHE_MAX_AGE_SECONDS = 6 * 60 * 60  # ADP shifts daily during draft season; 6h keeps it fresh without hammering the API on every poll


def _nearest_supported_teams(num_teams):
    return min(_SUPPORTED_TEAM_SIZES, key=lambda t: abs(t - num_teams))


def format_for_league(league_context):
    """
    2qb for any Superflex league regardless of PPR settings (no combined
    format exists), otherwise matched to this league's actual PPR setting.
    """
    if league_context.get("has_superflex"):
        return "2qb"
    rec = (league_context.get("scoring_settings") or {}).get("rec", 0)
    if rec == 1:
        return "ppr"
    if rec == 0.5:
        return "half-ppr"
    return "standard"


def _cache_path(format_key, teams, year):
    return os.path.join(_CACHE_DIR, f"adp_{format_key}_{teams}_{year}.json")


def fetch_adp(format_key, teams, year, force_refresh=False):
    path = _cache_path(format_key, teams, year)
    if not force_refresh and os.path.exists(path) and (time.time() - os.path.getmtime(path)) < _CACHE_MAX_AGE_SECONDS:
        with open(path) as f:
            return json.load(f)

    resp = requests.get(
        f"https://fantasyfootballcalculator.com/api/v1/adp/{format_key}",
        params={"teams": teams, "year": year},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def build_adp_map(league_context, players, year):
    """
    {sleeper_player_id: {"adp": float, "adp_formatted": str, "times_drafted": int}}
    for every player matched between FFC's real ADP data and this league's
    format (nearest-supported team count, format per format_for_league).
    """
    format_key = format_for_league(league_context)
    num_teams = _nearest_supported_teams(league_context.get("num_teams", 12))

    data = fetch_adp(format_key, num_teams, year)
    name_index = _build_name_index(players)

    adp_map = {}
    for entry in data.get("players", []):
        match = _resolve_match(name_index.get(_normalize_name(entry["name"])), players)
        if match:
            adp_map[match] = {
                "adp": entry["adp"],
                "adp_formatted": entry["adp_formatted"],
                "times_drafted": entry.get("times_drafted", 0),
            }
    return adp_map
