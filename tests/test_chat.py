"""
Tests for answering tagged messages (cogs/chat.py) with fake Discord objects and a fake AI.
No internet, no real server, temporary database.

Run from the project folder:
    python -m unittest discover tests -v
"""

import json
import logging
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import discord

import config
import database
import n7x_facts
from ai_providers import AllProvidersFailed
from tests.test_facts import FakeAI


def perms(mod=False, **extra):
    return NS(moderate_members=mod, manage_messages=mod, kick_members=extra.get("kick", False),
              administrator=extra.get("admin", False))


class Role:
    def __init__(self, position):
        self.position = position

    def __ge__(self, other):
        return self.position >= other.position

    def __gt__(self, other):
        return self.position > other.position


class Member:
    def __init__(self, user_id, name, position=10, mod=False, bot=False, **extra):
        self.id, self.name, self.display_name, self.bot = user_id, name, name, bot
        self.mention = f"<@{user_id}>"
        self.guild_permissions = perms(mod, **extra)
        self.top_role = Role(position)
        self.display_avatar = NS(url="https://example.com/a.png")

    def __str__(self):
        return self.name

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return getattr(other, "id", None) == self.id

    def is_timed_out(self):
        return False


class Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Channel:
    def __init__(self, channel_id=700, name="general"):
        self.id, self.name, self.mention = channel_id, name, f"<#{channel_id}>"

    def typing(self):
        return Typing()

    def permissions_for(self, member):
        return NS(view_channel=True, read_message_history=True, send_messages=True, embed_links=True)

    async def send(self, *args, **kwargs):
        return None


BOT = Member(999000000000000001, "N7X Bot", position=50, mod=True, kick=True)
OWNER = Member(100000000000000001, "owner", position=100, mod=True, kick=True, admin=True)
MOD = Member(200000000000000002, "mod", position=40, mod=True)
ALT = Member(300000000000000003, "alt")
SAM = Member(300000000000000004, "sam")
MEMBERS = {m.id: m for m in (BOT, OWNER, MOD, ALT, SAM)}


async def fetch_member(member_id):
    raise discord.NotFound(NS(status=404, reason="Not Found"), "unknown member")

GUILD = NS(id=1, name="N7X", me=BOT, owner_id=OWNER.id, get_member=MEMBERS.get, fetch_member=fetch_member,
           get_channel=lambda channel_id: None)


class Message:
    """A message in #general. `tag=True` puts the bot in the mentions, like a real @mention."""

    def __init__(self, author, text, tag=True, mentions=(), reference=None, channel=None):
        self.author = author
        self.guild = GUILD
        self.channel = channel or Channel()
        self.content = (f"<@{BOT.id}> " if tag else "") + text
        self.mentions = ([BOT] if tag else []) + list(mentions)
        self.channel_mentions = []
        self.reference = reference
        self.replies = []

    async def reply(self, content=None, **kwargs):
        self.replies.append((content, kwargs))
        return NS(edit=self._noop, id=1234)

    @staticmethod
    async def _noop(*args, **kwargs):
        pass

    @property
    def text(self):
        return self.replies[-1][0] if self.replies else None


def routed(kind="question", answer="", language="en", coins=()):
    return json.dumps({"language": language, "kind": kind, "coins": list(coins), "answer": answer})


class FakeModAssistant:
    def __init__(self):
        from assistant_memory import ConversationMemory
        self.memory = ConversationMemory()
        self.handled = []

    @staticmethod
    def memory_key(message):
        return (message.guild.id, message.channel.id, message.author.id)

    async def handle_tagged(self, message):
        self.handled.append(message)


class FakeCrypto:
    def __init__(self):
        self.asked = []

    async def lookup(self, coin):
        self.asked.append(coin)
        if coin == "notacoin":
            return None, "🤔 I couldn't find a coin called **notacoin**."
        embed = discord.Embed(title=f"{coin.title()} ({coin.upper()[:3]})")
        embed.add_field(name="Price", value="$1,234.00")
        embed.add_field(name="24h change", value="🟢 ▲ +2.50%")
        embed.set_footer(text="Source: CoinGecko, updated 5s ago • Not financial advice")
        return embed, None


class ChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.member_patch = mock.patch.object(discord, "Member", Member)
        self.member_patch.start()
        import cogs.chat
        self.chat_module = cogs.chat
        self.mod_assistant = FakeModAssistant()
        self.crypto = FakeCrypto()
        self.cogs = {"ModAssistant": self.mod_assistant, "Crypto": self.crypto}
        self.cog = cogs.chat.Chat(NS(get_cog=self.cogs.get))
        self.ai = FakeAI()
        self.cog.ai = self.ai

    def tearDown(self):
        self.member_patch.stop()

    async def say(self, message, ai_answer=None):
        if ai_answer is not None:
            self.ai.answer = ai_answer
        await self.cog.on_message(message)
        return message

    def assert_no_pings(self, message):
        for _, kwargs in message.replies:
            self.assertFalse(kwargs["mention_author"])
            mentions = kwargs["allowed_mentions"]
            self.assertFalse(mentions.everyone)
            self.assertFalse(mentions.roles)
            self.assertFalse(mentions.users)

    # ------------------------------------------------------------- facts
    async def test_facts_question_answered_from_facts_md(self):
        message = await self.say(Message(ALT, "who is backing N7X?"),
                                 routed(answer="N7X is backed by ZeroSet AI."))
        self.assertEqual(message.text, "N7X is backed by ZeroSet AI.")
        system, user, json_mode = self.ai.calls[0]
        self.assertTrue(json_mode)
        self.assertIn("ZeroSet AI", system)
        self.assertIn("October 20: Project Resources Release", system)
        self.assertIn("<question>who is backing N7X?</question>", user)
        self.assert_no_pings(message)

    async def test_unknown_n7x_question(self):
        message = await self.say(Message(ALT, "who is the founder of N7X?"),
                                 routed(answer=n7x_facts.DONT_KNOW))
        self.assertEqual(message.text, "I don't know yet, please ask a mod.")

    async def test_link_request_gets_release_dates(self):
        message = await self.say(Message(ALT, "send the N7X whitepaper link"),
                                 routed(answer="Here: https://n7x-whitepaper.io"))
        self.assertIn("October 20", message.text)
        self.assertIn("October 22", message.text)
        self.assertNotIn("http", message.text)

    async def test_link_request_when_ai_is_down(self):
        self.ai.fail = True
        message = await self.say(Message(ALT, "where is the website?"))
        self.assertIn("October 22", message.text)

    async def test_general_question_and_length_limit(self):
        message = await self.say(Message(ALT, "explain blockchains"),
                                 routed(answer="A blockchain is a shared ledger. " * 100))
        self.assertLessEqual(len(message.text), 1500)

    async def test_ai_down_says_trouble(self):
        self.ai.fail = True
        message = await self.say(Message(ALT, "what is an AI agent?"))
        self.assertEqual(message.text, "🤖 I'm having trouble, try again in a minute.")

    # ------------------------------------------------------------ routing
    async def test_non_mod_asking_for_a_timeout(self):
        message = await self.say(Message(ALT, "timeout @sam for 10 minutes", mentions=[SAM]),
                                 routed(kind="mod_action"))
        self.assertTrue(message.text.startswith("Sorry, only mods can do that."))
        self.assertIn("happy to answer", message.text)
        self.assertEqual(self.mod_assistant.handled, [])
        self.assert_no_pings(message)

    async def test_non_mod_mod_action_in_spanish(self):
        message = await self.say(Message(ALT, "expulsa a @sam", mentions=[SAM]),
                                 routed(kind="mod_action", language="es"))
        self.assertTrue(message.text.startswith("Lo siento, solo los moderadores"))

    async def test_mod_asking_a_question(self):
        message = await self.say(Message(MOD, "what is N7X building?"),
                                 routed(answer="AI agents, Web3 innovation and intelligent applications."))
        self.assertEqual(message.text, "AI agents, Web3 innovation and intelligent applications.")
        self.assertEqual(self.mod_assistant.handled, [])

    async def test_mod_action_goes_to_the_mod_assistant(self):
        message = Message(MOD, "warn @sam for spam", mentions=[SAM])
        await self.say(message, routed(kind="mod_action"))
        self.assertEqual(self.mod_assistant.handled, [message])
        self.assertEqual(message.replies, [])

    async def test_mod_tag_alone_shows_the_mod_help_and_member_tag_alone_says_hi(self):
        await self.say(Message(MOD, ""))
        self.assertEqual(len(self.mod_assistant.handled), 1)
        message = await self.say(Message(ALT, ""))
        self.assertIn("Ask me anything", message.text)
        self.assertEqual(self.ai.calls, [])

    # -------------------------------------------------------------- other
    async def test_another_language(self):
        french = "Un agent IA est un programme qui agit tout seul pour atteindre un objectif."
        message = await self.say(Message(ALT, "c'est quoi un agent IA ?"), routed(answer=french, language="fr"))
        self.assertEqual(message.text, french)
        self.assertIn("same language", self.ai.calls[0][0])

    async def test_price_question(self):
        message = await self.say(Message(ALT, "what's the price of eth and btc?"),
                                 routed(kind="price", coins=["eth", "btc"]))
        self.assertEqual(self.crypto.asked, ["eth", "btc"])
        content, kwargs = message.replies[-1]
        embeds = kwargs["embeds"]
        self.assertEqual(len(embeds), 2)
        self.assertEqual(embeds[0].fields[1].name, "24h change")
        self.assertTrue(embeds[0].footer.text.startswith("Source: CoinGecko"))
        self.assert_no_pings(message)

    async def test_price_unknown_coin(self):
        message = await self.say(Message(ALT, "price of notacoin"), routed(kind="price", coins=["notacoin"]))
        self.assertIn("couldn't find", message.text)

    async def test_cooldown(self):
        first = await self.say(Message(ALT, "what is staking?"), routed(answer="Locking coins to help a network."))
        second = await self.say(Message(ALT, "and what is slashing?"))
        self.assertEqual(first.text, "Locking coins to help a network.")
        self.assertEqual(second.text, "⏳ Slow down a little, you can ask me again in a few seconds.")
        self.assertEqual(second.replies[0][1]["delete_after"], 10)
        self.assertEqual(len(self.ai.calls), 1)
        other = await self.say(Message(SAM, "what is slashing?"), routed(answer="A penalty."))
        self.assertEqual(other.text, "A penalty.")          # the cooldown is per person
        self.cog.last_answer[ALT.id] -= 11                   # 11 seconds later
        third = await self.say(Message(ALT, "and what is slashing?"), routed(answer="A penalty."))
        self.assertEqual(third.text, "A penalty.")

    async def test_follow_up_memory(self):
        await self.say(Message(ALT, "what is a DAO?"), routed(answer="A community-run organization."))
        self.cog.last_answer.clear()
        await self.say(Message(ALT, "how do they vote?"), routed(answer="With governance tokens."))
        user = self.ai.calls[-1][1]
        self.assertIn("Member: what is a DAO?", user)
        self.assertIn("Bot: A community-run organization.", user)
        await self.say(Message(SAM, "hi"), routed(answer="Hello!"))
        self.assertNotIn("DAO", self.ai.calls[-1][1])        # nobody else's messages

    async def test_ignores_bots_and_untagged_messages(self):
        await self.say(Message(Member(77, "otherbot", bot=True), "what is N7X?"), routed(answer="x"))
        untagged = await self.say(Message(ALT, "what is N7X?", tag=False), routed(answer="x"))
        self.assertEqual(self.ai.calls, [])
        self.assertEqual(untagged.replies, [])

    async def test_reply_to_the_bot_counts_as_a_tag(self):
        reference = NS(message_id=55, resolved=None)   # Discord didn't include the message: the bot fetches it
        message = Message(ALT, "and the website?", tag=False, reference=reference)

        async def fetch_message(message_id):
            return NS(author=BOT)
        message.channel.fetch_message = fetch_message
        await self.say(message, routed(answer="October 22: Official Website Launch, and October 20 for the rest."))
        self.assertIn("October 22", message.text)

    async def test_no_message_text_in_logs(self):
        with self.assertLogs("n7x.chat", level="INFO") as logs:
            await self.say(Message(ALT, "super secret question about staking"), routed(answer="An answer."))
        self.assertNotIn("secret", " ".join(logs.output))
        self.assertIn("kind=question", " ".join(logs.output))

    async def test_everyone_in_an_answer_never_pings(self):
        message = await self.say(Message(ALT, "say hi to everyone"), routed(answer="Hi @everyone and @here!"))
        self.assert_no_pings(message)


