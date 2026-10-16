"""
Tests for the live scoreboard and the host panel (game_panel.py), with fake Discord objects.
No internet, no real server, temporary database.

Run from the project folder:
    python -m unittest discover tests -v
"""

import asyncio
import json
import os
import shutil
import tempfile
import time
import unittest
from types import SimpleNamespace as NS

import database
import game_panel
from tests.test_tournament_discord import FakeChannel, FakeMember, FakeMessage

ROCK, PAPER, SCISSORS = 0, 1, 2


class BoardChannel(FakeChannel):
    """A FakeChannel that also keeps the scoreboard message, so tests can look at it and press its button."""

    def __init__(self, tc):
        super().__init__(tc)
        self.board_message = None

    async def send(self, embed=None, view=None, **kwargs):
        message = await super().send(embed=embed, view=view, **kwargs)
        if isinstance(view, game_panel.PanelButton):
            message.view = view
            self.board_message = message
        return message


class Replies:
    """Collects private replies (text, embed, view) from fake interactions."""

    def __init__(self):
        self.items = []

    def response(self):
        async def send_message(content=None, embed=None, view=None, **kwargs):
            self.items.append((content, embed, view))

        async def edit_message(content=None, embed=None, view=None, **kwargs):
            self.items.append((content, embed, view))
        return NS(send_message=send_message, edit_message=edit_message)

    @property
    def last(self):
        return self.items[-1]


def embed_text(embed):
    return json.dumps(embed.to_dict(), ensure_ascii=False)


class HostPanelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_db = database.DB_FILE
        database.DB_FILE = os.path.join(self.tmp, "test.db")
        database.setup_database()

        import cogs.duels as dc
        import cogs.tournament as tc
        self.tc, self.dc = tc, dc
        tc.SEND_GAP = 0
        tc.ROUND_SECONDS = 5          # long enough for the host to act while players are pending
        dc.DICE_FRAME_DELAY = 0
        self.channel = BoardChannel(tc)
        self.guild = NS(id=1)
        self.host = FakeMember(1, "host", mod=True)
        self.mod = FakeMember(9, "othermod", mod=True)
        self.alt = FakeMember(2, "alt")
        self.kim = FakeMember(3, "kim")
        self.lee = FakeMember(4, "lee")

    def tearDown(self):
        database.DB_FILE = self.old_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ----------------------------------------------------------- helpers
    def start_tournament(self, players, kind="rps"):
        cog = self.tc.Tournament(None)
        lobby = self.tc.LobbyView(self.host, kind, 8, int(time.time()) + 60)
        lobby.players = list(players)
        lobby.message = FakeMessage()
        return asyncio.get_running_loop().create_task(
            cog.run_tournament(NS(channel=self.channel, guild=self.guild), lobby, 0.01, False))

    async def board(self):
        """Wait until the game is in a round (the board exists and a round is open)."""
        for _ in range(500):
            message = self.channel.board_message
            if message and message.view.board.session.current_view is not None:
                return message.view.board
            await asyncio.sleep(0.01)
        self.fail("the game never reached a round")

    async def open_panel(self, board, user):
        replies = Replies()
        await board.view.panel.callback(NS(user=user, response=replies.response()))
        return replies.last

    async def finish(self, task):
        await asyncio.wait_for(task, 20)

    # -------------------------------------------------------- scoreboard
    async def test_scoreboard_is_edited_and_becomes_the_final_card(self):
        # Group round: alt & kim scissors beat lee paper; final: alt rock beats kim scissors.
        self.channel.plan = {2: lambda v: SCISSORS if len(v.humans) == 3 else ROCK,
                             3: lambda v: SCISSORS, 4: lambda v: PAPER}
        task = self.start_tournament([self.alt, self.kim, self.lee])
        board = await self.board()
        live = board.message.embeds[0]
        self.assertTrue(live.title.startswith("📊 Live scoreboard"))
        await self.finish(task)

        self.assertGreaterEqual(board.edits, 4)     # after each round, plus the final card
        final = board.message.embeds[0]
        self.assertTrue(final.title.startswith("🏁 Final scoreboard"))
        self.assertIn("🥇 <@2>", final.description)
        self.assertIn("<@2> 👑 · **2**", final.fields[0].value)   # 2 rounds won and the leader
        self.assertIn("❌ out", final.fields[0].value)
        self.assertTrue(board.view.panel.disabled)
        self.assertEqual(self.channel.titles()[-1], "🏆 Tournament results")   # the results card is still sent

    async def test_bracket_shows_byes_and_round_name(self):
        players = [FakeMember(n, f"p{n}") for n in range(2, 7)]   # 5 players: 3 byes
        task = self.start_tournament(players)
        board = await self.board()
        text = embed_text(board.embed())
        self.assertIn("Bracket of 8", text)
        self.assertIn("🎟️ bye", text)
        self.assertIn("Quarter-final", text)
        await board.session.cancel(self.host)
        await self.finish(task)

    # --------------------------------------------------------- the panel
    async def test_panel_for_host_and_mod_but_not_a_normal_member(self):
        task = self.start_tournament([self.alt, self.kim])
        board = await self.board()
        content, embed, view = await self.open_panel(board, self.alt)
        self.assertEqual(content, "Only the host or a mod can use this.")
        self.assertIsNone(embed)
        for user in (self.host, self.mod):   # the host isn't playing; a mod can open any running game
            content, embed, view = await self.open_panel(board, user)
            self.assertEqual(embed.title, "🎛️ Host panel")
            self.assertIsInstance(view, game_panel.HostControls)
        await board.session.cancel(self.host)
        await self.finish(task)

    async def test_panel_never_shows_picks(self):
        self.channel.plan = {2: lambda v: ROCK}   # alt picks rock, kim never picks
        task = self.start_tournament([self.alt, self.kim])
        board = await self.board()
        await asyncio.sleep(0.1)                  # alt's pick is in
        _, embed, _ = await self.open_panel(board, self.host)
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(fields["✅ Answered (1)"], "alt")
        self.assertEqual(fields["⏳ Pending (1)"], "kim")
        self.assertIn("s", fields["⏱️ Time left"])
        text = embed_text(embed).lower()
        for secret in ("rock", "🪨", "paper", "scissors"):
            self.assertNotIn(secret, text)
        public = " ".join(f.value for f in board.embed().fields).lower()   # nor on the public scoreboard
        self.assertNotIn("rock", public)
        self.assertNotIn("🪨", public)
        await board.session.cancel(self.host)
        await self.finish(task)

    async def test_skip_waiting(self):
        self.channel.plan = {2: lambda v: ROCK}
        task = self.start_tournament([self.alt, self.kim])
        board = await self.board()
        await asyncio.sleep(0.1)
        _, _, controls = await self.open_panel(board, self.host)
        replies = Replies()
        started = time.monotonic()
        with self.assertLogs("n7x.tournament", level="INFO") as logs:
            await controls.skip.callback(NS(user=self.host, response=replies.response()))
        self.assertIn("1 pending player(s) count as timed out", replies.last[0])
        self.assertTrue(any("Host panel: skip waiting" in line for line in logs.output))
        await self.finish(task)
        self.assertLess(time.monotonic() - started, 4)            # didn't wait for the 5-second timer
        self.assertEqual(database.get_duel_stats(1, self.alt.id)["gold"], 1)
        self.assertIn("skipped waiting", self.channel.all_text())

    async def test_remove_a_player(self):
        # 3 players; lee is removed while pending -> alt and kim play the final.
        self.channel.plan = {2: lambda v: ROCK if len(v.humans) == 2 else PAPER,
                             3: lambda v: SCISSORS if len(v.humans) == 2 else PAPER}
        task = self.start_tournament([self.alt, self.kim, self.lee])
        board = await self.board()
        await asyncio.sleep(0.1)
        _, _, controls = await self.open_panel(board, self.host)
        replies = Replies()
        await controls.remove.callback(NS(user=self.host, response=replies.response()))
        picker = replies.last[2]
        self.assertIsInstance(picker, game_panel.RemovePicker)
        key = next(k for k, p in picker.choices.items() if p == self.lee)
        picker.select._values = [key]
        with self.assertLogs("n7x.tournament", level="INFO") as logs:
            await picker.picked(NS(user=self.host, response=replies.response()))
        self.assertEqual(replies.last[0], "🚫 Removed lee.")
        self.assertTrue(any("Host panel: removed user=4" in line for line in logs.output))
        await self.finish(task)

        self.assertIn("Host removed <@4>", self.channel.all_text())
        self.assertIn("🚫 removed", embed_text(board.message.embeds[0]))
        self.assertEqual(database.get_duel_stats(1, self.alt.id)["gold"], 1)
        self.assertEqual(database.get_duel_stats(1, self.lee.id)["bronze"], 0)    # no podium credit
        profile = database.get_game_profile(1, self.lee.id, self.tc.today())
        self.assertEqual(profile["game_xp_today"], 0)                               # and no XP
        self.assertIn("removed by the host, no XP", self.channel.all_text())

    async def test_removal_down_to_fewer_than_2_cancels(self):
        task = self.start_tournament([self.alt, self.kim])
        board = await self.board()
        result = await board.session.remove(self.kim, self.host)
        self.assertEqual(result, "cancelled")
        await self.finish(task)
        self.assertEqual(board.message.embeds[0].title, "🛑 Game cancelled")
        self.assertIn("fewer than 2 players", board.message.embeds[0].description)
        self.assertEqual(database.fetch_all("SELECT * FROM duel_stats"), [])

    async def test_cancel_needs_confirm(self):
        task = self.start_tournament([self.alt, self.kim])
        board = await self.board()
        _, _, controls = await self.open_panel(board, self.host)
        replies = Replies()
        await controls.cancel.callback(NS(user=self.host, response=replies.response()))
        confirm = replies.last[2]
        self.assertIsInstance(confirm, game_panel.ConfirmCancel)
        await confirm.no.callback(NS(user=self.host, response=replies.response()))
        self.assertEqual(replies.last[0], "👍 The game continues.")
        self.assertFalse(task.done())

        await controls.cancel.callback(NS(user=self.host, response=replies.response()))
        await replies.last[2].yes.callback(NS(user=self.host, response=replies.response()))
        await self.finish(task)
        self.assertEqual(board.message.embeds[0].title, "🛑 Game cancelled")
        self.assertTrue(board.view.panel.disabled)
        self.assertIn("cancelled the game", self.channel.all_text())
        self.assertEqual(database.fetch_all("SELECT * FROM duel_stats"), [])
        self.assertNotIn("🏆 Tournament results", self.channel.titles())

    async def test_finished_game_panel_is_closed(self):
        self.channel.plan = {2: lambda v: PAPER, 3: lambda v: ROCK}
        task = self.start_tournament([self.alt, self.kim])
        board = await self.board()
        await self.finish(task)
        content, _, _ = await self.open_panel(board, self.host)
        self.assertEqual(content, "This game is over.")

    # ---------------------------------------------------------- duels too
    async def test_team_duel_scoreboard_and_mod_panel(self):
        cog = self.dc.Duels(None)
        lobby = self.dc.TeamLobbyView(self.alt, "rps", 2)    # a normal member hosts
        lobby.teams = {"A": [self.alt, self.kim], "B": [self.lee, FakeMember(5, "max")]}
        lobby.message = FakeMessage()
        lobby.start_now.set()
        self.channel.plan = {2: lambda v: ROCK, 3: lambda v: ROCK, 4: lambda v: SCISSORS}   # 5 never picks
        task = asyncio.get_running_loop().create_task(cog.run_team_game(self.channel, self.guild, lobby, 3, 5))
        board = await self.board()
        names = [f.name for f in board.embed().fields]
        self.assertTrue(any(name.startswith("🔵 Team A") for name in names))
        _, embed, _ = await self.open_panel(board, self.alt)      # the host
        self.assertEqual(embed.title, "🎛️ Host panel")
        content, _, _ = await self.open_panel(board, self.kim)    # a player who isn't host or mod
        self.assertEqual(content, "Only the host or a mod can use this.")
        await board.session.skip(self.mod)                          # a mod skips the waiting player
        await self.finish(task)
        self.assertTrue(board.message.embeds[0].title.startswith("🏁 Final scoreboard"))


if __name__ == "__main__":
    unittest.main()
