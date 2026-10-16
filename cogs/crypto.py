"""
cogs/crypto.py - Crypto prices from the free CoinGecko API.

/price <coin> accepts a name ("bitcoin"), a ticker ("btc") or a CoinGecko ID ("solana").

How it works:
1. Ask CoinGecko's search to turn what the user typed into a coin ID.
2. Ask CoinGecko for that coin's current price and 24-hour change.
3. Remember the answer for a few seconds (PRICE_CACHE_SECONDS), so many
   people asking at once doesn't use up the free API limit.

Commands: /price
"""

import logging
import time
from datetime import datetime

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config

log = logging.getLogger("n7x.crypto")

# --- Settings you can tweak ---
API_URL = "https://api.coingecko.com/api/v3"
PRICE_CACHE_SECONDS = 15  # reuse a price for this long before asking CoinGecko again
                          # (lower = fresher, but uses more of the free API limit)
REQUEST_TIMEOUT = 10    # give up on CoinGecko after this many seconds


class CoinNotFound(Exception):
    """The coin the user typed doesn't exist on CoinGecko."""


class RateLimited(Exception):
    """CoinGecko says we asked too often."""


def format_price(price):
    """Show big prices with 2 decimals, tiny prices with more (e.g. $0.00001234)."""
    if price >= 1:
        return f"${price:,.2f}"
    if price >= 0.01:
        return f"${price:.4f}"
    return f"${price:.8f}"


def format_big_number(number):
    """Turn 1234567890 into '$1.23B'."""
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if number >= limit:
            return f"${number / limit:.2f}{suffix}"
    return f"${number:,.0f}"