class RealModAssistantTests(unittest.IsolatedAsyncioTestCase):
    """A mod action still works end to end: chat routes it, the real mod assistant asks to Confirm."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_db = database.DB_FILE
        database.DB_FILE = os.path.join(self.tmp, "test.db")
        database.setup_database()
        self.patches = [mock.patch.object(discord, "Member", Member),
                        mock.patch.object(config, "MOD_LOG_CHANNEL_ID", None),
                        mock.patch.object(config, "MOD_ASSISTANT_CHANNEL_ID", None)]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        database.DB_FILE = self.old_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_mod_warn_request_reaches_the_confirm_step(self):
        import cogs.chat
        import cogs.mod_assistant
        assistant = cogs.mod_assistant.ModAssistant(None)
        plan = {"language": "en", "confidence": 0.97, "alternatives": [],
                "actions": [{"tool": "add_warning", "args": {"member": f"<@{SAM.id}>", "reason": "spam"},
                             "delay_minutes": 0}]}
        assistant.ai = FakeAI(json.dumps(plan))
        chat = cogs.chat.Chat(NS(get_cog={"ModAssistant": assistant}.get))
        chat.ai = FakeAI(routed(kind="mod_action"))
        message = Message(MOD, f"warn <@{SAM.id}> for spam", mentions=[SAM])
        await chat.on_message(message)
        text, kwargs = message.replies[-1]
        self.assertIn("Please confirm this plan", text)
        self.assertIn(f"Warn <@{SAM.id}>", text)
        self.assertIsNotNone(kwargs.get("view"))

        # The same words from a non-mod never reach the mod assistant.
        assistant.ai.calls.clear()
        message = Message(ALT, f"warn <@{SAM.id}> for spam", mentions=[SAM])
        await chat.on_message(message)
        self.assertTrue(message.text.startswith("Sorry, only mods can do that."))
        self.assertEqual(assistant.ai.calls, [])
        await assistant.handle_tagged(message)               # and even if called directly, it refuses
        self.assertEqual(assistant.ai.calls, [])


if __name__ == "__main__":
    unittest.main()
