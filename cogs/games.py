"""
cogs/games.py - The N7X Arcade: mini games with animations and buttons.

Games:    /flip, /dice, /rps, /wheel, /trivia, /highlow
Stats:    /gameprofile, /gamesleaderboard

Rules (all games):
- Results use Python's `secrets` module (secure randomness), so nobody can predict or rig them.
- Wins give a little XP that goes into the SAME XP as chatting, so games count toward /rank.
  Game XP is capped at DAILY_GAME_XP_CAP per member per day (resets 00:00 UTC).
- Each game has a 5-second cooldown per member.
- Buttons only work for the player who started the game.
- Prizes are XP and bragging rights ONLY. No money, tokens or crypto - ever.
"""

import asyncio
import json
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import database
from cogs.ranks import level_from_xp, progress_bar

log = logging.getLogger("n7x.games")

# =====================================================================
# Settings you can tweak
# =====================================================================
DAILY_GAME_XP_CAP = 100      # max game XP per member per day
COOLDOWN_SECONDS = 5         # per member, per game
FRAME_DELAY = 0.7            # seconds between animation frames (keeps us well inside Discord's rate limits)

FLIP_WIN_XP = 5
DICE_WIN_XP = 5
RPS_WIN_XP = 8
TRIVIA_WIN_XP = 10
HIGHLOW_XP_PER_GUESS = 2     # per correct Higher/Lower guess...
HIGHLOW_MAX_XP = 20          # ...up to this much per game
HIGHLOW_WIN_STREAK = 3       # a /highlow game counts as a win from this streak on

RPS_SECONDS = 30             # how long the buttons wait
TRIVIA_SECONDS = 20
HIGHLOW_SECONDS = 30         # per guess

# /gameprofile titles: (minimum level, title). Edit freely.
TITLES = [
    (0, "🐣 Rookie"),
    (3, "🎮 Player"),
    (6, "⚡ Pro"),
    (10, "👑 Master"),
    (15, "🌌 N7X Legend"),
]

# Lucky wheel: (weight, emoji, text, XP). Weight = % chance (they add up to 100).
# A spin "wins" if it gives XP.
WHEEL = [
    (4,  "💎", "JACKPOT! Diamond hands pay off", 20),
    (10, "🚀", "To the moon!", 12),
    (16, "🤖", "Your AI agent found alpha", 8),
    (20, "🍀", "Lucky charm", 5),
    (15, "⛽", "Gas refund (in XP, sadly)", 3),
    (15, "😅", "Rugged! Only vibes this time", 0),
    (12, "🧻", "Paper hands. Try again!", 0),
    (8,  "🐻", "The bear market got you", 0),
]

TRIVIA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trivia.json")

# =====================================================================
# The N7X Arcade look: one color for games in progress, green = win, red = lose
# =====================================================================
COLOR_GAME = 0x8B5CF6   # N7X arcade violet
COLOR_WIN = 0x22C55E    # green
COLOR_LOSE = 0xEF4444   # red
COLOR_DRAW = 0xF59E0B   # amber
FOOTER = "N7X Bot"

DICE_FACES = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]
RPS_CHOICES = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
RPS_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
LETTERS = ["A", "B", "C", "D"]
LETTER_EMOJI = {"A": "🇦", "B": "🇧", "C": "🇨", "D": "🇩"}

_shuffler = secrets.SystemRandom()  # secure shuffling (for trivia answers)


def today():
    """Today's date in UTC, e.g. '2026-10-16' (the daily XP cap resets at 00:00 UTC)."""
    return datetime.now(timezone.utc).date().isoformat()


def arcade_embed(player, title, description="", color=COLOR_GAME):
    """The one look shared by every game."""
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_author(name=f"N7X Arcade • {player.display_name}", icon_url=player.display_avatar.url)
    embed.set_footer(text=FOOTER)
    return embed


