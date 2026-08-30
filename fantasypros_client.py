"""
fantasypros_client.py — loads FantasyPros overall PPR redraft rankings from
a hand-downloaded CSV and converts them into a value scale compatible with
the app's VORP pipeline (same rough magnitude as FantasyCalc's
fc_redraft_value: ~10000 for the #1 overall player, decaying toward 0 by
the bottom of the rankings).

Used only for redraft, full-PPR leagues — see server.py for the exact
qualification check. Name matching reuses salary_cap.py's matcher since
it already handles suffixes (Jr./Sr./III) and active-vs-stale duplicate
resolution.
"""

import csv

from salary_cap import _normalize_name, _build_name_index, _resolve_match

# Decay rate tuned so the value curve roughly tracks FantasyCalc's shape:
# steep drop through the first ~50 picks, long near-replacement tail by
# rank 300+. Exact calibration doesn't matter much — VORP only cares about
# relative ordering and gaps, not the absolute scale.
_TOP_VALUE = 10000
_DECAY = 0.985


def load_fantasypros_redraft_values(path, players):
    """
    Returns (values, unmatched):
      values:    dict of player_id -> value (int), derived from FantasyPros'
                 overall rank (RK column)
      unmatched: list of (csv_name, rank) that couldn't be matched to
                 exactly one Sleeper player
    """
    name_index = _build_name_index(players)
    values = {}
    unmatched = []

    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            csv_name = (row.get("PLAYER NAME") or "").strip()
            rank_str = (row.get("RK") or "").strip()
            if not csv_name or not rank_str:
                continue
            try:
                rank = int(rank_str)
            except ValueError:
                continue

            match = _resolve_match(name_index.get(_normalize_name(csv_name)), players)
            if not match:
                unmatched.append((csv_name, rank))
                continue

            values[match] = round(_TOP_VALUE * (_DECAY ** (rank - 1)))

    return values, unmatched


if __name__ == "__main__":
    import json
    with open("fantasy_players.json") as f:
        players = json.load(f)

    values, unmatched = load_fantasypros_redraft_values(
        "FantasyPros_2026_Draft_ALL_Rankings.csv", players
    )
    print(f"Matched {len(values)}, {len(unmatched)} unmatched:")
    for name, rank in unmatched:
        print(f"  {name} (rank {rank})")

    top10 = sorted(values.items(), key=lambda x: -x[1])[:10]
    for pid, val in top10:
        print(players[pid].get("full_name"), val)
