"""
cogs/automod.py - Automatic spam and bad-word filter.

It catches:
- Repeated messages: the same message sent REPEAT_LIMIT times within REPEAT_WINDOW seconds.
- Too many links: more than MAX_LINKS links in one message.
- Bad words: any word or phrase listed in bad_words.txt.

When it catches something, it:
1. Deletes the message.
2. Warns the member (a short notice in the channel, a DM, and a saved warning).
3. Posts a note in the mod-log channel so a human can review.

Mods (anyone with "Manage Messages") and bots are never filtered.
The bot never bans anyone - mods make the final call.
"""

import logging
import os
import re
import time
from collections import defaultdict, deque

import discord
from discord.ext import commands

import mod_tools

log = logging.getLogger("n7x.automod")

# --- Settings you can tweak ---
REPEAT_LIMIT = 3          # this many identical messages...
REPEAT_WINDOW = 30        # ...within this many seconds counts as spam
MAX_LINKS = 3             # more links than this in one message counts as spam
WARN_COOLDOWN = 60        # seconds: during a burst, warn a member only once (but delete everything)
NOTICE_SECONDS = 10       # the in-channel notice deletes itself after this long

BAD_WORDS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bad_words.txt")
LINK_PATTERN = re.compile(r"https?://\S+|discord\.gg/\S+|www\.\S+", re.IGNORECASE)


def load_bad_words():
    """Read bad_words.txt: one word/phrase per line, # lines are comments."""
    try:
        with open(BAD_WORDS_FILE, encoding="utf-8") as file:
            lines = [line.strip().lower() for line in file]
    except FileNotFoundError:
        log.warning("bad_words.txt not found - bad-word filter is off")
        return []
    return [line for line in lines if line and not line.startswith("#")]


def build_bad_word_pattern(words):
    """One regex that matches any bad word as a WHOLE word ("ass" won't match "class")."""
    if not words:
        return None
    escaped = (re.escape(word) for word in words)
    return re.compile(r"(?<!\w)(" + "|".join(escaped) + r")(?!\w)", re.IGNORECASE)


class AutoMod(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Recent messages per member: (guild_id, user_id) -> deque of (time, text)
        self.recent = defaultdict(lambda: deque(maxlen=REPEAT_LIMIT * 2))
        # When each member was last warned by the auto-mod: (guild_id, user_id) -> time
        self.last_warned = {}
        # Bad-word list, reloaded automatically when bad_words.txt changes.
        self.bad_word_pattern = None
        self.bad_words_mtime = None

    def get_bad_word_pattern(self):
        """Reload bad_words.txt only if it changed since last time."""
        try:
            mtime = os.path.getmtime(BAD_WORDS_FILE)
        except OSError:
            mtime = None
        if mtime != self.bad_words_mtime:
            words = load_bad_words()
            self.bad_word_pattern = build_bad_word_pattern(words)
            self.bad_words_mtime = mtime
            log.info("Loaded %d bad word(s) from bad_words.txt", len(words))
        return self.bad_word_pattern

    def find_problem(self, message):
        """Return a short reason if the message breaks a rule, else None."""
        text = message.content
        key = (message.guild.id, message.author.id)
        now = time.time()

        # 1. Bad words
        pattern = self.get_bad_word_pattern()
        if pattern and pattern.search(text):
            return "Used a word from the bad-word list"

        # 2. Too many links
        link_count = len(LINK_PATTERN.findall(text))
        if link_count > MAX_LINKS:
            return f"Spam: {link_count} links in one message (max {MAX_LINKS})"

        # 3. Repeated messages (ignore empty ones, e.g. image-only)
        normalized = " ".join(text.lower().split())
        if normalized:
            history = self.recent[key]
            history.append((now, normalized))
            repeats = sum(1 for sent_at, old in history if old == normalized and now - sent_at <= REPEAT_WINDOW)
            if repeats >= REPEAT_LIMIT:
                return f"Spam: same message sent {repeats} times in {REPEAT_WINDOW}s"

        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Only check real members in servers.
        if message.author.bot or message.guild is None or not isinstance(message.author, discord.Member):
            return
        # Mods are trusted.
        if message.channel.permissions_for(message.author).manage_messages:
            return

        reason = self.find_problem(message)
        if reason is None:
            return

        member = message.author
        guild = message.guild
        log.info("Auto-mod: %s (%s) in #%s - %s", member, member.id, message.channel, reason)

        # 1. Delete the message.
        deleted = True
        try:
            await message.delete()
        except discord.NotFound:
            pass  # already deleted
        except discord.HTTPException as error:
            deleted = False
            log.warning("Auto-mod couldn't delete a message in #%s: %s "
                        "(give the bot 'Manage Messages')", message.channel, error)

        # During a burst, only warn once per WARN_COOLDOWN (but keep deleting).
        key = (guild.id, member.id)
        if time.time() - self.last_warned.get(key, 0) < WARN_COOLDOWN:
            return
        self.last_warned[key] = time.time()

        # 2. Short public notice that removes itself.
        what_happened = "your message was removed" if deleted else "your message breaks the rules"
        try:
            await message.channel.send(
                f"⚠️ {member.mention}, {what_happened}: **{reason}**. Please follow the server rules.",
                delete_after=NOTICE_SECONDS,
                allowed_mentions=discord.AllowedMentions(users=True, everyone=False, roles=False),
            )
        except discord.HTTPException:
            pass

        # 3. Saved warning + DM + mod-log note (with a short excerpt so mods can judge).
        excerpt = discord.utils.escape_mentions(message.content)[:300] or "(no text)"
        await mod_tools.warn_member(
            guild, member, guild.me, f"Auto-mod: {reason}",
            extra_fields=[
                ("Channel", message.channel.mention),
                ("Message deleted", "Yes" if deleted else "❌ No - I'm missing 'Manage Messages'"),
                ("Excerpt", f"||{excerpt}||"),  # spoiler-hidden in mod-log
            ],
        )


async def setup(bot):
    await bot.add_cog(AutoMod(bot))
