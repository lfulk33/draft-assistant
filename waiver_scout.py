"""
waiver_scout.py — daily waiver-wire scouting report.

For a given league (post-draft, in-season), pulls the user's current
roster and every unrostered player league-wide, then asks Claude (with
live web search enabled) to recommend adds — both immediate roster
upgrades and longer-term value stashes: a rookie who might earn an
expanded role, an injured player who'll have value again once healthy,
or a backup positioned to gain value from a depth-chart shift ahead of
him. The structured roster/pool data comes straight from Sleeper +
FantasyCalc (already loaded in server.py); the situational judgment
(who's actually trending up, who just got hurt, who's about to lose
touches) comes from Claude's own web search, since that data goes stale
fast and isn't in this app's cached player file.
"""

import os
import anthropic
from dotenv import load_dotenv

load_dotenv()

REPORT_MODEL = "claude-haiku-4-5-20251001"
SKILL_POSITIONS = ("QB", "RB", "WR", "TE")


def _get_all_rostered_ids(rosters):
    return {pid for r in rosters for pid in (r.get("players") or [])}


def build_roster_summary(my_roster, players, league_detail=None):
    """
    My current roster, grouped by position, with real values, plus overall
    capacity — total roster spots vs. how many are filled — so the model
    knows whether an add needs a paired drop at all, or if there's a
    genuinely open bench spot to fill for free.
    """
    by_pos = {pos: [] for pos in SKILL_POSITIONS}
    for pid in my_roster.get("players") or []:
        p = players.get(pid, {})
        pos = p.get("position")
        if pos not in SKILL_POSITIONS:
            continue
        by_pos[pos].append({
            "name": p.get("full_name"),
            "team": p.get("team"),
            "age": p.get("fc_age"),
            "years_exp": p.get("years_exp"),
            "dynasty_value": p.get("fc_value"),
            "redraft_value": p.get("fc_redraft_value"),
            "depth_chart_order": p.get("depth_chart_order"),
            "injury_status": p.get("injury_status"),
        })

    total_spots = len(league_detail.get("roster_positions", [])) if league_detail else None
    filled_spots = len(my_roster.get("players") or [])
    return {
        "roster_by_position": by_pos,
        "total_roster_spots": total_spots,
        "filled_roster_spots": filled_spots,
        "open_roster_spots": (total_spots - filled_spots) if total_spots is not None else None,
    }


def build_available_summary(rosters, players, per_position=25):
    """
    Unrostered players league-wide, grouped by position. Deliberately
    includes real bench-tier names (not just top-value players) — the
    whole point of this report is catching situational value (a rookie
    or injury-comeback name) that hasn't been priced in yet, which a
    pure value sort would miss.
    """
    rostered_ids = _get_all_rostered_ids(rosters)
    by_pos = {pos: [] for pos in SKILL_POSITIONS}
    for pid, p in players.items():
        pos = p.get("position")
        if pos not in SKILL_POSITIONS or pid in rostered_ids:
            continue
        if not p.get("team"):
            continue  # not on an active NFL roster, not a real option
        by_pos[pos].append(p)

    summary = {}
    for pos, plist in by_pos.items():
        ranked = sorted(
            plist,
            key=lambda p: max(p.get("fc_value") or 0, p.get("fc_redraft_value") or 0, -(p.get("search_rank") or 9999)),
            reverse=True
        )
        summary[pos] = [
            {
                "name": p.get("full_name"),
                "team": p.get("team"),
                "age": p.get("fc_age"),
                "years_exp": p.get("years_exp"),
                "dynasty_value": p.get("fc_value"),
                "redraft_value": p.get("fc_redraft_value"),
                "depth_chart_order": p.get("depth_chart_order"),
                "injury_status": p.get("injury_status"),
            }
            for p in ranked[:per_position]
        ]
    return summary


