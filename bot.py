"""
bot.py - The starting point. Run this file to start the bot:

    python bot.py

It does four things:
1. Creates the bot with only the permissions (intents) it needs.
2. Sets up the database.
3. Loads every feature file ("cog") from the cogs/ folder.
4. Catches errors so a broken command never crashes the bot.
"""

import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

import config
import database

# Show helpful messages in the terminal (e.g. "Logged in as ...").
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("n7x")


# --- Intents: what events Discord sends to the bot ---
# We start with the defaults and switch on the extras our features need.
# message_content and members are "privileged": you MUST also switch them on in the
# Developer Portal (your app -> Bot -> Privileged Gateway Intents), or the bot won't start.
intents = discord.Intents.default()
intents.message_content = True  # read message text (spam filter, /summarize)
intents.members = True          # see members joining (welcome messages)
intents.voice_states = True     # see who is in voice channels (voice ranks)


class N7XBot(commands.Bot):
    def __init__(self):
        # The "!" prefix is unused (we only use slash commands) but discord.py requires one.
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        """Runs once when the bot starts, before it connects to Discord."""
        database.setup_database()

        # Load every .py file in cogs/ as a feature. To add a feature later,
        # just drop a new file in cogs/ - no need to edit this file.
        cogs_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cogs")
        for filename in sorted(os.listdir(cogs_folder)):
            if filename.endswith(".py") and not filename.startswith("_"):
                name = f"cogs.{filename[:-3]}"
                try:
                    await self.load_extension(name)
                    log.info("Loaded %s", name)
                except Exception:
                    # One broken cog shouldn't stop the whole bot.
                    log.exception("Failed to load %s", name)

        # Send our slash commands to Discord.
        if config.GUILD_ID:
            # Test server: commands appear instantly.
            guild = discord.Object(id=config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d commands to test server %s", len(synced), config.GUILD_ID)
        else:
            # Everywhere: can take up to an hour to show up.
            synced = await self.tree.sync()
            log.info("Synced %d commands globally", len(synced))

    async def on_ready(self):
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        log.info(
            "Intents: message_content=%s members=%s voice_states=%s",
            self.intents.message_content, self.intents.members, self.intents.voice_states,
        )
        await self.change_presence(activity=discord.Game(name="/help | N7X"))


bot = N7XBot()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """
    Catches ANY error from ANY slash command, so the bot never crashes.
    The user gets a friendly message; the details go to the terminal.
    """
    if isinstance(error, app_commands.MissingPermissions):
        message = "You don't have permission to use this command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        message = "I'm missing permissions to do that. Please ask an admin to check my role."
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = f"Slow down! Try again in {error.retry_after:.0f} seconds."
    elif isinstance(error, app_commands.NoPrivateMessage):
        message = "This command only works inside a server."
    else:
        message = "Something went wrong. Please try again later."
        log.error("Error in /%s", interaction.command.name if interaction.command else "?", exc_info=error)

    # If we already replied, we must use followup instead of response.
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass  # The interaction may have expired; nothing more we can do.


if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN is missing. Copy .env.example to .env and add your token.")
    try:
        bot.run(config.DISCORD_TOKEN, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        raise SystemExit(
            "Discord refused the connection because privileged intents are switched off.\n"
            "Fix: go to https://discord.com/developers/applications -> your app -> Bot ->\n"
            "turn ON 'Server Members Intent' and 'Message Content Intent', click Save, then restart."
        )