def finish_game(guild_id, user_id, outcome, xp, highlow_streak=None):
    """
    Save a finished game and give its XP (0 for a loss), respecting the daily cap.
    outcome: "win", "loss" or "draw". Returns the XP lines for the result embed.
    """
    given, earned_today = database.record_game(
        guild_id, user_id, outcome, xp, today(), DAILY_GAME_XP_CAP, highlow_streak=highlow_streak,
    )
    meter = f"{progress_bar(earned_today, DAILY_GAME_XP_CAP)} {earned_today}/{DAILY_GAME_XP_CAP} game XP today"
    if xp <= 0:
        return f"No XP this time\n{meter}"
    if given == 0:
        return f"🔒 Daily game XP cap reached. Come back after 00:00 UTC!\n{meter}"
    capped = " (daily cap)" if given < xp else ""
    return f"✨ **+{given} XP**{capped}\n{meter}"


async def animate(interaction, frames, final):
    """
    Show a little animation: send frames[0], edit to each next frame FRAME_DELAY apart,
    then show `final`. 3 edits in ~2 seconds stays well inside Discord's rate limits,
    and discord.py waits automatically if Discord ever says "slow down".
    """
    await interaction.response.send_message(embed=frames[0])
    for frame in frames[1:] + [final]:
        await asyncio.sleep(FRAME_DELAY)
        try:
            await interaction.edit_original_response(embed=frame)
        except discord.HTTPException:
            if frame is final:  # if even the final edit fails, post the result as a new message
                await interaction.followup.send(embed=final)
            # a skipped animation frame doesn't matter


def spin_wheel():
    """Pick a wheel slot fairly using the weights. Returns (emoji, text, xp)."""
    total = sum(slot[0] for slot in WHEEL)
    pick = secrets.randbelow(total)
    for weight, emoji, text, xp in WHEEL:
        if pick < weight:
            return emoji, text, xp
        pick -= weight
    return WHEEL[-1][1:]  # never reached


def title_for_level(level):
    """The title for a level, and the next title (or None)."""
    current, upcoming = TITLES[0], None
    for entry in TITLES:
        if level >= entry[0]:
            current = entry
        elif upcoming is None:
            upcoming = entry
    return current[1], upcoming


# =====================================================================
# Trivia questions (trivia.json, re-read automatically when it changes)
# =====================================================================
_trivia_cache = {"mtime": None, "questions": []}


def load_trivia():
    """Valid questions from trivia.json. Broken entries are skipped (and logged once)."""
    try:
        mtime = os.path.getmtime(TRIVIA_FILE)
    except OSError:
        log.warning("trivia.json not found - /trivia has no questions")
        return []
    if mtime == _trivia_cache["mtime"]:
        return _trivia_cache["questions"]

    try:
        with open(TRIVIA_FILE, encoding="utf-8") as file:
            data = json.load(file)
    except ValueError as error:
        log.warning("trivia.json is not valid JSON (%s) - keeping the previous questions", error)
        return _trivia_cache["questions"]

    raw = data.get("questions", []) if isinstance(data, dict) else data
    questions = []
    for number, item in enumerate(raw if isinstance(raw, list) else [], start=1):
        try:
            choices = [str(choice).strip() for choice in item["choices"]]
            answer = LETTERS.index(str(item["answer"]).strip().upper())
            if len(choices) != 4 or not all(choices) or not str(item["question"]).strip():
                raise ValueError
        except (KeyError, TypeError, ValueError):
            log.warning("trivia.json: question %d is incomplete (needs question, 4 choices, answer A-D) - skipped", number)
            continue
        questions.append({"question": str(item["question"]).strip(), "choices": choices,
                          "answer": answer, "explain": str(item.get("explain", "")).strip()})
    _trivia_cache.update(mtime=mtime, questions=questions)
    log.info("Loaded %d trivia question(s)", len(questions))
    return questions


# =====================================================================
# Button games
# =====================================================================

