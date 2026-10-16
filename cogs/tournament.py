"""
cogs/tournament.py - /tournament, plus the lobby, round and session code /duel reuses.

The rules live in tournament_engine.py. This file handles the buttons and messages:
- /tournament (mods only): a lobby with Join / Leave / Start now. The host is NOT a
  player unless they press Join too. It starts when the join timer ends (or the host
  presses Start now) with whoever joined. Max players (2-100) is a hard cap; at least 2 are
  needed (2 players = one final, no 3rd place).
  Then rounds are played in the channel with buttons.
- /duel and /duelstats live in cogs/duels.py (they reuse the classes here).

Tournament podium bonus XP (50/30/20) uses the shared game XP daily cap (100, resets
00:00 UTC). Secure randomness (secrets). XP only - no real prizes.

Test mode: empty spots are filled with 🤖 bot players that pick randomly, so a whole
tournament can be demoed alone. Bots never get XP or stats.
"""

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

import database
import tournament_engine as te
import trivia_bank
from game_panel import MAX_NAMES, Scoreboard, clip, clip_list, fit_embed, is_mod, mention_chunks
from cogs.games import (COLOR_DRAW, COLOR_GAME, COLOR_LOSE, COLOR_WIN, DAILY_GAME_XP_CAP, DICE_FACES, FOOTER,
                        LETTER_EMOJI, LETTERS, load_trivia, today)

log = logging.getLogger("n7x.tournament")

# --- Settings you can tweak ---
ROUND_SECONDS = 30            # a player who doesn't respond in time loses that round
PODIUM_XP = (50, 30, 20)      # bonus game XP for 1st, 2nd, 3rd (still limited by the daily cap)
MAX_PLAYERS_LIMIT = 100      # biggest lobby for /tournament and /duel free for all
FREE_FOR_ALL_MIN = 2          # /duel free for all needs at least this many players
SEND_GAP = 0.5                # pause after each channel message (keeps us well inside Discord's rate limits)
SEND_RETRIES = 3              # a rate-limited message is retried this many times
RATE_LIMIT_PAUSE = 2.0        # seconds to slow down when rate-limited (if Discord doesn't say)
BATCH_WINDOW = 0.3            # matches/results arriving within this many seconds are posted together
MAX_MATCH_LINES = 20          # matches listed in one round message before "and N more"
LOCKED_IN_EDIT_GAP = 2.0      # update a round's "Locked in" line at most this often
COLOR_GOLD = 0xFACC15

GAMES = {"rps": "🪨📄✂️ Rock Paper Scissors", "dice": "🎲 Dice", "trivia": "🧠 Trivia"}
GAME_CHOICES = [app_commands.Choice(name=label, value=key) for key, label in GAMES.items()]


@dataclass(frozen=True)
class BotPlayer:
    """A test-mode bot. Never gets XP, never saved in stats."""
    id: int
    display_name: str

    @property
    def mention(self):
        return f"**{self.display_name}**"


def is_bot(player):
    return isinstance(player, BotPlayer)


def t_embed(title, description="", color=COLOR_GAME, header="🏆 N7X Tournament"):
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_author(name=header)
    embed.set_footer(text=FOOTER)
    return embed


def new_trivia_question(asked=()):
    """A random Mixed question (answers shuffled securely), avoiding ones already asked when possible."""
    picked = trivia_bank.pick("mixed", 1, avoid=asked)
    return picked[0] if picked else None


def bot_response(kind, question):
    """What a test bot answers (random)."""
    if kind == "rps":
        return secrets.choice(list(te.RPS_SHAPES))
    if kind == "dice":
        return secrets.randbelow(6) + 1
    correct = secrets.randbelow(100) < 60           # bots are right 60% of the time
    seconds = 3 + secrets.randbelow(2200) / 100     # and take 3-25 seconds
    return correct, seconds


def describe_response(kind, response, question=None):
    if response is None:
        return "⏰ no answer"
    if isinstance(response, int) and kind == "trivia":
        return f"🧠 {response} point{'s' if response != 1 else ''}"   # mini quiz score
    if kind == "rps":
        return f"{te.RPS_SHAPES[response]} {response.capitalize()}"
    if kind == "dice":
        return f"{DICE_FACES[response - 1]} {response}"
    correct, seconds = response
    return f"✅ right in {seconds:.1f}s" if correct else f"❌ wrong ({seconds:.1f}s)"