def seconds_since_update(coin, fetched_at):
    """
    How old is this price, in seconds?
    CoinGecko tells us when IT last updated the price ("last_updated"), which is
    the honest answer. If that's missing, we use when the bot fetched it.
    """
    updated_at = fetched_at
    last_updated = coin.get("last_updated")
    if last_updated:
        try:
            # e.g. "2026-10-16T12:47:20.123Z" ("Z" means UTC)
            updated_at = datetime.fromisoformat(last_updated.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass  # unexpected format: keep the fetch time
    return max(0, int(time.time() - updated_at))


def format_age(seconds):
    """Turn 42 into '42s ago' and 150 into '2m ago'."""
    if seconds < 120:
        return f"{seconds}s ago"
    return f"{seconds // 60}m ago"


def pick_best_match(query, coins):
    """
    Choose the right coin from CoinGecko's search results.
    Many coins share a ticker (lots of tokens call themselves "BTC"),
    so we prefer: exact ID match, then exact ticker match, then exact name.
    Search results are already sorted with the biggest coins first.
    """
    query = query.lower()
    for coin in coins:
        if coin["id"].lower() == query:
            return coin
    for coin in coins:
        if coin["symbol"].lower() == query:
            return coin
    for coin in coins:
        if coin["name"].lower() == query:
            return coin
    return coins[0] if coins else None


def price_embed(data, fetched_at):
    """The price card: price, 24h change, market cap, rank and the CoinGecko source footer."""
    change = data.get("price_change_percentage_24h") or 0.0
    arrow = "🟢 ▲" if change >= 0 else "🔴 ▼"

    embed = discord.Embed(
        title=f"{data['name']} ({data['symbol'].upper()})",
        url=f"https://www.coingecko.com/en/coins/{data['id']}",
        color=0x16C784 if change >= 0 else 0xEA3943,  # green up, red down
    )
    if data.get("image"):
        embed.set_thumbnail(url=data["image"])
    embed.add_field(name="Price", value=format_price(data["current_price"]))
    embed.add_field(name="24h change", value=f"{arrow} {change:+.2f}%")
    if data.get("market_cap"):
        embed.add_field(name="Market cap", value=format_big_number(data["market_cap"]))
    if data.get("market_cap_rank"):
        embed.add_field(name="Rank", value=f"#{data['market_cap_rank']}")
    # Tell members how old the price is, since other sites may show a slightly different number.
    age = format_age(seconds_since_update(data, fetched_at))
    embed.set_footer(text=f"Source: CoinGecko, updated {age} • Not financial advice")
    return embed


class Crypto(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = None   # web connection, created in cog_load
        self.cache = {}       # coin query -> (time saved, coin data)

    async def cog_load(self):
        headers = {"accept": "application/json"}
        if config.COINGECKO_API_KEY:
            # Optional free "demo" key gives a higher rate limit.
            headers["x-cg-demo-api-key"] = config.COINGECKO_API_KEY
        self.session = aiohttp.ClientSession(
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            trust_env=True,  # respect HTTPS_PROXY if your network uses one
        )

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    async def get_json(self, path, params):
        """Call CoinGecko and return the JSON, or raise a clear error."""
        async with self.session.get(f"{API_URL}{path}", params=params) as response:
            if response.status == 429:
                raise RateLimited()
            response.raise_for_status()  # any other bad status -> error
            return await response.json()

    async def fetch_coin(self, query):
        """Find a coin and return its market data (price, 24h change, ...)."""
        # 1. Turn "btc" / "Bitcoin" into CoinGecko's ID "bitcoin".
        search = await self.get_json("/search", {"query": query})
        match = pick_best_match(query, search.get("coins", []))
        if match is None:
            raise CoinNotFound()

        # 2. Get the price data for that ID.
        markets = await self.get_json(
            "/coins/markets",
            {"vs_currency": "usd", "ids": match["id"], "price_change_percentage": "24h"},
        )
        if not markets or markets[0].get("current_price") is None:
            raise CoinNotFound()  # listed, but no price available
        return markets[0]

    async def get_coin(self, query):
        """
        Like fetch_coin, but reuses recent answers from the cache.
        Returns (coin data, time we fetched it).
        """
        key = query.lower()
        saved = self.cache.get(key)
        if saved and time.time() - saved[0] < PRICE_CACHE_SECONDS:
            return saved[1], saved[0]
        coin = await self.fetch_coin(query)
        fetched_at = time.time()
        self.cache[key] = (fetched_at, coin)
        return coin, fetched_at

    async def lookup(self, coin):
        """Price card for one coin: returns (embed, None), or (None, a friendly error message).
        Used by /price and by tagged price questions."""
        try:
            data, fetched_at = await self.get_coin(coin)
        except CoinNotFound:
            return None, (f"🤔 I couldn't find a coin called **{discord.utils.escape_markdown(coin)}**. "
                          f"Check the spelling, or try its full name (e.g. `bitcoin` instead of `bitcoinn`).")
        except RateLimited:
            log.warning("CoinGecko rate limit hit")
            return None, "⏳ Too many price checks right now. Please try again in a minute."
        except (aiohttp.ClientError, TimeoutError) as error:
            # CoinGecko is down, slow, or returned an error. Not our bug: one short log line.
            log.warning("CoinGecko request failed for %r: %s", coin, str(error) or "timed out")
            return None, "⚠️ I couldn't reach the price service. Please try again in a minute."
        except Exception:
            # Something unexpected (e.g. CoinGecko changed its data format): full details.
            log.exception("Unexpected error getting the price of %r", coin)
            return None, "⚠️ Something went wrong getting that price. Please try again later."
        return price_embed(data, fetched_at), None

    @app_commands.command(name="price", description="Current price and 24h change of a crypto coin")
    @app_commands.describe(coin="Coin name or ticker, e.g. bitcoin, btc, eth, sol")
    @app_commands.checks.cooldown(1, 5)  # each user: once every 5 seconds
    async def price(self, interaction: discord.Interaction, coin: str):
        coin = coin.strip()
        if not coin or len(coin) > 50:
            await interaction.response.send_message("Please type a coin name, like `bitcoin` or `eth`.", ephemeral=True)
            return

        # CoinGecko can take a few seconds; "defer" shows "Bot is thinking..."
        await interaction.response.defer()
        embed, problem = await self.lookup(coin)
        if problem:
            await interaction.followup.send(problem)
        else:
            await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Crypto(bot))
