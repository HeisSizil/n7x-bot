"""
Tests for the official N7X facts, /ask and /about (fake AI, no internet).

Run from the project folder:
    python -m unittest discover tests -v
"""

import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace as NS

import n7x_facts
import qa
from ai_providers import AllProvidersFailed


class FakeAI:
    """Pretends to be the AI chain: returns a fixed answer and remembers what it was asked."""

    def __init__(self, answer="An answer.", fail=False):
        self.answer = answer
        self.fail = fail
        self.calls = []

    async def ask(self, system, user, json_mode=False, max_tokens=None):
        self.calls.append((system, user, json_mode))
        if self.fail:
            raise AllProvidersFailed()
        return (self.answer(system, user) if callable(self.answer) else self.answer), "fake"


class FactsFileTests(unittest.TestCase):
    def test_official_facts_are_in_facts_md(self):
        facts = n7x_facts.load_facts()
        for text in ("community-driven project focused on AI agents, Web3 innovation",
                     "ZeroSet AI", "intersection of artificial intelligence and decentralized technologies",
                     "AI Agents", "Web3 Innovation", "Intelligent Applications",
                     "October 20: Project Resources Release", "October 22: Official Website Launch",
                     "not published yet", "official N7X channels"):
            self.assertIn(text, facts)
        self.assertNotIn("http", facts)          # no links until they're really published
        self.assertNotIn("<!--", facts)

    def test_link_requests(self):
        for question in ("where is the whitepaper?", "N7X website link pls", "send your twitter", "roadmap?",
                         "when is the docs out", "what is the official roadmap", "N7X whitepaper"):
            self.assertTrue(n7x_facts.is_link_request(question), question)
        for question in ("what is a whitepaper?", "bitcoin whitepaper link", "how do I make a website",
                         "what is ethereum", "explain smart contracts"):
            self.assertFalse(n7x_facts.is_link_request(question), question)


class AnswerTests(unittest.IsolatedAsyncioTestCase):
    async def test_facts_question_uses_facts_md(self):
        ai = FakeAI("N7X is backed by ZeroSet AI.")
        answer, source = await qa.answer_question(ai, "Who backs N7X?")
        self.assertEqual((answer, source), ("N7X is backed by ZeroSet AI.", "fake"))
        system, user, _ = ai.calls[0]
        self.assertIn("ZeroSet AI", system)
        self.assertIn("October 22: Official Website Launch", system)
        self.assertIn(n7x_facts.DONT_KNOW, system)
        self.assertIn("<question>Who backs N7X?</question>", user)

    async def test_unknown_n7x_question(self):
        ai = FakeAI(n7x_facts.DONT_KNOW)
        answer, _ = await qa.answer_question(ai, "Who is the founder of N7X?")
        self.assertEqual(answer, "I don't know yet, please ask a mod.")
        self.assertIn("ONLY using the N7X FACTS", ai.calls[0][0])

    async def test_no_facts_means_dont_know_without_asking_the_ai(self):
        old = n7x_facts.FACTS_FILE
        n7x_facts.FACTS_FILE = "/nonexistent/facts.md"
        try:
            ai = FakeAI("made up")
            with self.assertLogs("n7x.facts", level="WARNING"):
                answer, source = await qa.answer_question(ai, "who is the founder of n7x")
            self.assertEqual((answer, source), (n7x_facts.DONT_KNOW, "facts"))
            self.assertEqual(ai.calls, [])
        finally:
            n7x_facts.FACTS_FILE = old

    async def test_link_request_gets_release_dates_never_a_link(self):
        ai = FakeAI("Sure! The whitepaper is at https://n7x.io/whitepaper.pdf")
        answer, _ = await qa.answer_question(ai, "Where is the N7X whitepaper?")
        self.assertIn("October 20", answer)
        self.assertIn("October 22", answer)
        self.assertNotIn("http", answer)
        self.assertNotIn("n7x.io", answer)

    async def test_link_request_when_every_ai_is_down(self):
        answer, source = await qa.answer_question(FakeAI(fail=True), "send me the website link")
        self.assertEqual(source, "facts")
        self.assertIn("October 20", answer)
        self.assertIn("October 22", answer)

    async def test_good_link_answer_in_another_language_is_kept(self):
        spanish = "Aún no están publicados: el 20 de octubre (recursos) y el 22 de octubre (sitio web)."
        answer, _ = await qa.answer_question(FakeAI(spanish), "¿Dónde está el whitepaper de N7X?")
        self.assertEqual(answer, spanish)

    async def test_general_links_are_kept_but_invented_n7x_links_removed(self):
        answer, _ = await qa.answer_question(FakeAI("Read https://ethereum.org and join https://n7x.gg"),
                                             "how do I learn Ethereum?")
        self.assertIn("https://ethereum.org", answer)
        self.assertNotIn("n7x.gg", answer)

    async def test_general_question_and_length_limit(self):
        long_answer = ("Blockchains are shared ledgers. " * 200).strip()
        answer, _ = await qa.answer_question(FakeAI(long_answer), "what is a blockchain?", limit=1500)
        self.assertLessEqual(len(answer), 1500)

    async def test_all_providers_down_raises(self):
        with self.assertRaises(AllProvidersFailed):
            await qa.answer_question(FakeAI(fail=True), "what is an AI agent?")


class SlashCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import cogs.ask
        self.ask_module = cogs.ask
        self.cog = cogs.ask.Ask(None)
        self.sent = []

    def interaction(self):
        async def defer(**kwargs):
            pass

        async def followup_send(content=None, embed=None, **kwargs):
            self.sent.append(content or embed)

        async def send_message(content=None, embed=None, **kwargs):
            self.sent.append(content or embed)
            self.kwargs = kwargs
        return NS(user=NS(id=5, display_avatar=NS(url="https://example.com/a.png")),
                  response=NS(defer=defer, send_message=send_message), followup=NS(send=followup_send))

    async def test_about_embed(self):
        await self.cog.about.callback(self.cog, self.interaction())
        embed = self.sent[0]
        self.assertEqual(embed.title, "About N7X")
        self.assertIn("community-driven project", embed.description)
        self.assertIn("**🤝 Backed by ZeroSet AI**", embed.description)
        fields = {f.name: f.value for f in embed.fields}
        building = fields["🛠️ What N7X is building"]
        for area in ("AI Agents", "Web3 Innovation", "Intelligent Applications"):
            self.assertIn(area, building)
        self.assertIn("October 20", fields["📅 Upcoming official releases"])
        self.assertIn("October 22", fields["📅 Upcoming official releases"])
        self.assertEqual(embed.footer.text, "N7X Bot")

    async def test_about_reads_facts_md_so_mods_can_edit_it(self):
        folder = tempfile.mkdtemp()
        old = n7x_facts.FACTS_FILE
        n7x_facts.FACTS_FILE = os.path.join(folder, "facts.md")
        try:
            with open(n7x_facts.FACTS_FILE, "w", encoding="utf-8") as file:
                file.write("<!-- note -->\n## About N7X\n- New description.\n\n## 🎉 Events\n- AMA on Friday.\n- Quiz night.\n")
            await self.cog.about.callback(self.cog, self.interaction())
            embed = self.sent[0]
            self.assertEqual(embed.description, "New description.")
            self.assertEqual(embed.fields[0].name, "🎉 Events")
            self.assertIn("• AMA on Friday.", embed.fields[0].value)
        finally:
            n7x_facts.FACTS_FILE = old
            shutil.rmtree(folder, ignore_errors=True)

    async def test_ask_command_link_question(self):
        self.cog.ai = FakeAI("Visit https://n7x.xyz")
        await self.cog.ask.callback(self.cog, self.interaction(), "where is the roadmap?")
        embed = self.sent[0]
        self.assertIn("October 20", embed.description)
        self.assertNotIn("n7x.xyz", embed.description)
        self.assertEqual(embed.footer.text, "N7X Bot")

    async def test_ask_command_ai_down(self):
        self.cog.ai = FakeAI(fail=True)
        with self.assertLogs("n7x.ask", level="WARNING") as logs:
            await self.cog.ask.callback(self.cog, self.interaction(), "what is staking?")
        self.assertIn("Try again in a minute", self.sent[0])
        self.assertNotIn("staking", " ".join(logs.output))   # never the question text in logs


if __name__ == "__main__":
    unittest.main()
