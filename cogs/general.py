"""
cogs/general.py - Basic commands: /ping and /help.

/help builds its list automatically from every slash command the bot has,
so new commands show up in /help without editing this file.
"""

import discord
from discord import app_commands
from discord.ext import commands

import config


class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Check that the bot is online")
    async def ping(self, interaction: discord.Interaction):
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"Pong! 🏓 ({latency_ms} ms)")

    @app_commands.command(name="help", description="List all bot commands")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="N7X Bot Commands",
            description="Your guide to AI agents & Web3 in the N7X community.",
            color=config.BRAND_COLOR,
        )

        # Group commands by the cog (feature) they belong to.
        sections = {}
        for command in self.bot.tree.get_commands():
            cog_name = command.binding.__class__.__name__ if getattr(command, "binding", None) else "Other"
            sections.setdefault(cog_name, []).append(f"`/{command.name}` - {command.description}")

        for cog_name, lines in sorted(sections.items()):
            embed.add_field(name=cog_name, value="\n".join(sorted(lines)), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    """discord.py calls this when loading the cog."""
    await bot.add_cog(General(bot))
