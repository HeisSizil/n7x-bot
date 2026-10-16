"""
Tests for the tournament rules (tournament_engine.py). No Discord needed.

Run from the project folder:
    python -m unittest discover tests -v
"""

import asyncio
import unittest

import tournament_engine as te


class Script:
    """A fake 'collect' that answers rounds from a script, and records what happened."""

    def __init__(self, answer):
        self.answer = answer          # function(players, label, attempt) -> {player: response}
        self.rounds = []              # (label, players, attempt)
        self.events = []              # (event, details)

    async def collect(self, players, kind, label, attempt):
        self.rounds.append((label, tuple(players), attempt))
        return self.answer(players, label, attempt)

    async def announce(self, event, **details):
        self.events.append((event, details))


def run(coro):
    return asyncio.run(coro)


def strength_dice(strength):
    """Dice where the stronger player always rolls higher (no ties) - makes brackets predictable."""
    def answer(players, label, attempt):
        return {p: strength[p] for p in players}
    return answer


class ResolverRules(unittest.TestCase):
    def test_rps_two_scissors_one_paper(self):
        advance, out = te.resolve_rps({"A": "scissors", "B": "scissors", "C": "paper"})
        self.assertEqual(sorted(advance), ["A", "B"])
        self.assertEqual(out, ["C"])

    def test_rps_all_same_is_replay(self):
        self.assertIsNone(te.resolve_rps({"A": "rock", "B": "rock", "C": "rock"}))

    def test_rps_all_different_is_replay(self):
        self.assertIsNone(te.resolve_rps({"A": "rock", "B": "paper", "C": "scissors"}))

    def test_rps_one_winner_two_out(self):
        advance, out = te.resolve_rps({"A": "scissors", "B": "paper", "C": "paper"})
        self.assertEqual(advance, ["A"])
        self.assertEqual(sorted(out), ["B", "C"])

    def test_rps_timeout_loses(self):
        advance, out = te.resolve_rps({"A": "rock", "B": None})
        self.assertEqual((advance, out), (["A"], ["B"]))

    def test_rps_everyone_timed_out_is_replay(self):
        self.assertIsNone(te.resolve_rps({"A": None, "B": None}))

    def test_dice_lowest_out_and_tied_lowest_rerolls(self):
        self.assertEqual(te.resolve_dice({"A": 6, "B": 4, "C": 2})[1], ["C"])
        self.assertIsNone(te.resolve_dice({"A": 6, "B": 2, "C": 2}))
        self.assertIsNone(te.resolve_dice({"A": 3, "B": 3}))

    def test_trivia_wrong_out_slowest_out_nobody_right_replay(self):
        self.assertEqual(te.resolve_trivia({"A": (True, 5), "B": (False, 2), "C": (True, 9)})[1], ["B"])
        self.assertEqual(te.resolve_trivia({"A": (True, 5), "B": (True, 2), "C": (True, 9)})[1], ["C"])
        self.assertIsNone(te.resolve_trivia({"A": (False, 5), "B": (False, 2), "C": None}))
        advance, out = te.resolve_trivia({"A": (True, 5), "B": (False, 2), "C": None})
        self.assertEqual(advance, ["A"])
        self.assertEqual(sorted(out), ["B", "C"])