class PlayerView(discord.ui.View):
    """Base for button games: only the player who started can press, double clicks are ignored."""

    def __init__(self, player, timeout):
        super().__init__(timeout=timeout)
        self.player = player
        self.message = None
        self.finished = False

    async def interaction_check(self, interaction):
        if interaction.user.id != self.player.id:
            await interaction.response.send_message("🙅 This isn't your game! Start your own.", ephemeral=True)
            return False
        return True

    def disable_all(self):
        for item in self.children:
            item.disabled = True


class RPSView(PlayerView):
    def __init__(self, player, guild_id):
        super().__init__(player, RPS_SECONDS)
        self.guild_id = guild_id
        for choice, emoji in RPS_CHOICES.items():
            button = discord.ui.Button(label=choice.capitalize(), emoji=emoji, style=discord.ButtonStyle.primary)
            button.callback = self.make_callback(choice, button)
            self.add_item(button)

    def make_callback(self, choice, button):
        async def callback(interaction: discord.Interaction):
            if self.finished:
                return
            self.finished = True
            self.stop()
            self.disable_all()

            # A tiny "Rock... paper... scissors... SHOOT!" moment.
            await interaction.response.edit_message(
                embed=arcade_embed(self.player, "🪨 Rock... 📄 paper... ✂️ scissors...", "**SHOOT!**"), view=self)
            await asyncio.sleep(FRAME_DELAY)

            bot_choice = secrets.choice(list(RPS_CHOICES))  # picked only after the player, fairly
            if choice == bot_choice:
                outcome, title, color, style = "draw", "🤝 It's a draw!", COLOR_DRAW, discord.ButtonStyle.secondary
            elif RPS_BEATS[choice] == bot_choice:
                outcome, title, color, style = "win", "🎉 You win!", COLOR_WIN, discord.ButtonStyle.success
            else:
                outcome, title, color, style = "loss", "💀 You lose!", COLOR_LOSE, discord.ButtonStyle.danger
            button.style = style  # highlight what the player picked

            text = (f"You: {RPS_CHOICES[choice]} **{choice.capitalize()}**\n"
                    f"N7X Bot: {RPS_CHOICES[bot_choice]} **{bot_choice.capitalize()}**\n\n"
                    + finish_game(self.guild_id, self.player.id, outcome, RPS_WIN_XP if outcome == "win" else 0))
            await interaction.edit_original_response(embed=arcade_embed(self.player, title, text, color), view=self)
        return callback

    async def on_timeout(self):
        if self.finished or self.message is None:
            return
        self.finished = True
        self.disable_all()
        try:
            await self.message.edit(embed=arcade_embed(self.player, "⌛ Game expired",
                                                       "No pick in time. No game played.", COLOR_DRAW), view=self)
        except discord.HTTPException:
            pass


