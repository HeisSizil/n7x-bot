"""
team_engine.py - The rules of /duel team modes (2v2, 3v3, 4v4), with no Discord code.

Like tournament_engine.py, it gets two async functions:
    collect(players, kind, label, attempt) -> {player: response or None}
    announce(event, **details)
so every rule can be tested exactly (see tests/test_duel_modes.py).

Scoring:
- RPS: players are put in random pairs (one from each team). Each pair plays a match
  (draws replay inside the pair, a timeout loses). The team with more pair wins wins.
  A tie = one sudden-death pair (a random player from each team).
- Dice: everyone rolls once. Higher team total wins. Equal totals = everyone re-rolls.
- Trivia: everyone answers the same questions. More correct answers in total wins.
  A tie = sudden death (one question at a time).
- Timeouts: no answer = a loss in RPS, 0 points in dice and trivia.
- Still tied after MAX_REPLAYS re-rolls / sudden deaths: a fair random pick.
"""

import asyncio
import secrets

import tournament_engine as te

_rng = secrets.SystemRandom()


def pair_up(team_a, team_b):
    """Random pairs, one player from each team. Teams must be the same size."""
    a, b = list(team_a), list(team_b)
    _rng.shuffle(a)
    _rng.shuffle(b)
    return list(zip(a, b))


class TeamResult:
    def __init__(self, winner, team_a, team_b, score_a, score_b, contributions, random_pick=False):
        self.winner = winner                    # "A" or "B"
        self.winners = team_a if winner == "A" else team_b
        self.losers = team_b if winner == "A" else team_a
        self.score_a = score_a
        self.score_b = score_b
        self.contributions = contributions      # {player: short text}
        self.random_pick = random_pick


class TeamEngine:
    def __init__(self, kind, collect, announce, removed=None):
        if kind not in te.RESOLVERS:
            raise ValueError(f"unknown game {kind!r}")
        self.kind = kind
        self.collect = collect
        self.announce = announce
        # Players removed by the host: they lose their pair (RPS) or score 0 (dice/trivia).
        self.removed = removed if removed is not None else set()

    async def play(self, team_a, team_b):
        team_a, team_b = list(team_a), list(team_b)
        if not team_a or len(team_a) != len(team_b):
            raise ValueError("teams must be the same size (at least 1 each)")
        if self.kind == "rps":
            return await self._rps(team_a, team_b)
        return await self._points(team_a, team_b)

    # --------------------------------------------------------------- RPS
    async def _rps(self, team_a, team_b):
        pair_engine = te.Engine("rps", self.collect, self.announce, removed=self.removed)
        pairs = pair_up(team_a, team_b)
        await self.announce("team_pairs", pairs=pairs)
        wins = {"A": 0, "B": 0}
        notes = {}
        results = await pair_engine.play_matches(pairs, "Pair")   # all pairs at the same time
        for winner, loser in results:
            wins["A" if winner in team_a else "B"] += 1
            notes[winner] = f"✅ beat {loser.mention}"
            notes[loser] = f"❌ lost to {winner.mention}"
        await self.announce("team_score", score_a=wins["A"], score_b=wins["B"])

        if wins["A"] == wins["B"]:
            a, b = _rng.choice(team_a), _rng.choice(team_b)
            await self.announce("sudden_death", players=[a, b])
            winner, loser = await pair_engine.match(a, b, "⚡ Sudden-death pair")
            wins["A" if winner in team_a else "B"] += 1
            notes[winner] += " · ⚡ won sudden death"
            notes[loser] += " · ⚡ lost sudden death"
        winner = "A" if wins["A"] > wins["B"] else "B"
        return TeamResult(winner, team_a, team_b, wins["A"], wins["B"], notes)

    # ------------------------------------------------------ dice / trivia
    async def _points(self, team_a, team_b):
        players = team_a + team_b
        totals = {player: 0 for player in players}
        label = "Team roll" if self.kind == "dice" else "Team quiz"
        for attempt in range(1, te.MAX_REPLAYS + 2):
            responses = await self.collect(players, self.kind, label, attempt)
            points = {player: 0 if player in self.removed else self.points(responses.get(player))
                      for player in players}
            if self.kind == "dice":
                totals = points                       # a re-roll replaces the earlier roll
            else:
                for player, value in points.items():  # sudden-death answers add up
                    totals[player] += value
            score_a = sum(points[p] for p in team_a)
            score_b = sum(points[p] for p in team_b)
            await self.announce("team_points", label=label, attempt=attempt, points=points,
                                score_a=score_a, score_b=score_b)
            if score_a != score_b:
                winner = "A" if score_a > score_b else "B"
                return TeamResult(winner, team_a, team_b, sum(totals[p] for p in team_a),
                                  sum(totals[p] for p in team_b), self.notes(totals))
            if attempt <= te.MAX_REPLAYS:
                await self.announce("team_tie", attempt=attempt)

        winner = _rng.choice(["A", "B"])
        await self.announce("team_random", winner=winner)
        return TeamResult(winner, team_a, team_b, sum(totals[p] for p in team_a),
                          sum(totals[p] for p in team_b), self.notes(totals), random_pick=True)

    def points(self, response):
        """Dice: the roll. Trivia: points from collect (an int), or 1/0 for a single answer."""
        if response is None:
            return 0
        if isinstance(response, tuple):        # (is_correct, seconds)
            return 1 if response[0] else 0
        return int(response)

    def notes(self, totals):
        if self.kind == "dice":
            return {player: f"🎲 rolled **{value}**" if value else "⏰ didn't roll (0)" for player, value in totals.items()}
        return {player: f"🧠 **{value}** correct" for player, value in totals.items()}
