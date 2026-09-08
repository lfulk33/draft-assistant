"""
algorithmic_waiver_scan.py — pure-Python waiver comparison, no Claude call.

Reuses waiver_scout.py's roster/available-pool summaries (same shape,
same underlying Sleeper data) but flags upgrades with plain comparison
instead of an LLM, using Sleeper's own `search_rank` field (its native
player-relevance ranking, lower is better) rather than FantasyCalc's
crowd-sourced value — this was explicitly requested as a Sleeper-only
data source, not a FantasyCalc substitute. Two signals:

  1. VALUE GAP — my weakest player at a position vs. the best available
     free agent at that position, by search_rank. No situational
     judgment, just "does Sleeper's own ranking say he's meaningfully
     better."
  2. DEPTH-CHART MISMATCH — an available player whose own team's depth
     chart has him at 1 (a real starter role, maintained by Sleeper as
     actual news breaks) despite a search_rank that hasn't caught up
     yet. Cheap proxy for "something changed and the market hasn't
     priced it in" — the exact class of thing Claude+search was manually
     verifying, just without the situational nuance a human/LLM read of
     the actual news would catch.

Deliberately NOT trying to replicate the "current week only" spike
detection or genuine situational judgment (a rookie's camp buzz, a
recovery timeline's real credibility) — those need live news, which
Sleeper's structured fields don't capture. This is the free, instant,
always-available first pass; Claude+search stays the tool for anything
this flags as borderline or for a deeper look.
"""

SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

# search_rank is missing for plenty of deep bench players — treat that as
# "worst possible," never let a None accidentally look better than a real
# ranked player.
MISSING_RANK = 9999

# Require a real margin, not noise, before flagging a value-gap upgrade —
# raw ranks swing on illiquid/thin bench-tier players. Ranks are
# lower-is-better, so a "real margin better" means the available player's
# rank is meaningfully smaller than my worst player's.
VALUE_GAP_MARGIN = 1.15

# Depth chart order meaning "the actual current starter on his own team."
# Anything looser than this (order <= 2, or including QB/TE) is nearly
# uninformative — most rostered backups at those spots sit at order 1-2
# by definition, since that's just "how many the team carries," not a
# signal anything real changed. A real depth_chart_order=1 is rare and
# means something: RB/WR specifically, since a backup-tier role at those
# positions still carries real standalone value, unlike QB2/TE2.
DEPTH_CHART_STARTER_THRESHOLD = 1
DEPTH_CHART_POSITIONS = ("RB", "WR")


def _rank(p):
    return p.get("search_rank") if p.get("search_rank") is not None else MISSING_RANK


def _is_available_now(p):
    """
    search_rank reflects general name-recognition/relevance — it does NOT
    discount for a current injury the way a well-maintained crowd value
    might. Live-verified this matters: without this filter, the scan
    flagged Jayden Higgins (torn ACL, out for the season) and James
    Conner (on IR) as real "adds" purely because they're well-known
    players, exactly the trap Claude's own report caught and called out
    by name for Higgins. Any current injury_status disqualifies a player
    from being flagged as a real, right-now add — same standard as the
    draft engine's strict_starter_health gate.
    """
    return not p.get("injury_status")


def scan_value_gaps(roster_summary, available_summary, is_dynasty):
    """
    For each position, compare my weakest rostered player against the
    best available free agent, by Sleeper's search_rank (lower is
    better). Flags only when the available player is worth a real
    margin more — not just marginally higher.
    """
    criterion = "dynasty_long_term" if is_dynasty else "rest_of_season"
    flags = []

    for pos in SKILL_POSITIONS:
        mine = roster_summary["roster_by_position"].get(pos, [])
        avail = [p for p in available_summary.get(pos, []) if _is_available_now(p)]
        if not mine or not avail:
            continue

        worst_mine = max(mine, key=_rank)
        best_avail = min(avail, key=_rank)

        worst_mine_rank = _rank(worst_mine)
        best_avail_rank = _rank(best_avail)
        if best_avail_rank == MISSING_RANK:
            continue

        if worst_mine_rank > best_avail_rank * VALUE_GAP_MARGIN:
            flags.append({
                "position": pos,
                "criterion": criterion,
                "add": best_avail["name"],
                "add_team": best_avail.get("team"),
                "add_search_rank": best_avail_rank,
                "drop": worst_mine["name"],
                "drop_search_rank": worst_mine_rank,
                "reason": f"search_rank {best_avail_rank} vs {worst_mine_rank}",
            })

    return flags


def scan_depth_chart_mismatches(roster_summary, available_summary, is_dynasty):
    """
    Available players who've moved into a real role (depth_chart_order 1
    on their own team) but whose search_rank hasn't caught up — flagged
    separately from the value-gap scan since these can be worth a look
    even when they don't yet clear the rank bar on their own.
    """
    flags = []

    for pos in DEPTH_CHART_POSITIONS:
        mine = roster_summary["roster_by_position"].get(pos, [])
        avail = [p for p in available_summary.get(pos, []) if _is_available_now(p)]
        if not mine:
            continue
        worst_mine_rank = max((_rank(p) for p in mine), default=MISSING_RANK)

        for p in avail:
            order = p.get("depth_chart_order")
            if order is None or order > DEPTH_CHART_STARTER_THRESHOLD:
                continue
            # Only worth surfacing if he's not already an obvious add per
            # the value-gap scan above (this is the "market hasn't caught
            # up" case, not the "clearly better" case).
            if _rank(p) <= worst_mine_rank * VALUE_GAP_MARGIN:
                continue
            flags.append({
                "position": pos,
                "criterion": "depth_chart_role",
                "add": p["name"],
                "add_team": p.get("team"),
                "depth_chart_order": order,
                "add_search_rank": _rank(p),
                "reason": f"depth_chart_order={order} on {p.get('team')}, search_rank not yet caught up",
            })

    return flags


def run_algorithmic_scan(roster_summary, available_summary, is_dynasty):
    return {
        "value_gaps": scan_value_gaps(roster_summary, available_summary, is_dynasty),
        "depth_chart_mismatches": scan_depth_chart_mismatches(roster_summary, available_summary, is_dynasty),
    }
