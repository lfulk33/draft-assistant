"""
historical_stats.py — translates a redraft league's market-value player
rankings onto a real, ground-truth points scale, so VORP comparisons
across positions (e.g. QB vs RB/WR/TE for a shared FLEX/SUPER_FLEX slot)
are correctly calibrated regardless of whether the market-value source
was built with this league's roster format in mind.

Root cause this fixes: FantasyPros' overall ranking (and, to a lesser
extent, FantasyCalc's market value) reflect a standard 1-QB-league
context — QBs aren't ranked highly because in a 1-QB league you only
ever need one. In a Superflex league, real QB demand roughly doubles,
but the ranking source has no way to know that, so it never inflates
QB's value accordingly, while RB/WR/TE values don't need that
adjustment. Comparing raw market values directly across positions
(e.g. in the FLEX/SUPER_FLEX slot-filling competition) mixes a
Superflex-blind QB number against correctly-calibrated RB/WR/TE
numbers. Verified live (2026-09-02): a naive comparison ranked
RB43/WR49-55 above QB22+ for a Superflex-eligible slot, despite 34
individual real QBs outscoring RB43 in actual 2025 points.

Design (confirmed 2026-09-02): we do NOT use last season's points as a
projection for any specific player — that would ignore everything a
forward-looking ranking captures (trades, coaching changes, injuries,
rookies) that a raw stat line from last year can't know about. Instead:
  1. A player's CURRENT positional rank (QB1, QB2, ... WR15, ...) still
     comes entirely from the existing forward-looking market-value
     source — that ordering is format-agnostic (whether Lamar is a
     better real QB than Maye this year doesn't depend on how many QBs
     your league starts).
  2. That positional rank is then translated onto a REAL points scale,
     using a 3-year rolling average of what that exact positional rank
     actually scored (weighted by this league's own scoring settings).
     3 years empirically verified sufficient (5-year curves came back
     nearly identical); it also smooths out single-season anomalies
     (e.g. 2025's TE1 slot was actually Trey McBride's output, not
     Brock Bowers' — Bowers played only 12 games that year).
This makes every position's translated value real-points-comparable,
so no position-specific special-casing is needed downstream in VORP or
replacement-level calculations — a plain numeric comparison is already
fair. Scoped to redraft leagues only: real points has no equivalent
concept for dynasty's long-term asset value.
"""

import json
import os

import requests

_CACHE_DIR = os.path.dirname(os.path.abspath(__file__))


def _cache_path(season):
    return os.path.join(_CACHE_DIR, f"season_stats_{season}.json")


def fetch_season_stats(season, force_refresh=False):
    """Raw per-player season stat totals from Sleeper's public stats API, cached to disk."""
    path = _cache_path(season)
    if not force_refresh and os.path.exists(path):
        with open(path) as f:
            return json.load(f)

    resp = requests.get(f"https://api.sleeper.app/v1/stats/nfl/regular/{season}", timeout=30)
    resp.raise_for_status()
    data = resp.json()
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def compute_league_points(raw_stats, scoring_settings):
    """Weighted point total for one player's raw stat dict, using this league's actual scoring rules."""
    total = 0.0
    for stat_key, point_value in scoring_settings.items():
        stat_amount = raw_stats.get(stat_key)
        if stat_amount:
            total += stat_amount * point_value
    return round(total, 2)


def build_real_points_map(season, scoring_settings):
    """
    {sleeper_player_id: real_points} for every real (non-team-aggregate)
    player with stats in the given season, weighted by scoring_settings.
    Sleeper's team-defense/special-teams aggregate rows use non-numeric
    IDs like "TEAM_BUF" — skipped here since this is for offensive
    skill-position cross-comparison (QB/RB/WR/TE), not defenses.
    """
    raw = fetch_season_stats(season)
    points = {}
    for pid, stats in raw.items():
        if not pid.isdigit() or not isinstance(stats, dict):
            continue
        pts = compute_league_points(stats, scoring_settings)
        if pts:
            points[pid] = pts
    return points


SKILL_POSITIONS = ("QB", "RB", "WR", "TE")
DEFAULT_CURVE_YEARS = (2023, 2024, 2025)

# Keyed by (years, sorted scoring_settings items) — this curve only depends
# on position tags (invariant across leagues) plus years/scoring, never on
# any per-league value field, so it's safe to share across every league
# with matching scoring. Avoids rebuilding it on every draft-state request
# (polled every ~5s during a live draft).
_curve_cache = {}


def build_positional_rank_curve(years, scoring_settings, players):
    """
    {position: [real_points_at_rank_1, real_points_at_rank_2, ...]},
    averaged across the given seasons. Each season's list is built
    independently (its own positional ranking that year) before
    averaging by rank index — we're averaging "what did the Nth-best
    real performer at this position score," not tracking any one player
    across years.
    """
    cache_key = (tuple(years), tuple(sorted(scoring_settings.items())))
    if cache_key in _curve_cache:
        return _curve_cache[cache_key]

    by_year_pos_points = {}
    for year in years:
        points_map = build_real_points_map(year, scoring_settings)
        by_year_pos_points[year] = {}
        for pos in SKILL_POSITIONS:
            ranked = sorted(
                (points_map[pid] for pid, p in players.items()
                 if p.get("position") == pos and pid in points_map),
                reverse=True
            )
            by_year_pos_points[year][pos] = ranked

    curve = {}
    for pos in SKILL_POSITIONS:
        max_len = max(len(by_year_pos_points[y][pos]) for y in years)
        pos_curve = []
        for i in range(max_len):
            vals = [by_year_pos_points[y][pos][i] for y in years if i < len(by_year_pos_points[y][pos])]
            pos_curve.append(round(sum(vals) / len(vals), 2))
        curve[pos] = pos_curve

    _curve_cache[cache_key] = curve
    return curve


def real_points_equivalent(position, rank, curve):
    """
    Real-points value for the Nth-ranked (1-indexed) player at a
    position, per the given curve. Past the end of real data depth,
    decays from the last known value rather than falling off a cliff —
    deep bench players still get a (small, shrinking) real value instead
    of zero.
    """
    pos_curve = curve.get(position) or []
    idx = rank - 1
    if idx < len(pos_curve):
        return pos_curve[idx]
    tail = pos_curve[-1] if pos_curve else 0
    overflow = idx - len(pos_curve) + 1
    return round(tail * (0.9 ** overflow), 2)


def apply_real_points_translation(players, value_key, scoring_settings, years=DEFAULT_CURVE_YEARS):
    """
    Returns a new {player_id: value} map translating each skill-position
    player's CURRENT positional rank (determined by their existing
    value_key, e.g. fc_redraft_value) onto the real-points curve for
    that position. Ranking order within a position is untouched — this
    only rescales what a given rank is "worth" so it's comparable across
    positions. Players without a position in SKILL_POSITIONS, or without
    a value_key, are omitted (kickers/defenses aren't part of this
    cross-position comparison).
    """
    curve = build_positional_rank_curve(years, scoring_settings, players)

    translated = {}
    for pos in SKILL_POSITIONS:
        ranked = sorted(
            (p for p in players.values() if p.get("position") == pos and p.get(value_key, 0)),
            key=lambda p: p.get(value_key, 0),
            reverse=True
        )
        for rank, p in enumerate(ranked, start=1):
            translated[p.get("player_id")] = real_points_equivalent(pos, rank, curve)
    return translated
