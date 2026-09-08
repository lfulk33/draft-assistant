"""
chopped_bid_advisor.py — weekly FAAB-style bid strategy for "Chopped"
(weekly-elimination) leagues.

Different mechanic from waiver_scout.py's add/drop report: in a Chopped
league, waivers are a season-long salary that never resets, so every
dollar bid this week is a dollar unavailable for the rest of the season.
This module recommends which available players are worth bidding on,
how much to bid, and a fallback bid ladder in case the top target is
lost — all weighed against how much budget needs to last through the
remaining chop weeks.

Reuses waiver_scout's roster/pool summarization (same underlying data,
same shape) since the "what do I have, what's available" question is
identical — only the recommendation itself (bid amounts vs. add/drop)
differs.
"""

import os
import anthropic
from dotenv import load_dotenv

from waiver_scout import build_roster_summary, build_available_summary

load_dotenv()

REPORT_MODEL = "claude-sonnet-5"


def build_budget_summary(roster, league_detail):
    """
    Remaining season-long salary budget, plus how many chop weeks are left
    to pace it across. `waiver_budget` is the league-wide starting budget;
    `waiver_budget_used` is per-roster and never resets week to week in a
    Chopped league, unlike a normal weekly FAAB reset.
    """
    settings = league_detail.get("settings", {})
    total_budget = settings.get("waiver_budget", 0)
    used = roster.get("settings", {}).get("waiver_budget_used", 0)
    remaining_budget = total_budget - used

    current_leg = settings.get("leg", 1)
    last_chopped_leg = settings.get("last_chopped_leg", current_leg)
    weeks_remaining = max(1, last_chopped_leg - current_leg + 1)

    return {
        "total_season_budget": total_budget,
        "spent_so_far": used,
        "remaining_budget": remaining_budget,
        "current_week": current_leg,
        "last_elimination_week": last_chopped_leg,
        "weeks_remaining_to_pace_across": weeks_remaining,
        "even_pace_baseline_per_week": round(remaining_budget / weeks_remaining) if weeks_remaining else remaining_budget,
    }


def _build_prompt(league_name, roster_summary, available_summary, budget_summary):
    import json
    return f"""You are advising on this week's waiver bidding for one team in the fantasy football league "{league_name}" — a "Chopped" weekly-elimination format on Sleeper.

HOW THIS LEAGUE'S WAIVERS WORK — important, don't reason about this like a normal league:
- Waiver budget is a SEASON-LONG salary, not a weekly FAAB reset. Every dollar bid this week is a dollar gone for the rest of the season.
- The lowest-scoring team is eliminated every week through the league's final elimination week, so budget has to last, but it also has diminishing value near the end — money unspent when your season ends is wasted.
- Trades are disabled in this league, so whatever you win on waivers is the only way to fix roster weaknesses all season.

MY BUDGET STATUS:
{json.dumps(budget_summary, indent=2)}

MY CURRENT ROSTER:
{json.dumps(roster_summary, indent=2)}

AVAILABLE (UNROSTERED) PLAYERS BY POSITION:
{json.dumps(available_summary, indent=2)}

Use web search to check current, real information before recommending anyone — injury status, snap counts, depth chart, recent role changes. The values and percent-owned/started signals above are a starting point, not the final word.

Your job: rank this week's real, worthwhile bid targets — compare them against EACH OTHER, not in isolation, since you only have one budget to split across however many you actually pursue this week. For each one:
1. Name who on my current roster they'd replace (or that there's an open bench spot — check "open_roster_spots" the same way as a normal add/drop call).
2. Give a PRIMARY bid amount, and 1-2 FALLBACK bid amounts (lower, in case you lose the primary bid on this player) sized so losing the top target doesn't leave you overpaying for a lesser need with the leftover ladder.
3. Weigh the bid against "even_pace_baseline_per_week" — a real difference-maker is worth bidding above pace, a marginal add should stay at or below it. Don't recommend blowing a large share of the remaining budget on a marginal upgrade.

OUTPUT FORMAT — scannable, no persuasive paragraphs:
- One line per recommendation: `POS — Player (Team): Replaces [Name|open spot]. Bid $X (fallback $Y, $Z). <reason, 12 words max>`
- Order by priority — your top real target first.
- If nothing this week clears the bar of a real roster need, say so explicitly instead of forcing bids: `No bids worth making this week — hold budget.`
- No opening context paragraph, no closing summary, no restating the roster or pool back to me.
"""


def generate_bid_report(league_name, roster_summary, available_summary, budget_summary):
    # No roster to advise against yet — Sleeper only populates roster.players
    # once the draft concludes, so mid-draft this would ask the model to
    # reason about "who does this replace" against an empty team, which is
    # ill-posed and burns a real API call for nothing useful.
    if roster_summary.get("filled_roster_spots", 0) == 0:
        return "Draft hasn't finished yet — no roster to advise waiver bids against. Check back once the draft concludes."

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    prompt = _build_prompt(league_name, roster_summary, available_summary, budget_summary)

    response = client.messages.create(
        model=REPORT_MODEL,
        # Generous budget: bid-ladder + budget-pacing reasoning is more
        # involved than the plain add/drop waiver report, which needed 6000
        # to avoid the same class of thinking-eats-the-whole-budget cutoff.
        max_tokens=12000,
        tools=[{
            "type": "web_search_20260209",
            "name": "web_search",
            # A real available pool easily has 8-10+ distinct players worth
            # checking (injury status, depth-chart moves, trades) — 5 was
            # verified live to run out almost immediately, after which the
            # model burns most of its token budget on a doomed retry loop
            # (repeatedly re-hitting max_uses_exceeded and narrating about
            # it) instead of writing the report, sometimes truncating the
            # response entirely before it finishes. 20 gives real headroom;
            # if the model still doesn't need all of it, the tool is simply
            # unused, no cost either way.
            "max_uses": 20,
        }],
        messages=[{"role": "user", "content": prompt}],
    )

    last_tool_idx = max(
        (i for i, b in enumerate(response.content)
         if b.type in ("server_tool_use", "web_search_tool_result", "bash_code_execution_tool_result")),
        default=-1
    )
    text_blocks = [
        block.text for i, block in enumerate(response.content)
        if block.type == "text" and i > last_tool_idx
    ]
    return "".join(text_blocks).strip()
