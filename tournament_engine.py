"""
tournament_engine.py - The rules of /tournament and /duel, with no Discord code.

Keeping the rules here (and the Discord buttons in cogs/tournament.py) means every
rule can be tested exactly - see tests/test_tournament.py.

How a "round" works:
- The players in a round each respond (an RPS shape, a dice roll, or a trivia answer).
  A player who doesn't respond in time gives None, and loses that round.
- A resolver turns the responses into (advance, eliminated), or None = replay the round.

Rules (same idea for every game):
- RPS: if exactly two different shapes are played, the winning shape advances and the
  others are out. All the same, or all three shapes, = replay.
- Dice: the lowest roll is out. If the lowest roll is tied, roll again.
- Trivia: wrong (or no) answers are out. If everyone is right, the slowest is out.
  If nobody is right, ask another question.
- Trivia mini quiz (more than 1 question per round): the lowest score is out. A tie for
  everyone = sudden death (one question at a time). After MAX_REPLAYS, a fair random pick.
- Timeouts: anyone who didn't respond is out. If nobody responded, replay.

Tournaments:
- 2 players: one match. Winner is 1st, loser is 2nd, no 3rd place.
- 3 players: one group round. If 2 advance, the 1 out gets 3rd and the 2 play the final.
  If only 1 advances, they're 1st and the 2 who are out play for 2nd and 3rd.
- 4+ players: single elimination (a group round is ONLY for exactly 3 players). The bracket
  is the next power of two (4, 8, 16 ... 128); free passes (byes) are drawn at random in
  round 1, so a player gets at most one bye and every semi-final is a real match. All matches
  of a round are played at the same time. Semi-final losers play for 3rd, then the final.
"""

import asyncio
import secrets

# --- Settings you can tweak ---
MIN_PLAYERS = 2
MAX_REPLAYS = 5   # after this many replays in one round, a random (fair) result is used

RPS_SHAPES = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
RPS_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

_rng = secrets.SystemRandom()  # secure randomness for byes and tie-breaks


class NotEnoughPlayers(Exception):
    pass


# =====================================================================
# Round resolvers: responses {player: answer or None} -> (advance, eliminated) or None (replay)
# =====================================================================

def _split_timeouts(responses):
    answered = {player: answer for player, answer in responses.items() if answer is not None}
    timed_out = [player for player, answer in responses.items() if answer is None]
    return answered, timed_out


def resolve_rps(picks):
    """picks: {player: "rock" | "paper" | "scissors" | None}"""
    answered, out = _split_timeouts(picks)
    advance = list(answered)
    shapes = set(answered.values())
    if len(shapes) == 2:
        # Exactly two shapes: the shape that beats the other one stays in.
        first, second = shapes
        winning = first if RPS_BEATS[first] == second else second
        advance = [player for player, shape in answered.items() if shape == winning]
        out += [player for player, shape in answered.items() if shape != winning]
    if not out or not advance:
        return None  # nobody out (all same / all different) or nobody answered: replay
    return advance, out


def resolve_dice(rolls):
    """rolls: {player: 1-6 | None}. Lowest roll is out; a tied lowest roll means roll again."""
    answered, out = _split_timeouts(rolls)
    if out:
        # Timeouts lose; everyone who rolled stays in this round.
        return (list(answered), out) if answered else None
    lowest = min(answered.values())
    lowest_players = [player for player, roll in answered.items() if roll == lowest]
    if len(lowest_players) > 1:
        return None
    return [player for player in answered if player not in lowest_players], lowest_players


def resolve_trivia(answers):
    """answers: {player: (is_correct, seconds_taken) | None}"""
    answered, out = _split_timeouts(answers)
    correct = {player: seconds for player, (ok, seconds) in answered.items() if ok}
    out += [player for player, (ok, _) in answered.items() if not ok]
    if not correct:
        return None  # nobody got it right: new question
    if out:
        return list(correct), out
    # Everyone was right: the slowest is out (an exact tie for slowest = new question).
    slowest = max(correct.values())
    slowest_players = [player for player, seconds in correct.items() if seconds == slowest]
    if len(slowest_players) > 1:
        return None
    return [player for player in correct if player not in slowest_players], slowest_players


def resolve_score(scores):
    """scores: {player: points | None}. Lowest score is out; everyone tied = sudden death (replay)."""
    points = {player: score or 0 for player, score in scores.items()}
    lowest = min(points.values())
    out = [player for player, score in points.items() if score == lowest]
    if len(out) == len(points):
        return None
    return [player for player in points if player not in out], out


RESOLVERS = {"rps": resolve_rps, "dice": resolve_dice, "trivia": resolve_trivia}


# =====================================================================
# Brackets
# =====================================================================

def bracket_size(count):
    """Next power of two: 4 -> 4, 5 -> 8, 8 -> 8, 9 -> 16."""
    size = 1
    while size < count:
        size *= 2
    return size


