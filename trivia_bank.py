"""
trivia_bank.py - Where trivia questions come from (no Discord code).

- One JSON file per category in the trivia/ folder (see trivia/README.md).
- Files are re-read automatically when they change (no restart needed).
- A broken file is skipped with ONE log line; broken questions inside a good file are
  skipped too (also one log line per file). Run `python check_trivia.py` to see details.
- Fun Maths questions are generated in code (fresh numbers every time), plus any in fun_maths.json.
- Optional: Open Trivia DB (OPENTDB_ENABLED=true in .env) for extra questions; if it's slow,
  down or rate limited, the local files are used instead.

Question format in the JSON files:
    {"question": "...", "answers": ["...", "...", "...", "..."], "correct": 0,
     "explanation": "optional", "image": "optional: https://... or a file name in images/"}
"correct" is the position of the right answer (0 = first). Answers are shuffled when shown.
"""

import asyncio
import html
import json
import logging
import os
import secrets
import time

log = logging.getLogger("n7x.trivia")

ROOT = os.path.dirname(os.path.abspath(__file__))
TRIVIA_DIR = os.path.join(ROOT, "trivia")
IMAGES_DIR = os.path.join(ROOT, "images")

# key -> (label shown in Discord, file in trivia/)
CATEGORIES = {
    "mixed": ("🎲 Mixed", None),
    "movies": ("🎬 Movies", "movies.json"),
    "anime": ("🍥 Anime", "anime.json"),
    "animals": ("🐾 Animals", "animals.json"),
    "geography": ("🌍 Geography", "geography.json"),
    "maths": ("➗ Fun Maths", "fun_maths.json"),
    "facts": ("💡 Fun Facts", "fun_facts.json"),
    "ai_web3": ("🤖 AI & Web3", "ai_web3.json"),
}

# Limits that keep questions readable in Discord.
MAX_QUESTION_LEN = 300
MAX_ANSWER_LEN = 80
MAX_EXPLANATION_LEN = 300
ALLOWED_KEYS = {"question", "answers", "correct", "explanation", "image"}

_rng = secrets.SystemRandom()  # secure shuffling and picking


# =====================================================================
# Checking one question (shared with check_trivia.py)
# =====================================================================

def check_question(item):
    """Returns (clean question dict, None) or (None, "what's wrong")."""
    if not isinstance(item, dict):
        return None, "not an object {...}"
    unknown = set(item) - ALLOWED_KEYS
    if unknown:
        return None, f"unknown field(s): {', '.join(sorted(unknown))} (allowed: {', '.join(sorted(ALLOWED_KEYS))})"
    question = item.get("question")
    if not isinstance(question, str) or not question.strip():
        return None, "missing \"question\""
    if len(question) > MAX_QUESTION_LEN:
        return None, f"question is longer than {MAX_QUESTION_LEN} characters"
    if "n7x" in question.lower():
        return None, "N7X facts belong in facts.md, not in trivia"
    answers = item.get("answers")
    if not isinstance(answers, list) or len(answers) != 4:
        return None, "\"answers\" must be a list of exactly 4"
    if not all(isinstance(a, (str, int, float)) and str(a).strip() for a in answers):
        return None, "every answer must be non-empty text"
    answers = [str(a).strip() for a in answers]
    if len({a.lower() for a in answers}) != 4:
        return None, "two answers are the same"
    if any(len(a) > MAX_ANSWER_LEN for a in answers):
        return None, f"an answer is longer than {MAX_ANSWER_LEN} characters"
    correct = item.get("correct")
    if isinstance(correct, bool) or not isinstance(correct, int) or not 0 <= correct <= 3:
        return None, "\"correct\" must be 0, 1, 2 or 3 (the position of the right answer)"
    explanation = item.get("explanation", "")
    if not isinstance(explanation, str):
        return None, "\"explanation\" must be text"
    if len(explanation) > MAX_EXPLANATION_LEN:
        return None, f"explanation is longer than {MAX_EXPLANATION_LEN} characters"
    image = item.get("image", "")
    if not isinstance(image, str):
        return None, "\"image\" must be text"
    image = image.strip()
    if image and not image.startswith(("http://", "https://")):
        if os.path.basename(image) != image:
            return None, "a local image must be just a file name inside images/"
        if not os.path.isfile(os.path.join(IMAGES_DIR, image)):
            return None, f"image file images/{image} not found"
    return {"question": question.strip(), "answers": answers, "correct": correct,
            "explanation": explanation.strip(), "image": image}, None


def read_file(path):
    """
    Read one category file. Returns (questions, problems).
    Raises ValueError if the whole file is broken (not JSON, or no "questions" list).
    """
    with open(path, encoding="utf-8") as file:
        data = json.load(file)  # json.JSONDecodeError is a ValueError
    raw = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise ValueError('needs a "questions": [ ... ] list')
    questions, problems, seen = [], [], set()
    for number, item in enumerate(raw, start=1):
        clean, problem = check_question(item)
        if clean and clean["question"].lower() in seen:
            problem, clean = "same question as an earlier one in this file", None
        if problem:
            problems.append(f"question {number}: {problem}")
            continue
        seen.add(clean["question"].lower())
        questions.append(clean)
    return questions, problems


