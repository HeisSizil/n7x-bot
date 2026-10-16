"""
cogs/voice.py - Voice time tracking.

How it works:
- Once a minute, the bot looks at every voice channel in every server.
- If a channel has at least 2 real people (bots don't count), everyone in it
  gets 1 minute of voice time.
- If someone is alone, they get nothing - sitting alone doesn't count.
- The server's AFK channel is ignored.

This "check every minute" approach is simple and can't get confused if the
bot restarts: at worst, a minute of time is missed.

Commands: /voicerank, /voiceleaderboard
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import database

log = logging.getLogger("n7x.voice")

# --- Settings you can tweak ---
CHECK_EVERY_SECONDS = 60  # how often we check voice channels
MIN_PEOPLE = 2            # how many real people must be in a channel for time to count


def format_duration(total_seconds):
    """Turn seconds into friendly text like '3h 25m' or '12m'."""
    hours, remainder = divmod(int(total_seconds), 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


class Voice(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.track_voice.start()  # start the once-a-minute loop

    def cog_unload(self):
        """Stop the loop if this cog is ever unloaded."""
        self.track_voice.cancel()

    @tasks.loop(seconds=CHECK_EVERY_SECONDS)
    async def track_voice(self):
        """Runs every minute: give voice time to people who aren't alone."""
        for guild in self.bot.guilds:
            # voice_channels + stage_channels covers every kind of voice channel.
            for channel in guild.voice_channels + guild.stage_channels:
                if channel == guild.afk_channel:
                    continue

                people = [member for member in channel.members if not member.bot]
                if len(people) < MIN_PEOPLE:
                    continue  # empty, or someone is alone: no time counts

                try:
                    database.add_voice_seconds(
                        guild.id, [member.id for member in people], CHECK_EVERY_SECONDS
                    )
                    log.info(
                        "Voice: +%ds for %d people in %s / #%s",
                        CHECK_EVERY_SECONDS, len(people), guild.name, channel.name,
                    )
                except Exception:
                    # Log and keep going - one failure shouldn't stop tracking.
                    log.exception("Could not save voice time for #%s", channel.name)

    @track_voice.before_loop
    async def before_track_voice(self):
        """Wait until the bot is connected before the first check."""
        await self.bot.wait_until_ready()

    @app_commands.command(name="voicerank", description="Show a member's voice time and position")
    @app_commands.describe(member="Whose voice time to show (leave empty for yourself)")
    @app_commands.guild_only()
    async def voicerank(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        stats = database.get_voice_stats(interaction.guild.id, member.id)

        if stats is None:
            await interaction.response.send_message(
                f"{member.display_name} has no voice time yet. "
                f"Join a voice channel with at least one other person to start!",
                ephemeral=True,
            )
            return

        embed = discord.Embed(title=f"Voice rank for {member.display_name}", color=config.BRAND_COLOR)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Voice time", value=format_duration(stats["seconds"]))
        embed.add_field(name="Position", value=f"#{stats['position']}")
        embed.set_footer(text="Time alone in a channel doesn't count.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="voiceleaderboard", description="Top 10 members by voice time")
    @app_commands.guild_only()
    async def voiceleaderboard(self, interaction: discord.Interaction):
        rows = database.top_by_voice(interaction.guild.id, limit=10)

        if not rows:
            await interaction.response.send_message("No voice time recorded yet!", ephemeral=True)
            return

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for position, row in enumerate(rows, start=1):
            badge = medals.get(position, f"**#{position}**")
            lines.append(f"{badge} <@{row['user_id']}> - {format_duration(row['seconds'])}")

        embed = discord.Embed(
            title="🎙️ N7X Voice Leaderboard",
            description="\n".join(lines),
            color=config.BRAND_COLOR,
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Voice(bot))
