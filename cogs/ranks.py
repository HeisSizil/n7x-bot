"""
cogs/ranks.py - Message XP and levels.

How it works:
- Every message a member sends is counted.
- Members earn a little XP for chatting, but at most once every 60 seconds,
  so spamming doesn't farm XP.
- We never store what people say - only how many messages and how much XP.

Commands: /rank, /leaderboard
"""

import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
import database

log = logging.getLogger("n7x.ranks")
# --- Settings you can tweak ---
XP_COOLDOWN_SECONDS = 60  # how often someone can earn XP
XP_MIN = 15               # smallest XP reward per message
XP_MAX = 25               # largest XP reward per message


def xp_for_next_level(level):
    """XP needed to go from `level` to `level + 1`. Each level needs a bit more."""
    return 5 * level ** 2 + 50 * level + 100


def level_from_xp(total_xp):
    """
    Turn total XP into (level, xp_into_this_level, xp_needed_for_next_level).
    Example: 250 XP -> level 1, 150 XP into level 1, 155 needed for level 2.
    """
    level = 0
    while total_xp >= xp_for_next_level(level):
        total_xp -= xp_for_next_level(level)
        level += 1
    return level, total_xp, xp_for_next_level(level)


def progress_bar(current, total, length=10):
    """Make a little text bar like ▰▰▰▱▱▱▱▱▱▱"""
    filled = int(length * current / total) if total else 0
    return "▰" * filled + "▱" * (length - filled)


class Ranks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Runs for every message the bot can see."""
        who = f"{message.author} ({message.author.id})"

        # Ignore bots (including ourselves) and private DMs.
        if message.author.bot:
            log.info("Skipped message from %s: author is a bot", who)
            return
        if message.guild is None:
            log.info("Skipped message from %s: sent in a DM", who)
            return

        # NOTE: we deliberately do NOT look at message.content here.
        # Short messages, emoji-only messages, images and stickers all count.

        xp_amount = random.randint(XP_MIN, XP_MAX)
        try:
            got_xp = database.record_message(
                guild_id=message.guild.id,
                user_id=message.author.id,
                xp_amount=xp_amount,
                now=time.time(),
                cooldown_seconds=XP_COOLDOWN_SECONDS,
            )
        except Exception:
            log.exception("Could not save message count for %s", who)
            return

        # We log WHO and WHERE, but never WHAT they said.
        if got_xp:
            log.info("Counted message from %s in #%s: +%d XP", who, message.channel, xp_amount)
        else:
            log.info("Counted message from %s in #%s: no XP (cooldown)", who, message.channel)

    @app_commands.command(name="rank", description="Show a member's level, XP and position")
    @app_commands.describe(member="Whose rank to show (leave empty for yourself)")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        stats = database.get_message_stats(interaction.guild.id, member.id)

        if stats is None:
            await interaction.response.send_message(
                f"{member.display_name} hasn't chatted yet. Send a message to start earning XP!",
                ephemeral=True,
            )
            return

        level, xp_in_level, xp_needed = level_from_xp(stats["xp"])

        embed = discord.Embed(title=f"Rank for {member.display_name}", color=config.BRAND_COLOR)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Level", value=str(level))
        embed.add_field(name="Position", value=f"#{stats['position']}")
        embed.add_field(name="Messages", value=f"{stats['messages']:,}")
        embed.add_field(
            name="XP",
            value=f"{progress_bar(xp_in_level, xp_needed)}\n"
                  f"{xp_in_level:,} / {xp_needed:,} to level {level + 1} "
                  f"({stats['xp']:,} total)",
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Top 10 members by messages")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction):
        rows = database.top_by_messages(interaction.guild.id, limit=10)

        if not rows:
            await interaction.response.send_message("No one has chatted yet!", ephemeral=True)
            return

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for position, row in enumerate(rows, start=1):
            level, _, _ = level_from_xp(row["xp"])
            badge = medals.get(position, f"**#{position}**")
            # <@id> shows the member's name inside an embed without pinging them.
            lines.append(f"{badge} <@{row['user_id']}> - {row['messages']:,} messages (level {level})")

        embed = discord.Embed(
            title="🏆 N7X Message Leaderboard",
            description="\n".join(lines),
            color=config.BRAND_COLOR,
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Ranks(bot))
