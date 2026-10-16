"""
cogs/welcome.py - Welcomes new members with a branded embed.

- Posts in the channel set by WELCOME_CHANNEL_ID in .env.
- The text comes from welcome.md (edit it anytime, no restart needed).
  {member} in the text becomes the new member's @mention.
- Thumbnail: WELCOME_IMAGE_URL if set, otherwise the server's icon.
- Only the new member is pinged. @everyone and roles are never pinged.
- If the bot can't post, it logs one short line and keeps running.

Commands: /testwelcome (mods only) - preview the welcome message.
"""

import logging
import os
import re

import discord
from discord import app_commands
from discord.ext import commands

import config

log = logging.getLogger("n7x.welcome")

WELCOME_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "welcome.md")
DEFAULT_TEXT = (
    "Welcome to N7X, {member}! A community focused on AI agents, Web3 innovation, and next-generation "
    "intelligent applications. Connect, collaborate, and help build the future of AI and Web3."
)
FOOTER = "N7X Bot"


def load_welcome_text():
    """Read welcome.md fresh, without the <!-- comment --> part. Falls back to the default text."""
    try:
        with open(WELCOME_FILE, encoding="utf-8") as file:
            text = file.read()
    except OSError:
        log.warning("welcome.md not found - using the default welcome text")
        return DEFAULT_TEXT
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
    return text or DEFAULT_TEXT


def build_welcome(member):
    """Make the welcome embed for `member`."""
    # Plain replace (not .format) so other {curly braces} in the text can't break anything.
    text = load_welcome_text().replace("{member}", member.mention)

    embed = discord.Embed(title="👋 Welcome to N7X!", description=text[:4000], color=config.BRAND_COLOR)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

    image = config.WELCOME_IMAGE_URL
    if image.startswith("https://"):
        embed.set_thumbnail(url=image)
    elif member.guild.icon:
        embed.set_thumbnail(url=member.guild.icon.url)

    embed.set_footer(text=FOOTER)
    return embed


def welcome_mentions(member):
    """Only the new member may be pinged - never @everyone, @here or roles."""
    return discord.AllowedMentions(everyone=False, roles=False, users=[member])


def get_welcome_channel(guild):
    """The welcome channel in this server, or None."""
    if not config.WELCOME_CHANNEL_ID:
        return None
    channel = guild.get_channel(config.WELCOME_CHANNEL_ID)
    return channel if isinstance(channel, discord.TextChannel) else None


class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        if not config.WELCOME_CHANNEL_ID:
            log.info("WELCOME_CHANNEL_ID is not set - welcome messages are off")
        if config.WELCOME_IMAGE_URL and not config.WELCOME_IMAGE_URL.startswith("https://"):
            log.warning("WELCOME_IMAGE_URL must start with https:// - using the server icon instead")

    async def send_welcome(self, member):
        """Post the welcome. Returns (ok, short reason)."""
        channel = get_welcome_channel(member.guild)
        if channel is None:
            return False, "the welcome channel isn't set or isn't a text channel in this server"
        try:
            # The mention in the message text is what notifies the new member
            # (mentions inside embeds don't send notifications).
            await channel.send(content=member.mention, embed=build_welcome(member),
                               allowed_mentions=welcome_mentions(member))
        except discord.HTTPException as error:
            return False, f"can't post in #{channel.name} ({error.status}): give the bot View Channel, Send Messages and Embed Links there"
        return True, f"posted in #{channel.name}"

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot or not config.WELCOME_CHANNEL_ID:
            return
        if get_welcome_channel(member.guild) is None:
            return  # the welcome channel belongs to another server (or is missing)
        ok, reason = await self.send_welcome(member)
        if ok:
            log.info("Welcomed member %s in %s", member.id, member.guild.name)
        else:
            log.warning("Welcome not sent for member %s: %s", member.id, reason)

    @app_commands.command(name="testwelcome", description="[Mods] Preview the welcome message (as if you just joined)")
    @app_commands.describe(post_in_channel="Also post it in the real welcome channel (to test permissions)")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)   # hidden from non-mods
    @app_commands.checks.has_permissions(moderate_members=True)  # enforced
    async def testwelcome(self, interaction: discord.Interaction, post_in_channel: bool = False):
        await interaction.response.defer(ephemeral=True, thinking=True)  # posting can take a moment
        member = interaction.user
        channel = get_welcome_channel(interaction.guild)
        if channel is None:
            status = "⚠️ Welcome messages are **off**: set `WELCOME_CHANNEL_ID` in `.env` to a text channel and restart."
        else:
            perms = channel.permissions_for(interaction.guild.me)
            missing = [name for name, ok in (("View Channel", perms.view_channel), ("Send Messages", perms.send_messages),
                                             ("Embed Links", perms.embed_links)) if not ok]
            status = (f"✅ New members are welcomed in {channel.mention}." if not missing
                      else f"⚠️ I can't post in {channel.mention}. Missing: **{', '.join(missing)}**.")

        if post_in_channel and channel is not None:
            ok, reason = await self.send_welcome(member)
            status += f"\n{'✅ Test welcome ' + reason if ok else '❌ Not sent: ' + reason}."

        # The preview is private, so it never pings anyone.
        await interaction.followup.send(
            content=f"**Preview** (edit the text in `welcome.md`):\n{status}",
            embed=build_welcome(member), ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot):
    await bot.add_cog(Welcome(bot))
