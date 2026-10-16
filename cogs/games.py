"""
cogs/games.py - Mini games: /flip, /dice, /rps, /wheel and /gamesleaderboard.

- Results use Python's `secrets` module (cryptographically secure randomness),
  so nobody can predict or rig them.
- Wins give a little XP that goes into the SAME XP as chatting, so games count
  toward /rank. Game XP is capped at DAILY_GAME_XP_CAP per member per day (UTC).
- Each game has a 5-second cooldown per member.
- Prizes are XP and bragging rights ONLY. No money, tokens or crypto - ever.

Commands: /flip, /dice, /rps, /wheel, /gamesleaderboard
"""

import asyncio
import secrets
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import config
import database

# --- Settings you can tweak ---
DAILY_GAME_XP_CAP = 100   # max game XP per member per day
COOLDOWN_SECONDS = 5      # per member, per game
FLIP_WIN_XP = 5
DICE_WIN_XP = 5
RPS_WIN_XP = 8
RPS_SECONDS = 30          # how long the rock/paper/scissors buttons wait for a pick
FOOTER = "N7X Bot"

# Lucky wheel: (weight, emoji + text, XP). Higher weight = more likely.
# A spin "wins" if it gives XP. Weights add up to 100, so weight = % chance.
WHEEL = [
    (4,  "💎 JACKPOT! Diamond hands pay off", 20),
    (10, "🚀 To the moon!", 12),
    (16, "🤖 Your AI agent found alpha", 8),
    (20, "🍀 Lucky charm", 5),
    (15, "⛽ Gas refund (in XP, sadly)", 3),
    (15, "😅 Rugged! Only vibes this time", 0),
    (12, "🧻 Paper hands. Try again!", 0),
    (8,  "🐻 The bear market got you", 0),
]

RPS_CHOICES = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
RPS_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


def today():
    """Today's date in UTC, e.g. '2026-10-16' (the daily XP cap resets at 00:00 UTC)."""
    return datetime.now(timezone.utc).date().isoformat()


def spin_wheel():
    """Pick a wheel slot fairly, using the weights."""
    total = sum(weight for weight, _, _ in WHEEL)
    pick = secrets.randbelow(total)
    for weight, text, xp in WHEEL:
        if pick < weight:
            return text, xp
        pick -= weight
    return WHEEL[-1][1], WHEEL[-1][2]  # never reached


def xp_line(won, xp_reward, given, earned_today):
    """The XP part of a result, e.g. '+5 XP (35/100 game XP today)'."""
    if not won:
        return f"No XP this time ({earned_today}/{DAILY_GAME_XP_CAP} game XP today)"
    if given == 0:
        return f"Daily game XP cap reached ({DAILY_GAME_XP_CAP}/{DAILY_GAME_XP_CAP}). Come back tomorrow!"
    capped = " (capped)" if given < xp_reward else ""
    return f"**+{given} XP**{capped} ({earned_today}/{DAILY_GAME_XP_CAP} game XP today)"


def game_embed(title, description, color):
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text=FOOTER)
    return embed


def save_result(interaction, won, xp_reward):
    """Record the game and give capped XP. Returns the XP line for the embed."""
    given, earned_today = database.record_game(
        interaction.guild.id, interaction.user.id, won, xp_reward, today(), DAILY_GAME_XP_CAP
    )
    return xp_line(won, xp_reward, given, earned_today)


WIN_COLOR = 0x16C784
LOSE_COLOR = 0xEA3943
DRAW_COLOR = 0x95A5A6


class RPSView(discord.ui.View):
    """Rock / Paper / Scissors buttons. Only the player who started can press them."""

    def __init__(self, player):
        super().__init__(timeout=RPS_SECONDS)
        self.player = player
        self.message = None
        self.done = False
        for choice, emoji in RPS_CHOICES.items():
            button = discord.ui.Button(label=choice.capitalize(), emoji=emoji, style=discord.ButtonStyle.primary)
            button.callback = self.make_callback(choice)
            self.add_item(button)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.player.id:
            await interaction.response.send_message("This isn't your game! Start your own with /rps.", ephemeral=True)
            return False
        return True

    def make_callback(self, choice):
        async def callback(interaction: discord.Interaction):
            if self.done:
                return
            self.done = True
            self.stop()
            bot_choice = secrets.choice(list(RPS_CHOICES))  # picked only now, fairly

            if choice == bot_choice:
                won, title, color = False, "🤝 It's a draw!", DRAW_COLOR
            elif RPS_BEATS[choice] == bot_choice:
                won, title, color = True, "🎉 You win!", WIN_COLOR
            else:
                won, title, color = False, "😢 You lose!", LOSE_COLOR

            text = (f"You: {RPS_CHOICES[choice]} **{choice}**\n"
                    f"N7X Bot: {RPS_CHOICES[bot_choice]} **{bot_choice}**\n\n"
                    + save_result(interaction, won, RPS_WIN_XP))
            await interaction.response.edit_message(embed=game_embed(title, text, color), view=None)
        return callback

    async def on_timeout(self):
        if self.done or self.message is None:
            return
        self.done = True
        try:
            await self.message.edit(
                embed=game_embed("⌛ Game expired", f"{self.player.mention} didn't pick in time. No game played.",
                                 DRAW_COLOR),
                view=None,
            )
        except discord.HTTPException:
            pass