# =====================================================================
# Loading with automatic reload
# =====================================================================
_cache = {}  # category key -> (mtime, questions)


def load_category(key):
    """Valid questions of one category file (re-read when the file changes)."""
    filename = CATEGORIES[key][1]
    if not filename:
        return []
    path = os.path.join(TRIVIA_DIR, filename)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    cached = _cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        questions, problems = read_file(path)
    except (OSError, ValueError) as error:
        log.warning("trivia/%s is broken (%s) - skipped. Run: python check_trivia.py", filename, error)
        questions, problems = [], []
    if problems:
        log.warning("trivia/%s: %d broken question(s) skipped. Run: python check_trivia.py", filename, len(problems))
    for question in questions:
        question["category"] = key
    _cache[key] = (mtime, questions)
    return questions


def total_questions():
    return sum(len(load_category(key)) for key in CATEGORIES)


# =====================================================================
# Fun Maths (generated, with believable wrong answers)
# =====================================================================

def _wrong_numbers(right, candidates):
    """Pick 3 different, believable wrong answers (positive, not the right one)."""
    options = []
    for value in candidates + [right + d for d in (1, -1, 2, -2, 10, -10, 3, 5)]:
        if value != right and value > 0 and value not in options:
            options.append(value)
    picked = _rng.sample(options[:6], 3) if len(options) >= 6 else options[:3]
    return picked


def make_maths_question():
    """One fresh maths question in the same format as the JSON files."""
    kind = _rng.choice(["add", "subtract", "multiply", "divide", "percent", "square", "sequence", "story"])
    r = _rng.randint
    if kind == "add":
        a, b = r(12, 89), r(12, 89)
        right = a + b
        question = f"What is {a} + {b}?"
        wrong = [right + 10, right - 10, right + 1, right - 1]   # forgot or added a carry
        explain = f"{a} + {b} = {right}"
    elif kind == "subtract":
        a, b = r(40, 99), r(11, 39)
        right = a - b
        question = f"What is {a} − {b}?"
        wrong = [right + 10, right - 10, right + 2, a + b]
        explain = f"{a} − {b} = {right}"
    elif kind == "multiply":
        a, b = r(3, 12), r(3, 12)
        right = a * b
        question = f"What is {a} × {b}?"
        wrong = [a * (b + 1), a * (b - 1), (a + 1) * b, a + b]
        explain = f"{a} × {b} = {right}"
    elif kind == "divide":
        b, right = r(2, 12), r(2, 12)
        a = b * right
        question = f"What is {a} ÷ {b}?"
        wrong = [right + 1, right - 1, right + 2, b]
        explain = f"{b} × {right} = {a}, so {a} ÷ {b} = {right}"
    elif kind == "percent":
        percent = _rng.choice([10, 20, 25, 50, 75])
        base = _rng.choice([40, 60, 80, 120, 200, 240, 300, 400])
        right = base * percent // 100
        question = f"What is {percent}% of {base}?"
        wrong = [base * _rng.choice([p for p in (10, 20, 25, 50, 75) if p != percent]) // 100,
                 right + 10, right * 2, right + 5]
        explain = f"{percent}% of {base} = {base} × {percent} ÷ 100 = {right}"
    elif kind == "square":
        a = r(4, 15)
        right = a * a
        question = f"What is {a} squared ({a}²)?"
        wrong = [a * 2, (a + 1) * (a + 1), (a - 1) * (a - 1), right + 10]
        explain = f"{a}² = {a} × {a} = {right}"
    elif kind == "sequence":
        start, step = r(2, 9), r(2, 9)
        if _rng.random() < 0.5:
            numbers = [start + step * i for i in range(4)]
            right = start + step * 4
            explain = f"It goes up by {step} each time."
        else:
            numbers = [start * 2 ** i for i in range(4)]
            right = start * 2 ** 4
            explain = "Each number doubles."
        question = f"What comes next: {', '.join(map(str, numbers))}, ?"
        wrong = [right + 2, right - 2, numbers[-1] + (numbers[-1] - numbers[-2]) + 1, right + step]
    else:
        slices, eaten = _rng.choice([6, 8, 10, 12]), r(2, 5)
        friends = r(2, 4)
        right = slices - eaten
        question = (f"A pizza has {slices} slices. {friends} friends eat {eaten} slices in total. "
                    f"How many slices are left?")
        wrong = [slices - friends, right + 1, right - 1, slices + eaten]
        explain = f"{slices} − {eaten} = {right}"

    answers = [right] + _wrong_numbers(right, wrong)
    return {"question": question, "answers": [str(a) for a in answers], "correct": 0,
            "explanation": explain, "image": "", "category": "maths"}


# =====================================================================
# Picking questions for a quiz
# =====================================================================

