"""
Tests for the trivia upgrade: /trivia quizzes, the trivia/ files, check_trivia.py,
Open Trivia DB (with a local fake server) and trivia mini quizzes in tournaments.
No internet and no real Discord needed. Uses a temporary database.

Run from the project folder:
    python -m unittest discover tests -v
"""

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
import unittest
from types import SimpleNamespace as NS

from aiohttp import web

import database
import trivia_bank
from tests.test_tournament_discord import FakeChannel, FakeMember, FakeMessage


def write_category(folder, filename, questions):
    with open(os.path.join(folder, filename), "w", encoding="utf-8") as file:
        json.dump({"questions": questions}, file)


def simple_questions(count, prefix="Question"):
    return [{"question": f"{prefix} {n}?", "answers": [f"Right {n}", "Wrong a", "Wrong b", "Wrong c"], "correct": 0}
            for n in range(1, count + 1)]


class QuizMessage(FakeMessage):
    """Remembers every edit (embed titles), and the view so the test can press its buttons."""

    def __init__(self):
        super().__init__()
        self.titles = []
        self.attachments = None

    async def edit(self, embed=None, view=None, attachments=None, **kwargs):
        if embed:
            self.embeds = [embed]
            self.titles.append(embed.title)
        if view is not None:
            self.view = view
        if attachments is not None:
            self.attachments = attachments


class TempDatabase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_db = database.DB_FILE
        database.DB_FILE = os.path.join(self.tmp, "test.db")
        database.setup_database()

    def tearDown(self):
        database.DB_FILE = self.old_db
        shutil.rmtree(self.tmp, ignore_errors=True)


