"""
cogs/chat.py - Answers everyone who @mentions the bot (or replies to one of its messages).

What happens with a tagged message:
1. Bots and messages that don't tag the bot are ignored.
2. One AI call (the free fallback chain) works out the language and what kind of message it is,
   and writes the answer for questions:
   - question / normal chat -> answered with the same rules and facts.md as /ask (qa.py):
     N7X only from facts.md, "I don't know yet, please ask a mod." otherwise, never invented links.
   - crypto price -> the same CoinGecko card as /price (price, 24h change, source footer).
   - moderation request (warn, timeout, kick, show/clear warnings, summarize...) ->
     from a mod: the mod assistant (cogs/mod_assistant.py) handles it, with Confirm as usual;
     from anyone else: a polite "only mods can do that".
3. Replies go to the person's message, in their language, with no pings at all.
4. 10-second cooldown per person ("Slow down a little"). A short memory per person
   (last 5 messages, 5 minutes, in memory only) makes follow-up questions work.
5. The terminal never shows message text: only IDs, the kind of message and which AI answered.
"""

import json
import logging
import re
import time

import discord
from discord.ext import commands

import n7x_facts
from ai_providers import AIClient, AllProvidersFailed
from assistant_memory import ConversationMemory
from assistant_text import ensure_language, normalize_language, t
from qa import check_answer, system_prompt

log = logging.getLogger("n7x.chat")

# --- Settings you can tweak ---
COOLDOWN_SECONDS = 10      # per person, for tagged questions
MAX_ANSWER_LENGTH = 1500   # characters
MAX_MESSAGE_LENGTH = 500   # longer messages are refused
MAX_PRICE_COINS = 3        # coins shown for one price question

SLOW_DOWN = "⏳ Slow down a little, you can ask me again in a few seconds."
TROUBLE = "🤖 I'm having trouble, try again in a minute."
NO_PING = discord.AllowedMentions.none()

ROUTER_RULES = """
Reply with ONLY a JSON object, no other text, in exactly this shape:
{"language": "<ISO 639-1 code of the person's message, like en, es, fr, pt, de, yo, ha, ig; pcm for Nigerian Pidgin>",
 "kind": "question" | "price" | "mod_action",
 "coins": ["<only for kind price: the coin names or tickers asked about>"],
 "answer": "<only for kind question: your answer, following every rule above, under 1500 characters>"}
How to pick "kind":
- "mod_action": the person asks the bot to DO moderation: warn, timeout/mute, remove a timeout/unmute, kick, ban,
  show or clear someone's warnings ("what has @x done"), summarize/recap a channel, cancel scheduled steps,
  or "deal with @x". A question ABOUT moderation ("how do timeouts work?") is a "question".
- "price": they ask for the current price, value or 24h change of one or more cryptocurrencies.
- "question": everything else, including normal chat.
"""


