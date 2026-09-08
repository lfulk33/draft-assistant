"""
algorithmic_waiver_scan.py — pure-Python waiver comparison, no Claude call.

Reuses waiver_scout.py's roster/available-pool summaries (same shape,
same underlying Sleeper + FantasyCalc data) but flags upgrades with plain
value/depth-chart comparison instead of an LLM. Two signals, both already
present in the data waiver_scout.py was already fetching:

  1. VALUE GAP — my weakest player at a position vs. the best available
     free agent at that position, using dynasty_value or redraft_value
     depending on league format. This is the same comparison the draft
     engine already does via VORP — no situational judgment, just "is he
     worth meaningfully more."
  2. DEPTH-CHART MISMATCH — an available player whose own team's depth
     chart has him at 1 or 2 (a real starter/lead-backup role, maintained
     by Sleeper as actual news breaks) despite a value that hasn't caught
     up yet. Cheap proxy for "something changed and the market hasn't
     priced it in" — the exact class of thing Claude+search was manually
     verifying, just without the situational nuance a human/LLM read of
     the actual news would catch.

Deliberately NOT trying to replicate the "current week only" spike
detection or genuine situational judgment (a rookie's camp buzz, a
recovery timeline's real credibility) — those need live news, which
neither Sleeper's structured fields nor FantasyCalc's crowd value
capture. This is the free, instant, always-available first pass; Claude+
search stays the tool for anything this flags as borderline or for a
deeper look.
"""

SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

# Require a real margin, not noise, before flagging a value-gap upgrade —
# raw values swing on illiquid/thin bench-tier players.
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


def _value_key(is_dynasty):
    return "dynasty_value" if is_dynasty else "redraft_value"


def scan_value_gaps(roster_summary, available_summary, is_dynasty):
    """
    For each position, compare my weakest rostered player against the
    best available free agent, using whichever value dimension matches
    the league format. Flags only when the available player is worth a
    real margin more — not just marginally higher.
    """
    value_key = _value_key(is_dynasty)
    criterion = "dynasty_long_term" if is_dynasty else "rest_of_season"
    flags = []

    for pos in SKILL_POSITIONS:
        mine = [p for p in roster_summary["roster_by_position"].get(pos, []) if p.get(value_key)]
        avail = [p for p in available_summary.get(pos, []) if p.get(value_key)]
        if not mine or not avail:
            continue

        worst_mine = min(mine, key=lambda p: p[value_key])
        best_avail = max(avail, key=lambda p: p[value_key])

        if best_avail[value_key] > worst_mine[value_key] * VALUE_GAP_MARGIN:
            flags.append({
                "position": pos,
                "criterion": criterion,
                "add": best_avail["name"],
                "add_team": best_avail.get("team"),
                "add_value": best_avail[value_key],
                "drop": worst_mine["name"],
                "drop_value": worst_mine[value_key],
                "reason": f"{value_key.replace('_', ' ')} {best_avail[value_key]:.0f} vs {worst_mine[value_key]:.0f}",
            })

    return flags


def scan_depth_chart_mismatches(roster_summary, available_summary, is_dynasty):
    """
    Available players who've moved into a real role (depth_chart_order
    1-2 on their own team) but whose value hasn't caught up — flagged
    separately from the value-gap scan since these can be worth a look
    even when they don't yet clear the value bar on their own.
    """
    value_key = _value_key(is_dynasty)
    flags = []

    for pos in DEPTH_CHART_POSITIONS:
        mine = roster_summary["roster_by_position"].get(pos, [])
        avail = available_summary.get(pos, [])
        if not mine:
            continue
        worst_mine_value = min((p.get(value_key) or 0) for p in mine) if mine else 0

        for p in avail:
            order = p.get("depth_chart_order")
            if order is None or order > DEPTH_CHART_STARTER_THRESHOLD:
                continue
            # Only worth surfacing if he's not already an obvious add per
            # the value-gap scan above (this is the "market hasn't caught
            # up" case, not the "clearly better" case).
            if (p.get(value_key) or 0) > worst_mine_value * VALUE_GAP_MARGIN:
                continue
            flags.append({
                "position": pos,
                "criterion": "depth_chart_role",
                "add": p["name"],
                "add_team": p.get("team"),
                "depth_chart_order": order,
                "add_value": p.get(value_key),
                "reason": f"depth_chart_order={order} on {p.get('team')}, value not yet caught up",
            })

    return flags


def run_algorithmic_scan(roster_summary, available_summary, is_dynasty):
    return {
        "value_gaps": scan_value_gaps(roster_summary, available_summary, is_dynasty),
        "depth_chart_mismatches": scan_depth_chart_mismatches(roster_summary, available_summary, is_dynasty),
    }