def prepare(question):
    """Shuffle the answers securely. Returns the shape the games use."""
    order = list(range(4))
    _rng.shuffle(order)
    return {"question": question["question"],
            "choices": [question["answers"][i] for i in order],
            "answer": order.index(question["correct"]),
            "explain": question.get("explanation", ""),
            "image": question.get("image", ""),
            "category": question.get("category", "")}


def pool(category):
    """All local questions for a category (Mixed = every file)."""
    if category == "mixed":
        return [q for key in CATEGORIES for q in load_category(key)]
    return list(load_category(category))


def has_questions(category="mixed"):
    return category in ("maths", "mixed") or bool(pool(category))


def pick(category, count, avoid=()):
    """
    Up to `count` prepared questions, never the same one twice in a quiz.
    Questions in `avoid` (recently asked texts) are used only if nothing else is left.
    Returns fewer than `count` if the category doesn't have enough.
    """
    avoid = {text.lower() for text in avoid}
    questions = pool(category)
    _rng.shuffle(questions)
    questions.sort(key=lambda q: q["question"].lower() in avoid)   # fresh ones first (stable sort)

    if category in ("maths", "mixed"):
        # Mix in generated maths: all of Fun Maths, about 1 in 8 of Mixed.
        wanted = count if category == "maths" else sum(_rng.random() < 1 / 8 for _ in range(count))
        generated, texts = [], {q["question"].lower() for q in questions}
        for _ in range(wanted * 10):
            if len(generated) >= wanted:
                break
            question = make_maths_question()
            if question["question"].lower() not in texts:
                texts.add(question["question"].lower())
                generated.append(question)
        if category == "maths":
            questions = generated + questions
        else:
            questions = generated + questions[:count - len(generated)]
            _rng.shuffle(questions)
    return [prepare(q) for q in questions[:count]]


# =====================================================================
# Optional: Open Trivia DB (https://opentdb.com, free, no key)
# =====================================================================
OPENTDB_URL = "https://opentdb.com/api.php"
OPENTDB_CATEGORIES = {"mixed": None, "movies": 11, "anime": 31, "animals": 27, "geography": 22, "facts": 9}
OPENTDB_GAP = 5.5        # Open Trivia DB allows one request per 5 seconds per IP
OPENTDB_TIMEOUT = 6
_opentdb = {"last": 0.0, "lock": None}


async def fetch_opentdb(session, category, count):
    """Easy multiple-choice questions from Open Trivia DB, or [] if it's unavailable."""
    if category not in OPENTDB_CATEGORIES:
        return []   # AI & Web3 and Fun Maths are local only
    if _opentdb["lock"] is None:
        _opentdb["lock"] = asyncio.Lock()
    async with _opentdb["lock"]:
        wait = OPENTDB_GAP - (time.monotonic() - _opentdb["last"])
        if wait > OPENTDB_TIMEOUT:
            return []
        if wait > 0:
            await asyncio.sleep(wait)
        _opentdb["last"] = time.monotonic()
        params = {"amount": min(count, 50), "type": "multiple", "difficulty": "easy"}
        if OPENTDB_CATEGORIES[category]:
            params["category"] = OPENTDB_CATEGORIES[category]
        try:
            import aiohttp
            async with session.get(OPENTDB_URL, params=params,
                                   timeout=aiohttp.ClientTimeout(total=OPENTDB_TIMEOUT)) as response:
                if response.status != 200:
                    log.warning("Open Trivia DB answered HTTP %s - using local questions", response.status)
                    return []
                data = await response.json(content_type=None)
        except Exception as error:   # network problems must never break /trivia
            log.warning("Open Trivia DB unavailable (%s) - using local questions", type(error).__name__)
            return []

    if not isinstance(data, dict) or data.get("response_code") != 0:
        code = data.get("response_code") if isinstance(data, dict) else "?"
        log.warning("Open Trivia DB response code %s (5 = rate limited) - using local questions", code)
        return []
    questions = []
    for item in data.get("results", []):
        try:
            answers = [html.unescape(item["correct_answer"])] + [html.unescape(a) for a in item["incorrect_answers"]]
            clean, problem = check_question({"question": html.unescape(item["question"]),
                                             "answers": answers, "correct": 0})
        except (KeyError, TypeError):
            continue
        if clean:
            clean["category"] = category
            questions.append(prepare(clean))
    return questions


async def get_questions(category, count, avoid=(), session=None, use_opentdb=False):
    """Questions for /trivia: Open Trivia DB first when enabled, topped up from the local files."""
    questions = []
    if use_opentdb and session is not None:
        avoid_lower = {text.lower() for text in avoid}
        questions = [q for q in await fetch_opentdb(session, category, count)
                     if q["question"].lower() not in avoid_lower][:count]
    if len(questions) < count:
        taken = {q["question"].lower() for q in questions}
        for question in pick(category, count, list(avoid) + list(taken)):
            if len(questions) >= count:
                break
            if question["question"].lower() not in taken:
                taken.add(question["question"].lower())
                questions.append(question)
    return questions