class TriviaView(PlayerView):
    def __init__(self, player, guild_id, question):
        super().__init__(player, TRIVIA_SECONDS)
        self.guild_id = guild_id
        self.question = question  # with shuffled "choices" and the matching "answer" index
        self.buttons = []
        for index, letter in enumerate(LETTERS):
            button = discord.ui.Button(label=letter, style=discord.ButtonStyle.secondary)
            button.callback = self.make_callback(index)
            self.add_item(button)
            self.buttons.append(button)

    def reveal(self, picked=None):
        """Disable the buttons, mark the right answer green and a wrong pick red."""
        self.disable_all()
        self.buttons[self.question["answer"]].style = discord.ButtonStyle.success
        if picked is not None and picked != self.question["answer"]:
            self.buttons[picked].style = discord.ButtonStyle.danger

    def result_text(self, headline):
        q = self.question
        right = q["answer"]
        lines = [f"**{q['question']}**", ""]
        for index, choice in enumerate(q["choices"]):
            mark = " ✅" if index == right else ""
            lines.append(f"{LETTER_EMOJI[LETTERS[index]]} {choice}{mark}")
        lines.append("")
        lines.append(headline)
        if q["explain"]:
            lines.append(f"💡 {q['explain']}")
        return "\n".join(lines)

    def make_callback(self, index):
        async def callback(interaction: discord.Interaction):
            if self.finished:
                return
            self.finished = True
            self.stop()
            self.reveal(index)
            correct = index == self.question["answer"]
            outcome = "win" if correct else "loss"
            headline = ("🎉 **Correct!**" if correct
                        else f"❌ **Wrong!** The answer was **{LETTERS[self.question['answer']]}**.")
            xp_line = finish_game(self.guild_id, self.player.id, outcome, TRIVIA_WIN_XP if correct else 0)
            text = self.result_text(headline) + "\n\n" + xp_line
            title = "🧠 Big brain!" if correct else "🧠 Not this time"
            await interaction.response.edit_message(
                embed=arcade_embed(self.player, title, text, COLOR_WIN if correct else COLOR_LOSE), view=self)
        return callback

    async def on_timeout(self):
        if self.finished or self.message is None:
            return
        self.finished = True
        self.reveal()
        text = (self.result_text(f"⌛ **Time's up!** The answer was **{LETTERS[self.question['answer']]}**.")
                + "\n\n" + finish_game(self.guild_id, self.player.id, "loss", 0))
        try:
            await self.message.edit(embed=arcade_embed(self.player, "🧠 Too slow!", text, COLOR_LOSE), view=self)
        except discord.HTTPException:
            pass


def number_scale(number):
    """A little 1-13 scale with the current number marked: 1 ▫▫▫🔷▫▫▫▫▫▫▫▫▫ 13"""
    return "`1` " + "".join("🔷" if n == number else "▫️" for n in range(1, 14)) + " `13`"


class HighLowView(PlayerView):
    def __init__(self, player, guild_id):
        super().__init__(player, HIGHLOW_SECONDS)
        self.guild_id = guild_id
        self.number = secrets.randbelow(13) + 1
        self.streak = 0
        self.higher = discord.ui.Button(label="Higher", emoji="⬆️", style=discord.ButtonStyle.success)
        self.lower = discord.ui.Button(label="Lower", emoji="⬇️", style=discord.ButtonStyle.danger)
        self.higher.callback = self.make_callback(True)
        self.lower.callback = self.make_callback(False)
        self.add_item(self.higher)
        self.add_item(self.lower)
        self.update_buttons()

    def xp(self):
        return min(self.streak * HIGHLOW_XP_PER_GUESS, HIGHLOW_MAX_XP)

    def update_buttons(self):
        # Can't go higher than 13 or lower than 1.
        self.higher.disabled = self.number == 13
        self.lower.disabled = self.number == 1

    def board(self, note=""):
        text = (f"# 🃏 {self.number}\n{number_scale(self.number)}\n\n"
                f"🔥 Streak: **{self.streak}** · ✨ XP so far: **{self.xp()}**\n"
                f"Will the next number (1-13) be **higher** or **lower**?\n"
                f"⏱️ {HIGHLOW_SECONDS}s per guess")
        return (note + "\n\n" + text) if note else text

    def end(self, reason):
        """Finish the game: save it and build the final embed."""
        self.finished = True
        self.stop()
        self.disable_all()
        won = self.streak >= HIGHLOW_WIN_STREAK
        xp_line = finish_game(self.guild_id, self.player.id, "win" if won else "loss", self.xp(),
                              highlow_streak=self.streak)
        verdict = (f"🏆 Final streak: **{self.streak}** - that counts as a win!" if won
                   else f"Final streak: **{self.streak}** (a streak of {HIGHLOW_WIN_STREAK}+ counts as a win)")
        text = f"{reason}\n\n{verdict}\n\n{xp_line}"
        return arcade_embed(self.player, "🃏 Higher or Lower - game over", text, COLOR_WIN if won else COLOR_LOSE)

    def make_callback(self, guessed_higher):
        async def callback(interaction: discord.Interaction):
            if self.finished:
                return
            old = self.number
            new = secrets.randbelow(13) + 1
            self.number = new
            arrow = "⬆️ higher" if guessed_higher else "⬇️ lower"

            if new == old:
                note = f"🤝 You said {arrow}... it's **{new}** again! Free pass, the streak stays."
            elif (new > old) == guessed_higher:
                self.streak += 1
                note = f"✅ You said {arrow}: **{old}** → **{new}**. Nice!"
            else:
                embed = self.end(f"❌ You said {arrow}: **{old}** → **{new}**. Game over!")
                await interaction.response.edit_message(embed=embed, view=self)
                return

            self.update_buttons()
            await interaction.response.edit_message(
                embed=arcade_embed(self.player, "🃏 Higher or Lower", self.board(note), COLOR_GAME), view=self)
        return callback

    async def on_timeout(self):
        if self.finished or self.message is None:
            return
        embed = self.end(f"⌛ No guess in {HIGHLOW_SECONDS}s, so the game ended. Your streak still counts!")
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            pass