class TriviaQuizTests(TempDatabase):
    def setUp(self):
        super().setUp()
        import cogs.games as games
        self.games = games
        self.old_times = games.TRIVIA_SECONDS, games.TRIVIA_REVEAL_SECONDS
        games.TRIVIA_SECONDS, games.TRIVIA_REVEAL_SECONDS = 0.3, 0
        self.player = FakeMember(10, "player")
        self.cog = games.Games(None)

        # A private trivia folder so the tests don't depend on the real question files.
        self.old_dir = trivia_bank.TRIVIA_DIR
        trivia_bank.TRIVIA_DIR = os.path.join(self.tmp, "trivia")
        os.makedirs(trivia_bank.TRIVIA_DIR)
        trivia_bank._cache.clear()
        write_category(trivia_bank.TRIVIA_DIR, "movies.json", simple_questions(30, "Movie"))
        write_category(trivia_bank.TRIVIA_DIR, "anime.json", simple_questions(3, "Anime"))

    def tearDown(self):
        self.games.TRIVIA_SECONDS, self.games.TRIVIA_REVEAL_SECONDS = self.old_times
        trivia_bank.TRIVIA_DIR = self.old_dir
        trivia_bank._cache.clear()
        super().tearDown()

    async def play(self, count, category, choose, user=None):
        """Start /trivia and answer with choose(question, number) -> index, or None to let time run out."""
        message = QuizMessage()
        sent = []

        async def send_message(embed=None, **kwargs):
            sent.append(embed)

        async def original_response():
            return message
        interaction = NS(user=self.player, guild=NS(id=1), response=NS(send_message=send_message),
                         original_response=original_response)
        quiz = asyncio.get_running_loop().create_task(
            self.cog.trivia.callback(self.cog, interaction, count, NS(value=category)))

        async def answer():
            done = -1
            while not quiz.done():
                view = message.view
                if view and view.accepting and view.number != done:
                    done = view.number
                    index = choose(view.question, view.number)
                    if index is not None:
                        press = NS(user=user or self.player,
                                   response=NS(edit_message=self.ignore, send_message=self.ignore))
                        if await view.interaction_check(press):
                            await view.buttons[index].callback(press)
                await asyncio.sleep(0.005)
        await asyncio.wait_for(asyncio.gather(quiz, answer()), 30)
        return message

    @staticmethod
    async def ignore(*args, **kwargs):
        pass

    def profile(self):
        return database.get_game_profile(1, self.player.id, self.games.today())

    def right(self, question, number):
        return question["answer"]

    async def test_ten_question_quiz_all_right(self):
        message = await self.play(10, "movies", self.right)
        question_titles = [t for t in message.titles if "Question" in t]
        self.assertIn("🧠 Question 1/10 • Score 0", question_titles)
        self.assertIn("🧠 Question 10/10 • Score 9", question_titles)
        final = message.embeds[0]
        self.assertEqual(final.title, "🏁 Quiz complete!")
        self.assertIn("**10/10** (100%)", final.description)
        self.assertIn("N7X Genius", final.description)
        self.assertIn("+100 XP", final.description)
        profile = self.profile()
        self.assertEqual((profile["plays"], profile["wins"], profile["game_xp_today"]), (1, 1, 100))
        self.assertTrue(all(item.disabled for item in message.view.children))

    async def test_no_repeats_in_a_quiz(self):
        asked = []
        await self.play(20, "movies", lambda q, n: asked.append(q["question"]) or 0)
        self.assertEqual(len(asked), 20)
        self.assertEqual(len(set(asked)), 20)

    async def test_score_and_ratings_and_win_at_60_percent(self):
        # 3 of 5 right = 60% = a win (+30 XP).
        message = await self.play(5, "movies", lambda q, n: q["answer"] if n < 3 else (q["answer"] + 1) % 4)
        final = message.embeds[0].description
        self.assertIn("**3/5** (60%)", final)
        self.assertIn("Getting there", final)
        self.assertIn("+30 XP", final)
        self.assertEqual(self.profile()["wins"], 1)

    async def test_below_60_percent_is_a_loss(self):
        message = await self.play(5, "movies", lambda q, n: q["answer"] if n < 2 else (q["answer"] + 1) % 4)
        self.assertIn("**2/5** (40%)", message.embeds[0].description)
        profile = self.profile()
        self.assertEqual((profile["plays"], profile["wins"], profile["game_xp_today"]), (1, 0, 20))

    async def test_two_timeouts_in_a_row_end_the_quiz(self):
        message = await self.play(10, "movies", lambda q, n: q["answer"] if n == 0 else None)
        final = message.embeds[0]
        self.assertEqual(final.title, "🏁 Quiz over")
        self.assertIn("**1/3**", final.description)
        self.assertIn("✅ ⌛ ⌛", final.description)
        self.assertIn("2 questions in a row without an answer", final.description)
        self.assertEqual(self.profile()["plays"], 1)

    async def test_one_timeout_does_not_end_the_quiz(self):
        message = await self.play(4, "movies", lambda q, n: None if n in (0, 2) else q["answer"])
        self.assertIn("⌛ ✅ ⌛ ✅", message.embeds[0].description)
        self.assertEqual(message.embeds[0].title, "🏁 Quiz complete!")

    async def test_fewer_questions_than_asked(self):
        message = await self.play(10, "anime", self.right)
        final = message.embeds[0].description
        self.assertIn("**3/3**", final)
        self.assertIn("Only 3 question(s) were available (you asked for 10)", final)

    async def test_only_the_player_can_press(self):
        other = FakeMember(11, "other")
        replies = []

        async def send_message(text=None, **kwargs):
            replies.append(text)
        view = self.games.QuizView(self.player, 1, trivia_bank.pick("movies", 1), "🎬 Movies", 1)
        view.accepting = True
        allowed = await view.interaction_check(NS(user=other, response=NS(send_message=send_message)))
        self.assertFalse(allowed)
        self.assertIn("isn't your game", replies[0])

    async def test_daily_cap_still_applies(self):
        database.give_game_xp(1, self.player.id, 95, self.games.today(), 100)
        message = await self.play(3, "movies", self.right)
        self.assertIn("+5 XP** (daily cap)", message.embeds[0].description)

    async def test_image_question_attaches_local_file(self):
        images = os.path.join(self.tmp, "images")
        os.makedirs(images)
        with open(os.path.join(images, "cat.png"), "wb") as file:
            file.write(b"\x89PNG fake")
        old_images = trivia_bank.IMAGES_DIR
        trivia_bank.IMAGES_DIR = images
        try:
            write_category(trivia_bank.TRIVIA_DIR, "animals.json", [
                {"question": "Which animal is this?", "answers": ["Cat", "Dog", "Cow", "Fox"], "correct": 0,
                 "image": "cat.png"}])
            message = QuizMessage()

            async def send_message(embed=None, **kwargs):
                pass

            async def original_response():
                return message
            interaction = NS(user=self.player, guild=NS(id=1), response=NS(send_message=send_message),
                             original_response=original_response)
            quiz = asyncio.get_running_loop().create_task(
                self.cog.trivia.callback(self.cog, interaction, 1, NS(value="animals")))
            while not (message.view and message.view.accepting):
                await asyncio.sleep(0.005)
            self.assertEqual(message.embeds[0].image.url, "attachment://cat.png")
            self.assertEqual(message.attachments[0].filename, "cat.png")
            await message.view.buttons[message.view.question["answer"]].callback(
                NS(user=self.player, response=NS(edit_message=self.ignore)))
            await asyncio.wait_for(quiz, 10)
            self.assertEqual(message.attachments, [])   # removed on the results card
        finally:
            trivia_bank.IMAGES_DIR = old_images


class TriviaBankTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_dir = trivia_bank.TRIVIA_DIR
        trivia_bank.TRIVIA_DIR = self.tmp
        trivia_bank._cache.clear()

    def tearDown(self):
        trivia_bank.TRIVIA_DIR = self.old_dir
        trivia_bank._cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_broken_file_is_skipped_with_one_log_line(self):
        with open(os.path.join(self.tmp, "movies.json"), "w") as file:
            file.write('{"questions": [ {"question": "oops", ')
        with self.assertLogs("n7x.trivia", level=logging.WARNING) as logs:
            self.assertEqual(trivia_bank.load_category("movies"), [])
        self.assertEqual(len(logs.output), 1)
        self.assertIn("movies.json is broken", logs.output[0])

    def test_broken_questions_skipped_with_one_log_line(self):
        questions = simple_questions(3) + [{"question": "No answers?"}, {"question": "x", "answers": ["a"] * 4,
                                                                         "correct": 0}]
        write_category(self.tmp, "movies.json", questions)
        with self.assertLogs("n7x.trivia", level=logging.WARNING) as logs:
            self.assertEqual(len(trivia_bank.load_category("movies")), 3)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("2 broken question(s)", logs.output[0])

    def test_hot_reload(self):
        write_category(self.tmp, "movies.json", simple_questions(2))
        self.assertEqual(len(trivia_bank.load_category("movies")), 2)
        write_category(self.tmp, "movies.json", simple_questions(5))
        path = os.path.join(self.tmp, "movies.json")
        os.utime(path, (time.time() + 5, time.time() + 5))   # make sure the change time differs
        self.assertEqual(len(trivia_bank.load_category("movies")), 5)

    def test_question_checks(self):
        good = {"question": "Q?", "answers": ["a", "b", "c", "d"], "correct": 3, "explanation": "because"}
        self.assertIsNotNone(trivia_bank.check_question(good)[0])
        for bad in ({**good, "correct": 4}, {**good, "correct": "A"}, {**good, "answers": ["a", "b", "c"]},
                    {**good, "answers": ["a", "a", "c", "d"]}, {**good, "answe": 1},
                    {**good, "question": "What is N7X?"}, {**good, "image": "missing.png"},
                    {**good, "image": "../secret.png"}):
            self.assertIsNone(trivia_bank.check_question(bad)[0], bad)
        self.assertIsNotNone(trivia_bank.check_question({**good, "image": "https://example.com/a.png"})[0])

    def test_answers_are_shuffled_but_right_answer_tracked(self):
        write_category(self.tmp, "movies.json", simple_questions(1))
        positions = set()
        for _ in range(60):
            q = trivia_bank.pick("movies", 1)[0]
            self.assertEqual(q["choices"][q["answer"]], "Right 1")
            positions.add(q["answer"])
        self.assertEqual(positions, {0, 1, 2, 3})

    def test_fun_maths_generated_questions_are_valid(self):
        for _ in range(500):
            question = trivia_bank.make_maths_question()
            clean, problem = trivia_bank.check_question(
                {key: question[key] for key in ("question", "answers", "correct", "explanation")})
            self.assertIsNone(problem, question)
        picked = trivia_bank.pick("maths", 20)
        self.assertEqual(len(picked), 20)
        self.assertEqual(len({q["question"] for q in picked}), 20)

    def test_real_trivia_files_pass_check_trivia(self):
        trivia_bank.TRIVIA_DIR = self.old_dir
        import check_trivia
        self.assertEqual(check_trivia.main(), 0)
        self.assertGreaterEqual(trivia_bank.total_questions(), 900)
        flags = [q for q in trivia_bank.load_category("geography") if "flag" in q["question"].lower()]
        emoji_animals = [q for q in trivia_bank.load_category("animals") if q["question"].startswith("Which animal is this?")]
        self.assertGreaterEqual(len(flags), 40)
        self.assertGreaterEqual(len(emoji_animals), 40)


