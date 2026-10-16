"""
config.py - Loads settings from the .env file in one place.

Every other file imports settings from here, so if you add a new
setting later, you only add it in two places: .env and this file.
"""

import os

from dotenv import load_dotenv

# Read the .env file and put its values into environment variables.
load_dotenv()


def _get_int(name):
    """Read a setting as a number. Returns None if it's empty or not a number."""
    value = os.getenv(name, "").strip()
    return int(value) if value.isdigit() else None


# --- Discord ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = _get_int("GUILD_ID")

# --- AI providers (Stage 5) ---
# The bot tries them in this order: Gemini -> Groq -> OpenRouter.
# A provider with no key is simply skipped.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

# Model names. Change them in .env when providers release new models.
# "or" means: use the default if the .env line is missing or empty.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip() or "gemini-2.5-flash"
GROQ_MODEL = os.getenv("GROQ_MODEL", "").strip() or "llama-3.3-70b-versatile"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "").strip() or "meta-llama/llama-3.3-70b-instruct:free"

# --- Crypto prices (Stage 4) ---
# Optional. The bot works without it; a free CoinGecko "demo" key just raises the rate limit.
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "").strip()

# --- Channels ---
# Where the bot posts moderation notes (deleted spam, warnings, kicks...).
MOD_LOG_CHANNEL_ID = _get_int("MOD_LOG_CHANNEL_ID")
# Optional. Every message mods send in this channel goes to the AI mod assistant.
# (Mods can also @mention the bot anywhere.)
MOD_ASSISTANT_CHANNEL_ID = _get_int("MOD_ASSISTANT_CHANNEL_ID")

# --- Branding ---
BRAND_COLOR = 0x7B2FF7  # N7X purple, used for embeds