class ThreePlayers(unittest.TestCase):
    def test_two_scissors_one_paper(self):
        # Group round: A and B pick scissors, C paper -> C is 3rd. Final: A rock beats B scissors.
        def answer(players, label, attempt):
            if label == "Group round":
                return {"A": "scissors", "B": "scissors", "C": "paper"}
            return {"A": "rock", "B": "scissors"}
        script = Script(answer)
        engine = te.Engine("rps", script.collect, script.announce)
        self.assertEqual(run(engine.run_tournament(["A", "B", "C"])), ("A", "B", "C"))
        self.assertEqual([r[0] for r in script.rounds], ["Group round", "Final"])

    def test_all_same_shape_replays_then_decides(self):
        def answer(players, label, attempt):
            if label == "Group round" and attempt == 1:
                return {p: "rock" for p in players}               # all the same -> replay
            if label == "Group round":
                return {"A": "paper", "B": "paper", "C": "rock"}  # C out
            return {"A": "scissors", "B": "paper"}
        script = Script(answer)
        engine = te.Engine("rps", script.collect, script.announce)
        self.assertEqual(run(engine.run_tournament(["A", "B", "C"])), ("A", "B", "C"))
        self.assertEqual(sum(1 for event, _ in script.events if event == "replay"), 1)

    def test_all_different_shapes_replays_then_decides(self):
        def answer(players, label, attempt):
            if label == "Group round" and attempt <= 2:
                return {"A": "rock", "B": "paper", "C": "scissors"}  # all different -> replay (twice)
            if label == "Group round":
                return {"A": "rock", "B": "rock", "C": "scissors"}
            return {"A": "paper", "B": "rock"}
        script = Script(answer)
        engine = te.Engine("rps", script.collect, script.announce)
        self.assertEqual(run(engine.run_tournament(["A", "B", "C"])), ("A", "B", "C"))
        self.assertEqual(sum(1 for event, _ in script.events if event == "replay"), 2)

    def test_one_winner_then_match_for_second(self):
        # A wins the group alone; B and C play for 2nd/3rd.
        def answer(players, label, attempt):
            if label == "Group round":
                return {"A": "scissors", "B": "paper", "C": "paper"}
            return {"B": "rock", "C": "scissors"}
        script = Script(answer)
        engine = te.Engine("rps", script.collect, script.announce)
        self.assertEqual(run(engine.run_tournament(["A", "B", "C"])), ("A", "B", "C"))
        self.assertEqual(script.rounds[-1][0], "Match for 2nd place")

    def test_dice_and_trivia_groups(self):
        script = Script(lambda players, label, attempt: {"A": 6, "B": 5, "C": 1} if len(players) == 3
                        else {"A": 4, "B": 2})
        self.assertEqual(run(te.Engine("dice", script.collect, script.announce).run_tournament(["A", "B", "C"])),
                         ("A", "B", "C"))
        script = Script(lambda players, label, attempt: {"A": (True, 3), "B": (True, 6), "C": (True, 9)}
                        if len(players) == 3 else {"A": (True, 2), "B": (False, 1)})
        self.assertEqual(run(te.Engine("trivia", script.collect, script.announce).run_tournament(["A", "B", "C"])),
                         ("A", "B", "C"))


class Brackets(unittest.TestCase):
    def check_bracket(self, count):
        players = [f"P{i}" for i in range(1, count + 1)]
        strength = {p: 100 - i for i, p in enumerate(players)}  # P1 strongest
        script = Script(strength_dice(strength))
        engine = te.Engine("dice", script.collect, script.announce)
        first, second, third = run(engine.run_tournament(players))

        self.assertEqual(first, "P1")                      # the strongest always wins
        self.assertEqual(len({first, second, third}), 3)
        labels = [r[0] for r in script.rounds]
        self.assertEqual(labels[-2:], ["3rd place match", "Final"])
        self.assertEqual(len(script.rounds), count)        # n-1 elimination matches + 3rd place match

        start = next(details for event, details in script.events if event == "bracket_start")
        byes = start["byes"]
        self.assertEqual(len(byes), te.bracket_size(count) - count)
        self.assertEqual(len(set(byes)), len(byes))        # nobody gets two byes

        # Every semi-final is a real match between two players, and the 3rd place
        # match is played by exactly the two semi-final losers.
        semis = [r for r in script.rounds if r[0].startswith("Semi-final")]
        self.assertEqual(len(semis), 2)
        semi_results = [details for event, details in script.events
                        if event == "round_result" and details["label"].startswith("Semi-final")]
        semi_losers = sorted(details["eliminated"][0] for details in semi_results)
        third_place = next(r for r in script.rounds if r[0] == "3rd place match")
        self.assertEqual(sorted(third_place[1]), semi_losers)
        self.assertIn(third, semi_losers)
        return script

    def test_4_players(self):
        self.check_bracket(4)

    def test_5_players(self):
        self.check_bracket(5)

    def test_6_players(self):
        self.check_bracket(6)

    def test_8_players(self):
        self.check_bracket(8)

    def test_other_sizes(self):
        for count in (7, 9, 12, 16):
            self.check_bracket(count)

    def test_byes(self):
        for count, expected in [(4, 0), (5, 3), (6, 2), (7, 1), (8, 0), (9, 7)]:
            pairs, byes = te.draw_first_round(list(range(count)))
            self.assertEqual(len(byes), expected)
            self.assertEqual(len(pairs) * 2 + len(byes), count)

    def test_too_few_players(self):
        engine = te.Engine("rps", Script(lambda *a: {}).collect, Script(lambda *a: {}).announce)
        with self.assertRaises(te.NotEnoughPlayers):
            run(engine.run_tournament(["A"]))