class OpenTriviaDBTests(unittest.IsolatedAsyncioTestCase):
    """A local fake Open Trivia DB server (no internet)."""

    async def asyncSetUp(self):
        self.reply = {"response_code": 0, "results": [{
            "question": "What does &quot;HTML&quot; stand for?",
            "correct_answer": "HyperText Markup Language",
            "incorrect_answers": ["Home Tool Markup", "Hyperlinks &amp; Text", "High Tech Modern Language"]}]}
        app = web.Application()

        async def handler(request):
            self.params = dict(request.query)
            return web.json_response(self.reply)
        app.router.add_get("/api.php", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.old = trivia_bank.OPENTDB_URL, trivia_bank.OPENTDB_GAP
        trivia_bank.OPENTDB_URL = f"http://127.0.0.1:{port}/api.php"
        trivia_bank.OPENTDB_GAP = 0
        import aiohttp
        self.session = aiohttp.ClientSession(trust_env=False)
        # Local questions to fall back on.
        self.tmp = tempfile.mkdtemp()
        self.old_dir = trivia_bank.TRIVIA_DIR
        trivia_bank.TRIVIA_DIR = self.tmp
        trivia_bank._cache.clear()
        write_category(self.tmp, "movies.json", simple_questions(5, "Movie"))
        write_category(self.tmp, "ai_web3.json", simple_questions(5, "Web3"))

    async def asyncTearDown(self):
        trivia_bank.TRIVIA_DIR = self.old_dir
        trivia_bank._cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)
        await self.session.close()
        await self.runner.cleanup()
        trivia_bank.OPENTDB_URL, trivia_bank.OPENTDB_GAP = self.old

    async def test_html_is_decoded(self):
        questions = await trivia_bank.get_questions("movies", 1, session=self.session, use_opentdb=True)
        q = questions[0]
        self.assertEqual(q["question"], 'What does "HTML" stand for?')
        self.assertIn("Hyperlinks & Text", q["choices"])
        self.assertEqual(q["choices"][q["answer"]], "HyperText Markup Language")
        self.assertEqual(self.params["category"], "11")
        self.assertEqual(self.params["type"], "multiple")

    async def test_rate_limited_falls_back_to_local(self):
        self.reply = {"response_code": 5, "results": []}
        questions = await trivia_bank.get_questions("ai_web3", 3, session=self.session, use_opentdb=True)
        self.assertEqual(len(questions), 3)   # AI & Web3 is local only: Open Trivia DB isn't even asked
        with self.assertLogs("n7x.trivia", level=logging.WARNING):
            questions = await trivia_bank.get_questions("movies", 3, session=self.session, use_opentdb=True)
        self.assertEqual(len(questions), 3)
        self.assertNotIn('What does "HTML" stand for?', [q["question"] for q in questions])

    async def test_server_down_falls_back_to_local(self):
        await self.runner.cleanup()
        with self.assertLogs("n7x.trivia", level=logging.WARNING):
            questions = await trivia_bank.get_questions("movies", 2, session=self.session, use_opentdb=True)
        self.assertEqual(len(questions), 2)


class TournamentTriviaQuizTests(TempDatabase):
    def setUp(self):
        super().setUp()
        import cogs.tournament as tc
        self.tc = tc
        tc.SEND_GAP = 0
        tc.ROUND_SECONDS = 2
        self.channel = FakeChannel(tc)
        self.host = FakeMember(1, "host", mod=True)
        self.alt = FakeMember(2, "alt")
        self.kim = FakeMember(3, "kim")

    async def test_mini_quiz_tie_then_sudden_death(self):
        # Both right on all 3 questions (tie 3-3), then sudden death: alt right, kim wrong.
        def alt(view):
            return view.question["answer"]

        def kim(view):
            sudden_death = len(self.channel.titles()) and "Sudden death" in (self.channel.sent[-1][0].title or "")
            return (view.question["answer"] + 1) % 4 if sudden_death else view.question["answer"]
        self.channel.plan = {2: alt, 3: kim}
        cog = self.tc.Tournament(None)
        lobby = self.tc.LobbyView(self.host, "trivia", 8, int(time.time()) + 60, questions=3)
        lobby.players = [self.alt, self.kim]
        lobby.message = FakeMessage()
        await asyncio.wait_for(cog.run_tournament(NS(channel=self.channel, guild=NS(id=1)), lobby, 0.01, False), 30)

        titles = self.channel.titles()
        self.assertIn("⚔️ Final • Question 3/3", titles)
        self.assertTrue(any("tied on points" in (e.description or "").lower() for e, _ in self.channel.sent if e))
        self.assertIn("⚔️ Final • ⚡ Sudden death 1", titles)
        self.assertEqual(titles[-1], "🏆 Tournament results")
        self.assertEqual(database.get_duel_stats(1, self.alt.id)["gold"], 1)
        self.assertEqual(database.get_duel_stats(1, self.kim.id)["silver"], 1)
        # No question repeats inside one tournament.
        questions = [e.description.split("**")[1] for e, _ in self.channel.sent
                     if e and e.title.startswith("⚔️ Final")]
        self.assertEqual(len(questions), len(set(questions)))


if __name__ == "__main__":
    unittest.main()
