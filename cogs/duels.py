"""
cogs/duels.py - /duel (1v1, team modes, free for all) and /duelstats.

Anyone can host a duel. Duels NEVER give XP: just bragging rights.
- 1v1: challenge a member; they accept or decline (same rules as before).
- 2v2 / 3v3 / 4v4: a team lobby with Join Team A / Join Team B / Leave / Start.
  Teams must be the same size (at least 1 each) to start. The lobby closes after 2 minutes.
  Scoring rules: team_engine.py.
- Free for all (2-100 players): a lobby like /tournament, played with the tournament rules
  and podium. Starts when the timer ends or the host presses Start now (at least 2).
- The host only plays if they join. One game per player at a time.
- Test mode (mods only): empty spots are filled with 🤖 bots. Bots never get stats.
"""

import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

import database
import team_engine
import tournament_engine as te
import trivia_bank
from cogs.games import COLOR_DRAW, COLOR_LOSE, COLOR_WIN, DICE_FACES
from cogs.tournament import (COLOR_GOLD, FREE_FOR_ALL_MIN, GAME_CHOICES, GAMES, MAX_PLAYERS_LIMIT, ROUND_SECONDS,
                             BotPlayer, DuelInvite,
                             LobbyView, Session, is_bot, is_mod, podium_text, t_embed)
from game_panel import fit_embed

log = logging.getLogger("n7x.duels")

# --- Settings you can tweak ---
TEAM_LOBBY_SECONDS = 120      # a team lobby that doesn't start in time is closed
FREE_FOR_ALL_SECONDS = 60     # join time for free for all
FREE_FOR_ALL_MAX = MAX_PLAYERS_LIMIT   # 100
DICE_FRAME_DELAY = 0.6        # seconds between team dice animation frames
HEADER = "⚔️ N7X Duel"

MODES = {"1v1": 1, "2v2": 2, "3v3": 3, "4v4": 4, "ffa": 0}
MODE_CHOICES = [app_commands.Choice(name=name, value=key) for key, name in
                [("1v1", "1v1"), ("2v2", "2v2"), ("3v3", "3v3"), ("4v4", "4v4"), ("ffa", "Free for all")]]
NO_PING = discord.AllowedMentions.none()


def bots(count, start=1):
    return [BotPlayer(id=-number, display_name=f"🤖 Bot {number}") for number in range(start, start + count)]