class RoundView(discord.ui.View):
    """Buttons for one round. Only players in the round can press, once each; picks stay hidden."""

    def __init__(self, humans, kind, question, responses, removed=()):
        super().__init__(timeout=ROUND_SECONDS + 10)
        self.humans = {player.id: player for player in humans}
        self.removed = removed                              # players the host removed (shown as 🚫)
        self.ends_at = time.monotonic() + ROUND_SECONDS     # for the host panel's "time left"
        self.kind = kind
        self.question = question
        self.responses = responses
        self.started = time.monotonic()
        self.all_done = asyncio.Event()
        self.closed = False
        self.message = None
        self.last_edit = 0.0

        if kind == "rps":
            options = [(shape.capitalize(), emoji, shape) for shape, emoji in te.RPS_SHAPES.items()]
        elif kind == "dice":
            options = [("Roll", "🎲", "roll")]
        else:
            options = [(letter, LETTER_EMOJI[letter], index) for index, letter in enumerate(LETTERS)]
        for label, emoji, value in options:
            button = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.primary)
            button.callback = self.make_callback(value)
            self.add_item(button)

    def waiting_line(self):
        def mark(player):
            if player in self.removed:
                return "🚫"
            return "✅" if player in self.responses else "⏳"
        players = list(self.humans.values())
        if len(players) > MAX_NAMES:   # big round: counts plus the first pending names
            pending = [p.display_name for p in players if p not in self.responses]
            done = len(players) - len(pending)
            return clip(f"✅ {done}/{len(players)} locked in · ⏳ waiting for: " + clip_list(pending, sep=", "), 1000)
        return clip(" · ".join(f"{mark(player)} {player.display_name}" for player in players), 1000)

    async def interaction_check(self, interaction):
        player = self.humans.get(interaction.user.id)
        if player is None:
            await interaction.response.send_message("👀 You're not playing in this round.", ephemeral=True)
            return False
        if self.closed:
            await interaction.response.send_message("⌛ This round is closed.", ephemeral=True)
            return False
        if player in self.responses:
            await interaction.response.send_message("✅ You're already locked in.", ephemeral=True)
            return False
        return True

    def make_callback(self, value):
        async def callback(interaction: discord.Interaction):
            player = self.humans[interaction.user.id]
            if self.kind == "rps":
                self.responses[player] = value
                note = f"Locked in: {te.RPS_SHAPES[value]} **{value}** (hidden until everyone has picked)"
            elif self.kind == "dice":
                roll = secrets.randbelow(6) + 1
                self.responses[player] = roll
                note = f"You rolled {DICE_FACES[roll - 1]} **{roll}**"
            else:
                seconds = round(time.monotonic() - self.started, 1)
                self.responses[player] = (value == self.question["answer"], seconds)
                note = f"Answer **{LETTERS[value]}** locked in after {seconds}s"
            await interaction.response.send_message(note, ephemeral=True)
            if player in self.removed:
                self.responses[player] = None   # removed while picking: still counts as a loss

            if all(p in self.responses for p in self.humans.values()):
                self.all_done.set()
            elif self.message and time.monotonic() - self.last_edit >= LOCKED_IN_EDIT_GAP:
                self.last_edit = time.monotonic()   # big rounds: at most one "locked in" edit every few seconds
                embed = self.message.embeds[0] if self.message.embeds else None
                if embed:
                    embed.set_field_at(0, name="Locked in", value=self.waiting_line(), inline=False)
                    try:
                        await self.message.edit(embed=embed)
                    except discord.HTTPException:
                        pass
        return callback

    def close(self):
        self.closed = True
        self.stop()
        for item in self.children:
            item.disabled = True


class RoundRequest:
    """One match (or group round) waiting for answers. Matches that start together share one message."""

    def __init__(self, players, humans, label, attempt, responses):
        self.players = players
        self.humans = humans
        self.label = label
        self.attempt = attempt
        self.responses = responses       # bots and removed players are filled in already
        self.done = asyncio.Event()


def round_title(requests):
    """One title for a batch: "Final", or "Quarter-final (8 matches)" for many matches."""
    first = requests[0]
    again = f" (replay {first.attempt - 1})" if first.attempt > 1 else ""
    if len(requests) == 1:
        return first.label + again
    head, _, tail = first.label.partition(" • ")
    words = head.split(" ")
    if len(words) > 1 and words[-1].isdigit():
        head = " ".join(words[:-1])           # "Quarter-final 3" -> "Quarter-final"
    name = head + (f" • {tail}" if tail else "")
    replays = " replays" if all(r.attempt > 1 for r in requests) else ""
    return f"{name} ({len(requests)} matches{replays})"


