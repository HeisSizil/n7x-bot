"""
cogs/tournament.py - /tournament, /duel and /duelstats (the Discord side).

The rules live in tournament_engine.py. This file handles the buttons and messages:
- /tournament (mods only): a lobby with Join / Leave / Start now. The host is NOT a
  player unless they press Join too. It starts when the join timer ends (or the host
  presses Start now) with whoever joined. Max players is only a cap; at least 3 are needed.
  Then rounds are played in the channel with buttons.
- /duel @member: a 1-vs-1 challenge using the same match rules. Duels give NO XP.
- /duelstats [member]: duel wins/losses and tournament podium finishes.

Tournament podium bonus XP (50/30/20) uses the shared game XP daily cap (100, resets
00:00 UTC). Secure randomness (secrets). XP only - no real prizes.

Test mode: empty spots are filled with 🤖 bot players that pick randomly, so a whole
tournament can be demoed alone. Bots never get XP or stats.
"""

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

import database
import tournament_engine as te
from cogs.games import (COLOR_DRAW, COLOR_GAME, COLOR_LOSE, COLOR_WIN, DAILY_GAME_XP_CAP, DICE_FACES, FOOTER,
                        LETTER_EMOJI, LETTERS, _shuffler, load_trivia, today)

log = logging.getLogger("n7x.tournament")

# --- Settings you can tweak ---
ROUND_SECONDS = 30            # a player who doesn't respond in time loses that round
PODIUM_XP = (50, 30, 20)      # bonus game XP for 1st, 2nd, 3rd (still limited by the daily cap)
MAX_PLAYERS_LIMIT = 16
SEND_GAP = 1.0                # pause after each channel message (keeps us inside Discord's rate limits)
COLOR_GOLD = 0xFACC15

GAMES = {"rps": "🪨📄✂️ Rock Paper Scissors", "dice": "🎲 Dice", "trivia": "🧠 Trivia"}
GAME_CHOICES = [app_commands.Choice(name=label, value=key) for key, label in GAMES.items()]


@dataclass(frozen=True)
class BotPlayer:
    """A test-mode bot. Never gets XP, never saved in stats."""
    id: int
    display_name: str

    @property
    def mention(self):
        return f"**{self.display_name}**"


def is_bot(player):
    return isinstance(player, BotPlayer)


def is_mod(member):
    perms = member.guild_permissions
    return perms.moderate_members or perms.manage_messages


def t_embed(title, description="", color=COLOR_GAME, header="🏆 N7X Tournament"):
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_author(name=header)
    embed.set_footer(text=FOOTER)
    return embed


def new_trivia_question():
    """A random question from trivia.json with the answers shuffled securely, or None."""
    questions = load_trivia()
    if not questions:
        return None
    picked = secrets.choice(questions)
    order = list(range(4))
    _shuffler.shuffle(order)
    return {"question": picked["question"], "choices": [picked["choices"][i] for i in order],
            "answer": order.index(picked["answer"]), "explain": picked["explain"]}


def bot_response(kind, question):
    """What a test bot answers (random)."""
    if kind == "rps":
        return secrets.choice(list(te.RPS_SHAPES))
    if kind == "dice":
        return secrets.randbelow(6) + 1
    correct = secrets.randbelow(100) < 60           # bots are right 60% of the time
    seconds = 3 + secrets.randbelow(2200) / 100     # and take 3-25 seconds
    return correct, seconds


def describe_response(kind, response, question=None):
    if response is None:
        return "⏰ no answer"
    if kind == "rps":
        return f"{te.RPS_SHAPES[response]} {response.capitalize()}"
    if kind == "dice":
        return f"{DICE_FACES[response - 1]} {response}"
    correct, seconds = response
    return f"✅ right in {seconds:.1f}s" if correct else f"❌ wrong ({seconds:.1f}s)"


