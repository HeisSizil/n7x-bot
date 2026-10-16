# N7X Discord Bot

A community bot for **N7X**, a community about AI agents and Web3.
Built with Python, [discord.py](https://discordpy.readthedocs.io/), and SQLite.

## Project layout

```
bot.py            Starts the bot, loads features, catches errors
config.py         Reads settings from .env
database.py       All SQLite database code
ai_providers.py   Talks to Gemini / Groq / OpenRouter with automatic fallback
cogs/             One file per feature (add a file here to add a feature)
  general.py      /ping and /help
  ranks.py        Message XP, /rank, /leaderboard
  voice.py        Voice time, /voicerank, /voiceleaderboard
  crypto.py       /price (CoinGecko)
  ask.py          /ask (AI answers, N7X facts from facts.md)
facts.md          Facts about N7X that /ask is allowed to use
.env.example      Template for your secret settings
requirements.txt  Python packages the bot needs
```

## How to run the bot

### 1. Install Python
Install **Python 3.10 or newer** from [python.org](https://www.python.org/downloads/).
On Windows, tick **"Add Python to PATH"** during install.

### 2. Create the bot on Discord
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**. Name it (e.g. "N7X Bot").
2. Open the **Bot** tab and click **Reset Token**. Copy the token. **Keep it secret** — anyone with it can control your bot.
3. On the same page, under **Privileged Gateway Intents**, switch ON **Server Members Intent** and **Message Content Intent**, then click **Save Changes**. The bot will refuse to start without these.

### 3. Invite the bot to your test server
1. Open **OAuth2 → URL Generator**.
2. Under **Scopes**, tick `bot` and `applications.commands`.
3. Under **Bot Permissions**, tick: `View Channels`, `Send Messages`, `Embed Links`, `Read Message History`.
   (Later stages add a few more; you'll be told which.)
4. Copy the URL at the bottom, open it in your browser, and pick your test server.

### 4. Download the code and install packages
Open a terminal in the project folder and run:

```bash
# Create a private Python environment for this project (do this once)
python -m venv venv

# Turn it on (do this every time you open a new terminal)
# Windows:
venv\Scripts\activate
# Mac / Linux:
source venv/bin/activate

# Install the packages
pip install -r requirements.txt
```

### 5. Add your secrets
1. Copy `.env.example` to a new file named `.env`.
2. Paste your bot token after `DISCORD_TOKEN=`.
3. Paste your test server's ID after `GUILD_ID=` so commands show up instantly.
   To get it: Discord **Settings → Advanced → Developer Mode** on, then right-click your server icon → **Copy Server ID**.

`.env` is listed in `.gitignore`, so it will never be uploaded to GitHub.

### 6. Start the bot
```bash
python bot.py
```
You should see `Logged in as N7X Bot#1234` in the terminal. Press **Ctrl + C** to stop it.

## Commands

| Command | What it does |
|---|---|
| `/help` | Lists every command |
| `/ping` | Checks the bot is online |
| `/rank [member]` | Shows level, XP, message count and position |
| `/leaderboard` | Top 10 members by messages |
| `/voicerank [member]` | Shows voice time and position |
| `/voiceleaderboard` | Top 10 members by voice time |
| `/price <coin>` | Current price and 24h change, e.g. `/price btc` |
| `/ask <question>` | AI answer about AI agents, crypto, Web3 or N7X |

### How message XP works
- Every message is counted. XP (15-25 per message) is given at most **once every 60 seconds** per member, so spamming doesn't farm XP.
- Only counts and XP are stored. **Message text is never saved.**
- To change the reward or cooldown, edit the settings at the top of `cogs/ranks.py`.

### How voice time works
- Once a minute, the bot checks every voice channel. If **at least 2 real people** are in a channel, each of them gets 1 minute.
- Time spent **alone** (or alone with only bots) doesn't count. The server's AFK channel is ignored.
- The bot needs **View Channel** on voice channels to see who is in them.
- To change the timing or the minimum number of people, edit the settings at the top of `cogs/voice.py`.

### How /price works
- Type a name (`bitcoin`), ticker (`btc`) or CoinGecko ID (`solana`). Prices come from the free [CoinGecko API](https://www.coingecko.com/en/api) - no key needed.
- Each price is reused for **15 seconds** (`PRICE_CACHE_SECONDS` at the top of `cogs/crypto.py`), and each member can use `/price` once every 5 seconds, to stay inside CoinGecko's free limit.
- The footer shows how old the price is (e.g. "Source: CoinGecko, updated 12s ago"), using CoinGecko's own update time. Prices can differ slightly from exchanges and other sites.
- Optional: if you often see "Too many price checks", get a free CoinGecko **demo** key and put it in `.env` as `COINGECKO_API_KEY`.

### How /ask works
- **Free AI with fallback:** the bot tries **Gemini** first, then **Groq**, then **OpenRouter** (free models only). If one fails or hits its free limit, it tries the next. If all fail, it says "Try again in a minute."
- **Keys:** you need at least one; providers without a key are skipped. All three are free:
  - Gemini: https://aistudio.google.com/apikey
  - Groq: https://console.groq.com/keys
  - OpenRouter: https://openrouter.ai/keys
- **Models** are set in `.env` (`GEMINI_MODEL`, `GROQ_MODEL`, `OPENROUTER_MODEL`). If a provider retires a model, just put the new name there. OpenRouter models must end in `:free`, or the bot skips OpenRouter (so you can never be billed).
- **N7X questions are answered ONLY from `facts.md`.** If the answer isn't there, the bot says "I don't know yet, please ask a mod." Add facts as lines starting with `- `; the bot re-reads the file on every question, so no restart is needed.
- Each member can ask **3 questions per minute** (setting at the top of `cogs/ask.py`).
- The terminal shows which provider answered, or why each one failed, e.g. `AI: Gemini failed: rate limit / free quota reached (429) - trying next provider`.

## Adding a new feature

1. Create a new file in `cogs/`, e.g. `cogs/myfeature.py`.
2. Copy the structure of `cogs/general.py` (a class, your commands, and an `async def setup(bot)` at the bottom).
3. Restart the bot. It loads every file in `cogs/` automatically, and `/help` lists the new commands.

## Troubleshooting

- **"privileged intents are switched off":** do step 2.3 above (turn on Server Members Intent and Message Content Intent), then restart the bot.
- **Messages aren't counted for XP:** watch the terminal while you chat. Every message the bot receives prints an `on_message:` line, then `Counted message...` or `Skipped message...`. If **nothing** prints, Discord isn't sending that channel to the bot:
  - At startup the bot lists every server it's in. Check your test server is there with the right ID.
  - At startup it also prints `Can't read #channel` for each channel it can't see. Give its role **View Channels** and **Read Message History** there.
- **Commands don't appear:** check `GUILD_ID` is set, restart the bot, then restart Discord (Ctrl + R).
- **"DISCORD_TOKEN is missing":** your `.env` file is missing or the token line is empty.
- **"Improper token has been passed":** reset the token in the Developer Portal and paste the new one.