class TwoPlayers(unittest.TestCase):
    """Exactly 2 players: one match, winner 1st, loser 2nd, no 3rd place."""

    def play(self, kind, answers):
        script = Script(lambda players, label, attempt: answers[min(attempt, len(answers)) - 1])
        engine = te.Engine(kind, script.collect, script.announce)
        return run(engine.run_tournament(["A", "B"])), script

    def test_rps_tie_replays_then_winner(self):
        podium, script = self.play("rps", [{"A": "rock", "B": "rock"}, {"A": "rock", "B": "paper"}])
        self.assertEqual(podium, ("B", "A", None))
        self.assertEqual([r[0] for r in script.rounds], ["Final", "Final"])

    def test_dice_higher_wins_tie_rerolls(self):
        podium, script = self.play("dice", [{"A": 4, "B": 4}, {"A": 6, "B": 2}])
        self.assertEqual(podium, ("A", "B", None))
        self.assertEqual(len(script.rounds), 2)

    def test_trivia_wrong_loses(self):
        podium, _ = self.play("trivia", [{"A": (False, 2.0), "B": (True, 9.0)}])
        self.assertEqual(podium, ("B", "A", None))

    def test_trivia_both_right_faster_wins(self):
        podium, _ = self.play("trivia", [{"A": (True, 5.0), "B": (True, 3.0)}])
        self.assertEqual(podium, ("B", "A", None))

    def test_trivia_both_wrong_new_question(self):
        podium, script = self.play("trivia", [{"A": (False, 1.0), "B": (False, 1.0)},
                                              {"A": (True, 4.0), "B": (False, 2.0)}])
        self.assertEqual(podium, ("A", "B", None))
        self.assertEqual(len(script.rounds), 2)


class Timeouts(unittest.TestCase):
    def test_player_who_times_out_loses_the_match(self):
        script = Script(lambda players, label, attempt: {"A": "rock", "B": None})
        engine = te.Engine("rps", script.collect, script.announce)
        self.assertEqual(run(engine.match("A", "B", "Final")), ("A", "B"))

    def test_timeout_in_group_round_is_eliminated(self):
        def answer(players, label, attempt):
            if label == "Group round":
                return {"A": "rock", "B": "rock", "C": None}   # C didn't answer -> C is 3rd
            return {"A": "paper", "B": "rock"}
        script = Script(answer)
        engine = te.Engine("rps", script.collect, script.announce)
        self.assertEqual(run(engine.run_tournament(["A", "B", "C"])), ("A", "B", "C"))

    def test_both_time_out_then_random_after_max_replays(self):
        script = Script(lambda players, label, attempt: {p: None for p in players})
        engine = te.Engine("rps", script.collect, script.announce)
        winner, loser = run(engine.match("A", "B", "Final"))
        self.assertEqual({winner, loser}, {"A", "B"})
        self.assertEqual(len(script.rounds), te.MAX_REPLAYS + 1)
        self.assertTrue(any(event == "random_result" for event, _ in script.events))


if __name__ == "__main__":
    unittest.main()
