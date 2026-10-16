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

# --- AI provider keys (used in Stage 5) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

# --- Crypto prices (Stage 4) ---
# Optional. The bot works without it; a free CoinGecko "demo" key just raises the rate limit.
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "").strip()

# --- Branding ---
BRAND_COLOR = 0x7B2FF7  # N7X purple, used for embeds
