"""
Tests for /tournament and /duel with fake Discord objects (no internet, no real server).
Uses a temporary database, so your real n7x.db is never touched.

Run from the project folder:
    python -m unittest discover tests -v
"""

import asyncio
import os
import tempfile
import time
import unittest
from types import SimpleNamespace as NS

import database


class FakeMember:
    """Looks enough like a discord.Member for the tournament code."""

    def __init__(self, user_id, name, mod=False):
        self.id = user_id
        self.display_name = name
        self.mention = f"<@{user_id}>"
        self.bot = False
        self.guild_permissions = NS(moderate_members=mod, manage_messages=mod)
        self.display_avatar = NS(url="https://example.com/avatar.png")

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return getattr(other, "id", None) == self.id


class FakeMessage:
    def __init__(self, embed=None, view=None):
        self.embeds = [embed] if embed else []
        self.view = view

    async def edit(self, embed=None, **kwargs):
        if embed:
            self.embeds = [embed]


class FakeChannel:
    """Records every message. When a round starts, presses buttons for players listed in `plan`."""

    id = 777
    name = "games"

    def __init__(self, tc):
        self.tc = tc
        self.sent = []          # (embed, pinged user ids)
        self.plan = {}          # member id -> function(view) -> button index (missing = never presses)

    async def send(self, embed=None, view=None, allowed_mentions=None, content=None, **kwargs):
        pinged = [u.id for u in (allowed_mentions.users or [])] if allowed_mentions and isinstance(
            allowed_mentions.users, list) else []
        self.sent.append((embed, pinged))
        if isinstance(view, self.tc.RoundView):
            for player in list(view.humans.values()):
                choose = self.plan.get(player.id)
                if choose:
                    asyncio.get_running_loop().create_task(self.press(view, player, choose(view)))
        return FakeMessage(embed, view)

    async def press(self, view, player, index):
        await asyncio.sleep(0.01 + (player.id % 1000) / 1000)   # lower ids press first (keeps tests predictable)
        interaction = NS(user=player, response=NS(send_message=self.ignore))
        if await view.interaction_check(interaction):
            await view.children[index].callback(interaction)

    @staticmethod
    async def ignore(*args, **kwargs):
        pass

    def titles(self):
        return [embed.title for embed, _ in self.sent if embed]

    def all_text(self):
        parts = []
        for embed, _ in self.sent:
            if embed:
                parts.append(f"{embed.title} {embed.description or ''} " + " ".join(f.value for f in embed.fields))
        return "\n".join(parts)


ROCK, PAPER, SCISSORS = 0, 1, 2


class TournamentDiscordTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_db = database.DB_FILE
        database.DB_FILE = os.path.join(self.tmp, "test.db")
        database.setup_database()

        import cogs.tournament as tc
        self.tc = tc
        tc.SEND_GAP = 0
        tc.BATCH_WINDOW = 0.01
        import game_panel
        game_panel.BOARD_MIN_INTERVAL = 0
        tc.ROUND_SECONDS = 2
        self.channel = FakeChannel(tc)
        self.guild = NS(id=1)
        self.host = FakeMember(1, "host", mod=True)
        self.alt = FakeMember(2, "alt")
        self.kim = FakeMember(3, "kim")
        self.lee = FakeMember(4, "lee")

    def tearDown(self):
        database.DB_FILE = self.old_db

    async def run_lobby(self, players, test_mode=False, max_players=8, kind="rps", host_starts=False):
        """Run a tournament as if `players` pressed Join and the join timer ended (or the host pressed Start now)."""
        cog = self.tc.Tournament(None)
        lobby = self.tc.LobbyView(self.host, kind, max_players, int(time.time()) + 60, test_mode)
        lobby.players = list(players)
        lobby.message = FakeMessage()
        join_seconds = 0.01
        if host_starts:
            join_seconds = 60   # would wait a minute, but the host presses Start now
            await lobby.start.callback(NS(user=self.host, response=NS(defer=self.channel.ignore)))
            self.assertTrue(lobby.start_now.is_set())
        await asyncio.wait_for(cog.run_tournament(NS(channel=self.channel, guild=self.guild), lobby,
                                                  join_seconds, test_mode), 30)

    def podium_row(self, member):
        stats = database.get_duel_stats(1, member.id)
        return stats["gold"], stats["silver"], stats["bronze"], stats["tournaments_played"]

    # ------------------------------------------------------------------ lobby
    async def test_host_is_not_added_automatically(self):
        lobby = self.tc.LobbyView(self.host, "rps", 8, int(time.time()) + 60)
        self.assertEqual(lobby.players, [])
        embed = lobby.embed()
        fields = {f.name: f.value for f in embed.fields}
        self.assertIn("Hosted by <@1>", fields["🎙️ Host"])
        players_field = next(v for name, v in fields.items() if name.startswith("👥 Players"))
        self.assertNotIn("<@1>", players_field)

    async def test_host_can_join_and_leave_like_everyone(self):
        lobby = self.tc.LobbyView(self.host, "rps", 8, int(time.time()) + 60)
        response = NS(edit_message=self.channel.ignore, send_message=self.channel.ignore)
        await lobby.join.callback(NS(user=self.host, response=response))
        self.assertEqual(lobby.players, [self.host])
        await lobby.leave.callback(NS(user=self.host, response=response))
        self.assertEqual(lobby.players, [])

    async def test_only_host_can_start_now(self):
        lobby = self.tc.LobbyView(self.host, "rps", 8, int(time.time()) + 60)
        lobby.players = [self.alt, self.kim]
        replies = []

        async def reply(text, **kwargs):
            replies.append(text)
        await lobby.start.callback(NS(user=self.alt, response=NS(send_message=reply)))
        self.assertFalse(lobby.start_now.is_set())
        self.assertIn("Only the host", replies[0])
        await lobby.start.callback(NS(user=self.host, response=NS(defer=self.channel.ignore)))
        self.assertTrue(lobby.start_now.is_set())

    # ------------------------------------------------- hard cap / minimum 2
    def lobby_response(self, replies):
        async def send_message(text=None, **kwargs):
            replies.append((text, kwargs.get("ephemeral")))
        return NS(edit_message=self.channel.ignore, send_message=send_message)

    def players_field(self, lobby):
        return next(f.name for f in lobby.embed().fields if f.name.startswith("👥 Players"))

    async def test_full_lobby_refuses_join_and_leave_frees_a_spot(self):
        lobby = self.tc.LobbyView(self.host, "rps", 2, int(time.time()) + 60)
        replies = []
        for member in (self.alt, self.kim):
            await lobby.join.callback(NS(user=member, response=self.lobby_response(replies)))
        self.assertEqual(self.players_field(lobby), "👥 Players (FULL 2/2)")
        await lobby.join.callback(NS(user=self.lee, response=self.lobby_response(replies)))
        self.assertEqual(replies, [("Lobby is full.", True)])
        self.assertEqual(lobby.players, [self.alt, self.kim])

        await lobby.leave.callback(NS(user=self.kim, response=self.lobby_response(replies)))
        self.assertEqual(self.players_field(lobby), "👥 Players (1/2)")
        await lobby.join.callback(NS(user=self.lee, response=self.lobby_response(replies)))
        self.assertEqual(lobby.players, [self.alt, self.lee])
        self.assertEqual(len(replies), 1)

    async def test_start_now_needs_two_players(self):
        lobby = self.tc.LobbyView(self.host, "rps", 8, int(time.time()) + 60)
        lobby.players = [self.alt]
        replies = []
        await lobby.start.callback(NS(user=self.host, response=self.lobby_response(replies)))
        self.assertFalse(lobby.start_now.is_set())
        self.assertIn("at least 2", replies[0][0])
        self.assertTrue(replies[0][1])

    async def test_host_starts_with_two_players(self):
        self.channel.plan = {2: lambda v: ROCK, 3: lambda v: SCISSORS}
        await self.run_lobby([self.alt, self.kim], host_starts=True)
        self.assertEqual(self.podium_row(self.alt), (1, 0, 0, 1))
        self.assertEqual(self.podium_row(self.kim), (0, 1, 0, 1))

    async def test_host_starts_with_three_players(self):
        self.channel.plan = {2: lambda v: SCISSORS if len(v.humans) == 3 else ROCK,
                             3: lambda v: SCISSORS, 4: lambda v: PAPER}
        await self.run_lobby([self.alt, self.kim, self.lee], host_starts=True)
        self.assertEqual(self.channel.titles()[-1], "🏆 Tournament results")
        self.assertEqual(self.podium_row(self.lee), (0, 0, 1, 1))

    async def test_host_starts_with_max_players(self):
        await self.run_lobby([], test_mode=True, max_players=16, host_starts=True)
        self.assertEqual(self.channel.titles()[-1], "🏆 Tournament results")
        self.assertIn("16 players", self.channel.all_text())

    async def test_timer_end_with_two_players_starts(self):
        self.channel.plan = {2: lambda v: PAPER, 3: lambda v: ROCK}
        await self.run_lobby([self.alt, self.kim])
        self.assertEqual(self.channel.titles()[-1], "🏆 Tournament results")
        self.assertEqual(self.podium_row(self.alt), (1, 0, 0, 1))

    async def check_two_player_card(self, kind, plan, winner, loser):
        self.channel.plan = plan
        await self.run_lobby([self.alt, self.kim], kind=kind)
        results, _ = self.channel.sent[-1]
        self.assertEqual(results.title, "🏆 Tournament results")
        self.assertIn("🥇", results.description)
        self.assertIn("🥈", results.description)
        self.assertNotIn("🥉", results.description)
        self.assertIn(f"🥇 {winner.mention} - ✨ +50 XP", results.description)
        self.assertIn(f"🥈 {loser.mention} - ✨ +30 XP", results.description)
        self.assertEqual(self.podium_row(winner), (1, 0, 0, 1))
        self.assertEqual(self.podium_row(loser), (0, 1, 0, 1))
        self.assertEqual(database.fetch_one("SELECT SUM(bronze) AS b FROM duel_stats")["b"], 0)
        # Only one round title for a real match (no 3rd place match, no group round).
        self.assertFalse(any("3rd" in title or "Group" in title for title in self.channel.titles()))

    async def test_two_player_card_rps(self):
        await self.check_two_player_card("rps", {2: lambda v: PAPER, 3: lambda v: ROCK}, self.alt, self.kim)

    async def test_two_player_card_dice(self):
        rolls = iter([2, 5])   # alt rolls 2, kim rolls 5
        real = self.tc.secrets.randbelow
        self.tc.secrets.randbelow = lambda n: next(rolls) - 1 if n == 6 else real(n)
        try:
            await self.check_two_player_card("dice", {2: lambda v: 0, 3: lambda v: 0}, self.kim, self.alt)
        finally:
            self.tc.secrets.randbelow = real

    async def test_two_player_card_trivia(self):
        right = lambda v: v.question["answer"]
        wrong = lambda v: (v.question["answer"] + 1) % 4
        await self.check_two_player_card("trivia", {2: wrong, 3: right}, self.kim, self.alt)

    # ---------------------------------------------------- host not playing
    async def test_host_not_joined_and_three_other_players(self):
        # alt & kim scissors, lee paper -> lee 3rd; final: alt rock beats kim scissors.
        self.channel.plan = {
            2: lambda v: SCISSORS if len(v.humans) == 3 else ROCK,
            3: lambda v: SCISSORS,
            4: lambda v: PAPER,
        }
        await self.run_lobby([self.alt, self.kim, self.lee])

        self.assertEqual(self.channel.titles()[-1], "🏆 Tournament results")
        self.assertEqual(self.podium_row(self.alt), (1, 0, 0, 1))
        self.assertEqual(self.podium_row(self.kim), (0, 1, 0, 1))
        self.assertEqual(self.podium_row(self.lee), (0, 0, 1, 1))
        # The host never played, was never pinged, and isn't in any round or result.
        self.assertEqual(self.podium_row(self.host), (0, 0, 0, 0))
        for _, pinged in self.channel.sent:
            self.assertNotIn(self.host.id, pinged)
        self.assertNotIn("<@1>", self.channel.all_text())

    async def test_only_one_player_cancels(self):
        await self.run_lobby([self.alt])
        embed, _ = self.channel.sent[-1]
        self.assertIn("cancelled", embed.title)
        self.assertIn("**1** player(s) joined", embed.description)
        self.assertIn("at least **2** are needed", embed.description)
        self.assertIn("only counts as a player if they pressed **Join**", embed.description)
        self.assertEqual(self.podium_row(self.alt), (0, 0, 0, 0))

    async def test_host_joined_as_player(self):
        # host & alt scissors, kim paper -> kim 3rd; final: host rock beats alt scissors.
        self.channel.plan = {
            1: lambda v: SCISSORS if len(v.humans) == 3 else ROCK,
            2: lambda v: SCISSORS,
            3: lambda v: PAPER,
        }
        await self.run_lobby([self.host, self.alt, self.kim])
        self.assertEqual(self.podium_row(self.host), (1, 0, 0, 1))
        self.assertEqual(database.get_game_profile(1, self.host.id, self.tc.today())["game_xp_today"], 50)
        self.assertTrue(any(self.host.id in pinged for _, pinged in self.channel.sent))

    async def test_test_mode_with_host_not_playing_is_bots_only(self):
        await self.run_lobby([], test_mode=True, max_players=4)
        self.assertEqual(self.channel.titles()[-1], "🏆 Tournament results")
        self.assertNotIn("<@1>", self.channel.all_text())
        self.assertEqual(database.fetch_all("SELECT user_id FROM duel_stats"), [])

    # ------------------------------------------------------ mods only
    async def test_non_mod_cannot_start_a_tournament(self):
        cog = self.tc.Tournament(None)
        replies = []

        async def reply(text=None, **kwargs):
            replies.append((text, kwargs.get("ephemeral")))
        interaction = NS(user=self.alt, channel=self.channel, response=NS(send_message=reply))
        await cog.tournament.callback(cog, interaction, NS(value="rps"), 8, 60, False)
        self.assertEqual(replies, [("Only mods can start tournaments.", True)])
        self.assertEqual(self.channel.sent, [])

    async def test_mod_can_start_a_tournament(self):
        cog = self.tc.Tournament(None)
        started = []
        cog.run_in_background = lambda coro: (started.append(coro), coro.close())
        sent = []

        async def send_message(embed=None, **kwargs):
            sent.append(embed)

        async def original_response():
            return FakeMessage()
        interaction = NS(user=self.host, channel=self.channel,
                         response=NS(send_message=send_message), original_response=original_response)
        await cog.tournament.callback(cog, interaction, NS(value="rps"), 8, 60, True)
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(started), 1)

    # ------------------------------------------------------------- duels
    async def test_duel_gives_no_xp(self):
        import cogs.duels
        cog = cogs.duels.Duels(None)
        invite = self.tc.DuelInvite(self.host, self.alt)
        self.channel.plan = {1: lambda v: PAPER, 2: lambda v: ROCK}

        async def edit_original_response(**kwargs):
            pass
        task = asyncio.get_running_loop().create_task(
            cog.run_duel(NS(channel=self.channel, guild=self.guild, edit_original_response=edit_original_response),
                         invite, "rps"))
        await asyncio.sleep(0.02)
        await invite.accept.callback(NS(user=self.alt, response=NS(edit_message=self.channel.ignore)))
        await task

        self.assertEqual(database.get_duel_stats(1, self.host.id)["duel_wins"], 1)
        self.assertEqual(database.get_duel_stats(1, self.alt.id)["duel_losses"], 1)
        for member in (self.host, self.alt):
            profile = database.get_game_profile(1, member.id, self.tc.today())
            self.assertEqual((profile["total_xp"], profile["game_xp"], profile["game_xp_today"]), (0, 0, 0))
        self.assertNotIn("XP", self.channel.all_text())

    async def test_duel_invite_mentions_no_xp(self):
        import cogs.duels
        cog = cogs.duels.Duels(None)
        cog.run_in_background = lambda coro, channel: coro.close()
        shown = []

        async def send_message(content=None, embed=None, **kwargs):
            shown.append(embed)
        interaction = NS(user=self.host, channel=self.channel, response=NS(send_message=send_message))
        await cog.duel.callback(cog, interaction, NS(value="rps"), None, self.alt)
        self.assertNotIn("XP", shown[0].description)


if __name__ == "__main__":
    unittest.main()