class Games(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------ /flip
    @app_commands.command(name="flip", description="Flip a coin: guess heads or tails")
    @app_commands.describe(guess="Your guess")
    @app_commands.choices(guess=[
        app_commands.Choice(name="Heads", value="heads"),
        app_commands.Choice(name="Tails", value="tails"),
    ])
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, COOLDOWN_SECONDS)
    async def flip(self, interaction: discord.Interaction, guess: app_commands.Choice[str]):
        result = secrets.choice(["heads", "tails"])
        won = result == guess.value
        title = "🎉 You guessed right!" if won else "😢 Wrong guess!"
        text = (f"You picked **{guess.value}**. The coin landed on **{result}** 🪙\n\n"
                + save_result(interaction, won, FLIP_WIN_XP))
        await interaction.response.send_message(embed=game_embed(title, text, WIN_COLOR if won else LOSE_COLOR))

    # ------------------------------------------------------------------ /dice
    @app_commands.command(name="dice", description="Roll a die against N7X Bot: higher number wins")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, COOLDOWN_SECONDS)
    async def dice(self, interaction: discord.Interaction):
        player_roll = secrets.randbelow(6) + 1   # 1-6
        bot_roll = secrets.randbelow(6) + 1
        if player_roll > bot_roll:
            won, title, color = True, "🎉 You win!", WIN_COLOR
        elif player_roll == bot_roll:
            won, title, color = False, "🤝 It's a draw!", DRAW_COLOR
        else:
            won, title, color = False, "😢 N7X Bot wins!", LOSE_COLOR
        text = (f"🎲 You rolled **{player_roll}**\n🎲 N7X Bot rolled **{bot_roll}**\n\n"
                + save_result(interaction, won, DICE_WIN_XP))
        await interaction.response.send_message(embed=game_embed(title, text, color))

    # ------------------------------------------------------------------- /rps
    @app_commands.command(name="rps", description="Rock, paper, scissors against N7X Bot")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, COOLDOWN_SECONDS)
    async def rps(self, interaction: discord.Interaction):
        view = RPSView(interaction.user)
        embed = game_embed("🪨📄✂️ Rock, paper, scissors!",
                           f"{interaction.user.mention}, pick one below. You have {RPS_SECONDS} seconds.",
                           config.BRAND_COLOR)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    # ----------------------------------------------------------------- /wheel
    @app_commands.command(name="wheel", description="Spin the lucky wheel for a chance at XP")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, COOLDOWN_SECONDS)
    async def wheel(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=game_embed("🎡 Spinning the wheel...", "Round and round it goes...", config.BRAND_COLOR))
        await asyncio.sleep(2)  # a little suspense

        text, xp = spin_wheel()
        won = xp > 0
        description = (f"**{text}**" + (f" — worth {xp} XP" if won else "") + "\n\n"
                       + save_result(interaction, won, xp)
                       + "\n\n_Prizes are XP and bragging rights only._")
        await interaction.edit_original_response(
            embed=game_embed("🎡 The wheel stopped!", description, WIN_COLOR if won else LOSE_COLOR))

    # ------------------------------------------------------- /gamesleaderboard
    @app_commands.command(name="gamesleaderboard", description="Top 10 game players by wins")
    @app_commands.guild_only()
    async def gamesleaderboard(self, interaction: discord.Interaction):
        rows = database.top_by_game_wins(interaction.guild.id, limit=10)
        if not rows:
            await interaction.response.send_message("No games played yet! Try /flip, /dice, /rps or /wheel.",
                                                    ephemeral=True)
            return

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for position, row in enumerate(rows, start=1):
            badge = medals.get(position, f"**#{position}**")
            rate = 100 * row["wins"] / row["plays"]
            lines.append(f"{badge} <@{row['user_id']}> - **{row['wins']}** wins · "
                         f"{row['plays']} plays · {rate:.0f}% win rate")

        embed = game_embed("🎮 N7X Games Leaderboard", "\n".join(lines), config.BRAND_COLOR)
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Games(bot))
