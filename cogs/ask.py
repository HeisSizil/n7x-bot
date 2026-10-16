"""
cogs/ask.py - /ask (AI answers about AI, crypto, Web3 and N7X) and /about.

Rules (shared with tagged questions, see qa.py):
- General AI / crypto / Web3 questions: answered normally.
- Questions about N7X itself: answered ONLY from facts.md. If the answer
  isn't there, the bot says "I don't know yet, please ask a mod."
- Links, docs, roadmap, whitepaper: the release dates from facts.md, never an invented link.
- Each member can ask a few questions per minute.
- If every AI provider fails: "Try again in a minute."

/about shows the facts.md sections as an embed (mods edit facts.md, no code changes).

Commands: /ask, /about
"""

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
import n7x_facts
from ai_providers import AIClient, AllProvidersFailed
from qa import answer_question

log = logging.getLogger("n7x.ask")

# --- Settings you can tweak ---
QUESTIONS_PER_MINUTE = 3   # per member
MAX_QUESTION_LENGTH = 500  # characters

DONT_KNOW = n7x_facts.DONT_KNOW
FOOTER = "N7X Bot"  # shown under every /ask answer and /about


def shorten(text, limit):
    """Cut text to `limit` characters, adding '...' if it was longer."""
    return text if len(text) <= limit else text[: limit - 3] + "..."


def about_embed():
    """The /about card, built from the sections of facts.md."""
    parts = n7x_facts.sections()
    if not parts:
        embed = discord.Embed(title="About N7X", description=DONT_KNOW, color=config.BRAND_COLOR)
        embed.set_footer(text=FOOTER)
        return embed
    title, intro = parts[0]
    description = "\n\n".join(intro)
    embed = discord.Embed(title=title, color=config.BRAND_COLOR)
    for heading, lines in parts[1:25]:
        if len(lines) == 1 and len(lines[0]) <= 40:
            description += f"\n\n**{heading} {lines[0]}**"     # e.g. "🤝 Backed by ZeroSet AI"
        else:
            embed.add_field(name=shorten(heading, 256), value=shorten("\n".join(f"• {line}" for line in lines), 1024),
                            inline=False)
    embed.description = shorten(description, 4000)
    embed.set_footer(text=FOOTER)
    return embed


class Ask(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ai = AIClient()

    async def cog_load(self):
        await self.ai.start()

    async def cog_unload(self):
        await self.ai.close()

    @app_commands.command(name="ask", description="Ask the AI about AI agents, crypto, Web3 or N7X")
    @app_commands.describe(question="Your question")
    @app_commands.checks.cooldown(QUESTIONS_PER_MINUTE, 60)  # per member
    async def ask(
        self,
        interaction: discord.Interaction,
        # Range makes Discord itself block questions that are too long,
        # so the bot never has to check (or reply) before deferring.
        question: app_commands.Range[str, 1, MAX_QUESTION_LENGTH],
    ):
        # FIRST THING: tell Discord we're working on it. Discord only waits 3 seconds
        # for a reply; defer() shows "N7X Bot is thinking..." and gives us 15 minutes.
        try:
            await interaction.response.defer(thinking=True)
        except discord.NotFound:
            # The 3 seconds had already passed before we got here (slow network or
            # a busy bot). Nothing we can send anymore - just log it.
            log.warning("/ask: interaction expired before we could defer (bot or network too slow)")
            return
        started = time.monotonic()

        question = question.strip()
        if not question:
            await interaction.followup.send("Please type a question.")
            return

        try:
            answer, source = await answer_question(self.ai, question)
        except AllProvidersFailed:
            log.warning("/ask by %s: all AI providers failed", interaction.user.id)
            await interaction.followup.send("🤖 I'm a bit overloaded. Try again in a minute.")
            return
        # Which AI answered goes to the terminal only - members never see it. Never the question text.
        log.info("/ask by %s: answered by %s in %.1fs", interaction.user.id, source, time.monotonic() - started)

        embed = discord.Embed(description=shorten(answer, 4000), color=config.BRAND_COLOR)
        embed.set_author(name=shorten(f"Q: {question}", 250), icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=FOOTER)

        # allowed_mentions=none: even if the AI writes @everyone, nobody gets pinged.
        await interaction.followup.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="about", description="About N7X: what it is, what it's building, upcoming releases")
    async def about(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=about_embed(), allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(Ask(bot))
