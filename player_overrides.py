"""
player_overrides.py — manual per-player corrections for the Chopped league,
hand-maintained via player_overrides.json (same pattern as the CSV overrides
in salary_cap.py/fantasypros_client.py — a human-verified fact Sleeper's own
data doesn't reliably carry, most commonly a suspension/exempt-list situation
that never shows up in injury_status).

Two states:
  "removed"     — excluded from the available pool entirely. For a player
                  with zero real path to touches for a long stretch (e.g.
                  a suspension), not just a single questionable week.
  "backup_only" — treated exactly like an injury-flagged player under
                  strict_starter_health (see draft_advisor._calculate_urgency):
                  excluded from starter opportunity-cost math, but still a
                  fully real, pickable candidate at his own value. For a
                  player worth stashing but not worth relying on as a
                  starter right now.

TODO: this is a hand-edited-file workflow for now (tell Claude, it edits
the JSON). A real UI toggle in the app itself is the natural next step —
not built yet.
"""

import json
import os

from salary_cap import _normalize_name, _build_name_index, _resolve_match

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "player_overrides.json")

VALID_STATES = {"removed", "backup_only"}


def load_overrides(players, path=_DEFAULT_PATH):
    """
    Returns (overrides, unmatched):
      overrides: {player_id: "removed" | "backup_only"}
      unmatched: list of (csv_name, state) that couldn't be matched

    Returns ({}, []) if the file doesn't exist — this is optional, not a
    hard dependency.
    """
    if not os.path.exists(path):
        return {}, []

    with open(path) as f:
        raw = json.load(f)

    name_index = _build_name_index(players)
    overrides = {}
    unmatched = []

    for name, state in raw.items():
        if state not in VALID_STATES:
            continue
        match = _resolve_match(name_index.get(_normalize_name(name)), players)
        if not match:
            unmatched.append((name, state))
            continue
        overrides[match] = state

    return overrides, unmatched