class Session:
    """
    One tournament or duel in one channel: gives the engine its collect() and announce().

    Built for big games too:
    - Matches that start at the same time (a bracket round runs in parallel) are posted as ONE
      message with one set of buttons, and only players who still have to act are pinged,
      in batches that fit Discord's 2000-character limit. Never @everyone/@here.
    - Results of matches that finish together are combined into as few messages as possible.
    - Messages are spaced out (SEND_GAP) and a rate-limited send is retried instead of crashing.
    """

    def __init__(self, channel, kind, header, quiz_questions=1, points_mode=False):
        self.channel = channel
        self.kind = kind
        self.header = header
        self.quiz_questions = quiz_questions   # trivia: questions per round (1 = first right & fastest)
        self.points_mode = points_mode or quiz_questions > 1   # trivia scored by points (team games too)
        self.question = None                   # the latest trivia question asked
        self.last_question = {}                # player -> the trivia question they last saw
        self.asked = []                        # trivia questions already used in this game
        self.removed = set()                   # players the host removed (they lose every round)
        self.board = None                      # live scoreboard (game_panel.Scoreboard)
        self.current_view = None               # the round being played right now (for the host panel)
        self.task = None                       # the asyncio task running this game (to cancel it)
        self.cancelled = False
        self.pending = []                      # matches waiting to be posted together
        self.batch_open = False                # a batch is still gathering matches
        self.batch_tasks = set()
        self.buffer = []                       # results waiting to be posted together: (embed, short line)
        self.flush_task = None
        self.send_lock = asyncio.Lock()

    # ------------------------------------------------------ scoreboard / host panel
    async def start_board(self, host, players, title):
        """Post the live scoreboard (with the Host panel button) and remember the running task."""
        self.task = asyncio.current_task()
        self.board = Scoreboard(self, host, players, title)
        await self.board.post()
        await asyncio.sleep(SEND_GAP)

    async def refresh_board(self):
        if self.board:
            await self.board.refresh()

    async def note(self, text):
        """A short public note about a host action (never pings anyone)."""
        await self.send(t_embed("🎛️ Host action", text, COLOR_DRAW, header=self.header))

    async def skip(self, by):
        """Host panel: stop waiting. Players who haven't answered count as timed out. Returns how many."""
        view = self.current_view
        if view is None or view.closed:
            return None
        pending = [p for p in view.humans.values() if p not in view.responses]
        view.all_done.set()
        log.info("Host panel: skip waiting by user=%s in #%s (%d pending)", by.id, self.channel.name, len(pending))
        await self.note(f"⏭️ {by.mention} skipped waiting: {len(pending)} player(s) count as timed out.")
        return len(pending)

    async def remove(self, player, by):
        """Host panel: remove a player (a loss, no XP). Returns "removed", "cancelled" or "gone"."""
        board = self.board
        if player not in board.active():
            return "gone"
        if len(board.active()) - 1 < te.MIN_PLAYERS:
            await self.cancel(by, f"Removing {player.mention} would leave fewer than {te.MIN_PLAYERS} players.")
            return "cancelled"
        self.removed.add(player)
        board.status[player] = "removed"
        view = self.current_view
        if view and not view.closed and player.id in view.humans:
            view.responses[player] = None     # loses the round being played right now
            if all(p in view.responses for p in view.humans.values()):
                view.all_done.set()
        log.info("Host panel: removed user=%s by user=%s in #%s", player.id, by.id, self.channel.name)
        await self.note(f"🚫 Host removed {player.mention} (counts as a loss, no XP).")
        await board.refresh()
        return "removed"

    async def cancel(self, by, reason=""):
        """Host panel: stop the whole game. Nothing is saved."""
        if self.cancelled:
            return
        self.cancelled = True
        log.info("Host panel: cancel by user=%s in #%s", by.id, self.channel.name)
        if self.current_view:
            self.current_view.close()
        self.buffer.clear()
        text = f"🛑 {by.mention} cancelled the game. No results were saved." + (f"\n{reason}" if reason else "")
        if self.board:
            await self.board.finish(text, cancelled=True)
        await self.note(text)
        for task in list(self.batch_tasks) + [self.flush_task]:
            if task and task is not asyncio.current_task():
                task.cancel()
        if self.task and self.task is not asyncio.current_task():
            self.task.cancel()

    # ------------------------------------------------------------- sending
    async def raw_send(self, **kwargs):
        """channel.send that never pings @everyone/@here or roles, waits SEND_GAP afterwards, and
        slows down instead of crashing when Discord rate-limits us."""
        mentions = kwargs.pop("allowed_mentions", None) or discord.AllowedMentions.none()
        mentions.everyone = False
        mentions.roles = False
        if kwargs.get("embed") is not None:
            kwargs["embed"] = fit_embed(kwargs["embed"])
        message = None
        for attempt in range(1, SEND_RETRIES + 1):
            try:
                message = await self.channel.send(allowed_mentions=mentions, **kwargs)
                break
            except discord.HTTPException as error:
                if error.status == 429 and attempt < SEND_RETRIES:
                    wait = float(getattr(error, "retry_after", 0) or RATE_LIMIT_PAUSE)
                    log.warning("Rate limited in #%s - slowing down for %.1fs", self.channel.name, wait)
                    await asyncio.sleep(wait)
                    continue
                log.warning("Couldn't send a game message in #%s (%s)", self.channel.name, error.status)
                break
        await asyncio.sleep(SEND_GAP)
        return message

    async def send(self, embed, view=None, ping=()):
        """Post now (earlier buffered results go first, so the order stays right)."""
        await self.flush()
        async with self.send_lock:
            return await self.raw_send(embed=embed, view=view,
                                       allowed_mentions=discord.AllowedMentions(users=list(ping)))

    async def post_result(self, embed, short):
        """Queue a result. Results arriving together are combined into one message."""
        self.buffer.append((embed, short))
        if self.flush_task is None or self.flush_task.done():
            self.flush_task = asyncio.create_task(self._flush_soon())

    async def _flush_soon(self):
        await asyncio.sleep(BATCH_WINDOW)
        await self.flush()

    async def flush(self):
        """Post every queued result now: one message, or a few combined ones for big rounds."""
        if not self.buffer:
            return
        async with self.send_lock:
            items, self.buffer = self.buffer, []
            if len(items) == 1:
                await self.raw_send(embed=items[0][0], allowed_mentions=discord.AllowedMentions.none())
                return
            # Many results: compact lines packed into as few embeds as possible.
            pages, page = [], ""
            for _, short in items:
                line = clip(short, 600)
                if len(page) + len(line) + 1 > 3900:
                    pages.append(page)
                    page = ""
                page += line + "\n"
            pages.append(page)
            for number, text in enumerate(pages, start=1):
                part = f" ({number}/{len(pages)})" if len(pages) > 1 else ""
                await self.raw_send(embed=t_embed(f"📣 Results: {len(items)} matches{part}", text, COLOR_WIN,
                                                  header=self.header),
                                    allowed_mentions=discord.AllowedMentions.none())

    # ---------------------------------------------------------- collecting
    async def collect(self, players, kind, label, attempt):
        if kind == "trivia" and self.points_mode:
            return await self.collect_quiz(players, label, attempt)
        return await self.ask(players, kind, label, attempt)

    async def collect_quiz(self, players, label, attempt):
        """A mini quiz: every player answers each question; returns {player: points}."""
        count = self.quiz_questions if attempt == 1 else 1   # replays are one sudden-death question
        points = {player: 0 for player in players}
        for number in range(1, count + 1):
            title = (f"{label} • Question {number}/{count}" if attempt == 1
                     else f"{label} • ⚡ Sudden death {attempt - 1}")
            responses = await self.ask(players, "trivia", title, 1)
            for player, response in responses.items():
                if response and response[0]:
                    points[player] += 1
                    if self.board:
                        self.board.score[player] += 1   # live scoreboard: total correct answers
            q = self.last_question.get(players[0])
            if q:   # (bots-only matches have no visible question)
                right = [p.mention for p, r in responses.items() if r and r[0]]
                score_line = " · ".join(f"{p.mention} **{points[p]}**" for p in players)
                await self.post_result(
                    t_embed(f"✅ {title}: answer",
                            f"**{LETTERS[q['answer']]}** - {q['choices'][q['answer']]}\n"
                            + (f"💡 {q['explain']}\n" if q["explain"] else "")
                            + f"\nRight: {clip_list(right, sep=', ') or 'nobody'}\n🧮 Points: {score_line}",
                            COLOR_WIN, header=self.header),
                    f"✅ **{title}**: {score_line}")
            await self.refresh_board()
        return points

    async def ask(self, players, kind, label, attempt):
        """Get one answer from each player. Matches asked at the same moment share one message."""
        responses = {player: None for player in players if player in self.removed}   # removed = no answer
        responses.update({player: bot_response(kind, None) for player in players
                          if is_bot(player) and player not in self.removed})
        humans = [player for player in players if not is_bot(player) and player not in self.removed]
        if not humans:
            for player in players:
                self.last_question[player] = None
            return responses  # bots only: instant, nothing to post
        request = RoundRequest(list(players), humans, label, attempt, responses)
        self.pending.append(request)
        if not self.batch_open:
            self.batch_open = True
            task = asyncio.create_task(self.run_batch(kind))
            self.batch_tasks.add(task)
            task.add_done_callback(self.batch_tasks.discard)
        await request.done.wait()
        return request.responses

    async def run_batch(self, kind):
        """Post every waiting match in one message, wait for the answers (max ROUND_SECONDS), share them out."""
        try:
            await asyncio.sleep(BATCH_WINDOW)  # let every match of this round queue up
        finally:
            self.batch_open = False
        requests, self.pending = self.pending, []
        try:
            await self.play_batch(kind, requests)
        finally:
            for request in requests:            # never leave a match waiting, even after an error
                for player in request.players:
                    request.responses.setdefault(player, None)
                request.done.set()

    async def play_batch(self, kind, requests):
        question = new_trivia_question(self.asked) if kind == "trivia" else None
        if question:
            self.asked.append(question["question"])
        self.question = question
        responses = {}
        humans = []
        for request in requests:
            responses.update(request.responses)
            humans += [p for p in request.humans if p not in humans]
            for player in request.players:
                self.last_question[player] = question
        title = round_title(requests)
        if self.board:
            self.board.round_label = title
            self.board.set_status([p for r in requests for p in r.players], "in")
            self.board.set_status(humans, "waiting")
            await self.board.refresh()

        if kind == "rps":
            ask = "Pick your move! Picks stay hidden until everyone has chosen."
        elif kind == "dice":
            ask = "Press **Roll**! Highest roll goes through."
        else:
            ask = (f"**{question['question']}**\n"
                   + "\n".join(f"{LETTER_EMOJI[l]} {c}" for l, c in zip(LETTERS, question["choices"]))
                   + "\nRight and fastest goes through.")
        matches = [" vs ".join(p.mention for p in r.players) for r in requests]
        listing = clip_list(matches, MAX_MATCH_LINES) if len(requests) > 1 else matches[0]
        ends = int(time.time()) + ROUND_SECONDS
        extra = "\nOnly players of a match can press. One shared set of buttons for every match." \
            if len(requests) > 1 else ""
        embed = t_embed(f"⚔️ {title}", f"{listing}\n\n{ask}{extra}\n⏱️ Closes <t:{ends}:R> - no answer = you lose "
                        "this round", header=self.header)
        view = RoundView(humans, kind, question, responses, self.removed)
        self.current_view = view
        embed.add_field(name="Locked in", value=view.waiting_line(), inline=False)

        # Ping only the players who have to act, in messages that fit the 2000-character limit.
        chunks = mention_chunks(humans)
        await self.flush()
        async with self.send_lock:
            view.message = await self.raw_send(content=chunks[0], embed=embed, view=view,
                                               allowed_mentions=discord.AllowedMentions(users=humans))
            for chunk in chunks[1:]:
                await self.raw_send(content=chunk, allowed_mentions=discord.AllowedMentions(users=humans))
        view.ends_at = time.monotonic() + ROUND_SECONDS
        try:
            await asyncio.wait_for(view.all_done.wait(), ROUND_SECONDS)
        except asyncio.TimeoutError:
            pass
        view.close()
        if self.current_view is view:
            self.current_view = None
        for player in humans:
            responses.setdefault(player, None)
        if self.board:
            self.board.set_status(humans, "in")
        embed.set_field_at(0, name="Locked in", value=view.waiting_line() + "\n🔒 Round closed", inline=False)
        if view.message:
            try:
                await view.message.edit(embed=fit_embed(embed), view=view)
            except discord.HTTPException:
                pass
        for request in requests:
            request.responses = {player: responses.get(player) for player in request.players}

    # ------------------------------------------------------- announcements
    def response_lines(self, responses):
        lines = [f"{player.mention}: {describe_response(self.kind, response)}" for player, response in responses.items()]
        return clip_list(lines)

    def update_board(self, event, details):
        board = self.board
        if not board:
            return
        if event == "bracket_start":
            board.set_status(details["byes"], "bye")
            board.bracket = (f"🗂️ Bracket of {details['size']}: {len(details['pairs'])} match(es)"
                             + (f", {len(details['byes'])} free pass(es)" if details["byes"] else ""))
        elif event in ("round_result", "random_result", "removed_result"):
            if not self.points_mode:
                for player in details["advance"]:
                    board.score[player] += 1    # live scoreboard: rounds won
            board.set_status(details["advance"], "in")
            board.set_status(details["eliminated"], "out")
            if board.bracket:
                board.bracket = f"🗂️ {len(board.active())} player(s) still in"

    async def announce(self, event, **details):
        self.update_board(event, details)
        await self._announce(event, **details)
        await self.refresh_board()

    async def _announce(self, event, **details):
        names = lambda players: clip_list((p.mention for p in players), sep=", ")
        if event == "group_start":
            await self.send(t_embed("👥 Group round", f"3 players: {names(details['players'])}\nOne round decides "
                                    "who's out. Then it's the final!", header=self.header))
        elif event == "bracket_start":
            lines = [f"⚔️ {a.mention} vs {b.mention}" for a, b in details["pairs"]]
            text = clip_list(lines, MAX_MATCH_LINES)
            if details["byes"]:
                text += f"\n\n🎟️ **Free pass** to the next round ({len(details['byes'])}): {names(details['byes'])}"
            await self.send(t_embed(f"🗂️ Bracket of {details['size']}", text, header=self.header))
        elif event == "round_result":
            text = self.response_lines(details["responses"])
            q = self.last_question.get(details["advance"][0])
            if self.kind == "trivia" and q and not self.points_mode:
                text += f"\n\n✅ Answer: **{LETTERS[q['answer']]}** - {q['choices'][q['answer']]}"
            text += f"\n\n➡️ Through: {names(details['advance'])}\n❌ Out: {names(details['eliminated'])}"
            await self.post_result(
                t_embed(f"📣 {details['label']}: result", text, COLOR_WIN, header=self.header),
                f"**{details['label']}**: ➡️ {names(details['advance'])} · ❌ {names(details['eliminated'])}")
        elif event == "replay":
            reason = {"rps": "All the same or all different shapes",
                      "dice": "Tied lowest roll",
                      "trivia": "Nobody got it right (or an exact tie)"}[self.kind]
            if self.kind == "trivia" and self.points_mode:
                reason = "⚡ Tied on points: sudden death! One question at a time"
            text = self.response_lines(details["responses"])
            if all(r is None for r in details["responses"].values()) and not self.points_mode:
                reason = "Nobody answered"
            await self.post_result(
                t_embed(f"🔁 {details['label']}: replay!", f"{text}\n\n{reason}, so we go again.", COLOR_DRAW,
                        header=self.header),
                f"🔁 **{details['label']}**: {reason.lower()}, replay")
        elif event == "removed_result":
            await self.post_result(
                t_embed(f"🚫 {details['label']}: decided by the host",
                        f"Removed: {names(details['eliminated'])} (a loss)\n➡️ Through: {names(details['advance'])}",
                        COLOR_DRAW, header=self.header),
                f"🚫 **{details['label']}**: removed {names(details['eliminated'])} · ➡️ {names(details['advance'])}")
        elif event == "random_result":
            await self.post_result(
                t_embed(f"🎲 {details['label']}: fair random pick",
                        f"Still undecided after {te.MAX_REPLAYS} replays, so a secure random draw decides.\n"
                        f"➡️ Through: {names(details['advance'])}\n❌ Out: {names(details['eliminated'])}",
                        COLOR_DRAW, header=self.header),
                f"🎲 **{details['label']}** (random after {te.MAX_REPLAYS} replays): ➡️ {names(details['advance'])}")