class RoundView(discord.ui.View):
    """Buttons for one round. Only players in the round can press, once each; picks stay hidden."""

    def __init__(self, humans, kind, question, responses):
        super().__init__(timeout=ROUND_SECONDS + 10)
        self.humans = {player.id: player for player in humans}
        self.kind = kind
        self.question = question
        self.responses = responses
        self.started = time.monotonic()
        self.all_done = asyncio.Event()
        self.closed = False
        self.message = None

        if kind == "rps":
            options = [(shape.capitalize(), emoji, shape) for shape, emoji in te.RPS_SHAPES.items()]
        elif kind == "dice":
            options = [("Roll", "🎲", "roll")]
        else:
            options = [(letter, LETTER_EMOJI[letter], index) for index, letter in enumerate(LETTERS)]
        for label, emoji, value in options:
            button = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.primary)
            button.callback = self.make_callback(value)
            self.add_item(button)

    def waiting_line(self):
        return " · ".join(f"{'✅' if player in self.responses else '⏳'} {player.display_name}"
                          for player in self.humans.values())

    async def interaction_check(self, interaction):
        player = self.humans.get(interaction.user.id)
        if player is None:
            await interaction.response.send_message("👀 You're not playing in this round.", ephemeral=True)
            return False
        if self.closed:
            await interaction.response.send_message("⌛ This round is closed.", ephemeral=True)
            return False
        if player in self.responses:
            await interaction.response.send_message("✅ You're already locked in.", ephemeral=True)
            return False
        return True

    def make_callback(self, value):
        async def callback(interaction: discord.Interaction):
            player = self.humans[interaction.user.id]
            if self.kind == "rps":
                self.responses[player] = value
                note = f"Locked in: {te.RPS_SHAPES[value]} **{value}** (hidden until everyone has picked)"
            elif self.kind == "dice":
                roll = secrets.randbelow(6) + 1
                self.responses[player] = roll
                note = f"You rolled {DICE_FACES[roll - 1]} **{roll}**"
            else:
                seconds = round(time.monotonic() - self.started, 1)
                self.responses[player] = (value == self.question["answer"], seconds)
                note = f"Answer **{LETTERS[value]}** locked in after {seconds}s"
            await interaction.response.send_message(note, ephemeral=True)

            if all(p in self.responses for p in self.humans.values()):
                self.all_done.set()
            elif self.message:
                embed = self.message.embeds[0] if self.message.embeds else None
                if embed:
                    embed.set_field_at(0, name="Locked in", value=self.waiting_line(), inline=False)
                    try:
                        await self.message.edit(embed=embed)
                    except discord.HTTPException:
                        pass
        return callback

    def close(self):
        self.closed = True
        self.stop()
        for item in self.children:
            item.disabled = True


class Session:
    """One tournament or duel in one channel: gives the engine its collect() and announce()."""

    def __init__(self, channel, kind, header):
        self.channel = channel
        self.kind = kind
        self.header = header
        self.question = None

    async def send(self, embed, view=None, ping=()):
        message = await self.channel.send(embed=embed, view=view,
                                          allowed_mentions=discord.AllowedMentions(everyone=False, roles=False,
                                                                                    users=list(ping)))
        await asyncio.sleep(SEND_GAP)
        return message

    async def collect(self, players, kind, label, attempt):
        question = new_trivia_question() if kind == "trivia" else None
        self.question = question
        responses = {player: bot_response(kind, question) for player in players if is_bot(player)}
        humans = [player for player in players if not is_bot(player)]
        if not humans:
            return responses  # bots only: instant

        vs = " vs ".join(player.mention for player in players)
        again = f" (replay {attempt - 1})" if attempt > 1 else ""
        if kind == "rps":
            ask = "Pick your move! Picks stay hidden until everyone has chosen."
        elif kind == "dice":
            ask = "Press **Roll**! Highest roll goes through."
        else:
            ask = (f"**{question['question']}**\n"
                   + "\n".join(f"{LETTER_EMOJI[l]} {c}" for l, c in zip(LETTERS, question["choices"]))
                   + "\nRight and fastest goes through.")
        ends = int(time.time()) + ROUND_SECONDS
        embed = t_embed(f"⚔️ {label}{again}", f"{vs}\n\n{ask}\n⏱️ Closes <t:{ends}:R> - no answer = you lose this round",
                        header=self.header)
        view = RoundView(humans, kind, question, responses)
        embed.add_field(name="Locked in", value=view.waiting_line(), inline=False)
        view.message = await self.channel.send(embed=embed, view=view,
                                               allowed_mentions=discord.AllowedMentions(users=humans,
                                                                                         everyone=False, roles=False))
        try:
            await asyncio.wait_for(view.all_done.wait(), ROUND_SECONDS)
        except asyncio.TimeoutError:
            pass
        view.close()
        for player in humans:
            responses.setdefault(player, None)
        embed.set_field_at(0, name="Locked in", value=view.waiting_line() + "\n🔒 Round closed", inline=False)
        try:
            await view.message.edit(embed=embed, view=view)
        except discord.HTTPException:
            pass
        return responses

    def response_lines(self, responses):
        return "\n".join(f"{player.mention}: {describe_response(self.kind, response, self.question)}"
                         for player, response in responses.items())

    async def announce(self, event, **details):
        if event == "group_start":
            names = ", ".join(p.mention for p in details["players"])
            await self.send(t_embed("👥 Group round", f"3 players: {names}\nOne round decides who's out. "
                                    "Then it's the final!", header=self.header))
        elif event == "bracket_start":
            lines = [f"⚔️ {a.mention} vs {b.mention}" for a, b in details["pairs"]]
            if details["byes"]:
                lines.append("\n🎟️ **Free pass** to the next round: " + ", ".join(p.mention for p in details["byes"]))
            await self.send(t_embed(f"🗂️ Bracket of {details['size']}", "\n".join(lines), header=self.header))
        elif event == "round_result":
            text = self.response_lines(details["responses"])
            if self.kind == "trivia" and self.question:
                q = self.question
                text += f"\n\n✅ Answer: **{LETTERS[q['answer']]}** - {q['choices'][q['answer']]}"
            text += ("\n\n➡️ Through: " + ", ".join(p.mention for p in details["advance"])
                     + "\n❌ Out: " + ", ".join(p.mention for p in details["eliminated"]))
            await self.send(t_embed(f"📣 {details['label']}: result", text, COLOR_WIN, header=self.header))
        elif event == "replay":
            reason = {"rps": "All the same or all different shapes",
                      "dice": "Tied lowest roll",
                      "trivia": "Nobody got it right (or an exact tie)"}[self.kind]
            text = self.response_lines(details["responses"])
            if all(r is None for r in details["responses"].values()):
                reason = "Nobody answered"
            await self.send(t_embed(f"🔁 {details['label']}: replay!", f"{text}\n\n{reason}, so we go again.",
                                    COLOR_DRAW, header=self.header))
        elif event == "random_result":
            await self.send(t_embed(f"🎲 {details['label']}: fair random pick",
                                    f"Still undecided after {te.MAX_REPLAYS} replays, so a secure random draw decides.\n"
                                    f"➡️ Through: {', '.join(p.mention for p in details['advance'])}\n"
                                    f"❌ Out: {', '.join(p.mention for p in details['eliminated'])}",
                                    COLOR_DRAW, header=self.header))