class TeamLobbyView(discord.ui.View):
    """Join Team A / Join Team B / Leave / Start. Anyone can join; only the host starts."""

    def __init__(self, host, kind, size, test_mode=False, on_join=None, on_leave=None, closes_at=None):
        super().__init__(timeout=None)
        self.host = host
        self.kind = kind
        self.size = size
        self.test_mode = test_mode
        self.on_join = on_join
        self.on_leave = on_leave
        self.closes_at = closes_at or int(time.time()) + TEAM_LOBBY_SECONDS
        self.teams = {"A": [], "B": []}
        self.start_now = asyncio.Event()
        self.message = None

    def team_of(self, member):
        for name, members in self.teams.items():
            if any(p.id == member.id for p in members):
                return name
        return None

    def members(self):
        return self.teams["A"] + self.teams["B"]

    def embed(self, status=None):
        if not status and self.is_full():
            status = f"✅ Both teams are FULL. Host: press **Start**! Lobby closes <t:{self.closes_at}:R>."
        status = status or (f"⏱️ The host presses **Start** when teams are even. "
                            f"Lobby closes <t:{self.closes_at}:R>.")
        embed = t_embed(f"👥 {GAMES[self.kind]} {self.size}v{self.size}",
                        f"{status}\n\nNo XP: just bragging rights. 😎", header=HEADER)
        for name in ("A", "B"):
            members = self.teams[name]
            listing = "\n".join(p.mention for p in members) or "_empty_"
            full = "FULL " if len(members) >= self.size else ""
            embed.add_field(name=f"{'🔵' if name == 'A' else '🔴'} Team {name} ({full}{len(members)}/{self.size})",
                            value=listing)
        embed.add_field(name="🎙️ Host", value=f"{self.host.mention}\n_(plays only if they join a team)_",
                        inline=False)
        if self.test_mode:
            embed.add_field(name="🧪 Test mode", value="Empty spots will be filled with 🤖 bots (no stats).")
        return fit_embed(embed)

    def is_full(self):
        return all(len(members) >= self.size for members in self.teams.values())

    async def refresh(self, interaction):
        await interaction.response.edit_message(embed=self.embed(), view=self, allowed_mentions=NO_PING)

    async def join(self, interaction, name):
        user = interaction.user
        current = self.team_of(user)
        if current == name:
            await interaction.response.send_message(f"You're already in Team {name}.", ephemeral=True)
            return
        if current is None and self.is_full():
            await interaction.response.send_message("Lobby is full.", ephemeral=True)
            return
        if len(self.teams[name]) >= self.size:
            await interaction.response.send_message(f"Team {name} is full.", ephemeral=True)
            return
        if current is None and self.on_join and (problem := self.on_join(user)):
            await interaction.response.send_message(problem, ephemeral=True)
            return
        if current:   # switching teams
            self.teams[current] = [p for p in self.teams[current] if p.id != user.id]
        self.teams[name].append(user)
        await self.refresh(interaction)

    @discord.ui.button(label="Join Team A", emoji="🔵", style=discord.ButtonStyle.primary)
    async def join_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.join(interaction, "A")

    @discord.ui.button(label="Join Team B", emoji="🔴", style=discord.ButtonStyle.danger)
    async def join_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.join(interaction, "B")

    @discord.ui.button(label="Leave", emoji="🚪", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = self.team_of(interaction.user)
        if current is None:
            await interaction.response.send_message("You haven't joined a team.", ephemeral=True)
            return
        self.teams[current] = [p for p in self.teams[current] if p.id != interaction.user.id]
        if self.on_leave:
            self.on_leave(interaction.user)
        await self.refresh(interaction)

    def start_problem(self):
        """Why the game can't start yet, or None."""
        if self.test_mode:
            return None
        a, b = len(self.teams["A"]), len(self.teams["B"])
        if a == 0 and b == 0:
            return "Both teams need players."
        if a < b:
            return f"Teams must be even: Team A needs {b - a} more player(s)."
        if b < a:
            return f"Teams must be even: Team B needs {a - b} more player(s)."
        return None

    @discord.ui.button(label="Start", emoji="▶️", style=discord.ButtonStyle.success)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.host.id:
            await interaction.response.send_message("Only the host can start.", ephemeral=True)
            return
        problem = self.start_problem()
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return
        await interaction.response.defer()
        self.start_now.set()

    def close(self):
        self.stop()
        for item in self.children:
            item.disabled = True


class TeamSession(Session):
    """A Session that also shows the team events (pairs, team scores, ties)."""

    def __init__(self, channel, kind, quiz_questions, team_a, team_b):
        super().__init__(channel, kind, HEADER, quiz_questions, points_mode=True)
        self.team_a = team_a
        self.team_b = team_b

    def team_name(self, player):
        return "🔵 A" if player in self.team_a else "🔴 B"

    def update_board(self, event, details):
        board = self.board
        if not board:
            return
        if event in ("round_result", "random_result", "removed_result"):   # an RPS pair was decided
            for player in details["advance"]:
                board.score[player] += 1
        elif event == "team_points" and self.kind == "dice":
            board.score.update(details["points"])                          # the latest roll counts
        elif event == "team_pairs":
            board.bracket = f"⚔️ {len(details['pairs'])} pair(s)"

    async def _announce(self, event, **details):
        if event == "team_pairs":
            lines = [f"⚔️ {a.mention} (🔵 A) vs {b.mention} (🔴 B)" for a, b in details["pairs"]]
            await self.send(t_embed("🎲 Random pairs", "\n".join(lines) + "\n\nMost pair wins takes it!",
                                    header=HEADER))
        elif event == "team_score":
            await self.send(t_embed("📊 Team score", f"🔵 Team A **{details['score_a']}** - "
                                    f"**{details['score_b']}** Team B 🔴", header=HEADER))
        elif event == "sudden_death":
            a, b = details["players"]
            await self.send(t_embed("⚡ Sudden death!", f"It's a tie! {a.mention} (🔵 A) vs {b.mention} (🔴 B) "
                                    "decides it.", COLOR_DRAW, header=HEADER))
        elif event == "team_points":
            await self.show_points(details)
        elif event == "team_tie":
            what = "re-roll" if self.kind == "dice" else "⚡ sudden-death question"
            await self.send(t_embed("🔁 Tied!", f"Equal totals, so it's a {what}.", COLOR_DRAW, header=HEADER))
        elif event == "team_random":
            await self.send(t_embed("🎲 Fair random pick", f"Still tied after {te.MAX_REPLAYS} tries, so a secure "
                                    f"random draw picks Team {details['winner']}.", COLOR_DRAW, header=HEADER))
        else:
            await super()._announce(event, **details)

    async def show_points(self, details):
        points = details["points"]

        def board(final):
            lines = []
            for name, team in (("🔵 Team A", self.team_a), ("🔴 Team B", self.team_b)):
                if self.kind == "dice":
                    rolls = " ".join(f"{p.display_name} {DICE_FACES[points[p] - 1] if points[p] else '⏰'}"
                                     if final else f"{p.display_name} {DICE_FACES[(i + self.frame) % 6]}"
                                     for i, p in enumerate(team))
                else:
                    rolls = " · ".join(f"{p.display_name} {points[p]}" for p in team)
                total = details["score_a"] if team is self.team_a else details["score_b"]
                lines.append(f"**{name}**: {rolls}" + (f"\n➡️ Total **{total}**" if final else ""))
            return "\n\n".join(lines)

        if self.kind == "dice":
            # A short rolling animation, then the real totals.
            self.frame = 0
            message = await self.send(t_embed("🎲 Rolling...", board(False), header=HEADER))
            for frame in (1, 2):
                self.frame = frame
                await asyncio.sleep(DICE_FRAME_DELAY)
                await self.edit(message, t_embed("🎲 Rolling...", board(False), header=HEADER))
            await self.edit(message, t_embed("🎲 Team totals", board(True), header=HEADER))
        else:
            await self.send(t_embed("🧠 Team totals", board(True), header=HEADER))

    @staticmethod
    async def edit(message, embed):
        try:
            await message.edit(embed=embed)
        except discord.HTTPException:
            pass


class Duels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.busy = set()      # member ids in a duel or duel lobby (one game per player at a time)
        self.games = {}        # running task -> channel (to cancel cleanly on restart)

    # ------------------------------------------------------------- helpers
    def run_in_background(self, coro, channel):
        task = asyncio.create_task(coro)
        self.games[task] = channel
        task.add_done_callback(lambda t: self.games.pop(t, None))
        return task

    def claim(self, member):
        """on_join check: refuse members already in another duel."""
        if member.id in self.busy:
            return "You're already in another duel. Finish that one first!"
        self.busy.add(member.id)
        return None

    def release(self, member):
        self.busy.discard(member.id)

    async def cog_unload(self):
        # The bot is shutting down or reloading: stop running duels cleanly.
        for task, channel in list(self.games.items()):
            task.cancel()
            try:
                await channel.send(embed=t_embed("⚠️ Duel cancelled", "The bot is restarting. No results were saved.",
                                                 COLOR_LOSE, header=HEADER))
            except Exception:
                pass

    # ------------------------------------------------------------------ /duel
    @app_commands.command(name="duel", description="⚔️ Duel: 1v1, teams (2v2-4v4) or free for all (2-100 players). No XP")
    @app_commands.describe(game="Which game", mode="1v1 (default), 2v2, 3v3, 4v4 or Free for all",
                           opponent="1v1 only: who you challenge",
                           questions="Trivia only: how many questions (1-10)",
                           max_players=f"Free for all only: most players ({FREE_FOR_ALL_MIN}-{FREE_FOR_ALL_MAX}, default 8)",
                           test_mode="Mods only: fill empty spots with 🤖 bots (team and free for all)")
    @app_commands.choices(game=GAME_CHOICES, mode=MODE_CHOICES)
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 5)
    async def duel(self, interaction: discord.Interaction, game: app_commands.Choice[str],
                   mode: app_commands.Choice[str] = None, opponent: discord.Member = None,
                   questions: app_commands.Range[int, 1, 10] = None,
                   max_players: app_commands.Range[int, FREE_FOR_ALL_MIN, FREE_FOR_ALL_MAX] = 8,
                   test_mode: bool = False):
        mode_key = mode.value if mode else "1v1"
        kind = game.value

        async def refuse(text):
            await interaction.response.send_message(text, ephemeral=True)

        if kind == "trivia" and not trivia_bank.has_questions("mixed"):
            return await refuse("No trivia questions available (run python check_trivia.py).")
        if test_mode and not is_mod(interaction.user):
            return await refuse("Only mods can use test mode.")
        if mode_key == "1v1":
            if test_mode:
                return await refuse("Test mode is for team and free-for-all modes.")
            if opponent is None:
                return await refuse("Pick an `opponent` for a 1v1 duel.")
            return await self.start_one_v_one(interaction, opponent, kind, questions or 1)
        if opponent is not None:
            return await refuse("`opponent` is only for 1v1. Leave it empty: players join with the buttons.")
        if mode_key == "ffa":
            return await self.start_free_for_all(interaction, kind, max_players, questions or 1, test_mode)
        return await self.start_team_lobby(interaction, kind, MODES[mode_key], questions or 3, test_mode)

    # ------------------------------------------------------------------ 1v1
    async def start_one_v_one(self, interaction, opponent, kind, questions):
        challenger = interaction.user
        if opponent.id == challenger.id:
            await interaction.response.send_message("You can't duel yourself! 😄", ephemeral=True)
            return
        if opponent.bot:
            await interaction.response.send_message("Bots don't duel. Pick a member!", ephemeral=True)
            return
        if challenger.id in self.busy or opponent.id in self.busy:
            who = "You're" if challenger.id in self.busy else f"{opponent.display_name} is"
            await interaction.response.send_message(f"{who} already in another duel.", ephemeral=True)
            return
        self.busy.update({challenger.id, opponent.id})

        invite = DuelInvite(challenger, opponent)
        embed = t_embed(f"⚔️ {GAMES[kind]} duel", f"{challenger.mention} challenges {opponent.mention}!\n"
                        f"{opponent.mention}, accept within {ROUND_SECONDS} seconds.\n"
                        f"Just for bragging rights. 😎", header=HEADER)
        await interaction.response.send_message(content=opponent.mention, embed=embed, view=invite,
                                                allowed_mentions=discord.AllowedMentions(users=[opponent]))
        self.run_in_background(self.run_duel(interaction, invite, kind, questions), interaction.channel)

    async def run_duel(self, interaction, invite, kind, questions=1):
        session = None
        try:
            try:
                await asyncio.wait_for(invite.answer.wait(), ROUND_SECONDS)
            except asyncio.TimeoutError:
                for item in invite.children:
                    item.disabled = True
                await interaction.edit_original_response(content="⌛ The challenge expired.", view=invite)
                return
            if not invite.accepted:
                return

            session = Session(interaction.channel, kind, HEADER, questions)
            engine = te.Engine(kind, session.collect, session.announce, questions, session.removed)
            await session.start_board(invite.challenger, [invite.challenger, invite.opponent], f"{GAMES[kind]} duel")
            winner, loser = await engine.match(invite.challenger, invite.opponent, "Duel")
            await session.flush()   # post any queued results first
            await session.board.finish(f"🏆 {winner.mention} beats {loser.mention}!")
            database.record_duel(interaction.guild.id, winner.id, loser.id)   # wins/losses only - duels give no XP
            await interaction.channel.send(
                embed=t_embed("🏅 Duel result", f"🏆 {winner.mention} beats {loser.mention}!", COLOR_GOLD,
                              header=HEADER),
                allowed_mentions=discord.AllowedMentions(users=[winner, loser]))
        except asyncio.CancelledError:
            if session and session.cancelled:
                return
            raise
        except Exception:
            log.exception("Duel crashed")
        finally:
            self.busy.difference_update({invite.challenger.id, invite.opponent.id})

    # ------------------------------------------------------------ team modes
    async def start_team_lobby(self, interaction, kind, size, questions, test_mode):
        lobby = TeamLobbyView(interaction.user, kind, size, test_mode, self.claim, self.release)
        await interaction.response.send_message(embed=lobby.embed(), view=lobby, allowed_mentions=NO_PING)
        lobby.message = await interaction.original_response()
        log.info("Team duel lobby opened in #%s: %dv%d %s", interaction.channel.name, size, size, kind)
        self.run_in_background(self.run_team_game(interaction.channel, interaction.guild, lobby, questions,
                                                  TEAM_LOBBY_SECONDS), interaction.channel)

    async def run_team_game(self, channel, guild, lobby, questions, lobby_seconds):
        session = None
        try:
            try:
                await asyncio.wait_for(lobby.start_now.wait(), lobby_seconds)
            except asyncio.TimeoutError:
                lobby.close()
                await self.edit_lobby(lobby, lobby.embed(status="⌛ Lobby closed: the game didn't start in time."))
                await channel.send(embed=t_embed("⌛ Team duel cancelled",
                                                 f"The lobby closed after {lobby_seconds // 60} minutes without a start.",
                                                 COLOR_LOSE, header=HEADER), allowed_mentions=NO_PING)
                return
            lobby.close()
            await self.edit_lobby(lobby, lobby.embed(status="🔒 Teams locked. Game on!"))

            team_a, team_b = list(lobby.teams["A"]), list(lobby.teams["B"])
            if lobby.test_mode:
                team_a += bots(lobby.size - len(team_a))
                team_b += bots(lobby.size - len(team_b), start=len(team_a) + 1)
            if not team_a or len(team_a) != len(team_b):
                await channel.send(embed=t_embed("❌ Team duel cancelled", "Teams weren't even.", COLOR_LOSE,
                                                 header=HEADER), allowed_mentions=NO_PING)
                return

            session = TeamSession(channel, lobby.kind, questions, team_a, team_b)
            engine = team_engine.TeamEngine(lobby.kind, session.collect, session.announce, session.removed)
            log.info("Team duel started in #%s: %dv%d %s", channel.name, len(team_a), len(team_b), lobby.kind)
            await session.start_board(lobby.host, team_a + team_b, f"{GAMES[lobby.kind]} {lobby.size}v{lobby.size}")
            session.board.teams = {"A": team_a, "B": team_b}
            await session.refresh_board()
            result = await engine.play(team_a, team_b)
            await session.flush()   # post any queued results first
            await session.board.finish(f"🏆 Team {result.winner} wins! 🔵 **{result.score_a}** - "
                                       f"**{result.score_b}** 🔴")
            await self.finish_team_game(channel, guild, result, session.removed)
        except asyncio.CancelledError:
            if session and session.cancelled:
                return
            raise
        except Exception:
            log.exception("Team duel in #%s crashed", getattr(channel, "name", "?"))
            await self.crash_note(channel)
        finally:
            for member in lobby.members():
                self.release(member)

    async def finish_team_game(self, channel, guild, result, removed=()):
        winner_icon = "🔵" if result.winner == "A" else "🔴"
        embed = t_embed(f"🏆 {winner_icon} Team {result.winner} wins!",
                        f"🔵 Team A **{result.score_a}** - **{result.score_b}** Team B 🔴"
                        + ("\n🎲 Decided by a fair random pick." if result.random_pick else "")
                        + "\n\nNo XP: just bragging rights. 😎", COLOR_GOLD, header=HEADER)
        for name, team in (("A", result.winners if result.winner == "A" else result.losers),
                           ("B", result.winners if result.winner == "B" else result.losers)):
            lines = "\n".join(f"{p.mention}: {result.contributions.get(p, '-')}" for p in team)
            crown = " 👑" if name == result.winner else ""
            embed.add_field(name=f"{'🔵' if name == 'A' else '🔴'} Team {name}{crown}", value=lines, inline=False)
        # A removed player counts as a loss, even if their team won.
        database.record_team_game(guild.id, [p.id for p in result.winners if not is_bot(p) and p not in removed],
                                  [p.id for p in result.losers + [p for p in result.winners if p in removed]
                                   if not is_bot(p)])
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(
            users=[p for p in result.winners + result.losers if not is_bot(p)], everyone=False, roles=False))
        log.info("Team duel finished in #%s", channel.name)

    # --------------------------------------------------------- free for all
    async def start_free_for_all(self, interaction, kind, max_players, questions, test_mode):
        lobby = LobbyView(interaction.user, kind, max_players, int(time.time()) + FREE_FOR_ALL_SECONDS, test_mode,
                          questions, free_for_all=True, on_join=self.claim, on_leave=self.release)
        await interaction.response.send_message(embed=lobby.embed(), view=lobby, allowed_mentions=NO_PING)
        lobby.message = await interaction.original_response()
        self.run_in_background(self.run_free_for_all(interaction.channel, interaction.guild, lobby,
                                                     FREE_FOR_ALL_SECONDS), interaction.channel)

    async def run_free_for_all(self, channel, guild, lobby, join_seconds):
        session = None
        try:
            try:
                await asyncio.wait_for(lobby.start_now.wait(), join_seconds)
            except asyncio.TimeoutError:
                pass
            lobby.close()
            await self.edit_lobby(lobby, lobby.embed(closed=True))

            players = list(lobby.players)
            if lobby.test_mode:
                players += bots(lobby.max_players - len(players))
            if len(players) < lobby.min_players:
                await channel.send(embed=t_embed(
                    "❌ Free for all cancelled: not enough players",
                    f"**{len(players)}** player(s) joined, but at least **{lobby.min_players}** are needed.\n\n"
                    f"ℹ️ The host ({lobby.host.mention}) only counts as a player if they pressed **Join**.",
                    COLOR_LOSE, header=HEADER), allowed_mentions=NO_PING)
                return

            session = Session(channel, lobby.kind, HEADER, lobby.questions)
            engine = te.Engine(lobby.kind, session.collect, session.announce, lobby.questions, session.removed)
            log.info("Free for all started in #%s: %d players (%d bots), game=%s", channel.name, len(players),
                     sum(map(is_bot, players)), lobby.kind)
            await session.start_board(lobby.host, players, f"{GAMES[lobby.kind]} Free for all")
            podium = await engine.run_tournament(players)
            await session.flush()   # post any queued results first
            await session.board.finish(podium_text(podium, session.removed))

            removed = session.removed
            lines = [f"{medal} {p.mention}" + (" - test bot" if is_bot(p) else "") + (" - removed" if p in removed else "")
                     for medal, p in zip(("🥇", "🥈", "🥉"), podium) if p is not None]
            database.record_free_for_all(guild.id, [p.id for p in players if not is_bot(p)],
                                         [None if p is None or is_bot(p) or p in removed else p.id for p in podium])
            await channel.send(embed=t_embed("🏆 Free for all results",
                                             "\n".join(lines) + f"\n\n{len(players)} players · no XP, just "
                                             "bragging rights. 😎", COLOR_GOLD, header=HEADER),
                               allowed_mentions=discord.AllowedMentions(
                                   users=[p for p in podium if p is not None and not is_bot(p)],
                                   everyone=False, roles=False))
        except asyncio.CancelledError:
            if session and session.cancelled:
                return
            raise
        except Exception:
            log.exception("Free for all in #%s crashed", getattr(channel, "name", "?"))
            await self.crash_note(channel)
        finally:
            for member in lobby.players:
                self.release(member)

    async def edit_lobby(self, lobby, embed):
        try:
            await lobby.message.edit(embed=embed, view=lobby, allowed_mentions=NO_PING)
        except discord.HTTPException:
            pass

    async def crash_note(self, channel):
        try:
            await channel.send(embed=t_embed("⚠️ Duel stopped", "Something went wrong, sorry! No results were saved.",
                                             COLOR_LOSE, header=HEADER))
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------- /duelstats
    @app_commands.command(name="duelstats", description="📊 Duel record, team wins and podiums")
    @app_commands.describe(member="Whose stats (leave empty for yours)")
    @app_commands.guild_only()
    async def duelstats(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        stats = database.get_duel_stats(interaction.guild.id, member.id)
        played = stats["duel_wins"] + stats["duel_losses"]
        rate = f"{100 * stats['duel_wins'] / played:.0f}%" if played else "-"
        embed = t_embed(f"📊 {member.display_name}", header="⚔️ N7X Duels & Tournaments")
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="⚔️ 1v1 duels", value=f"**{stats['duel_wins']}** W · **{stats['duel_losses']}** L ({rate})")
        embed.add_field(name="👥 Team duels", value=f"**{stats['team_wins']}** W · **{stats['team_losses']}** L")
        embed.add_field(name="🎯 Free for all",
                        value=f"**{stats['ffa_played']}** played\n🥇 **{stats['ffa_gold']}**   "
                              f"🥈 **{stats['ffa_silver']}**   🥉 **{stats['ffa_bronze']}**", inline=False)
        embed.add_field(name="🏆 Tournaments",
                        value=f"**{stats['tournaments_played']}** played\n🥇 **{stats['gold']}**   "
                              f"🥈 **{stats['silver']}**   🥉 **{stats['bronze']}**", inline=False)
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Duels(bot))
