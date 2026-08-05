"""
salary_cap.py — POC salary-cap tracking for one specific league (Dark Side GM).

Loads a fixed player price sheet and a per-owner keeper cost list (both
hand-maintained CSVs, not Sleeper API data — Sleeper has no concept of a
custom salary sheet), and matches them against the Sleeper player pool by
name. Name matching is the main practical risk here (Jr./Sr./II/III suffixes,
punctuation) — unmatched entries are always returned alongside the matched
dict so they can be reviewed rather than silently dropped.
"""

import csv
import re

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Known nickname/spelling mismatches between the salary sheet and Sleeper's
# full_name field. Add to this as new ones turn up in the unmatched list.
_ALIASES = {
    "cameron ward": "cam ward",
    "hollywood brown": "marquise brown",
    "jacory crosky-merritt": "jacory croskey-merritt",
}


def _normalize_name(name):
    name = name.strip().lower()
    name = re.sub(r"[.'']", "", name)
    if name in _ALIASES:
        name = _ALIASES[name]
    parts = name.split()
    if parts and parts[-1] in _SUFFIXES:
        parts = parts[:-1]
    return " ".join(parts)


def _build_name_index(players):
    """
    Map normalized full_name -> list of player_ids, sorted so an active
    player (has a team) comes first — Sleeper's player database carries
    stale/retired duplicate entries with no team for some common names
    (e.g. two "Frank Gore" entries), and those should never win a collision
    over the actual active player.
    """
    index = {}
    for pid, p in players.items():
        full_name = p.get("full_name")
        if not full_name:
            continue
        index.setdefault(_normalize_name(full_name), []).append(pid)
    for norm, pids in index.items():
        pids.sort(key=lambda pid: players[pid].get("team") is None)
    return index


def _parse_dollar(value):
    value = (value or "").strip().lstrip("$")
    try:
        return int(value)
    except ValueError:
        return None


def _resolve_match(matches, players):
    """
    matches is sorted active-player-first. A single active player wins over
    any number of stale/retired no-team duplicates without ambiguity. Only
    a genuine collision — two or more players who both have a team — is
    truly ambiguous.
    """
    if not matches:
        return None
    active = [pid for pid in matches if players[pid].get("team") is not None]
    if len(active) == 1:
        return active[0]
    if len(active) == 0 and len(matches) == 1:
        return matches[0]
    return None


def load_salaries(path, players):
    """
    Returns (salaries, unmatched):
      salaries:  dict of player_id -> salary (int)
      unmatched: list of (csv_name, salary) that couldn't be matched
                 to exactly one Sleeper player
    """
    name_index = _build_name_index(players)
    salaries = {}
    unmatched = []

    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            csv_name = (row.get("PLAYER") or "").strip()
            price = _parse_dollar(row.get("2026 PRICE"))
            if not csv_name or price is None:
                continue
            match = _resolve_match(name_index.get(_normalize_name(csv_name)), players)
            if not match:
                unmatched.append((csv_name, price))
                continue
            salaries[match] = price

    return salaries, unmatched


def load_keepers(path, owner, players):
    """
    Returns (keepers, unmatched) for one owner's 2026 keeper costs:
      keepers:   dict of player_id -> keeper_cost (int)
      unmatched: list of (csv_name, cost) that couldn't be matched
    """
    name_index = _build_name_index(players)
    keepers = {}
    unmatched = []

    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("Owner") or "").strip() != owner:
                continue
            csv_name = (row.get("Player") or "").strip()
            cost = _parse_dollar(row.get("2026"))
            if not csv_name or cost is None:
                continue
            match = _resolve_match(name_index.get(_normalize_name(csv_name)), players)
            if not match:
                unmatched.append((csv_name, cost))
                continue
            keepers[match] = cost

    return keepers, unmatched


if __name__ == "__main__":
    import json
    with open("fantasy_players.json") as f:
        players = json.load(f)

    salaries, unmatched_salaries = load_salaries("dsff_salaries.csv", players)
    print(f"Matched {len(salaries)} salaries, {len(unmatched_salaries)} unmatched:")
    for name, price in unmatched_salaries:
        print(f"  {name} (${price})")

    keepers, unmatched_keepers = load_keepers("dsff_keepers.csv", "lfulk33", players)
    print(f"\nMatched {len(keepers)} keepers for lfulk33, {len(unmatched_keepers)} unmatched:")
    for name, cost in unmatched_keepers:
        print(f"  {name} (${cost})")

    print(f"\nTotal keeper cost: ${sum(keepers.values())}")
