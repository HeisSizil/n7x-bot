# N7X Discord Bot

A community bot for **N7X**, a community about AI agents and Web3.
Built with Python, [discord.py](https://discordpy.readthedocs.io/), and SQLite.

## Project layout

```
bot.py            Starts the bot, loads features, catches errors
config.py         Reads settings from .env
database.py       All SQLite database code
cogs/             One file per feature (add a file here to add a feature)
  general.py      /ping and /help
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
3. On the same page, under **Privileged Gateway Intents**, you can leave everything off for now. (Later stages will tell you which to switch on.)

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

## Adding a new feature

1. Create a new file in `cogs/`, e.g. `cogs/myfeature.py`.
2. Copy the structure of `cogs/general.py` (a class, your commands, and an `async def setup(bot)` at the bottom).
3. Restart the bot. It loads every file in `cogs/` automatically, and `/help` lists the new commands.

## Troubleshooting

- **Commands don't appear:** check `GUILD_ID` is set, restart the bot, then restart Discord (Ctrl + R).
- **"DISCORD_TOKEN is missing":** your `.env` file is missing or the token line is empty.
- **"Improper token has been passed":** reset the token in the Developer Portal and paste the new one.