def podium_text(podium, removed=()):
    """🥇/🥈/🥉 lines for the final scoreboard."""
    return "\n".join(f"{medal} {player.mention}" + (" (removed)" if player in removed else "")
                     for medal, player in zip(("🥇", "🥈", "🥉"), podium) if player is not None)


class LobbyView(discord.ui.View):
    """
    Join / Leave / Start now. Max players is a hard cap (a full lobby refuses Join; leaving frees a spot).
    The host is NOT added automatically: they press Join like everyone else if they want to play.
    """

    def __init__(self, host, kind, max_players, ends_at, test_mode=False, questions=1, free_for_all=False,
                 on_join=None, on_leave=None):
        super().__init__(timeout=None)
        self.questions = questions   # trivia only: questions per match
        self.free_for_all = free_for_all   # /duel free for all: no XP, needs 3 players
        self.min_players = FREE_FOR_ALL_MIN if free_for_all else te.MIN_PLAYERS
        self.on_join = on_join       # optional check: function(member) -> "why not" text or None
        self.on_leave = on_leave     # optional: function(member) called after leaving
        self.host = host
        self.kind = kind
        self.max_players = max_players
        self.ends_at = ends_at
        self.test_mode = test_mode   # bots fill empty spots, so Start now works with anyone
        self.players = []   # only people who pressed Join (the host too, if they joined)
        self.start_now = asyncio.Event()
        self.message = None

    def is_full(self):
        return len(self.players) >= self.max_players

    def embed(self, closed=False):
        # Big lobbies: the first 20 names, then "and N more" (keeps the embed inside Discord's limits).
        listing = clip_list(f"{i}. {p.mention}" for i, p in enumerate(self.players, start=1)) \
            or "_nobody yet - press **Join**!_"
        status = ("🔒 Registration closed." if closed else
                  f"⏱️ Starts <t:{self.ends_at}:R>, or when the host presses **Start now**.")
        if self.free_for_all:
            embed = t_embed(f"⚔️ {GAMES[self.kind]} Free for all",
                            f"{status}\n\nNeeds at least {self.min_players} players. 🥇🥈🥉 podium, "
                            "no XP: just bragging rights. 😎", header="⚔️ N7X Duel")
        else:
            embed = t_embed(f"🏆 {GAMES[self.kind]} Tournament",
                            f"{status}\n\n"
                            f"Needs at least {te.MIN_PLAYERS} players. 🥇 {PODIUM_XP[0]} · 🥈 {PODIUM_XP[1]} · "
                            f"🥉 {PODIUM_XP[2]} bonus game XP (no 🥉 with only 2 players). No real prizes.")
        if self.kind == "trivia" and self.questions > 1:
            embed.add_field(name="🧠 Mini quiz", value=f"{self.questions} questions per match. Most points goes "
                                                      "through; a tie = ⚡ sudden death.", inline=False)
        embed.add_field(name="🎙️ Host", value=f"Hosted by {self.host.mention}\n_(plays only if they press Join)_",
                        inline=False)
        count = f"{len(self.players)}/{self.max_players}"
        embed.add_field(name=f"👥 Players ({'FULL ' + count if self.is_full() else count})", value=listing,
                        inline=False)
        if self.test_mode:
            embed.add_field(name="🧪 Test mode", value="Empty spots will be filled with 🤖 bot players "
                                                      "(they pick randomly and never get XP).")
        return fit_embed(embed)

    async def refresh(self, interaction):
        await interaction.response.edit_message(embed=self.embed(), view=self,
                                                allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Join", emoji="✅", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if any(p.id == interaction.user.id for p in self.players):
            await interaction.response.send_message("You're already in! 🎮", ephemeral=True)
        elif self.is_full():
            await interaction.response.send_message("Lobby is full.", ephemeral=True)
        elif self.on_join and (problem := self.on_join(interaction.user)):
            await interaction.response.send_message(problem, ephemeral=True)
        else:
            self.players.append(interaction.user)
            await self.refresh(interaction)

    @discord.ui.button(label="Leave", emoji="🚪", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        before = len(self.players)
        self.players = [p for p in self.players if p.id != interaction.user.id]
        if len(self.players) == before:
            await interaction.response.send_message("You haven't joined.", ephemeral=True)
        else:
            if self.on_leave:
                self.on_leave(interaction.user)
            await self.refresh(interaction)

    @discord.ui.button(label="Start now", emoji="▶️", style=discord.ButtonStyle.primary)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.host.id:
            await interaction.response.send_message("Only the host can start early.", ephemeral=True)
            return
        if len(self.players) < self.min_players and not self.test_mode:
            await interaction.response.send_message(
                f"Need at least {self.min_players} players to start ({len(self.players)} joined so far).", ephemeral=True)
            return
        await interaction.response.defer()
        self.start_now.set()

    def close(self):
        self.stop()
        for item in self.children:
            item.disabled = True


class DuelInvite(discord.ui.View):
    def __init__(self, challenger, opponent):
        super().__init__(timeout=ROUND_SECONDS)
        self.challenger = challenger
        self.opponent = opponent
        self.answer = asyncio.Event()
        self.accepted = False

    async def interaction_check(self, interaction):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("This challenge isn't for you. Start your own with /duel!",
                                                    ephemeral=True)
            return False
        return not self.answer.is_set()

    @discord.ui.button(label="Accept", emoji="⚔️", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.accepted = True
        await self.finish(interaction, "⚔️ Challenge accepted! Get ready...")

    @discord.ui.button(label="Decline", emoji="🏳️", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.finish(interaction, "🏳️ Challenge declined.")

    async def finish(self, interaction, text):
        self.answer.set()
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self, content=text)


class Tournament(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.busy_channels = set()   # one tournament per channel at a time
        self.tasks = set()

    def run_in_background(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    # ------------------------------------------------------------ /tournament
    @app_commands.command(name="tournament", description="🏆 [Mods] Host a tournament (2-100 players): RPS, dice or trivia")
    @app_commands.describe(game="Which game", max_players=f"Most players allowed (2-{MAX_PLAYERS_LIMIT}); fewer is fine",
                           join_seconds="How long people can join (15-300 seconds)",
                           test_mode="Fill empty spots with 🤖 bot players for a demo (they never get XP)",
                           questions="Trivia only: questions per match (1-10, default 1)")
    @app_commands.choices(game=GAME_CHOICES)
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 60)
    async def tournament(self, interaction: discord.Interaction, game: app_commands.Choice[str],
                         max_players: app_commands.Range[int, 2, MAX_PLAYERS_LIMIT] = 8,
                         join_seconds: app_commands.Range[int, 15, 300] = 60,
                         test_mode: bool = False, questions: app_commands.Range[int, 1, 10] = 1):
        if not is_mod(interaction.user):
            await interaction.response.send_message("Only mods can start tournaments.", ephemeral=True)
            return
        if interaction.channel.id in self.busy_channels:
            await interaction.response.send_message("A tournament is already running in this channel.", ephemeral=True)
            return
        if game.value == "trivia" and not load_trivia():
            await interaction.response.send_message("No trivia questions available (run python check_trivia.py).", ephemeral=True)
            return

        self.busy_channels.add(interaction.channel.id)
        lobby = LobbyView(interaction.user, game.value, max_players, int(time.time()) + join_seconds, test_mode,
                          questions if game.value == "trivia" else 1)
        embed = lobby.embed()
        await interaction.response.send_message(embed=embed, view=lobby,
                                                allowed_mentions=discord.AllowedMentions.none())
        lobby.message = await interaction.original_response()
        self.run_in_background(self.run_tournament(interaction, lobby, join_seconds, test_mode))

    async def run_tournament(self, interaction, lobby, join_seconds, test_mode):
        channel = interaction.channel
        guild = interaction.guild
        session = None
        try:
            try:
                await asyncio.wait_for(lobby.start_now.wait(), join_seconds)
            except asyncio.TimeoutError:
                pass
            lobby.close()
            try:
                await lobby.message.edit(embed=lobby.embed(closed=True), view=lobby,
                                         allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass

            players = list(lobby.players)
            if test_mode:
                for number in range(1, lobby.max_players - len(players) + 1):
                    players.append(BotPlayer(id=-number, display_name=f"🤖 Bot {number}"))
            if len(players) < te.MIN_PLAYERS:
                await channel.send(embed=t_embed(
                    "❌ Tournament cancelled: not enough players",
                    f"**{len(players)}** player(s) joined, but at least **{te.MIN_PLAYERS}** are needed.\n\n"
                    f"ℹ️ The host ({lobby.host.mention}) only counts as a player if they pressed **Join**.",
                    COLOR_LOSE), allowed_mentions=discord.AllowedMentions.none())
                return

            session = Session(channel, lobby.kind, "🏆 N7X Tournament", lobby.questions)
            engine = te.Engine(lobby.kind, session.collect, session.announce, lobby.questions, session.removed)
            log.info("Tournament started in #%s: %d players (%d bots), game=%s", channel.name, len(players),
                     sum(map(is_bot, players)), lobby.kind)
            await session.start_board(lobby.host, players, f"{GAMES[lobby.kind]} Tournament")
            podium = await engine.run_tournament(players)
            await session.flush()   # post any queued results first
            await session.board.finish(podium_text(podium, session.removed))
            await self.finish_tournament(channel, guild, players, podium, session.removed)
        except asyncio.CancelledError:
            if session and session.cancelled:
                return   # the host cancelled: the scoreboard already says so
            raise
        except Exception:
            log.exception("Tournament in #%s crashed", getattr(channel, "name", "?"))
            try:
                await channel.send(embed=t_embed("⚠️ Tournament stopped", "Something went wrong, sorry! "
                                                 "No results were saved.", COLOR_LOSE))
            except discord.HTTPException:
                pass
        finally:
            self.busy_channels.discard(channel.id)

    async def finish_tournament(self, channel, guild, players, podium, removed=()):
        lines = []
        for medal, player, xp in zip(("🥇", "🥈", "🥉"), podium, PODIUM_XP):
            if player is None:
                continue   # only 2 players: no 3rd place
            if is_bot(player):
                lines.append(f"{medal} {player.mention} - test bot, no XP")
                continue
            if player in removed:
                lines.append(f"{medal} {player.mention} - removed by the host, no XP")
                continue
            given, earned = database.give_game_xp(guild.id, player.id, xp, today(), DAILY_GAME_XP_CAP)
            if given == xp:
                bonus = f"+{given} XP"
            elif given:
                bonus = f"+{given} XP (daily cap)"
            else:
                bonus = "daily game XP cap reached"
            lines.append(f"{medal} {player.mention} - ✨ {bonus}")

        database.record_tournament(guild.id, [p.id for p in players if not is_bot(p)],
                                   [None if p is None or is_bot(p) or p in removed else p.id for p in podium])
        embed = t_embed("🏆 Tournament results", "\n".join(lines) + f"\n\n{len(players)} players · thanks for playing!",
                        COLOR_GOLD)
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(
            users=[p for p in podium if p is not None and not is_bot(p)], everyone=False, roles=False))
        log.info("Tournament finished in #%s", channel.name)


async def setup(bot):
    await bot.add_cog(Tournament(bot))
