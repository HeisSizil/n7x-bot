"""
cogs/moderation.py - Slash commands for mods.

Every command checks Discord permissions, so only mods can use them:
- /warn, /warnings, /clearwarnings, /timeout, /untimeout  need "Timeout Members" (Moderate Members)
- /kick                                       needs "Kick Members"
- /summarize                                  needs "Manage Messages"

They're also hidden from regular members' command list.
The real work (and all safety checks) lives in mod_tools.py, shared with
the AI mod assistant (cogs/mod_assistant.py).
There is deliberately no /ban: the bot suggests, humans decide.
"""

import discord
from discord import app_commands
from discord.ext import commands

import mod_tools
from ai_providers import AIClient


async def reply(interaction, result):
    """Send an action's result (text or embed) privately to the mod."""
    _, content = result
    kwargs = {"ephemeral": True, "allowed_mentions": discord.AllowedMentions.none()}
    if isinstance(content, discord.Embed):
        kwargs["embed"] = content
    else:
        kwargs["content"] = content
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ai = AIClient()

    async def cog_load(self):
        await self.ai.start()

    async def cog_unload(self):
        await self.ai.close()

    @app_commands.command(name="warn", description="[Mods] Warn a member")
    @app_commands.describe(member="Who to warn", reason="Why (the member will see this)")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)   # hidden from non-mods
    @app_commands.checks.has_permissions(moderate_members=True)  # enforced
    async def warn(self, interaction: discord.Interaction, member: discord.Member,
                   reason: app_commands.Range[str, 1, 300]):
        await interaction.response.defer(ephemeral=True)  # DMing the member can take a moment
        await reply(interaction, await mod_tools.do_warn(interaction.guild, interaction.user, member, reason))

    @app_commands.command(name="warnings", description="[Mods] Show a member's warnings")
    @app_commands.describe(member="Whose warnings to show")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        await reply(interaction, mod_tools.build_warnings_embed(interaction.guild, interaction.user, member))

    @app_commands.command(name="clearwarnings", description="[Mods] Clear all of a member's warnings, or one by number")
    @app_commands.describe(
        member="Whose warnings to clear",
        number="Only clear this warning (the # shown in /warnings). Leave empty to clear ALL.",
        reason="Why (posted in mod-log)",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def clearwarnings(self, interaction: discord.Interaction, member: discord.Member,
                            number: app_commands.Range[int, 1, 10000] = None,
                            reason: app_commands.Range[str, 1, 300] = None):
        await reply(interaction, await mod_tools.do_clear_warnings(
            interaction.guild, interaction.user, member, number, reason))

    @app_commands.command(name="kick", description="[Mods] Kick a member (they can rejoin with an invite)")
    @app_commands.describe(member="Who to kick", reason="Why (saved in the audit log and mod-log)")
    @app_commands.guild_only()
    @app_commands.default_permissions(kick_members=True)
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member,
                   reason: app_commands.Range[str, 1, 300]):
        await reply(interaction, await mod_tools.do_kick(interaction.guild, interaction.user, member, reason))

    @app_commands.command(name="timeout", description="[Mods] Stop a member from chatting for a while")
    @app_commands.describe(
        member="Who to time out",
        minutes="How long, in minutes (max 40320 = 28 days)",
        reason="Why (saved in the audit log and mod-log)",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(self, interaction: discord.Interaction, member: discord.Member,
                      minutes: app_commands.Range[int, 1, mod_tools.MAX_TIMEOUT_MINUTES],
                      reason: app_commands.Range[str, 1, 300]):
        await reply(interaction, await mod_tools.do_timeout(
            interaction.guild, interaction.user, member, minutes, reason))

    @app_commands.command(name="untimeout", description="[Mods] End a member's timeout early")
    @app_commands.describe(member="Whose timeout to remove", reason="Why (posted in mod-log)")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member,
                        reason: app_commands.Range[str, 1, 300] = None):
        # Same shared code as the mod assistant's remove_timeout tool.
        await reply(interaction, await mod_tools.do_remove_timeout(interaction.guild, interaction.user, member, reason))

    @app_commands.command(name="summarize", description="[Mods] AI summary of a channel's last 50 messages")
    @app_commands.describe(channel="Which channel (leave empty for this one)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.cooldown(1, 30)  # AI is limited; once every 30s per mod
    async def summarize(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        # Only the mod sees the summary. Defer first: reading + AI can take a while.
        await interaction.response.defer(ephemeral=True, thinking=True)
        await reply(interaction, await mod_tools.summarize_channel(
            self.ai, interaction.guild, interaction.user, channel or interaction.channel))


async def setup(bot):
    await bot.add_cog(Moderation(bot))
