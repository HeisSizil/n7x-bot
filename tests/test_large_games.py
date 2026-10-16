"""
Tests for big lobbies and big games (up to 100 players): Discord limits, pings, parallel
bracket rounds, batched scoreboard updates and rate limits. Fake Discord objects only.

Run from the project folder:
    python -m unittest discover tests -v
"""

import asyncio
import os
import re
import secrets
import shutil
import tempfile
import time
import unittest
from types import SimpleNamespace as NS

import discord

import database
import game_panel
import tournament_engine as te
from tests.test_tournament_discord import FakeChannel, FakeMember, FakeMessage

BIG_ID = 10 ** 18          # real Discord ids are this long, so mentions are ~22 characters


def check_embed(test, embed):
    """Fail if an embed breaks any of Discord's limits."""
    if embed is None:
        return
    test.assertLessEqual(len(embed.title or ""), 256)
    test.assertLessEqual(len(embed.description or ""), 4096)
    test.assertLessEqual(len(embed.fields), 25)
    for field in embed.fields:
        test.assertLessEqual(len(field.name), 256)
        test.assertLessEqual(len(field.value), 1024)
    test.assertLessEqual(len(embed.footer.text or "") if embed.footer else 0, 2048)
    test.assertLessEqual(len(embed.author.name or "") if embed.author else 0, 256)
    test.assertLessEqual(len(embed), 6000)


class CheckedMessage(FakeMessage):
    def __init__(self, test, embed, view):
        super().__init__(embed, view)
        self.test = test

    async def edit(self, embed=None, **kwargs):
        check_embed(self.test, embed)
        await super().edit(embed=embed, **kwargs)


class LimitChannel(FakeChannel):
    """Checks every message: embed limits, 2000 characters, no @everyone, pings only for players who must act."""

    def __init__(self, tc, test):
        super().__init__(tc)
        self.test = test
        self.contents = []       # (content, mentioned ids)
        self.round_messages = []

    async def send(self, embed=None, view=None, allowed_mentions=None, content=None, **kwargs):
        check_embed(self.test, embed)
        self.test.assertIsNotNone(allowed_mentions)
        self.test.assertFalse(allowed_mentions.everyone)
        if content:
            self.test.assertLessEqual(len(content), 2000)
            self.test.assertNotIn("@everyone", content)
            self.test.assertNotIn("@here", content)
            mentioned = {int(i) for i in re.findall(r"<@(\d+)>", content)}
            self.contents.append((content, mentioned))
        message = await super().send(embed=embed, view=view, allowed_mentions=allowed_mentions, content=content)
        if isinstance(view, self.tc.RoundView):
            self.round_messages.append((embed, view, content))
            if content:   # only players of this round who still have to act
                self.test.assertTrue(self.contents[-1][1] <= set(view.humans))
        checked = CheckedMessage(self.test, embed, view)
        if isinstance(view, game_panel.PanelButton):
            self.board_message = checked
        return checked


def members(count, start=1):
    return [FakeMember(BIG_ID + n, f"player{n}") for n in range(start, start + count)]


class LargeGameTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_db = database.DB_FILE
        database.DB_FILE = os.path.join(self.tmp, "test.db")
        database.setup_database()

        import cogs.duels as dc
        import cogs.tournament as tc
        self.tc, self.dc = tc, dc
        tc.SEND_GAP = 0
        tc.BATCH_WINDOW = 0.01
        tc.ROUND_SECONDS = 1
        tc.RATE_LIMIT_PAUSE = 0
        game_panel.BOARD_MIN_INTERVAL = 0
        self.channel = LimitChannel(tc, self)
        self.guild = NS(id=1)
        self.host = FakeMember(1, "host", mod=True)

    def tearDown(self):
        database.DB_FILE = self.old_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def run_tournament(self, players, kind="rps", test_mode=False, max_players=100, host_starts=False):
        cog = self.tc.Tournament(None)
        lobby = self.tc.LobbyView(self.host, kind, max_players, int(time.time()) + 60, test_mode)
        lobby.players = list(players)
        lobby.message = FakeMessage()
        join_seconds = 0.01
        if host_starts:
            join_seconds = 60
            await lobby.start.callback(NS(user=self.host, response=NS(defer=self.channel.ignore)))
        await asyncio.wait_for(cog.run_tournament(NS(channel=self.channel, guild=self.guild), lobby,
                                                  join_seconds, test_mode), 120)
        return self.channel.sent[-1][0]

    # ------------------------------------------------------------- lobby
    async def test_lobby_hits_the_cap_of_100(self):
        lobby = self.tc.LobbyView(self.host, "rps", 100, int(time.time()) + 60)
        replies = []

        async def send_message(text=None, **kwargs):
            replies.append(text)
        response = NS(edit_message=self.channel.ignore, send_message=send_message)
        for member in members(100):
            await lobby.join.callback(NS(user=member, response=response))
        self.assertEqual(replies, [])
        extra = FakeMember(BIG_ID + 999, "late")
        await lobby.join.callback(NS(user=extra, response=response))
        self.assertEqual(replies, ["Lobby is full."])
        embed = lobby.embed()
        check_embed(self, embed)
        field = next(f for f in embed.fields if f.name.startswith("👥 Players"))
        self.assertEqual(field.name, "👥 Players (FULL 100/100)")
        self.assertIn("…and 80 more", field.value)
        self.assertEqual(field.value.count("<@"), 20)
        # Leaving frees a spot.
        await lobby.leave.callback(NS(user=lobby.players[0], response=response))
        await lobby.join.callback(NS(user=extra, response=response))
        self.assertEqual(len(lobby.players), 100)
        self.assertEqual(replies, ["Lobby is full."])

    async def test_host_starts_with_100_players(self):
        result = await self.run_tournament([], test_mode=True, max_players=100, host_starts=True)
        self.assertEqual(result.title, "🏆 Tournament results")
        self.assertIn("100 players", result.description)

    async def test_tournament_and_free_for_all_accept_2_to_100(self):
        for command in (self.tc.Tournament.tournament, self.dc.Duels.duel):
            option = next(p for p in command.parameters if p.name == "max_players")
            self.assertEqual((option.min_value, option.max_value, option.default), (2, 100, 8))

    # ----------------------------------------------- 20 / 64 / 100 players
    async def test_big_tournaments_all_games_and_odd_sizes(self):
        for kind in ("rps", "dice", "trivia"):
            for size in (20, 21, 64, 63, 100, 99):
                self.channel.sent.clear()
                result = await self.run_tournament([], kind=kind, test_mode=True, max_players=size)
                self.assertEqual(result.title, "🏆 Tournament results", (kind, size))
                self.assertIn(f"{size} players", result.description)
                for medal in ("🥇", "🥈", "🥉"):
                    self.assertIn(medal, result.description)
                titles = self.channel.titles()
                self.assertNotIn("👥 Group round", titles)            # groups are only for exactly 3
                self.assertIn(f"🗂️ Bracket of {te.bracket_size(size)}", titles)
                byes = te.bracket_size(size) - size
                bracket = next(e for e, _ in self.channel.sent if e and e.title.startswith("🗂️ Bracket"))
                if byes:
                    self.assertIn(f"**Free pass** to the next round ({byes})", bracket.description)
                # Results of a round are combined, so even 100 players need few messages.
                self.assertLess(len(self.channel.sent), 40, (kind, size, len(self.channel.sent)))

    async def test_64_players_with_humans_one_message_per_round(self):
        humans = members(20)
        self.channel.plan = {m.id: (lambda v: secrets.randbelow(3)) for m in humans}
        bots_needed = 64 - len(humans)
        result = await self.run_tournament(humans, test_mode=True, max_players=64)
        self.assertEqual(result.title, "🏆 Tournament results")
        self.assertIn("64 players", result.description)
        self.assertGreater(bots_needed, 0)
        first_round = [embed for embed, view, _ in self.channel.round_messages if "Round of 64" in embed.title]
        self.assertGreaterEqual(len(first_round), 1)
        self.assertIn("matches", first_round[0].title)       # many matches, one combined message
        for embed, view, content in self.channel.round_messages:
            mentioned = {int(i) for i in re.findall(r"<@(\d+)>", content or "")}
            self.assertEqual(mentioned, set(view.humans))      # everyone who must act, nobody else

    async def test_100_humans_ping_batches_fit_2000_characters(self):
        players = members(100)
        session = self.tc.Session(self.channel, "rps", "🏆 N7X Tournament")
        pairs = [(players[2 * n], players[2 * n + 1]) for n in range(50)]
        self.tc.ROUND_SECONDS = 0.2                          # nobody presses: everyone times out
        started = time.monotonic()
        results = await asyncio.wait_for(asyncio.gather(*(session.ask([a, b], "rps", f"Round of 100 {n}", 1)
                                                          for n, (a, b) in enumerate(pairs, start=1))), 10)
        self.assertLess(time.monotonic() - started, 2)       # 50 matches in parallel, one time limit
        self.assertEqual(len(results), 50)
        self.assertEqual(len(self.channel.round_messages), 1)
        embed, view, _ = self.channel.round_messages[0]
        self.assertEqual(embed.title, "⚔️ Round of 100 (50 matches)")
        self.assertIn("…and 30 more", embed.description)
        pinged = [ids for content, ids in self.channel.contents]
        self.assertGreaterEqual(len(pinged), 2)              # 100 long mentions don't fit in one message
        all_pinged = set().union(*pinged)
        self.assertEqual(all_pinged, {p.id for p in players})
        self.assertEqual(sum(len(ids) for ids in pinged), 100)   # each player pinged exactly once

    def test_mention_chunks(self):
        chunks = game_panel.mention_chunks(members(200))
        self.assertTrue(all(len(chunk) <= 2000 for chunk in chunks))
        self.assertEqual(sum(chunk.count("<@") for chunk in chunks), 200)

    async def test_bracket_round_runs_in_parallel(self):
        # 8 humans: in every first-round match one player never presses, so each match takes the full
        # time limit. Played one by one that's 4 x limit; in parallel it's 1 x limit.
        players = members(8)
        self.tc.ROUND_SECONDS = 0.6
        self.channel.plan = {}
        for index, player in enumerate(players):
            if index % 2 == 0:
                self.channel.plan[player.id] = lambda v: secrets.randbelow(3)
        started = time.monotonic()
        original = te.draw_first_round
        te.draw_first_round = lambda ps: ([(ps[i], ps[i + 1]) for i in range(0, len(ps), 2)], [])
        try:
            result = await self.run_tournament(players, max_players=8)
        finally:
            te.draw_first_round = original
        elapsed = time.monotonic() - started
        self.assertEqual(result.title, "🏆 Tournament results")
        self.assertLess(elapsed, 2.0)
        quarter = [e for e, _, _ in self.channel.round_messages if e.title.startswith("⚔️ Quarter-final")]
        self.assertEqual(quarter[0].title, "⚔️ Quarter-final (4 matches)")

    async def test_free_for_all_100_players(self):
        cog = self.dc.Duels(None)
        lobby = self.tc.LobbyView(self.host, "dice", 100, int(time.time()) + 60, True, free_for_all=True)
        lobby.message = FakeMessage()
        await asyncio.wait_for(cog.run_free_for_all(self.channel, self.guild, lobby, 0.01), 60)
        result = self.channel.sent[-1][0]
        self.assertEqual(result.title, "🏆 Free for all results")
        self.assertIn("100 players", result.description)

    # -------------------------------------------- scoreboard / host panel
    async def test_scoreboard_top_10_totals_and_my_position(self):
        players = members(100)
        session = self.tc.Session(self.channel, "rps", "🏆 N7X Tournament")
        await session.start_board(self.host, players, "Big test")
        board = session.board
        for rank, player in enumerate(players):
            board.score[player] = 100 - rank
        board.set_status(players[50:], "out")
        board.set_status(players[:5], "waiting")
        embed = board.embed()
        check_embed(self, embed)
        top = embed.fields[0]
        self.assertEqual(top.name, "Top 10 of 100")
        self.assertEqual(top.value.count("\n"), 9)
        self.assertIn("👑", top.value)
        totals = embed.fields[1].value
        self.assertIn("🟢 in **45**", totals)
        self.assertIn("❌ out **50**", totals)
        self.assertIn("⏳ waiting **5**", totals)

        replies = []

        async def send_message(text=None, **kwargs):
            replies.append((text, kwargs.get("ephemeral")))
        await board.view.my_position.callback(NS(user=players[41], response=NS(send_message=send_message)))
        self.assertTrue(replies[-1][0].startswith("You: #42 of 100"))
        self.assertTrue(replies[-1][1])
        await board.view.my_position.callback(NS(user=self.host, response=NS(send_message=send_message)))
        self.assertEqual(replies[-1][0], "You're not playing in this game.")

    async def test_host_panel_big_round(self):
        players = members(100)
        session = self.tc.Session(self.channel, "rps", "🏆 N7X Tournament")
        await session.start_board(self.host, players, "Big test")
        view = self.tc.RoundView(players, "rps", None, {p: "rock" for p in players[:30]})
        session.current_view = view
        embed = session.board.panel_embed()
        check_embed(self, embed)
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(fields["✅ Answered (30)"], "30 players")
        pending = fields["⏳ Pending (70)"]
        self.assertEqual(pending.count("\n"), 20)
        self.assertIn("…and 50 more", pending)
        self.assertNotIn("rock", str(embed.to_dict()).lower())
        skipped = await session.skip(self.host)               # Skip waiting still works
        self.assertEqual(skipped, 70)
        self.assertTrue(view.all_done.is_set())
        self.assertLessEqual(len(view.waiting_line()), 1024)

    async def test_scoreboard_edits_are_batched(self):
        game_panel.BOARD_MIN_INTERVAL = 0.3
        try:
            session = self.tc.Session(self.channel, "rps", "🏆 N7X Tournament")
            await session.start_board(self.host, members(10), "Batch test")
            board = session.board
            for _ in range(30):
                await board.refresh()
            self.assertEqual(board.edits, 0)        # just posted: the update waits
            await asyncio.sleep(0.45)
            self.assertEqual(board.edits, 1)        # 30 requests -> one edit
            await board.finish("done")
            self.assertEqual(board.edits, 2)
        finally:
            game_panel.BOARD_MIN_INTERVAL = 0

    async def test_rate_limited_send_slows_down_and_keeps_going(self):
        calls = []
        real_send = self.channel.send

        async def flaky_send(**kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise discord.HTTPException(NS(status=429, reason="Too Many Requests"), "rate limited")
            return await real_send(**kwargs)
        self.channel.send = flaky_send
        session = self.tc.Session(self.channel, "rps", "🏆 N7X Tournament")
        with self.assertLogs("n7x.tournament", level="WARNING") as logs:
            message = await session.send(self.tc.t_embed("Hello"))
        self.assertIsNotNone(message)
        self.assertEqual(len(calls), 2)
        self.assertIn("Rate limited", logs.output[0])

    def test_fit_embed_trims_everything(self):
        embed = discord.Embed(title="t" * 400, description="d" * 5000)
        for n in range(30):
            embed.add_field(name="n" * 300, value="v" * 2000)
        embed.set_footer(text="f" * 3000)
        check_embed(self, game_panel.fit_embed(embed))


class TeamLobbyLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_team_lobbies_refuse_extra_joins_and_start_only_when_equal(self):
        import cogs.duels as dc
        for size in (2, 3, 4):
            lobby = dc.TeamLobbyView(FakeMember(1, "host"), "rps", size)
            replies = []

            async def send_message(text=None, **kwargs):
                replies.append(text)
            response = NS(send_message=send_message, edit_message=FakeChannel.ignore, defer=FakeChannel.ignore)
            people = members(2 * size + 1)
            for index, member in enumerate(people[:2 * size]):
                await lobby.join(NS(user=member, response=response), "A" if index % 2 == 0 else "B")
            self.assertTrue(lobby.is_full())
            self.assertEqual(replies, [])
            await lobby.join(NS(user=people[-1], response=response), "A")
            self.assertEqual(replies, ["Lobby is full."])
            check_embed(self, lobby.embed())
            self.assertIn("FULL", lobby.embed().description)

            # Equal teams from 1 each up to the mode size can start; unequal teams can't.
            for count in range(1, size + 1):
                lobby.teams = {"A": people[:count], "B": people[size:size + count]}
                self.assertIsNone(lobby.start_problem())
            lobby.teams = {"A": people[:size], "B": people[size:size + size - 1]}
            self.assertIn("Team B needs 1 more", lobby.start_problem())


class EngineShapeTests(unittest.TestCase):
    def test_group_round_only_for_exactly_3(self):
        for count in range(2, 12):
            events = []

            async def collect(players, kind, label, attempt):
                return {p: p for p in players}       # higher number rolls higher

            async def announce(event, **details):
                events.append(event)
            engine = te.Engine("dice", collect, announce)
            asyncio.run(engine.run_tournament(list(range(1, count + 1))))
            self.assertEqual("group_start" in events, count == 3, count)
            self.assertEqual("bracket_start" in events, count >= 4, count)

    def test_big_dice_and_trivia_rounds_never_stall(self):
        async def collect(players, kind, label, attempt):
            return {p: 3 if kind == "dice" else (False, 1.0) for p in players}   # always tied / always wrong

        async def announce(event, **details):
            pass
        for kind in ("dice", "trivia"):
            engine = te.Engine(kind, collect, announce)
            advance, out = asyncio.run(engine.play_round(list(range(50)), "Big round"))
            self.assertEqual(len(advance) + len(out), 50)
            self.assertEqual(len(out), 1)            # random pick after 5 replays


if __name__ == "__main__":
    unittest.main()
