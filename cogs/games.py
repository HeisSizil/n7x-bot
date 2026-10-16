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
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import config
import database
import trivia_bank
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

TRIVIA_REVEAL_SECONDS = 2    # how long the answer + explanation stay up before the next question
TRIVIA_MAX_TIMEOUTS = 2      # this many unanswered questions in a row end the quiz
TRIVIA_WIN_PERCENT = 60      # a quiz counts as a win from this score on
# Results card ratings: (minimum %, rating)
TRIVIA_RATINGS = [(90, "🧠 N7X Genius"), (70, "🔥 Pro"), (40, "📈 Getting there"), (0, "💪 Keep practicing")]

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


def load_trivia():
    """Every local trivia question (all categories). Empty list = no questions available."""
    return trivia_bank.pool("mixed")


def question_image(question):
    """(image url for the embed, discord.File to attach or None) for a question's optional picture."""
    image = question.get("image") or ""
    if not image:
        return None, None
    if image.startswith(("http://", "https://")):
        return image, None
    path = os.path.join(trivia_bank.IMAGES_DIR, image)
    if not os.path.isfile(path):
        return None, None
    return f"attachment://{image}", discord.File(path, filename=image)


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


class QuizView(PlayerView):
    """
    A /trivia quiz in ONE message: each question is edited in, with A-D buttons and a timer.
    Only the player can press. Two unanswered questions in a row end the quiz.
    """

    def __init__(self, player, guild_id, questions, category_label, asked_for):
        super().__init__(player, timeout=None)   # the quiz loop handles the time itself
        self.guild_id = guild_id
        self.questions = questions
        self.category_label = category_label
        self.asked_for = asked_for
        self.number = 0              # current question (0-based)
        self.score = 0
        self.marks = []              # ✅ ❌ ⌛ per question
        self.timeouts_in_row = 0
        self.pick = None
        self.accepting = False
        self.answered = asyncio.Event()
        self.buttons = []
        for index, letter in enumerate(LETTERS):
            button = discord.ui.Button(label=letter, emoji=LETTER_EMOJI[letter], style=discord.ButtonStyle.secondary)
            button.callback = self.make_callback(index)
            self.add_item(button)
            self.buttons.append(button)

    @property
    def question(self):
        return self.questions[self.number]

    def header(self):
        return f"Question {self.number + 1}/{len(self.questions)} • Score {self.score}"

    def question_embed(self, footer_line):
        q = self.question
        lines = [f"**{q['question']}**", ""]
        lines += [f"{LETTER_EMOJI[letter]} {choice}" for letter, choice in zip(LETTERS, q["choices"])]
        lines += ["", footer_line]
        embed = arcade_embed(self.player, f"🧠 {self.header()}", "\n".join(lines))
        embed.add_field(name="Category", value=self.category_label)
        image_url, file = question_image(q)
        if image_url:
            embed.set_image(url=image_url)
        return embed, file

    def reveal_embed(self, headline, color):
        q = self.question
        right = q["answer"]
        lines = [f"**{q['question']}**", ""]
        for index, choice in enumerate(q["choices"]):
            lines.append(f"{LETTER_EMOJI[LETTERS[index]]} {choice}{' ✅' if index == right else ''}")
        lines += ["", headline]
        if q["explain"]:
            lines.append(f"💡 {q['explain']}")
        embed = arcade_embed(self.player, f"🧠 {self.header()}", "\n".join(lines), color)
        embed.add_field(name="Category", value=self.category_label)
        image_url, _ = question_image(q)
        if image_url:
            embed.set_image(url=image_url)
        return embed

    def set_buttons(self, picked=None, reveal=False):
        for index, button in enumerate(self.buttons):
            button.disabled = reveal
            button.style = discord.ButtonStyle.secondary
            if reveal and index == self.question["answer"]:
                button.style = discord.ButtonStyle.success
            elif reveal and index == picked:
                button.style = discord.ButtonStyle.danger

    def make_callback(self, index):
        async def callback(interaction: discord.Interaction):
            if not self.accepting:
                await interaction.response.send_message("⌛ Too late for this question.", ephemeral=True)
                return
            self.accepting = False          # set before any await, so a double click can't count twice
            self.pick = index
            correct = index == self.question["answer"]
            if correct:
                self.score += 1
                headline, color = "🎉 **Correct!**", COLOR_WIN
            else:
                headline, color = f"❌ **Wrong!** The answer was **{LETTERS[self.question['answer']]}**.", COLOR_LOSE
            self.marks.append("✅" if correct else "❌")
            self.timeouts_in_row = 0
            self.set_buttons(index, reveal=True)
            await interaction.response.edit_message(embed=self.reveal_embed(headline, color), view=self)
            self.answered.set()
        return callback

    async def edit(self, **kwargs):
        try:
            await self.message.edit(**kwargs)
        except discord.HTTPException as error:
            log.warning("Trivia: couldn't update the quiz message (%s)", error.status)

    async def run(self):
        """Ask every question, then show the results card. Returns the outcome ("win"/"loss")."""
        ended_early = False
        for number in range(len(self.questions)):
            self.number = number
            self.pick = None
            self.answered.clear()
            self.set_buttons()
            ends = int(time.time()) + TRIVIA_SECONDS
            embed, file = self.question_embed(f"⏱️ Time's up <t:{ends}:R> · ✨ {TRIVIA_WIN_XP} XP per right answer")
            await self.edit(embed=embed, view=self, attachments=[file] if file else [])
            self.accepting = True
            try:
                await asyncio.wait_for(self.answered.wait(), TRIVIA_SECONDS)
            except asyncio.TimeoutError:
                pass
            self.accepting = False
            if self.pick is None:
                self.marks.append("⌛")
                self.timeouts_in_row += 1
                self.set_buttons(reveal=True)
                await self.edit(embed=self.reveal_embed(
                    f"⌛ **Time's up!** The answer was **{LETTERS[self.question['answer']]}**.", COLOR_DRAW), view=self)
            if self.timeouts_in_row >= TRIVIA_MAX_TIMEOUTS:
                ended_early = True
                break
            if self.number + 1 < len(self.questions):
                await asyncio.sleep(TRIVIA_REVEAL_SECONDS)

        self.finished = True
        self.stop()
        self.disable_all()
        asked = len(self.marks)
        percent = round(100 * self.score / asked) if asked else 0
        outcome = "win" if percent >= TRIVIA_WIN_PERCENT else "loss"
        rating = next(name for minimum, name in TRIVIA_RATINGS if percent >= minimum)
        lines = [f"**{rating}**", "",
                 f"🎯 Score: **{self.score}/{asked}** ({percent}%)",
                 f"📚 {self.category_label}",
                 " ".join(self.marks)]
        if ended_early:
            lines.append(f"\n⏹️ Quiz ended: {TRIVIA_MAX_TIMEOUTS} questions in a row without an answer.")
        if len(self.questions) < self.asked_for:
            lines.append(f"\nℹ️ Only {len(self.questions)} question(s) were available (you asked for {self.asked_for}).")
        lines += ["", finish_game(self.guild_id, self.player.id, outcome, TRIVIA_WIN_XP * self.score)]
        title = "🏁 Quiz complete!" if not ended_early else "🏁 Quiz over"
        color = COLOR_WIN if outcome == "win" else COLOR_LOSE
        await self.edit(embed=arcade_embed(self.player, title, "\n".join(lines), color), view=self, attachments=[])
        return outcome


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
        self.recent_trivia = defaultdict(lambda: deque(maxlen=100))  # member -> recently asked questions
        self.http = None   # aiohttp session, only made if Open Trivia DB is turned on

    async def cog_unload(self):
        if self.http:
            await self.http.close()

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
    @app_commands.command(name="trivia", description="🧠 Trivia quiz: pick a category and how many questions")
    @app_commands.describe(questions="How many questions (1-20, default 5)", category="Topic (default: Mixed)")
    @app_commands.choices(category=[app_commands.Choice(name=label, value=key)
                                    for key, (label, _) in trivia_bank.CATEGORIES.items()])
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, COOLDOWN_SECONDS)
    async def trivia(self, interaction: discord.Interaction, questions: app_commands.Range[int, 1, 20] = 5,
                     category: app_commands.Choice[str] = None):
        player = interaction.user
        key = category.value if category else "mixed"
        label = trivia_bank.CATEGORIES[key][0]
        if not trivia_bank.has_questions(key):
            await interaction.response.send_message(
                f"No {label} questions available right now. Try another category!", ephemeral=True)
            return

        # Answer Discord first (it only waits 3 seconds), then get the questions.
        await interaction.response.send_message(embed=arcade_embed(
            player, "🧠 N7X Trivia", f"Getting **{questions}** {label} question(s) ready... 🎲"))
        message = await interaction.original_response()

        session = None
        if config.OPENTDB_ENABLED:
            if self.http is None or self.http.closed:
                import aiohttp
                self.http = aiohttp.ClientSession()
            session = self.http
        recent = self.recent_trivia[player.id]
        picked = await trivia_bank.get_questions(key, questions, recent, session, config.OPENTDB_ENABLED)
        if not picked:
            await message.edit(embed=arcade_embed(player, "🧠 N7X Trivia", f"No {label} questions available. "
                                                  "Try another category!", COLOR_DRAW))
            return
        recent.extend(q["question"] for q in picked)

        view = QuizView(player, interaction.guild.id, picked, label, questions)
        view.message = message
        log.info("Trivia quiz started: user=%s category=%s questions=%d", player.id, key, len(picked))
        await view.run()

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
