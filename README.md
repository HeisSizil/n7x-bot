# N7X Discord Bot

A community bot for **N7X**, a community about AI agents and Web3.
Built with Python, [discord.py](https://discordpy.readthedocs.io/), and SQLite.

## Project layout

```
bot.py            Starts the bot, loads features, catches errors
config.py         Reads settings from .env
database.py       All SQLite database code
ai_providers.py   Talks to Gemini / Groq / OpenRouter with automatic fallback
mod_tools.py      Shared moderation actions + safety checks (used by slash commands, auto-mod and assistant)
assistant_plan.py Mod assistant: checks the AI's plan (steps, delays, dependencies)
assistant_text.py Mod assistant: everything it says (5 built-in languages, others translated by the AI)
assistant_memory.py Mod assistant: short per-mod conversation memory (5 messages / 5 minutes)
tournament_engine.py Tournament & duel rules (no Discord code, fully tested)
trivia_bank.py    Trivia questions: loads trivia/, makes Fun Maths, optional Open Trivia DB
check_trivia.py   Checks every trivia file: python check_trivia.py
cogs/             One file per feature (add a file here to add a feature)
  general.py      /ping and /help
  ranks.py        Message XP, /rank, /leaderboard
  voice.py        Voice time, /voicerank, /voiceleaderboard
  crypto.py       /price (CoinGecko)
  ask.py          /ask (AI answers, N7X facts from facts.md)
  games.py        N7X Arcade: /flip, /dice, /rps, /wheel, /trivia, /highlow, /gameprofile, /gamesleaderboard
  tournament.py   /tournament, /duel, /duelstats
  welcome.py      Welcome embed for new members, /testwelcome
  automod.py      Auto-deletes spam and bad words, warns, logs
  moderation.py   /warn, /warnings, /clearwarnings, /kick, /timeout, /untimeout, /summarize
  mod_assistant.py  AI mod assistant: mods ask in plain words, confirm with a button
facts.md          Facts about N7X that /ask is allowed to use
bad_words.txt     Words the auto-mod deletes (edit freely)
welcome.md        The welcome message text (edit freely; {member} = the new member)
trivia/           Trivia questions, one JSON file per category (edit freely, see trivia/README.md)
images/           Pictures for trivia questions (see images/README.md)
.env.example      Template for your secret settings
requirements.txt  Python packages the bot needs
tests/            Automatic tests: python -m unittest discover tests -v
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
3. Under **Bot Permissions**, tick: `View Channels`, `Send Messages`, `Embed Links`, `Read Message History`,
   `Manage Messages` (delete spam), `Kick Members` (/kick) and `Moderate Members` (/timeout).
   No admin and no ban permission: the bot never bans anyone.
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
| `/flip <heads/tails>` | 🪙 Guess the coin flip (animated) |
| `/dice` | 🎲 Roll a die against N7X Bot; higher number wins (animated) |
| `/rps` | ✂️ Rock, paper, scissors against N7X Bot (buttons) |
| `/wheel` | 🎡 Spin the lucky wheel for a chance at XP (animated) |
| `/trivia [questions] [category]` | 🧠 A trivia quiz: 1-20 questions (default 5) from 🎲 Mixed, 🎬 Movies, 🍥 Anime, 🐾 Animals, 🌍 Geography, ➗ Fun Maths, 💡 Fun Facts or 🤖 AI & Web3 |
| `/highlow` | 🃏 Higher or Lower: guess the next number (1-13) and build a streak |
| `/gameprofile [member]` | 🎮 Level, title, game XP, wins and best streaks |
| `/gamesleaderboard` | 🏆 Top 10 by game wins, with plays and win rate |
| `/tournament <game> [max_players] [join_seconds] [test_mode] [questions]` | 🏆 **Mods only** (Manage Messages or Timeout Members): host an RPS, dice or trivia tournament (2+ players) |
| `/duel <member> <game>` | ⚔️ Challenge a member to a 1-vs-1 RPS, dice or trivia duel (no XP, just bragging rights) |
| `/duelstats [member]` | 📊 Duel wins/losses and tournament 🥇🥈🥉 finishes |

**Mod commands** (hidden from regular members; Discord checks the mod's permissions):

| Command | Who can use it | What it does |
|---|---|---|
| `/warn <member> <reason>` | Timeout Members | Saves a warning, DMs the member, posts to mod-log |
| `/warnings <member>` | Timeout Members | Shows a member's latest 10 warnings, numbered (#1 = oldest) |
| `/clearwarnings <member> [number] [reason]` | Timeout Members | Clears ALL of a member's warnings, or just one by its number from `/warnings` |
| `/kick <member> <reason>` | Kick Members | Kicks a member (they can rejoin with an invite) |
| `/timeout <member> <minutes> <reason>` | Timeout Members | Mutes a member for up to 28 days |
| `/untimeout <member> [reason]` | Timeout Members | Ends a member's timeout early (same code as the assistant's "unmute") |
| `/summarize [channel]` | Manage Messages | Private AI summary of a channel's last 50 messages |
| `/testwelcome [post_in_channel]` | Timeout Members | Private preview of the welcome message for yourself; optionally post it for real to test permissions |

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
- Members only see "N7X Bot" under answers. **Which AI answered is shown only in the terminal**, e.g. `/ask by sam: answered by Groq (llama-3.3-70b-versatile) in 2.1s`, along with why any provider failed, e.g. `AI: Gemini failed: rate limit / free quota reached (429) - trying next provider`.
- The bot shows "N7X Bot is thinking..." instantly, so slow AI answers never time out. Discord itself blocks questions over 500 characters.

### How the welcome message works
- When someone joins, the bot posts a branded embed in the welcome channel, mentioning the new member.
- **Setup:** right-click your welcome channel → **Copy Channel ID**, and add it to `.env`: `WELCOME_CHANNEL_ID=123456789012345678`. Give the bot **View Channel**, **Send Messages** and **Embed Links** there, then restart the bot. Leave it empty to turn welcomes off.
- **Change the text** in `welcome.md`. Write `{member}` where the @mention should go. The file is read fresh for every new member, so no restart is needed. Discord formatting (bold, links, #channels, emoji) works.
- **Picture:** the embed shows the server's icon. To use another image, put a direct `https://` image link in `.env` as `WELCOME_IMAGE_URL` (then restart).
- **Pings:** only the new member is pinged (so they get a notification). `@everyone`, `@here` and role mentions in `welcome.md` never ping anyone. Bots that join are not welcomed.
- If the bot can't post (missing permissions or a wrong channel ID), it prints one short line in the terminal and keeps running.
- **Preview:** `/testwelcome` (mods only) shows you a private preview, as if you just joined, and tells you if the bot is missing permissions in the welcome channel. Use `/testwelcome post_in_channel:True` to post a real test welcome.

### How the games work (N7X Arcade)
**The games**
- `/flip`, `/dice` and `/wheel` play a short animation (the message updates 3 times, 0.7 seconds apart) before showing the result.
- `/rps`: press **Rock**, **Paper** or **Scissors**. The bot picks only *after* you press, so it can't cheat. Buttons are disabled after the result, or after 30 seconds.
- `/trivia [questions] [category]`: a quiz of 1-20 questions (default 5, category default 🎲 Mixed) in **one message** that updates as you go: "Question X/Y • Score Z", **A/B/C/D** buttons, **20 seconds** per question. Answers are shuffled, no question repeats in a quiz (and your recent questions are avoided), and the right answer plus a short explanation shows for about 2 seconds before the next one. Only you can press your buttons. **2 unanswered questions in a row end the quiz.** The results card shows your score, a rating (🧠 N7X Genius 90%+ · 🔥 Pro 70%+ · 📈 Getting there 40%+ · 💪 Keep practicing) and your XP: **10 XP per right answer** (daily cap still applies). A quiz counts as one game played, and as a **win at 60% or more**. If a category has fewer questions than you asked for, you get all it has (the card says so).
- `/highlow`: you see a number from 1 to 13. Tap **Higher** or **Lower** for the next one. Each right guess adds to your streak (+2 XP, up to 20 per game). Same number = free pass. One wrong guess ends the game; a streak of **3 or more counts as a win**. You have 30 seconds per guess (if time runs out, the game ends and your streak still counts).
- `/gameprofile [member]`: level (the same level as `/rank`), a **title** that grows with your level, lifetime game XP, today's game XP, wins and win rate, best win streak, best Higher/Lower streak and leaderboard position.
  - Titles: 🐣 Rookie (level 0+), 🎮 Player (3+), ⚡ Pro (6+), 👑 Master (10+), 🌌 N7X Legend (15+).
- `/gamesleaderboard`: top 10 by wins with 🥇🥈🥉 for the podium, plays and win rate. If you're outside the top 10 it shows your position.

**Rules**
- **Fair:** every result uses Python's `secrets` module (secure randomness).
- **XP:** wins go into the **same XP as chatting**, so games count toward `/rank`:
  `/flip` 5 · `/dice` 5 · `/rps` 8 · `/trivia` 10 per right answer · `/highlow` 2 per right guess (max 20) · `/wheel` 0-20.
  Draws and losses give no XP.
- **Daily cap: 100 game XP per member**, resetting at 00:00 UTC, so nobody can farm it. Games still count for wins and the leaderboard after the cap.
- **Cooldown:** each game can be started once every **5 seconds** per member.
- Buttons only work for the player who started the game.
- **Prizes are XP and bragging rights only.** No money, tokens or crypto.
- Look: violet while a game is running, **green** for a win, **red** for a loss, amber for a draw, "N7X Bot" in the footer.

**Editing**
- **Trivia questions** live in the `trivia/` folder, one file per category (about 900 questions, plus endless Fun Maths made in code with believable wrong answers). Full guide: `trivia/README.md`. Each question looks like this:
  ```json
  {"question": "Which planet is known as the Red Planet?",
   "answers": ["Mars", "Venus", "Jupiter", "Mercury"],
   "correct": 0,
   "explanation": "Rust-coloured dust makes Mars look red."}
  ```
  `correct` is the position of the right answer (0 = first … 3 = fourth); answers are shuffled when shown. `explanation` is optional.
- **Adding a picture:** put the file (e.g. `okapi.jpg`) in `images/` and add `"image": "okapi.jpg"` to the question, or use a direct link: `"image": "https://..."`. Emoji work right in the question text too, e.g. `"Which animal is this? 🦒"` or `"Which country's flag is this? 🇧🇷"`. See `images/README.md`.
- **After editing, run `python check_trivia.py`.** It lists every problem by file and question number (missing answers, wrong `correct`, duplicates, missing image files...). The bot re-reads changed files automatically (no restart). A broken question is skipped; a broken file is skipped completely with one line in the Termux log.
- Easy, checkable facts only. No song lyrics or long quotes. Facts about N7X belong in `facts.md`, not in trivia.
- **Optional: Open Trivia DB.** Set `OPENTDB_ENABLED=true` in `.env` and `/trivia` also uses free easy questions from [opentdb.com](https://opentdb.com) (Mixed, Movies, Anime, Animals, Geography, Fun Facts). The bot respects its limit of one request per 5 seconds, and if it's slow, down or rate limited, it quietly uses the local files. Off by default.
- XP amounts, timers, the cap, the cooldown, the titles and the wheel's slots and odds are settings at the top of `cogs/games.py`.
- Upgrading from Stage 7: your existing game stats are kept. Lifetime game XP and streaks start counting from this version.

### How tournaments and duels work
**Starting a tournament (mods only):** `/tournament game:<RPS/Dice/Trivia>` with optional `max_players` (2-16, default 8), `join_seconds` (15-300, default 60) and `test_mode`.
- Only members with **Manage Messages** or **Timeout Members** can start one. Anyone else gets a private "Only mods can start tournaments."
- A lobby appears with **Join**, **Leave** and **Start now**. It shows **"Hosted by @host"** separately from the player list.
- **The host is not a player automatically.** To play, the host presses **Join** like everyone else; otherwise they just host. A host who didn't join is never pinged and never put in the bracket.
- `max_players` is a **hard cap**: when the lobby is full, Join privately says "Lobby is full." and the lobby shows **FULL (X/X)**. Leaving frees a spot.
- The tournament starts when the join timer ends with whoever joined (2 or more), or earlier when the **host** presses **Start now** (only the host can, and only with at least 2 players).
- At least **2 players** are needed, counting only people who pressed Join (plus 🤖 bots in test mode). Otherwise it's cancelled with a message saying how many joined, how many are needed, and that the host only counts if they pressed Join.
- One tournament per channel at a time.

**Playing a round:** the players in the round are pinged and press buttons (others can watch but not press). Picks stay hidden until everyone has chosen. **No answer within 30 seconds = you lose that round.**
- **RPS:** if exactly two different shapes are played, the winning shape goes through and the others are out (e.g. 2 scissors + 1 paper → the scissors players go through). All the same, or all three shapes → replay.
- **Dice:** press Roll. The lowest roll is out; a tied lowest roll → roll again.
- **Trivia:** the same question for everyone. Wrong answers are out; if everyone is right, the slowest is out; if nobody is right → new question.
- **Trivia mini quiz:** use `questions:` (1-10, default 1) on `/tournament` to play several questions per match. Most points goes through (lowest score is out in a 3-player group round). A tie = **⚡ sudden death**: one question at a time until it's decided; after 5 sudden-death questions a fair random pick decides. Questions never repeat within a tournament.
- If a round is still undecided after 5 replays, a fair random draw decides (so a game can't get stuck).

**Brackets and the podium**
- **2 players:** one match (the final). Winner 🥇, loser 🥈, no 🥉 and no 3rd place XP. RPS ties replay, dice ties re-roll (higher wins), trivia: a wrong answer loses, both right = faster wins, both wrong = new question.
- **3 players:** one group round. The player who's out gets 🥉 and the other two play the final for 🥇/🥈. (If only one player survives the group round, they get 🥇 and the other two play for 🥈/🥉.)
- **4+ players:** single elimination. If the number of players doesn't fit the bracket (5, 6, 7, 9...), random **free passes (byes)** are given in round 1 only, so nobody gets two byes and every semi-final is a real match. The two semi-final losers play a **3rd place match**, then the **final**.
- **Results card:** 🥇 🥈 🥉 with mentions and bonus game XP: **50 / 30 / 20**, still limited by the 100 game XP daily cap. Podium finishes are saved and shown in `/duelstats`.

**Test mode:** empty spots up to `max_players` are filled with **🤖 bot players** that pick randomly, so you can demo a whole tournament alone (press Join yourself to play against them, or don't, for a bots-only demo). Bots never get XP and are never saved in stats.

**Duels:** `/duel @member game:<RPS/Dice/Trivia>`. They have 30 seconds to **Accept**. Same round rules as tournaments (ties replay). **Duels give no XP**: just bragging rights. Wins and losses are saved for `/duelstats` (which shows duel wins/losses and tournament 🥇🥈🥉 finishes, never XP).

**Tests:** run `python -m unittest discover tests -v` (no extra installs, uses a temporary database). They cover 2 scissors vs 1 paper, all the same shape, all different shapes, 4/5/6/8 players, byes, timeouts, full lobbies, leaving freeing a spot, the host starting with 2/3/max players, the timer starting with 2, 2-player finals with no 🥉 in all three games, the host not joining (3 other players / only 1 player), the host joining as a player, a non-mod trying to start a tournament, duels giving no XP, 10-question trivia quizzes, quiz timeouts, categories with fewer questions than asked, trivia sudden death, Open Trivia DB (with a fake local server), broken trivia files, and `check_trivia.py` passing on the real files.

Settings (round time, podium XP, max players) are at the top of `cogs/tournament.py`; the rules are in `tournament_engine.py`.

### How the mod helper works
- **Set up a mod-log channel:** create a private channel only mods can see, give the bot **View Channel**, **Send Messages** and **Embed Links** there, then put its ID in `.env` as `MOD_LOG_CHANNEL_ID` (right-click the channel → Copy Channel ID).
- **Auto-mod** checks every message from non-mods (anyone with Manage Messages is trusted):
  - **Repeated messages:** the same message 3 times within 30 seconds.
  - **Too many links:** more than 3 links in one message.
  - **Bad words:** anything in `bad_words.txt` (whole words only, so "ass" won't catch "class"). Edit the file anytime; no restart needed.
  - It **deletes** the message, posts a short notice in the channel (it disappears after 10s), **DMs a warning**, saves it, and posts a note in mod-log with a hidden excerpt so mods can judge. During a spam burst it keeps deleting but warns only once a minute.
  - Settings are at the top of `cogs/automod.py`.
- **The bot suggests, humans decide:** when a member reaches 3 warnings, mod-log suggests a human review. The bot has no ban command and never bans.
- **Clearing warnings:** run `/warnings` to see the numbers, then `/clearwarnings member:@x number:3` to remove one, or leave out `number` to clear all of them. Numbers count from the oldest (#1), so after clearing one, the later ones move up (old #4 becomes #3). Every clear is posted in mod-log (who, whose, how many, which, reason). Old mod-log messages are never deleted, so the history stays.
- Mods can't act on themselves, the owner, the bot, or anyone with an equal or higher role. For `/kick` and `/timeout`, the **bot's role must be above the member's role** (Server Settings → Roles → drag the bot's role up).
- **`/summarize` privacy:** the last 50 messages are sent to the AI provider to make the summary. Nothing is saved. Mods can only summarize channels they can read themselves.

### How the mod assistant works
Mods can ask the bot to do moderation in plain words instead of slash commands.

**It only answers when you tag it:** **@mention the bot**, or use Discord's **Reply** on one of the bot's messages. Normal chat, even in the mod-assistant channel, is never sent to the AI.

**Optional private channel:** `MOD_ASSISTANT_CHANNEL_ID` only decides where *private* results (warnings lists, summaries) are shown. Asked there (or in mod-log), they're posted in the channel; asked anywhere else, they're sent to you by **DM**.
1. Create a private channel only mods can see, e.g. `#mod-assistant`. Give the bot **View Channel**, **Send Messages**, **Embed Links** and **Read Message History** there.
2. Right-click it → **Copy Channel ID** and add it to `.env`: `MOD_ASSISTANT_CHANNEL_ID=123456789012345678`
3. Restart the bot.

**Say it however you like.** The AI matches meaning, not exact words, and handles typos, slang and Nigerian Pidgin. Some examples (always start with `@N7X Bot`):

| What | Examples |
|---|---|
| Show warnings | `what has @user done`, `check @user's record`, `how many warnings does @user have`, `any strikes on @user` |
| Warn | `warn @user for spamming`, `give @user a warning for links`, `strike @user for links`, `caution @user, rude` |
| Clear warnings | `wipe warnings for @user`, `reset @user record`, `forgive @user`, `pardon @user`, `remove warning 2 from @user` |
| Timeout | `mute @user for 10 minutes`, `silence @user for a day`, `shush @user 30 mins`, `put @user on timeout for 2h`, `gag @user 15m`, `abeg mute @user for 10 mins` |
| Remove timeout | `unmute @user`, `untimeout @user`, `free @user`, `let @user talk again`, `lift the mute on @user`, `release @user` |
| Kick | `boot @user for scams`, `throw @user out, spam bot`, `remove @user from the server, raiding` |
| Summarize | `what happened in #general`, `catch me up on #general`, `recap #general`, `tldr #general` |
| Cancel a scheduled step | `cancel the scheduled removal for @user`, `stop the planned unmute for @user` |

Durations like `10m`, `ten minutes`, `an hour`, `2 days` and `a week` all work.

**Multi-step requests** (up to 3 steps in one sentence):
- `@N7X Bot warn @user for spam and timeout them for 10 minutes`
- `@N7X Bot timeout @user for 30 minutes then remove it after 20 minutes`

The bot shows **one** numbered plan to confirm, e.g.
```
Please confirm this plan:
1. Timeout @user for 30m — reason: spam (starts now)
2. Remove @user's timeout (after 20m)
ℹ️ The timeout would run for 30m, but it will be removed after 20m.
```
- Delays count from the moment you press **Confirm** (max **7 days**). Steps with no delay run right away; delayed steps are saved in the database (`scheduled_actions`), so they still run after a restart, and the result is posted in the channel where you asked.
- **Every step re-runs all safety checks when it runs**, not just at Confirm. If a step fails (e.g. the member left, or their timeout was already removed), the steps that depend on it are skipped, and each step's result is posted in mod-log.
- A step can only depend on an **earlier** step of the same request: e.g. "remove the timeout" needs the member to be timed out already, or an earlier "timeout" step. Nothing can come after kicking the same member.
- If **any** step is unclear or invalid, or the AI is less than **90% sure**, nothing runs and the bot asks what you meant.
- **Changed your mind?** `@N7X Bot cancel the scheduled removal for @user` deletes that member's pending scheduled steps (after you Confirm) and notes it in mod-log. It can't be combined with other steps.
- Showing warnings and summaries can't be part of a multi-step plan; ask for them on their own.
- A delayed step that was running when the bot crashed is marked failed, never re-run blindly.

**Conversation memory (follow-ups):**
- The bot remembers your last **5 messages** to it in that channel (and its replies) for **5 minutes** of silence. Other members' messages are never remembered. Memory is per mod and per channel, and is forgotten when the bot restarts.
- Vague requests get a question back: `@N7X Bot deal with @user` → "What would you like to do with @user? For example: show their warnings, warn them, time them out…"
- Then just reply (or tag the bot): `timeout him for 30 mins`. Words like **him / her / them / this guy / that user** only ever mean the member **you** @mentioned earlier in that window, and only if you mentioned exactly one. Otherwise the bot asks you to @mention the member.

**Languages:**
- Replies are in your language. English, Spanish, French, Portuguese and German are built in (`assistant_text.py`).
- Any other language (e.g. Yoruba, Hausa, Igbo, Nigerian Pidgin) is translated by the AI **once**, kept in memory until the bot restarts, and falls back to English if the AI can't translate. Mod-log notes always stay in English.
- Details coming from the action itself (e.g. "has the same or a higher role than you") stay in English.

**Rules it follows:**
- Only members with **Timeout Members** or **Manage Messages** can use it; anyone else is ignored silently. Each tool still needs the same permission as its slash command (e.g. kicking needs **Kick Members**).
- Members must be **@mentioned** (or given by user ID), or be the one you mentioned earlier in the conversation (see above). If you just type a name, it asks you to mention them.
- It can only do these 8 things: show warnings, warn, clear warnings, timeout (max 28 days), remove a timeout, kick, summarize, cancel scheduled steps. **No bans, no role changes.**
- Anything that changes something shows **Confirm / Cancel** buttons. Only the mod who asked can press them, they expire after **60 seconds**, and every safety check runs **again** when each step runs.
- **5 requests per minute** per mod.
- Every request and every step's result is posted in mod-log: who asked, what was understood, and the outcome.
- Only your own messages (and the bot's replies) are sent to the AI, plus the channel's messages for `summarize`. The terminal logs IDs and outcomes, never message text.

## Adding a new feature

1. Create a new file in `cogs/`, e.g. `cogs/myfeature.py`.
2. Copy the structure of `cogs/general.py` (a class, your commands, and an `async def setup(bot)` at the bottom).
3. Restart the bot. It loads every file in `cogs/` automatically, and `/help` lists the new commands.
   (A feature without slash commands can add a `/help` line by setting `help_entry = ("Title", "text")` on its cog, like `cogs/mod_assistant.py`.)

## Troubleshooting

- **"privileged intents are switched off":** do step 2.3 above (turn on Server Members Intent and Message Content Intent), then restart the bot.
- **Messages aren't counted for XP:** watch the terminal while you chat. Every message the bot receives prints an `on_message:` line, then `Counted message...` or `Skipped message...`. If **nothing** prints, Discord isn't sending that channel to the bot:
  - At startup the bot lists every server it's in. Check your test server is there with the right ID.
  - At startup it also prints `Can't read #channel` for each channel it can't see. Give its role **View Channels** and **Read Message History** there.
- **Commands don't appear:** check `GUILD_ID` is set, restart the bot, then restart Discord (Ctrl + R).
- **"DISCORD_TOKEN is missing":** your `.env` file is missing or the token line is empty.
- **"Improper token has been passed":** reset the token in the Developer Portal and paste the new one.