def parse_reply(text):
    """The router's JSON, tolerant of code fences. Plain text counts as an answer in English."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        data = json.loads(cleaned)
    except ValueError:
        return {"language": "en", "kind": "question", "coins": [], "answer": text or ""}
    if not isinstance(data, dict):
        return {"language": "en", "kind": "question", "coins": [], "answer": str(data)}
    kind = data.get("kind") if data.get("kind") in ("question", "price", "mod_action") else "question"
    coins = [str(c).strip() for c in data.get("coins") or [] if str(c).strip()] if isinstance(
        data.get("coins"), list) else []
    return {"language": normalize_language(data.get("language")), "kind": kind, "coins": coins,
            "answer": str(data.get("answer") or "")}


class Chat(commands.Cog):
    # Shown in /help (see cogs/general.py).
    help_entry = (
        "Ask me by tagging me (everyone)",
        "**@mention me** (or reply to one of my messages) with a question in any language: "
        "`@N7X Bot what is N7X building?`, `@N7X Bot what's an AI agent?`, `@N7X Bot price of eth`. "
        "Same rules as /ask. I remember our last few messages for 5 minutes. One question every 10 seconds.",
    )

    def __init__(self, bot):
        self.bot = bot
        self.ai = AIClient()
        self.memory = ConversationMemory()   # (server, person) -> last few messages
        self.last_answer = {}                # person ID -> time of their last answered question

    async def cog_load(self):
        await self.ai.start()

    async def cog_unload(self):
        await self.ai.close()

    # ------------------------------------------------------------- helpers
    async def is_for_me(self, message):
        """True only if the bot is @mentioned, or the message is a Reply to one of the bot's messages."""
        me = message.guild.me
        if me in message.mentions:
            return True
        reference = message.reference
        if reference is None or reference.message_id is None:
            return False
        replied_to = reference.resolved
        if not isinstance(replied_to, discord.Message):
            try:
                replied_to = await message.channel.fetch_message(reference.message_id)
            except discord.HTTPException:
                return False
        return replied_to.author.id == me.id

    @staticmethod
    def is_mod(member):
        perms = member.guild_permissions
        return perms.moderate_members or perms.manage_messages

    def cooling_down(self, user_id):
        return time.monotonic() - self.last_answer.get(user_id, -COOLDOWN_SECONDS) < COOLDOWN_SECONDS

    async def reply(self, message, content=None, **kwargs):
        """Reply to the person's message. Never pings anyone (not even them, not @everyone/@here/roles)."""
        try:
            return await message.reply(content, mention_author=False, allowed_mentions=NO_PING, **kwargs)
        except discord.HTTPException as error:
            log.warning("Couldn't reply in #%s (%s)", getattr(message.channel, "name", "?"), error.status)
            return None

    # ------------------------------------------------------------ receiving
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or not isinstance(message.author, discord.Member):
            return
        if not await self.is_for_me(message):
            return
        try:
            await self.handle(message)
        except Exception:
            log.exception("Tagged message from %s: unexpected error", message.author.id)
            await self.reply(message, TROUBLE)

    async def handle(self, message):
        author = message.author
        is_mod = self.is_mod(author)
        me = message.guild.me
        text = re.sub(rf"<@!?{me.id}>", "", message.content).strip()
        mod_assistant = self.bot.get_cog("ModAssistant") if self.bot else None

        if not text:
            if is_mod and mod_assistant:
                await mod_assistant.handle_tagged(message)   # shows the mod assistant's help
                return
            await self.reply(message, t("en", "chat_hello"))
            return
        if len(text) > MAX_MESSAGE_LENGTH:
            await self.reply(message, f"Please keep it under {MAX_MESSAGE_LENGTH} characters.")
            return
        if not is_mod and self.cooling_down(author.id):
            await self.reply(message, SLOW_DOWN, delete_after=10)
            return

        # One AI call: language + kind of message + the answer for questions.
        key = (message.guild.id, author.id)
        history = self.memory.as_prompt(key, speaker="Member")
        if is_mod and mod_assistant:   # so "timeout him for 30 mins" after a mod-assistant question routes right
            mod_history = mod_assistant.memory.as_prompt(mod_assistant.memory_key(message))
            history = "\n".join(part for part in (mod_history, history) if part)
        facts = n7x_facts.load_facts()
        user = (f"<conversation>\n{history}\n</conversation>\n" if history else "") + f"<question>{text}</question>"
        started = time.monotonic()
        try:
            async with message.channel.typing():
                raw, source = await self.ai.ask(system_prompt(facts, words=200) + ROUTER_RULES, user, json_mode=True)
        except AllProvidersFailed:
            log.warning("Tagged message from %s: all AI providers failed", author.id)
            if facts and n7x_facts.is_link_request(text):
                await self.answer(message, key, text, n7x_facts.links_answer(facts), "en", "facts")
            else:
                await self.reply(message, TROUBLE)
            return
        reply = parse_reply(raw)
        lang = reply["language"]
        log.info("Tagged message from %s in #%s: kind=%s lang=%s [%s, %.1fs]", author.id,
                 getattr(message.channel, "name", "?"), reply["kind"], lang, source, time.monotonic() - started)

        if reply["kind"] == "mod_action":
            if is_mod and mod_assistant:
                await mod_assistant.handle_tagged(message)
                return
            if is_mod:   # mod assistant not loaded
                await self.reply(message, "The mod assistant isn't available right now. Please use the slash commands.")
                return
            await ensure_language(lang, self.ai)
            await self.answer(message, key, text, t(lang, "chat_only_mods"), lang, source)
            return

        if is_mod and self.cooling_down(author.id):   # mods' questions have the same cooldown
            await self.reply(message, SLOW_DOWN, delete_after=10)
            return

        if reply["kind"] == "price" and reply["coins"]:
            await self.answer_price(message, key, text, reply["coins"])
            return

        answer = check_answer(reply["answer"], text, facts, MAX_ANSWER_LENGTH) if reply["answer"].strip() else ""
        if not answer:
            await ensure_language(lang, self.ai)
            answer = t(lang, "chat_rephrase")
        await self.answer(message, key, text, answer, lang, source)

    async def answer(self, message, key, text, answer, lang, source):
        self.last_answer[message.author.id] = time.monotonic()
        await self.reply(message, answer)
        self.memory.add(key, text, answer, [])

    async def answer_price(self, message, key, text, coins):
        crypto = self.bot.get_cog("Crypto") if self.bot else None
        self.last_answer[message.author.id] = time.monotonic()
        if crypto is None:
            await self.reply(message, "Prices aren't available right now. Try /price in a minute.")
            return
        embeds, problems = [], []
        for coin in coins[:MAX_PRICE_COINS]:
            embed, problem = await crypto.lookup(coin[:50])
            if embed:
                embeds.append(embed)
            else:
                problems.append(problem)
        await self.reply(message, "\n".join(problems) or None, embeds=embeds)
        self.memory.add(key, text, f"(showed prices for {', '.join(coins[:MAX_PRICE_COINS])})", [])


async def setup(bot):
    await bot.add_cog(Chat(bot))