def _build_prompt(league_name, is_dynasty, roster_summary, available_summary):
    import json
    format_label = "dynasty" if is_dynasty else "redraft"
    return f"""You are scouting the waiver wire for one fantasy football team, in the league "{league_name}" ({format_label} format). Today's date matters — check it and use it to reason about how far into the season we are.

Use web search to check current, real information before recommending anyone — injury status, snap counts, depth chart, recent role changes. The values below are a starting-point signal, not the final word; they can be stale or wrong, especially for backups whose role just changed.

MY CURRENT ROSTER:
{json.dumps(roster_summary, indent=2)}

AVAILABLE (UNROSTERED) PLAYERS BY POSITION:
{json.dumps(available_summary, indent=2)}

My roster's "open_roster_spots" count above tells you whether I actually have a free bench spot right now. If it's 0 or negative, EVERY add below requires cutting someone — there is no such thing as "just add him" on a full roster. If it's positive, only that many adds can be free; anything beyond that count still needs a drop.

Your job: identify a short list of real, worthwhile pickups from the available pool, each falling into one of these categories:
1. IMMEDIATE UPGRADE — a player who should replace someone on my active roster right now, because he's a clear improvement at that spot.
2. FUTURE VALUE STASH — someone worth adding now for what he might become, not for this week: a rookie who could see a bigger role soon, a player who's currently injured but will have real value once back, or a backup who could see a real opportunity if the starter ahead of him is dealing with something.

For EVERY recommendation, you must say one of two things explicitly:
- "Drop [specific current roster player] for him" — name the exact player from my roster you'd cut, and why this specific swap is a real upgrade (not just "this guy has positive value" — he has to be better than the specific guy he's replacing, or worth more than what an open bench spot could otherwise hold).
- "Fits into an open bench spot, no drop needed" — only if open_roster_spots genuinely covers it.

When a drop is needed, don't stop at one name — list every current roster player who would ALSO be a reasonable drop for this same add, ranked in order from most obvious cut to least. Only include a name if cutting him for this add is genuinely defensible on its own; stop the list the moment the next candidate wouldn't actually be worth dropping. Most adds will only have one or two real candidates — a long list is a signal you've gone past who's actually worth cutting, not a target to hit. If truly only one player on my roster is worth cutting for this add, give just that one name; don't pad the list.

Never recommend an add without covering one of those two cases. If my roster is full and genuinely nobody on it is worth cutting for what's available, say so explicitly instead of forcing a recommendation.

For each recommendation, name the specific real, current situation that makes him worth grabbing — not just his listed value. Skip anyone if the pool genuinely has nothing worthwhile at a position; don't force a recommendation.

Keep the whole report brief — a manager should be able to read it in under a minute. For each position with a real recommendation, 1-3 sentences: name, category (upgrade or stash), the drop/no-drop call (as a ranked list of names when more than one is worth it, e.g. "Drop, in order of preference: X, then Y"), and the concrete reason. Skip positions with nothing worth flagging. Do not restate the roster or the full player list back to me.
"""


def generate_waiver_report(league_name, is_dynasty, roster_summary, available_summary):
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    prompt = _build_prompt(league_name, is_dynasty, roster_summary, available_summary)

    response = client.messages.create(
        model=REPORT_MODEL,
        # Generous budget: extended thinking + several rounds of web search
        # tool-use content eats tokens before the actual report text starts,
        # even though the final report itself stays short per the prompt.
        max_tokens=6000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
        messages=[{"role": "user", "content": prompt}],
    )

    # Drop any text block that appears before the last tool-use/search-result
    # block — that's the model's "let me go check on a few things first"
    # narration, not part of the actual report. Citations then split the
    # real report across multiple adjacent TextBlocks (each covering a span
    # with its own citation attached) — join those directly with no
    # separator so sentences don't fracture mid-thought.
    last_tool_idx = max(
        (i for i, b in enumerate(response.content) if b.type in ("server_tool_use", "web_search_tool_result")),
        default=-1
    )
    text_blocks = [
        block.text for i, block in enumerate(response.content)
        if block.type == "text" and i > last_tool_idx
    ]
    return "".join(text_blocks).strip()