def draw_first_round(players):
    """
    Random first round. Returns (pairs, byes).
    Exactly enough players get a bye for round 2 to be a full power of two,
    so each player gets at most one bye and byes never meet each other.
    """
    order = list(players)
    _rng.shuffle(order)
    matches = len(order) - bracket_size(len(order)) // 2
    playing, byes = order[:2 * matches], order[2 * matches:]
    pairs = [(playing[i], playing[i + 1]) for i in range(0, len(playing), 2)]
    return pairs, byes


def round_name(players_left):
    return {2: "Final", 4: "Semi-final", 8: "Quarter-final"}.get(players_left, f"Round of {players_left}")


# =====================================================================
# Matches and tournaments
# =====================================================================

class Engine:
    """
    Runs rounds, matches and whole tournaments.
    collect(players, kind, label, attempt) -> {player: response or None}   (async; Discord buttons or a test script)
    announce(event, **details)                                                (async; posts updates)
    """

    def __init__(self, kind, collect, announce, quiz_questions=1, removed=None):
        if kind not in RESOLVERS:
            raise ValueError(f"unknown game {kind!r}")
        self.kind = kind
        self.collect = collect
        self.announce = announce
        # Trivia with several questions per round is scored by points (collect returns {player: points}).
        self.resolver = resolve_score if kind == "trivia" and quiz_questions > 1 else RESOLVERS[kind]
        # Players removed by the host: they lose every round they're in (shared set, can grow mid-game).
        self.removed = removed if removed is not None else set()

    async def play_round(self, players, label):
        """Play (and replay) a round until somebody is out. Returns (advance, eliminated)."""
        for attempt in range(1, MAX_REPLAYS + 2):
            gone = [player for player in players if player in self.removed]
            active = [player for player in players if player not in self.removed]
            if gone and active:
                await self.announce("removed_result", label=label, advance=active, eliminated=gone)
                return active, gone
            if gone:
                break   # everyone here was removed: a fair random pick below
            responses = await self.collect(list(players), self.kind, label, attempt)
            if any(player in self.removed for player in players):
                continue   # someone was removed during this round: decided at the top of the loop
            result = self.resolver({player: responses.get(player) for player in players})
            # A head-to-head round must end with exactly one winner.
            if result and len(players) == 2 and (len(result[0]) != 1 or len(result[1]) != 1):
                result = None
            if result:
                await self.announce("round_result", label=label, responses=responses,
                                    advance=result[0], eliminated=result[1])
                return result
            if attempt <= MAX_REPLAYS:
                await self.announce("replay", label=label, responses=responses, attempt=attempt)
        # Still undecided after MAX_REPLAYS replays: a fair random result keeps things moving.
        removed = [player for player in players if player in self.removed]
        loser = _rng.choice(removed or list(players))   # a removed player never wins a random pick
        advance = [player for player in players if player != loser]
        await self.announce("random_result", label=label, advance=advance, eliminated=[loser])
        return advance, [loser]

    async def match(self, a, b, label):
        """Head-to-head. Returns (winner, loser)."""
        advance, eliminated = await self.play_round([a, b], label)
        return advance[0], eliminated[0]

    async def play_matches(self, pairs, name):
        """All matches of a bracket round at the same time (a round of 50 matches is as quick as 1).
        Returns [(winner, loser), ...] in the same order as the pairs."""
        return await asyncio.gather(*(self.match(a, b, f"{name} {number}")
                                      for number, (a, b) in enumerate(pairs, start=1)))

    async def run_tournament(self, players):
        """Returns (first, second, third). third is None with only 2 players."""
        players = list(players)
        if len(players) < MIN_PLAYERS:
            raise NotEnoughPlayers()
        if len(players) == 2:
            first, second = await self.match(players[0], players[1], "Final")
            return first, second, None
        if len(players) == 3:
            return await self._three_player_group(players)
        return await self._bracket(players)

    async def _three_player_group(self, players):
        await self.announce("group_start", players=players)
        advance, eliminated = await self.play_round(players, "Group round")
        if len(advance) == 2:
            third = eliminated[0]
            first, second = await self.match(advance[0], advance[1], "Final")
        else:
            first = advance[0]
            second, third = await self.match(eliminated[0], eliminated[1], "Match for 2nd place")
        return first, second, third

    async def _bracket(self, players):
        pairs, byes = draw_first_round(players)
        await self.announce("bracket_start", pairs=pairs, byes=byes, size=bracket_size(len(players)))

        # Round 1 (only the drawn pairs play; bye players go straight through).
        name = round_name(bracket_size(len(players)))
        results = await self.play_matches(pairs, name)
        alive = list(byes) + [winner for winner, _ in results]
        semi_losers = [loser for _, loser in results] if name == "Semi-final" else []

        # Following rounds: always a power of two, so no more byes.
        while len(alive) > 2:
            _rng.shuffle(alive)
            name = round_name(len(alive))
            pairs = [(alive[2 * n], alive[2 * n + 1]) for n in range(len(alive) // 2)]
            results = await self.play_matches(pairs, name)
            alive = [winner for winner, _ in results]
            if name == "Semi-final":
                semi_losers = [loser for _, loser in results]

        third, _ = await self.match(semi_losers[0], semi_losers[1], "3rd place match")
        first, second = await self.match(alive[0], alive[1], "Final")
        return first, second, third
