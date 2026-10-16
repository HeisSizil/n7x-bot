"""
mod_tools.py - Shared moderation helpers, used by cogs/automod.py and cogs/moderation.py.

- send_mod_log(): post a note in the mod-log channel.
- warn_member(): save a warning, DM the member, post to mod-log, and
  SUGGEST (never take) further action when warnings pile up.

The bot only suggests and logs. Humans make the final call on bans.
"""

import logging
import time

import discord

import config
import database

log = logging.getLogger("n7x.mod")

# --- Settings you can tweak ---
SUGGEST_ACTION_AT = 3  # after this many warnings, mod-log suggests a human review

# Colors for mod-log notes
COLOR_WARN = 0xF1C40F    # yellow
COLOR_ACTION = 0xE67E22  # orange (kick, timeout)
COLOR_INFO = 0x95A5A6    # grey


def get_mod_log_channel(guild):
    """Return the mod-log channel, or None if it's not set up."""
    if not config.MOD_LOG_CHANNEL_ID:
        return None
    channel = guild.get_channel(config.MOD_LOG_CHANNEL_ID)
    return channel if isinstance(channel, discord.TextChannel) else None


async def send_mod_log(guild, embed):
    """Post an embed in the mod-log channel. Never crashes; logs problems instead."""
    channel = get_mod_log_channel(guild)
    if channel is None:
        log.warning("Mod-log not sent: set MOD_LOG_CHANNEL_ID in .env to a text channel in %s", guild.name)
        return
    try:
        # Mentions in the log are shown as names but never ping anyone.
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as error:
        log.warning("Couldn't post in mod-log #%s: %s (check the bot can Send Messages + Embed Links there)",
                    channel.name, error)


async def warn_member(guild, member, moderator, reason, extra_fields=()):
    """
    Give `member` a warning.
    - Saves it to the database.
    - Sends the member a DM (if their DMs are open).
    - Posts a note in mod-log, with a suggestion if they have many warnings.
    Returns (total warnings, whether the DM was delivered).
    """
    total = database.add_warning(guild.id, member.id, moderator.id, reason, time.time())

    # 1. Tell the member privately.
    dm_sent = True
    try:
        dm = discord.Embed(
            title=f"⚠️ You received a warning in {guild.name}",
            description=f"**Reason:** {reason}\n\nPlease read the server rules. Repeated warnings may lead to action by the mods.",
            color=COLOR_WARN,
        )
        await member.send(embed=dm)
    except discord.HTTPException:
        dm_sent = False  # DMs closed - that's fine, the warning still counts

    # 2. Tell the mods.
    note = discord.Embed(title="⚠️ Warning", color=COLOR_WARN, timestamp=discord.utils.utcnow())
    note.add_field(name="Member", value=f"{member.mention} ({member})")
    note.add_field(name="By", value=moderator.mention)
    note.add_field(name="Total warnings", value=str(total))
    note.add_field(name="Reason", value=reason, inline=False)
    for name, value in extra_fields:
        note.add_field(name=name, value=value, inline=False)
    if not dm_sent:
        note.set_footer(text="Couldn't DM the member (their DMs are closed).")
    if total >= SUGGEST_ACTION_AT:
        note.add_field(
            name="💡 Suggestion",
            value=f"{member.mention} now has **{total} warnings**. A mod may want to review and consider "
                  f"`/timeout` or other action. The bot will not ban anyone - that's a human decision.",
            inline=False,
        )
    await send_mod_log(guild, note)

    log.info("Warned %s (%s) in %s: %s [total %d]", member, member.id, guild.name, reason, total)
    return total, dm_sent


def check_can_act(interaction, member, needs_bot_above=True):
    """
    Safety checks before a mod acts on a member.
    Returns an error message to show the mod, or None if it's OK.
    """
    guild = interaction.guild
    moderator = interaction.user

    if member.id == moderator.id:
        return "You can't do that to yourself."
    if member.id == guild.me.id:
        return "I can't do that to myself."
    if member.id == guild.owner_id:
        return "You can't do that to the server owner."
    # Mods can only act on members with a lower top role (the owner can act on anyone).
    if moderator.id != guild.owner_id and member.top_role >= moderator.top_role:
        return f"{member.display_name} has the same or a higher role than you."
    # For kick/timeout, the BOT's role must be above the member's role, or Discord refuses.
    if needs_bot_above and member.top_role >= guild.me.top_role:
        return (f"My role must be above {member.display_name}'s top role. "
                f"Move the bot's role higher in Server Settings → Roles.")
    return None
