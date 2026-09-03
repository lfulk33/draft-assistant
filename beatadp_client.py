"""
beatadp_client.py — real, current Sleeper-platform ADP, extracted from
BeatADP's platform-adp comparison page.

Sleeper computes its own ADP internally from real drafts run on its own
platform, segmented by format — but doesn't expose it via a public API
(see adp_client.py's docstring for the fallback this overrides). FantasyPros
and DraftSharks both publish "Sleeper ADP" pages but resist automated
fetching (FantasyPros' export redirects to a login wall; DraftSharks
renders its table client-side with no discoverable backing API).

BeatADP's comparison page, however, server-renders its full player dataset
— including Sleeper's own ADP per format — directly into the page's
initial HTML as a Next.js React Server Components payload, unauthenticated.
This module extracts that embedded JSON rather than calling a documented
API endpoint. If BeatADP ever changes its page structure, extraction just
returns nothing and adp_client.py falls back to FFC — same defensive
pattern as every other ADP failure mode in this pipeline. Cached to disk
like FFC's client, so normal use never re-fetches more than once per
cache window.

No exact PPR+2QB combo exists here either (same real-world gap as FFC) —
HALF_PPR|REDRAFT|2QB is the closest exact Superflex-format match, same
reasoning FFC's client uses for "2qb" regardless of a league's own PPR
setting.
"""

import json
import os
import time

import requests

from salary_cap import _normalize_name, _build_name_index, _resolve_match

_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "beatadp_players.json")
_CACHE_MAX_AGE_SECONDS = 6 * 60 * 60
_URL = "https://www.beatadp.com/platform-adp"


def _extract_players(html):
    """
    The player list is embedded as `\"players\":[...]` inside a Next.js
    RSC streaming payload (a big escaped JSON string within a <script>
    tag) — find that array, unescape it back to real JSON, and decode
    just that array rather than the whole page's script blob.
    """
    key_idx = html.find('\\"players\\":[')
    if key_idx == -1:
        return []
    arr_start = html.find('[', key_idx)
    if arr_start == -1:
        return []
    unescaped = html[arr_start:].replace('\\"', '"')
    try:
        players, _ = json.JSONDecoder().raw_decode(unescaped)
    except (json.JSONDecodeError, ValueError):
        return []
    return players


def fetch_players(force_refresh=False):
    if not force_refresh and os.path.exists(_CACHE_PATH) and (time.time() - os.path.getmtime(_CACHE_PATH)) < _CACHE_MAX_AGE_SECONDS:
        with open(_CACHE_PATH) as f:
            return json.load(f)

    resp = requests.get(_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    resp.raise_for_status()
    players = _extract_players(resp.text)
    with open(_CACHE_PATH, "w") as f:
        json.dump(players, f)
    return players


def _adp_key_for_league(league_context):
    qb_mode = "2QB" if league_context.get("has_superflex") else "1QB"
    if qb_mode == "2QB":
        # No PPR|2QB combo exists on BeatADP (or anywhere else we've
        # checked) — HALF_PPR is the closest real Superflex-format data.
        scoring = "HALF_PPR"
    else:
        rec = (league_context.get("scoring_settings") or {}).get("rec", 0)
        scoring = "PPR" if rec == 1 else "HALF_PPR" if rec == 0.5 else "STANDARD"
    return f"SLEEPER|{scoring}|REDRAFT|{qb_mode}"


def build_adp_map(league_context, players):
    """
    {sleeper_player_id: {"adp": float, "adp_formatted": str}} from
    BeatADP's Sleeper-platform data, matched to this league's format.
    Returns {} on any fetch/parse/match failure — adp_client.py treats
    this as an optional override and falls back to FFC for anyone (or
    everyone) not covered here.
    """
    try:
        beatadp_players = fetch_players()
    except Exception:
        return {}

    if not beatadp_players:
        return {}

    adp_key = _adp_key_for_league(league_context)
    num_teams = league_context.get("num_teams") or 12
    name_index = _build_name_index(players)

    adp_map = {}
    for p in beatadp_players:
        adp = (p.get("adps") or {}).get(adp_key)
        if adp is None:
            continue
        match = _resolve_match(name_index.get(_normalize_name(p.get("fullName", ""))), players)
        if match:
            # BeatADP gives a raw overall pick number — reformat as
            # round.pick to match FFC's adp_formatted convention, since
            # both feed the same raw-mode UI field.
            round_num = int((adp - 1) // num_teams) + 1
            pick_in_round = int(round(adp - 1)) % num_teams + 1
            adp_map[match] = {"adp": adp, "adp_formatted": f"{round_num}.{pick_in_round:02d}"}
    return adp_map


if __name__ == "__main__":
    import json as _json
    with open("fantasy_players.json") as f:
        players = _json.load(f)

    adp_map = build_adp_map({"has_superflex": True}, players)
    print(f"Matched {len(adp_map)} players for SLEEPER|HALF_PPR|REDRAFT|2QB")
    for name in ["Travis Etienne", "Garrett Wilson", "Rashee Rice", "Jaylen Waddle"]:
        pid = next((p.get("player_id") for p in players.values() if p.get("full_name") == name), None)
        print(f"  {name}: {adp_map.get(pid)}")
