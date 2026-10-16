"""
n7x_facts.py - Reads facts.md: the ONLY source the bot uses for anything about N7X.

- load_facts(): the facts as text (comments removed), read fresh every time.
- sections(): the facts as (title, [lines]) for /about.
- Link / docs / roadmap / whitepaper requests get an answer built straight from the
  facts (release dates), so the bot can never invent a link.
- remove_invented_links(): a safety net that strips any N7X link that isn't in facts.md.
"""

import logging
import os
import re

log = logging.getLogger("n7x.facts")

FACTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "facts.md")
DONT_KNOW = "I don't know yet, please ask a mod."

URL = re.compile(r"(?:https?://|www\.|discord\.gg/)[^\s<>()\]]+", re.IGNORECASE)
LINK_WORDS = re.compile(
    r"\b(links?|website|site|url|web ?page|socials?|social media|twitter|telegram|instagram|"
    r"tiktok|youtube|linkedin|docs?|documentation|gitbook|roadmap|white ?paper|lite ?paper|"
    r"tokenomics paper)\b", re.IGNORECASE)
# Words that show the question is about N7X's own links, not e.g. "what is a whitepaper?"
OWN_PROJECT = re.compile(
    r"\b(n7x|official|your|ur|our|the project|this project|y'?all|una|where|when|send|share|drop|"
    r"get|find|release[ds]?|launch(?:ed|es)?|out yet|available|published)\b", re.IGNORECASE)
OTHER_PROJECTS = re.compile(
    r"\b(bitcoin|btc|ethereum|eth|solana|sol|bnb|binance|cardano|ripple|xrp|dogecoin|polygon|"
    r"chainlink|avalanche|uniswap|aave|openai|chatgpt|gemini|claude|llama)\b", re.IGNORECASE)


def load_facts():
    """Read facts.md fresh every time, without the <!-- comment --> sections."""
    try:
        with open(FACTS_FILE, encoding="utf-8") as file:
            text = file.read()
    except FileNotFoundError:
        log.warning("facts.md not found at %s", FACTS_FILE)
        return ""
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()


def sections(text=None):
    """[(title, [lines])] from "## Title" headings and "- line" bullets."""
    text = load_facts() if text is None else text
    result = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            result.append((line.lstrip("#").strip(), []))
        elif line.startswith(("- ", "* ")) and line[2:].strip():
            if not result:
                result.append(("About N7X", []))
            result[-1][1].append(line[2:].strip())
    return [(title, lines) for title, lines in result if lines]


def mentions_n7x(text):
    return re.search(r"\bn7x\b", text, re.IGNORECASE) is not None


def is_link_request(question):
    """True for "where is the N7X whitepaper?", "send the website link", "roadmap?"...
    but not for general questions like "what is a whitepaper?" or "bitcoin whitepaper"."""
    if not LINK_WORDS.search(question):
        return False
    if mentions_n7x(question):
        return True
    if OTHER_PROJECTS.search(question):
        return False
    if re.match(r"\s*(what|wetin|explain|define)\b", question, re.IGNORECASE) \
            and not re.search(r"\b(official|your|ur|our|the project|this project)\b", question, re.IGNORECASE):
        return False   # "what is a roadmap?" is a general question
    return bool(OWN_PROJECT.search(question)) or len(question.split()) <= 4


def release_lines(text=None):
    """Lines from the release / links sections of facts.md (empty if there are none)."""
    lines = []
    for title, items in sections(text):
        if re.search(r"release|link|document|website|roadmap|whitepaper", title, re.IGNORECASE):
            lines += items
    return lines


def links_answer(text=None):
    """The honest answer to "where are the links / docs / roadmap / whitepaper?" built from facts.md."""
    lines = release_lines(text)
    if not lines:
        return DONT_KNOW
    return "Here's what's official so far:\n" + "\n".join(f"• {line}" for line in lines)


def has_release_dates(answer, text=None):
    """Does an answer mention the release dates from facts.md (e.g. 20 and 22)?"""
    days = set(re.findall(r"\b(\d{1,2})\b", " ".join(release_lines(text))))
    return bool(days) and all(re.search(rf"\b{day}\b", answer) for day in days)


def remove_invented_links(answer, facts, about_n7x):
    """Remove links that aren't written in facts.md: every link in an N7X answer, and any link
    that looks like an N7X link anywhere. Real links added to facts.md later are kept."""
    def check(match):
        url = match.group(0).rstrip(".,!?")
        if url in facts:
            return match.group(0)
        if about_n7x or "n7x" in url.lower():
            return "(no official link published yet)"
        return match.group(0)
    return URL.sub(check, answer)
