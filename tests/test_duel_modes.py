"""
Tests for /duel team modes (2v2, 3v3, 4v4) and free for all, with fake Discord objects.
No internet, no real server, temporary database.

Run from the project folder:
    python -m unittest discover tests -v
"""

import asyncio
import os
import shutil
import tempfile
import time
import unittest
from types import SimpleNamespace as NS

import database
import team_engine
from tests.test_tournament_discord import FakeChannel, FakeMember, FakeMessage

ROCK, PAPER, SCISSORS = 0, 1, 2


class FakeSecrets:
    """Stands in for the `secrets` module in cogs/tournament.py so dice rolls are predictable."""

    def __init__(self, rolls):
        import secrets
        self.real = secrets
        self.rolls = list(rolls)

    def randbelow(self, n):
        if n == 6 and self.rolls:
            return self.rolls.pop(0) - 1
        return self.real.randbelow(n)

    def __getattr__(self, name):
        return getattr(self.real, name)


class DuelModeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_db = database.DB_FILE
        database.DB_FILE = os.path.join(self.tmp, "test.db")
        database.setup_database()

        import cogs.duels as dc
        import cogs.tournament as tc
        self.dc, self.tc = dc, tc
        tc.SEND_GAP = 0
        tc.BATCH_WINDOW = 0.01
        import game_panel
        game_panel.BOARD_MIN_INTERVAL = 0
        tc.ROUND_SECONDS = 1
        dc.DICE_FRAME_DELAY = 0
        self.old_secrets = tc.secrets
        self.old_pair_up = team_engine.pair_up
        self.channel = FakeChannel(tc)
        self.guild = NS(id=1)
        self.cog = dc.Duels(None)
        self.host = FakeMember(1, "host", mod=True)
        self.p = {n: FakeMember(n, f"p{n}") for n in range(2, 12)}

    def tearDown(self):
        self.tc.secrets = self.old_secrets
        team_engine.pair_up = self.old_pair_up
        database.DB_FILE = self.old_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ----------------------------------------------------------- helpers
    def lobby(self, kind, a, b, size=None, test_mode=False):
        lobby = self.dc.TeamLobbyView(self.host, kind, size or max(len(a), len(b), 1), test_mode)
        lobby.teams = {"A": [self.p[n] for n in a], "B": [self.p[n] for n in b]}
        lobby.message = FakeMessage()
        return lobby

    async def play_team(self, lobby, questions=3):
        lobby.start_now.set()
        await asyncio.wait_for(self.cog.run_team_game(self.channel, self.guild, lobby, questions, 5), 60)
        return self.channel.sent[-1][0]

    def stats(self, n):
        return database.get_duel_stats(1, self.p[n].id if n in self.p else n)

    def no_xp_anywhere(self):
        self.assertEqual(database.fetch_all("SELECT * FROM game_xp_daily"), [])
        self.assertEqual(database.fetch_all("SELECT * FROM game_stats"), [])
        self.assertNotIn("XP **", self.channel.all_text())

    def lobby_reply(self, replies):
        async def send_message(text=None, **kwargs):
            replies.append(text)
        return NS(send_message=send_message, edit_message=self.channel.ignore, defer=self.channel.ignore)

    def in_sudden_death(self):
        return any("Sudden death" in (embed.title or "") or "sudden-death" in (embed.description or "")
                   for embed, _ in self.channel.sent[-3:] if embed)

    # -------------------------------------------------------------- RPS
    async def test_2v2_rps(self):
        # Team A always rock, team B always scissors: A wins both pairs.
        self.channel.plan = {2: lambda v: ROCK, 3: lambda v: ROCK, 4: lambda v: SCISSORS, 5: lambda v: SCISSORS}
        result = await self.play_team(self.lobby("rps", [2, 3], [4, 5]))
        self.assertEqual(result.title, "🏆 🔵 Team A wins!")
        self.assertIn("**2** - **0**", result.description)
        fields = {f.name: f.value for f in result.fields}
        self.assertIn("✅ beat", fields["🔵 Team A 👑"])
        self.assertIn("❌ lost to", fields["🔴 Team B"])
        self.assertIn("🎲 Random pairs", self.channel.titles())
        self.assertEqual((self.stats(2)["team_wins"], self.stats(4)["team_losses"]), (1, 1))
        self.assertEqual(self.stats(2)["duel_wins"], 0)   # counted apart from 1v1 duels
        self.no_xp_anywhere()

    async def test_2v2_rps_tie_goes_to_sudden_death_pair(self):
        team_engine.pair_up = lambda a, b: list(zip(a, b))   # pairs: 2 vs 4, 3 vs 5
        # 2 rock beats 4 scissors (A), 3 rock loses to 5 paper (B) -> 1-1 -> sudden death.
        self.channel.plan = {2: lambda v: ROCK, 3: lambda v: ROCK, 4: lambda v: SCISSORS, 5: lambda v: PAPER}
        result = await self.play_team(self.lobby("rps", [2, 3], [4, 5]))
        self.assertIn("⚡ Sudden death!", self.channel.titles())
        self.assertTrue("**2** - **1**" in result.description or "**1** - **2**" in result.description)
        self.assertIn("⚡", " ".join(f.value for f in result.fields))

    async def test_rps_draw_replays_inside_the_pair(self):
        team_engine.pair_up = lambda a, b: list(zip(a, b))
        first = {}

        def changes(v):   # rock first, then paper on the replay
            first.setdefault(2, 0)
            first[2] += 1
            return ROCK if first[2] == 1 else PAPER
        self.channel.plan = {2: changes, 3: lambda v: ROCK}
        result = await self.play_team(self.lobby("rps", [2], [3]))
        self.assertTrue(any(t.startswith("🔁 Pair 1") for t in self.channel.titles()))
        self.assertEqual(result.title, "🏆 🔵 Team A wins!")

    # ------------------------------------------------------------- Dice
    async def test_3v3_dice(self):
        # Presses happen in id order: A (2, 3, 4) roll 6, 5, 4 = 15; B (5, 6, 7) roll 1, 2, 3 = 6.
        self.tc.secrets = FakeSecrets([6, 5, 4, 1, 2, 3])
        self.channel.plan = {n: (lambda v: 0) for n in range(2, 8)}
        result = await self.play_team(self.lobby("dice", [2, 3, 4], [5, 6, 7]))
        self.assertEqual(result.title, "🏆 🔵 Team A wins!")
        self.assertIn("**15** - **6**", result.description)
        self.assertIn("rolled **6**", result.fields[0].value)
        self.assertIn("🎲 Rolling...", self.channel.titles())   # animated, then edited to the totals
        self.no_xp_anywhere()

    async def test_dice_equal_totals_reroll(self):
        self.tc.secrets = FakeSecrets([3, 4, 4, 3, 1, 1, 6, 6])   # 7 vs 7, then 2 vs 12
        self.channel.plan = {n: (lambda v: 0) for n in range(2, 6)}
        result = await self.play_team(self.lobby("dice", [2, 3], [4, 5]))
        self.assertIn("🔁 Tied!", self.channel.titles())
        self.assertEqual(result.title, "🏆 🔴 Team B wins!")
        self.assertIn("**2** - **12**", result.description)

    async def test_timeout_counts_as_zero(self):
        # Player 5 never presses Roll: 0 points.
        self.tc.secrets = FakeSecrets([2, 2, 3])
        self.channel.plan = {2: lambda v: 0, 3: lambda v: 0, 4: lambda v: 0}
        result = await self.play_team(self.lobby("dice", [2, 3], [4, 5]))
        self.assertEqual(result.title, "🏆 🔵 Team A wins!")
        self.assertIn("**4** - **3**", result.description)
        self.assertIn("didn't roll (0)", result.fields[1].value)

    # ----------------------------------------------------------- Trivia
    async def test_2v2_trivia_tie_then_sudden_death(self):
        def right(v):
            return v.question["answer"]

        def wrong(v):
            return (v.question["answer"] + 1) % 4

        def lee(v):   # right in the main quiz, wrong in sudden death
            return wrong(v) if self.in_sudden_death() else right(v)
        # A: 2 right, 3 wrong = 2 points; B: 4 right, 5 wrong = 2 points -> tie -> sudden death.
        self.channel.plan = {2: right, 3: wrong, 4: lee, 5: wrong}
        result = await self.play_team(self.lobby("trivia", [2, 3], [4, 5]), questions=2)
        titles = self.channel.titles()
        self.assertIn("⚔️ Team quiz • Question 2/2", titles)
        self.assertIn("🔁 Tied!", titles)
        self.assertIn("⚔️ Team quiz • ⚡ Sudden death 1", titles)
        self.assertEqual(result.title, "🏆 🔵 Team A wins!")
        self.assertIn("**3** - **2**", result.description)
        self.assertIn("🧠 **3** correct", result.fields[0].value)
        self.no_xp_anywhere()

    # ------------------------------------------------------------ lobby
    async def test_uneven_teams_are_refused(self):
        lobby = self.lobby("rps", [2, 3], [4], size=2)
        replies = []
        await lobby.start.callback(NS(user=self.host, response=self.lobby_reply(replies)))
        self.assertEqual(replies, ["Teams must be even: Team B needs 1 more player(s)."])
        self.assertFalse(lobby.start_now.is_set())
        lobby.teams = {"A": [], "B": []}
        await lobby.start.callback(NS(user=self.host, response=self.lobby_reply(replies)))
        self.assertEqual(replies[-1], "Both teams need players.")
        lobby.teams = {"A": [self.p[2]], "B": [self.p[3]]}   # 1v1 inside a 2v2 lobby is fine
        await lobby.start.callback(NS(user=self.host, response=self.lobby_reply(replies)))
        self.assertTrue(lobby.start_now.is_set())

    async def test_only_host_starts_and_teams_switch_and_fill(self):
        lobby = self.dc.TeamLobbyView(self.host, "rps", 2, on_join=self.cog.claim, on_leave=self.cog.release)
        replies = []
        for n, team in ((2, "A"), (3, "A"), (4, "B")):
            await lobby.join(NS(user=self.p[n], response=self.lobby_reply(replies)), team)
        await lobby.join(NS(user=self.p[5], response=self.lobby_reply(replies)), "A")
        self.assertEqual(replies, ["Team A is full."])
        await lobby.join(NS(user=self.p[3], response=self.lobby_reply(replies)), "B")   # switch
        self.assertEqual([m.id for m in lobby.teams["A"]], [2])
        self.assertEqual([m.id for m in lobby.teams["B"]], [4, 3])
        self.assertIn("🔴 Team B (FULL 2/2)", [f.name for f in lobby.embed().fields])
        await lobby.start.callback(NS(user=self.p[2], response=self.lobby_reply(replies)))
        self.assertEqual(replies[-1], "Only the host can start.")
        await lobby.leave.callback(NS(user=self.p[4], response=self.lobby_reply(replies)))
        self.assertNotIn(4, self.cog.busy)
        self.assertEqual(self.cog.busy, {2, 3})

    async def test_one_game_per_player(self):
        first = self.dc.TeamLobbyView(self.host, "rps", 2, on_join=self.cog.claim, on_leave=self.cog.release)
        second = self.dc.TeamLobbyView(self.host, "dice", 2, on_join=self.cog.claim, on_leave=self.cog.release)
        replies = []
        await first.join(NS(user=self.p[2], response=self.lobby_reply(replies)), "A")
        await second.join(NS(user=self.p[2], response=self.lobby_reply(replies)), "B")
        self.assertEqual(replies, ["You're already in another duel. Finish that one first!"])
        self.assertEqual(second.members(), [])

    async def test_lobby_timeout_cancels(self):
        lobby = self.lobby("rps", [2], [], size=2)
        self.cog.busy.add(2)
        await asyncio.wait_for(self.cog.run_team_game(self.channel, self.guild, lobby, 3, 0.01), 5)
        self.assertEqual(self.channel.titles()[-1], "⌛ Team duel cancelled")
        self.assertIn("didn't start in time", lobby.message.embeds[0].description)
        self.assertTrue(all(item.disabled for item in lobby.children))
        self.assertEqual(self.cog.busy, set())

    async def test_host_not_joined_is_not_a_player(self):
        self.channel.plan = {2: lambda v: PAPER, 3: lambda v: ROCK}
        result = await self.play_team(self.lobby("rps", [2], [3]))
        self.assertNotIn("<@1>", self.channel.all_text())
        self.assertEqual(self.stats(1)["team_wins"] + self.stats(1)["team_losses"], 0)
        self.assertEqual(result.title, "🏆 🔵 Team A wins!")

    async def test_4v4_test_mode_bots_get_no_stats(self):
        self.channel.plan = {2: lambda v: ROCK}
        lobby = self.lobby("rps", [2], [], size=4, test_mode=True)
        result = await self.play_team(lobby)
        self.assertTrue(result.title.startswith("🏆"))
        self.assertIn("🤖 Bot", self.channel.all_text())
        rows = database.fetch_all("SELECT user_id FROM duel_stats")
        self.assertEqual([row["user_id"] for row in rows], [2])

    async def test_test_mode_is_mods_only(self):
        replies = []

        async def send_message(text=None, **kwargs):
            replies.append(text)
        interaction = NS(user=self.p[2], channel=self.channel, response=NS(send_message=send_message))
        await self.cog.duel.callback(self.cog, interaction, NS(value="rps"), NS(value="2v2"), None, None, 6, True)
        self.assertEqual(replies, ["Only mods can use test mode."])
        await self.cog.duel.callback(self.cog, interaction, NS(value="rps"), None, None, None, 6, False)
        self.assertEqual(replies[-1], "Pick an `opponent` for a 1v1 duel.")
        await self.cog.duel.callback(self.cog, interaction, NS(value="rps"), NS(value="2v2"), self.p[3], None, 6,
                                     False)
        self.assertIn("only for 1v1", replies[-1])

    async def test_anyone_can_host_a_team_lobby(self):
        started = []
        self.cog.run_in_background = lambda coro, channel: (started.append(coro), coro.close())
        sent = []

        async def send_message(embed=None, **kwargs):
            sent.append(embed)

        async def original_response():
            return FakeMessage()
        interaction = NS(user=self.p[2], channel=self.channel, guild=self.guild,
                         response=NS(send_message=send_message), original_response=original_response)
        await self.cog.duel.callback(self.cog, interaction, NS(value="dice"), NS(value="3v3"), None, None, 6, False)
        self.assertEqual(sent[0].title, "👥 🎲 Dice 3v3")
        self.assertEqual(len(started), 1)

    # ------------------------------------------------------- free for all
    async def run_ffa(self, humans=(), test_mode=False, max_players=10):
        lobby = self.tc.LobbyView(self.host, "rps", max_players, int(time.time()) + 60, test_mode,
                                  free_for_all=True, on_join=self.cog.claim, on_leave=self.cog.release)
        lobby.players = [self.p[n] for n in humans]
        lobby.message = FakeMessage()
        await asyncio.wait_for(self.cog.run_free_for_all(self.channel, self.guild, lobby, 0.01), 60)
        return self.channel.sent[-1][0]

    async def test_free_for_all_with_3_humans(self):
        self.channel.plan = {2: lambda v: SCISSORS if len(v.humans) == 3 else ROCK,
                             3: lambda v: SCISSORS, 4: lambda v: PAPER}
        result = await self.run_ffa([2, 3, 4])
        self.assertEqual(result.title, "🏆 Free for all results")
        self.assertIn("🥇 <@2>", result.description)
        self.assertIn("no XP", result.description)
        self.assertEqual((self.stats(2)["ffa_gold"], self.stats(3)["ffa_silver"], self.stats(4)["ffa_bronze"]),
                         (1, 1, 1))
        self.assertEqual(self.stats(2)["ffa_played"], 1)
        self.assertEqual(self.stats(2)["tournaments_played"] + self.stats(2)["gold"], 0)   # not a tournament
        self.no_xp_anywhere()

    async def test_free_for_all_5_and_10_players(self):
        for size in (5, 10):
            self.channel.sent.clear()
            result = await self.run_ffa(test_mode=True, max_players=size)
            self.assertEqual(result.title, "🏆 Free for all results")
            self.assertIn(f"{size} players", result.description)
        self.assertEqual(database.fetch_all("SELECT * FROM duel_stats"), [])   # bots never get stats
        self.no_xp_anywhere()

    async def test_free_for_all_one_player_cancels(self):
        result = await self.run_ffa([2])
        self.assertIn("cancelled", result.title)
        self.assertIn("at least **2** are needed", result.description)
        self.assertIn("only counts as a player if they pressed **Join**", result.description)
        self.assertEqual(self.cog.busy, set())

    async def test_free_for_all_with_2_players_has_no_bronze(self):
        self.channel.plan = {2: lambda v: PAPER, 3: lambda v: ROCK}
        result = await self.run_ffa([2, 3])
        self.assertEqual(result.title, "🏆 Free for all results")
        self.assertIn("🥇 <@2>", result.description)
        self.assertIn("🥈 <@3>", result.description)
        self.assertNotIn("🥉", result.description)
        self.no_xp_anywhere()

    async def test_free_for_all_host_start_needs_2_and_lobby_cap(self):
        lobby = self.tc.LobbyView(self.host, "dice", 3, int(time.time()) + 60, free_for_all=True,
                                  on_join=self.cog.claim, on_leave=self.cog.release)
        replies = []
        await lobby.join.callback(NS(user=self.p[2], response=self.lobby_reply(replies)))
        await lobby.start.callback(NS(user=self.host, response=self.lobby_reply(replies)))
        self.assertIn("at least 2", replies[-1])
        self.assertFalse(lobby.start_now.is_set())
        for n in (3, 4):
            await lobby.join.callback(NS(user=self.p[n], response=self.lobby_reply(replies)))
        await lobby.join.callback(NS(user=self.p[5], response=self.lobby_reply(replies)))
        self.assertEqual(replies[-1], "Lobby is full.")
        self.assertNotIn(5, self.cog.busy)
        self.assertIn("👥 Players (FULL 3/3)", [f.name for f in lobby.embed().fields])
        self.assertEqual(lobby.embed().title, "⚔️ 🎲 Dice Free for all")
        await lobby.start.callback(NS(user=self.host, response=self.lobby_reply(replies)))
        self.assertTrue(lobby.start_now.is_set())

    # ------------------------------------------------------------ restart
    async def test_restart_cancels_running_games(self):
        lobby = self.lobby("rps", [2], [3])
        task = self.cog.run_in_background(self.cog.run_team_game(self.channel, self.guild, lobby, 3, 60),
                                          self.channel)
        await asyncio.sleep(0.01)
        await self.cog.cog_unload()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.channel.titles()[-1], "⚠️ Duel cancelled")
        self.assertEqual(self.cog.busy, set())

    async def test_duelstats_shows_modes_separately(self):
        database.record_team_game(1, [2], [3])
        database.record_free_for_all(1, [2, 3, 4], [2, None, 4])
        shown = []

        async def send_message(embed=None, **kwargs):
            shown.append(embed)
        await self.cog.duelstats.callback(self.cog, NS(user=self.p[2], guild=self.guild,
                                                       response=NS(send_message=send_message)), None)
        fields = {f.name: f.value for f in shown[0].fields}
        self.assertEqual(fields["👥 Team duels"], "**1** W · **0** L")
        self.assertIn("**1** played", fields["🎯 Free for all"])
        self.assertIn("🥇 **1**", fields["🎯 Free for all"])
        self.assertIn("**0** played", fields["🏆 Tournaments"])


if __name__ == "__main__":
    unittest.main()