class LobbyView(discord.ui.View):
    """
    Join / Leave / Start now. Max players is only a cap.
    The host is NOT added automatically: they press Join like everyone else if they want to play.
    """

    def __init__(self, host, kind, max_players, ends_at):
        super().__init__(timeout=None)
        self.host = host
        self.kind = kind
        self.max_players = max_players
        self.ends_at = ends_at
        self.players = []   # only people who pressed Join (the host too, if they joined)
        self.start_now = asyncio.Event()
        self.message = None

    def embed(self, closed=False):
        listing = "\n".join(f"{i}. {p.mention}" for i, p in enumerate(self.players, start=1)) or "_nobody yet - press **Join**!_"
        status = ("🔒 Registration closed." if closed else
                  f"⏱️ Starts <t:{self.ends_at}:R>, or when the host presses **Start now**.")
        embed = t_embed(f"🏆 {GAMES[self.kind]} Tournament",
                        f"{status}\n\n"
                        f"Needs at least {te.MIN_PLAYERS} players. 🥇 {PODIUM_XP[0]} · 🥈 {PODIUM_XP[1]} · "
                        f"🥉 {PODIUM_XP[2]} bonus game XP. No real prizes.")
        embed.add_field(name="🎙️ Host", value=f"Hosted by {self.host.mention}\n_(plays only if they press Join)_",
                        inline=False)
        embed.add_field(name=f"👥 Players ({len(self.players)}/{self.max_players})", value=listing, inline=False)
        return embed

    async def refresh(self, interaction):
        await interaction.response.edit_message(embed=self.embed(), view=self,
                                                allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Join", emoji="✅", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if any(p.id == interaction.user.id for p in self.players):
            await interaction.response.send_message("You're already in! 🎮", ephemeral=True)
        elif len(self.players) >= self.max_players:
            await interaction.response.send_message(f"Sorry, the tournament is full ({self.max_players}).", ephemeral=True)
        else:
            self.players.append(interaction.user)
            await self.refresh(interaction)

    @discord.ui.button(label="Leave", emoji="🚪", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        before = len(self.players)
        self.players = [p for p in self.players if p.id != interaction.user.id]
        if len(self.players) == before:
            await interaction.response.send_message("You haven't joined.", ephemeral=True)
        else:
            await self.refresh(interaction)

    @discord.ui.button(label="Start now", emoji="▶️", style=discord.ButtonStyle.primary)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.host.id:
            await interaction.response.send_message("Only the host can start the tournament early.", ephemeral=True)
            return
        await interaction.response.defer()
        self.start_now.set()

    def close(self):
        self.stop()
        for item in self.children:
            item.disabled = True


class DuelInvite(discord.ui.View):
    def __init__(self, challenger, opponent):
        super().__init__(timeout=ROUND_SECONDS)
        self.challenger = challenger
        self.opponent = opponent
        self.answer = asyncio.Event()
        self.accepted = False

    async def interaction_check(self, interaction):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("This challenge isn't for you. Start your own with /duel!",
                                                    ephemeral=True)
            return False
        return not self.answer.is_set()

    @discord.ui.button(label="Accept", emoji="⚔️", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.accepted = True
        await self.finish(interaction, "⚔️ Challenge accepted! Get ready...")

    @discord.ui.button(label="Decline", emoji="🏳️", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.finish(interaction, "🏳️ Challenge declined.")

    async def finish(self, interaction, text):
        self.answer.set()
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self, content=text)


class Tournament(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.busy_channels = set()   # one tournament per channel at a time
        self.tasks = set()

    def run_in_background(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    # ------------------------------------------------------------ /tournament
    @app_commands.command(name="tournament", description="🏆 [Mods] Host a tournament: RPS, dice or trivia")
    @app_commands.describe(game="Which game", max_players=f"Most players allowed (3-{MAX_PLAYERS_LIMIT}); fewer is fine",
                           join_seconds="How long people can join (15-300 seconds)",
                           test_mode="Fill empty spots with 🤖 bot players for a demo (they never get XP)")
    @app_commands.choices(game=GAME_CHOICES)
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 60)
    async def tournament(self, interaction: discord.Interaction, game: app_commands.Choice[str],
                         max_players: app_commands.Range[int, 3, MAX_PLAYERS_LIMIT] = 8,
                         join_seconds: app_commands.Range[int, 15, 300] = 60,
                         test_mode: bool = False):
        if not is_mod(interaction.user):
            await interaction.response.send_message("Only mods can start tournaments.", ephemeral=True)
            return
        if interaction.channel.id in self.busy_channels:
            await interaction.response.send_message("A tournament is already running in this channel.", ephemeral=True)
            return
        if game.value == "trivia" and not load_trivia():
            await interaction.response.send_message("No trivia questions available (check trivia.json).", ephemeral=True)
            return

        self.busy_channels.add(interaction.channel.id)
        lobby = LobbyView(interaction.user, game.value, max_players, int(time.time()) + join_seconds)
        embed = lobby.embed()
        if test_mode:
            embed.add_field(name="🧪 Test mode", value="Empty spots will be filled with 🤖 bot players "
                                                      "(they pick randomly and never get XP).")
        await interaction.response.send_message(embed=embed, view=lobby,
                                                allowed_mentions=discord.AllowedMentions.none())
        lobby.message = await interaction.original_response()
        self.run_in_background(self.run_tournament(interaction, lobby, join_seconds, test_mode))

    async def run_tournament(self, interaction, lobby, join_seconds, test_mode):
        channel = interaction.channel
        guild = interaction.guild
        try:
            try:
                await asyncio.wait_for(lobby.start_now.wait(), join_seconds)
            except asyncio.TimeoutError:
                pass
            lobby.close()
            try:
                await lobby.message.edit(embed=lobby.embed(closed=True), view=lobby,
                                         allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass

            players = list(lobby.players)
            if test_mode:
                for number in range(1, lobby.max_players - len(players) + 1):
                    players.append(BotPlayer(id=-number, display_name=f"🤖 Bot {number}"))
            if len(players) < te.MIN_PLAYERS:
                await channel.send(embed=t_embed(
                    "❌ Tournament cancelled: not enough players",
                    f"**{len(players)}** player(s) joined, but at least **{te.MIN_PLAYERS}** are needed.\n\n"
                    f"ℹ️ The host ({lobby.host.mention}) only counts as a player if they pressed **Join**.",
                    COLOR_LOSE), allowed_mentions=discord.AllowedMentions.none())
                return

            session = Session(channel, lobby.kind, "🏆 N7X Tournament")
            engine = te.Engine(lobby.kind, session.collect, session.announce)
            log.info("Tournament started in #%s: %d players (%d bots), game=%s", channel.name, len(players),
                     sum(map(is_bot, players)), lobby.kind)
            podium = await engine.run_tournament(players)
            await self.finish_tournament(channel, guild, players, podium)
        except Exception:
            log.exception("Tournament in #%s crashed", getattr(channel, "name", "?"))
            try:
                await channel.send(embed=t_embed("⚠️ Tournament stopped", "Something went wrong, sorry! "
                                                 "No results were saved.", COLOR_LOSE))
            except discord.HTTPException:
                pass
        finally:
            self.busy_channels.discard(channel.id)

    async def finish_tournament(self, channel, guild, players, podium):
        lines = []
        for medal, player, xp in zip(("🥇", "🥈", "🥉"), podium, PODIUM_XP):
            if is_bot(player):
                lines.append(f"{medal} {player.mention} - test bot, no XP")
                continue
            given, earned = database.give_game_xp(guild.id, player.id, xp, today(), DAILY_GAME_XP_CAP)
            if given == xp:
                bonus = f"+{given} XP"
            elif given:
                bonus = f"+{given} XP (daily cap)"
            else:
                bonus = "daily game XP cap reached"
            lines.append(f"{medal} {player.mention} - ✨ {bonus}")

        database.record_tournament(guild.id, [p.id for p in players if not is_bot(p)],
                                   [None if is_bot(p) else p.id for p in podium])
        embed = t_embed("🏆 Tournament results", "\n".join(lines) + f"\n\n{len(players)} players · thanks for playing!",
                        COLOR_GOLD)
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(
            users=[p for p in podium if not is_bot(p)], everyone=False, roles=False))
        log.info("Tournament finished in #%s", channel.name)

    # ------------------------------------------------------------------ /duel
    @app_commands.command(name="duel", description="⚔️ Challenge a member to a 1-vs-1 duel")
    @app_commands.describe(opponent="Who you challenge", game="Which game")
    @app_commands.choices(game=GAME_CHOICES)
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 5)
    async def duel(self, interaction: discord.Interaction, opponent: discord.Member, game: app_commands.Choice[str]):
        challenger = interaction.user
        if opponent.id == challenger.id:
            await interaction.response.send_message("You can't duel yourself! 😄", ephemeral=True)
            return
        if opponent.bot:
            await interaction.response.send_message("Bots don't duel. Pick a member!", ephemeral=True)
            return
        if game.value == "trivia" and not load_trivia():
            await interaction.response.send_message("No trivia questions available (check trivia.json).", ephemeral=True)
            return

        invite = DuelInvite(challenger, opponent)
        embed = t_embed(f"⚔️ {GAMES[game.value]} duel", f"{challenger.mention} challenges {opponent.mention}!\n"
                        f"{opponent.mention}, accept within {ROUND_SECONDS} seconds.\n"
                        f"Just for bragging rights. 😎", header="⚔️ N7X Duel")
        await interaction.response.send_message(content=opponent.mention, embed=embed, view=invite,
                                                allowed_mentions=discord.AllowedMentions(users=[opponent]))
        self.run_in_background(self.run_duel(interaction, invite, game.value))

    async def run_duel(self, interaction, invite, kind):
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

            session = Session(interaction.channel, kind, "⚔️ N7X Duel")
            engine = te.Engine(kind, session.collect, session.announce)
            winner, loser = await engine.match(invite.challenger, invite.opponent, "Duel")
            database.record_duel(interaction.guild.id, winner.id, loser.id)   # wins/losses only - duels give no XP
            await interaction.channel.send(
                embed=t_embed("🏅 Duel result", f"🏆 {winner.mention} beats {loser.mention}!",
                              COLOR_GOLD, header="⚔️ N7X Duel"),
                allowed_mentions=discord.AllowedMentions(users=[winner, loser]))
        except Exception:
            log.exception("Duel crashed")

    # ------------------------------------------------------------- /duelstats
    @app_commands.command(name="duelstats", description="📊 Duel record and tournament podiums")
    @app_commands.describe(member="Whose stats (leave empty for yours)")
    @app_commands.guild_only()
    async def duelstats(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        stats = database.get_duel_stats(interaction.guild.id, member.id)
        played = stats["duel_wins"] + stats["duel_losses"]
        rate = f"{100 * stats['duel_wins'] / played:.0f}%" if played else "-"
        embed = t_embed(f"📊 {member.display_name}", header="⚔️ N7X Duels & Tournaments")
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="⚔️ Duels", value=f"**{stats['duel_wins']}** W · **{stats['duel_losses']}** L ({rate})")
        embed.add_field(name="🏆 Tournaments", value=f"**{stats['tournaments_played']}** played")
        embed.add_field(name="🏅 Podium finishes",
                        value=f"🥇 **{stats['gold']}**   🥈 **{stats['silver']}**   🥉 **{stats['bronze']}**",
                        inline=False)
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Tournament(bot))