# =====================================================================
# The commands
# =====================================================================

class Games(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.recent_trivia = defaultdict(lambda: deque(maxlen=8))  # member -> recently asked questions

    # ------------------------------------------------------------------ /flip
    @app_commands.command(name="flip", description="🪙 Flip a coin: guess heads or tails")
    @app_commands.describe(guess="Your guess")
    @app_commands.choices(guess=[
        app_commands.Choice(name="Heads", value="heads"),
        app_commands.Choice(name="Tails", value="tails"),
    ])
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, COOLDOWN_SECONDS)
    async def flip(self, interaction: discord.Interaction, guess: app_commands.Choice[str]):
        player = interaction.user
        result = secrets.choice(["heads", "tails"])
        won = result == guess.value
        frames = [
            arcade_embed(player, "🪙 Flipping the coin...", f"You called **{guess.value}**\n\n# 🌕"),
            arcade_embed(player, "🪙 Spinning in the air...", f"You called **{guess.value}**\n\n# 🌗"),
            arcade_embed(player, "🪙 Coming down...", f"You called **{guess.value}**\n\n# 🌑"),
        ]
        face = "🌕 HEADS" if result == "heads" else "🌑 TAILS"
        text = (f"# {face}\nYou called **{guess.value}**.\n\n"
                + finish_game(interaction.guild.id, player.id, "win" if won else "loss", FLIP_WIN_XP if won else 0))
        final = arcade_embed(player, "🎉 You called it!" if won else "💀 Wrong call!", text,
                             COLOR_WIN if won else COLOR_LOSE)
        await animate(interaction, frames, final)

    # ------------------------------------------------------------------ /dice
    @app_commands.command(name="dice", description="🎲 Roll a die against N7X Bot: higher number wins")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, COOLDOWN_SECONDS)
    async def dice(self, interaction: discord.Interaction):
        player = interaction.user
        mine = secrets.randbelow(6) + 1
        bots = secrets.randbelow(6) + 1

        def rolling(title):
            # Random faces while "rolling" (just for show).
            return arcade_embed(player, title, f"**You**  vs  **N7X Bot**\n"
                                               f"# {secrets.choice(DICE_FACES)}  ⚔️  {secrets.choice(DICE_FACES)}")

        frames = [rolling("🎲 Rolling the dice..."), rolling("🎲 Tumbling..."), rolling("🎲 Almost...")]
        if mine > bots:
            outcome, title, color = "win", "🎉 You win!", COLOR_WIN
        elif mine == bots:
            outcome, title, color = "draw", "🤝 It's a draw!", COLOR_DRAW
        else:
            outcome, title, color = "loss", "💀 N7X Bot wins!", COLOR_LOSE
        text = (f"**You**  vs  **N7X Bot**\n# {DICE_FACES[mine - 1]}  ⚔️  {DICE_FACES[bots - 1]}\n"
                f"You rolled **{mine}**, N7X Bot rolled **{bots}**.\n\n"
                + finish_game(interaction.guild.id, player.id, outcome, DICE_WIN_XP if outcome == "win" else 0))
        await animate(interaction, frames, arcade_embed(player, title, text, color))

    # ------------------------------------------------------------------- /rps
    @app_commands.command(name="rps", description="✂️ Rock, paper, scissors against N7X Bot")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, COOLDOWN_SECONDS)
    async def rps(self, interaction: discord.Interaction):
        player = interaction.user
        view = RPSView(player, interaction.guild.id)
        embed = arcade_embed(player, "🪨📄✂️ Rock, paper, scissors!",
                             f"Pick your move below.\n⏱️ You have {RPS_SECONDS} seconds.")
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    # ----------------------------------------------------------------- /wheel
    @app_commands.command(name="wheel", description="🎡 Spin the lucky wheel for a chance at XP")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, COOLDOWN_SECONDS)
    async def wheel(self, interaction: discord.Interaction):
        player = interaction.user
        emoji, text, xp = spin_wheel()
        slots = " ".join(slot[1] for slot in WHEEL)

        def spinning(title):
            passing = secrets.choice(WHEEL)  # a random slot flashing past (just for show)
            return arcade_embed(player, title, f"{slots}\n\n# ▶ {passing[1]}")

        frames = [spinning("🎡 Spinning the wheel..."), spinning("🎡 Round and round..."), spinning("🎡 Slowing down...")]
        won = xp > 0
        description = (f"{slots}\n\n# ▶ {emoji}\n**{text}**" + (f" — worth {xp} XP" if won else "") + "\n\n"
                       + finish_game(interaction.guild.id, player.id, "win" if won else "loss", xp)
                       + "\n\n_Prizes are XP and bragging rights only._")
        final = arcade_embed(player, "🎡 The wheel stopped!", description, COLOR_WIN if won else COLOR_LOSE)
        await animate(interaction, frames, final)

    # ---------------------------------------------------------------- /trivia
    @app_commands.command(name="trivia", description="🧠 AI & Web3 trivia: pick A, B, C or D")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, COOLDOWN_SECONDS)
    async def trivia(self, interaction: discord.Interaction):
        player = interaction.user
        questions = load_trivia()
        if not questions:
            await interaction.response.send_message(
                "No trivia questions available. Check that `trivia.json` exists and is valid.", ephemeral=True)
            return

        # Avoid repeating the member's recent questions when possible.
        recent = self.recent_trivia[player.id]
        fresh = [q for q in questions if q["question"] not in recent] or questions
        picked = secrets.choice(fresh)
        recent.append(picked["question"])

        # Shuffle the answers (securely) and remember where the right one went.
        order = list(range(4))
        _shuffler.shuffle(order)
        question = {
            "question": picked["question"],
            "choices": [picked["choices"][i] for i in order],
            "answer": order.index(picked["answer"]),
            "explain": picked["explain"],
        }

        ends = int(time.time()) + TRIVIA_SECONDS
        lines = [f"**{question['question']}**", ""]
        lines += [f"{LETTER_EMOJI[letter]} {choice}" for letter, choice in zip(LETTERS, question["choices"])]
        lines += ["", f"⏱️ Time's up <t:{ends}:R> · ✨ {TRIVIA_WIN_XP} XP for the right answer"]
        view = TriviaView(player, interaction.guild.id, question)
        await interaction.response.send_message(embed=arcade_embed(player, "🧠 N7X Trivia", "\n".join(lines)),
                                                view=view)
        view.message = await interaction.original_response()

    # --------------------------------------------------------------- /highlow
    @app_commands.command(name="highlow", description="🃏 Higher or Lower: build the longest streak you can")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, COOLDOWN_SECONDS)
    async def highlow(self, interaction: discord.Interaction):
        player = interaction.user
        view = HighLowView(player, interaction.guild.id)
        intro = (f"Guess if the next number is higher or lower. Every right guess = +{HIGHLOW_XP_PER_GUESS} XP "
                 f"(max {HIGHLOW_MAX_XP}). One wrong guess ends the game.")
        await interaction.response.send_message(
            embed=arcade_embed(player, "🃏 Higher or Lower", view.board(intro)), view=view)
        view.message = await interaction.original_response()

    # ----------------------------------------------------------- /gameprofile
    @app_commands.command(name="gameprofile", description="🎮 Show a member's game profile: level, title, wins and streaks")
    @app_commands.describe(member="Whose profile (leave empty for yours)")
    @app_commands.guild_only()
    async def gameprofile(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        guild_id = interaction.guild.id
        profile = database.get_game_profile(guild_id, member.id, today())

        level, xp_in_level, xp_needed = level_from_xp(profile["total_xp"])
        title, upcoming = title_for_level(level)
        plays, wins = profile["plays"], profile["wins"]
        rate = f"{100 * wins / plays:.0f}%" if plays else "-"
        position = database.game_leaderboard_position(guild_id, member.id)

        embed = arcade_embed(member, f"{title}", f"Level **{level}**  ·  {progress_bar(xp_in_level, xp_needed)}  "
                                                 f"{xp_in_level:,}/{xp_needed:,} XP to level {level + 1}")
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="🏆 Wins", value=f"**{wins}** of {plays} games ({rate})")
        embed.add_field(name="✨ Game XP", value=f"**{profile['game_xp']:,}** total")
        embed.add_field(name="📅 Today",
                        value=f"{profile['game_xp_today']}/{DAILY_GAME_XP_CAP} game XP")
        embed.add_field(name="🔥 Best win streak",
                        value=f"**{profile['best_win_streak']}** (now: {profile['win_streak']})")
        embed.add_field(name="🃏 Best Higher/Lower", value=f"**{profile['best_highlow']}** in a row")
        embed.add_field(name="📊 Leaderboard", value=f"#{position}" if position else "Not ranked yet")
        if upcoming:
            embed.add_field(name="Next title", value=f"{upcoming[1]} at level {upcoming[0]}", inline=False)
        else:
            embed.add_field(name="Next title", value="You've reached the top title! 🌌", inline=False)
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------- /gamesleaderboard
    @app_commands.command(name="gamesleaderboard", description="🏆 Top 10 game players by wins")
    @app_commands.guild_only()
    async def gamesleaderboard(self, interaction: discord.Interaction):
        guild = interaction.guild
        rows = database.top_by_game_wins(guild.id, limit=10)
        if not rows:
            await interaction.response.send_message(
                "No games played yet! Try /flip, /dice, /rps, /wheel, /trivia or /highlow.", ephemeral=True)
            return

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for position, row in enumerate(rows, start=1):
            badge = medals.get(position, f"`#{position:>2}`")
            rate = 100 * row["wins"] / row["plays"]
            name = f"**<@{row['user_id']}>**" if position <= 3 else f"<@{row['user_id']}>"
            lines.append(f"{badge} {name}\n╰ 🏆 **{row['wins']}** wins · 🎮 {row['plays']} plays · 📈 {rate:.0f}%")
            if position == 3 and len(rows) > 3:
                lines.append("")  # small gap after the podium

        embed = discord.Embed(title="🏆 N7X Arcade Leaderboard", description="\n".join(lines), color=COLOR_GAME)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        position = database.game_leaderboard_position(guild.id, interaction.user.id)
        if position and position > len(rows):
            embed.add_field(name="You", value=f"#{position} - keep playing to reach the top 10!")
        embed.set_footer(text=FOOTER)
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(Games(bot))
