"""
game_panel.py - The live scoreboard and the host panel for tournaments and duels.

Scoreboard: ONE message per game, edited after each round or question. It shows every
player's score and status (in / out / bye / waiting / removed) and a 👑 for the leader.
At the end it becomes the final card and its Host panel button is disabled.

Host panel (host of the game, or any mod): a private view of the current round:
who has answered and who is still pending, and the seconds left. It NEVER shows what
anyone picked. Controls: Cancel game (with confirm), Remove a player, Skip waiting.

The game logic behind the controls is in Session (cogs/tournament.py):
session.cancel(by), session.remove(player, by), session.skip(by).
"""

import asyncio
import logging
import time

import discord

log = logging.getLogger("n7x.panel")

# --- Settings you can tweak ---
BOARD_MIN_INTERVAL = 2.0     # edit the scoreboard at most once every this many seconds (updates are batched)
BOARD_TOP = 10               # players shown on the scoreboard (everyone can check "My position")
MAX_NAMES = 20               # names shown in lists before "and N more"
MESSAGE_LIMIT = 2000         # Discord's limit for normal message text

COLOR_BOARD = 0x8B5CF6
STATUS = {"in": "🟢 in", "out": "❌ out", "bye": "🎟️ bye", "waiting": "⏳ waiting", "removed": "🚫 removed"}
NO_PING = discord.AllowedMentions.none()


def is_mod(member):
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and (perms.moderate_members or perms.manage_messages))


# =====================================================================
# Keeping messages inside Discord's limits
# =====================================================================

def clip_list(items, limit=MAX_NAMES, sep="\n"):
    """The first `limit` items, then "...and N more"."""
    items = list(items)
    shown = sep.join(items[:limit])
    if len(items) > limit:
        shown += f"{sep}…and {len(items) - limit} more"
    return shown


def clip(text, limit):
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def fit_embed(embed):
    """Trim an embed so it can never break Discord's limits (title 256, description 4096,
    25 fields of 256/1024, footer 2048, author 256, 6000 characters in total)."""
    if embed is None:
        return None
    if embed.title:
        embed.title = clip(embed.title, 256)
    if embed.description:
        embed.description = clip(embed.description, 4096)
    while len(embed.fields) > 25:
        embed.remove_field(len(embed.fields) - 1)
    for index, field in enumerate(embed.fields):
        embed.set_field_at(index, name=clip(field.name or "-", 256), value=clip(field.value or "-", 1024),
                           inline=field.inline)
    if embed.footer and embed.footer.text:
        embed.set_footer(text=clip(embed.footer.text, 2048), icon_url=embed.footer.icon_url)
    if embed.author and embed.author.name:
        embed.set_author(name=clip(embed.author.name, 256), icon_url=embed.author.icon_url)
    while len(embed) > 6000 and embed.description and len(embed.description) > 100:
        embed.description = clip(embed.description, max(100, len(embed.description) - (len(embed) - 5990)))
    while len(embed) > 6000 and embed.fields:
        embed.remove_field(len(embed.fields) - 1)
    return embed


def mention_chunks(players, limit=MESSAGE_LIMIT, prefix=""):
    """Mentions split into messages that each fit Discord's 2000-character limit."""
    chunks, current = [], prefix
    for player in players:
        piece = player.mention + " "
        if len(current) + len(piece) > limit:
            chunks.append(current.rstrip())
            current = ""
        current += piece
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


class Scoreboard:
    """Keeps the scores/statuses and edits the scoreboard message."""

    def __init__(self, session, host, players, title):
        self.session = session
        self.host = host
        self.players = list(players)
        self.title = title
        self.score = {player: 0 for player in self.players}
        self.status = {player: "in" for player in self.players}
        self.teams = None            # {"A": [...], "B": [...]} for team games
        self.round_label = "Starting..."
        self.bracket = ""            # short bracket summary (tournaments)
        self.finished = False
        self.message = None
        self.view = PanelButton(self)
        self.edits = 0               # how many times the board was updated (handy for tests)
        self.last_edit = 0.0
        self.pending = None          # a scheduled (batched) update

    # ------------------------------------------------------------- state
    def active(self):
        """Players still in the game (not out, not removed)."""
        return [p for p in self.players if self.status[p] not in ("out", "removed")]

    def set_status(self, players, status):
        for player in players:
            if player in self.status and self.status[player] != "removed":
                self.status[player] = status

    def leader(self):
        best = max((self.score[p] for p in self.players if self.status[p] != "removed"), default=0)
        if best <= 0:
            return None
        leaders = [p for p in self.players if self.score[p] == best and self.status[p] != "removed"]
        return leaders[0] if len(leaders) == 1 else None

    # ------------------------------------------------------------ display
    def line(self, player, leader, rank=None):
        crown = " 👑" if player == leader else ""
        number = f"**#{rank}** " if rank else ""
        return f"{number}{player.mention}{crown} · **{self.score[player]}** · {STATUS[self.status[player]]}"

    def ranking(self):
        """Players best first (removed players last)."""
        return sorted(self.players, key=lambda p: (self.status[p] == "removed", self.status[p] == "out",
                                                   -self.score[p]))

    def position(self, member):
        """(rank, player) for a member, or None if they're not playing."""
        for rank, player in enumerate(self.ranking(), start=1):
            if player.id == member.id:
                return rank, player
        return None

    def totals(self):
        counts = {status: 0 for status in STATUS}
        for status in self.status.values():
            counts[status] += 1
        return " · ".join(f"{STATUS[s]} **{n}**" for s, n in counts.items() if n or s in ("in", "out", "waiting"))

    def embed(self, final_text=None, cancelled=False):
        leader = self.leader()
        if cancelled:
            title, color = "🛑 Game cancelled", 0xEF4444
        elif self.finished:
            title, color = f"🏁 Final scoreboard • {self.title}", 0xFACC15
        else:
            title, color = f"📊 Live scoreboard • {self.title}", COLOR_BOARD
        top = final_text or f"🎯 **{self.round_label}**"
        if self.bracket and not self.finished:
            top += f"\n{self.bracket}"
        embed = discord.Embed(title=title, description=top, color=color)
        embed.set_author(name=self.session.header)
        if self.teams:
            for name, team in self.teams.items():
                total = sum(self.score[p] for p in team)
                lines = "\n".join(self.line(p, None) for p in team) or "-"
                embed.add_field(name=f"{'🔵' if name == 'A' else '🔴'} Team {name}: {total}", value=lines)
        else:
            ranked = self.ranking()
            top = "\n".join(self.line(p, leader, rank) for rank, p in enumerate(ranked[:BOARD_TOP], start=1))
            name = "Players · score · status" if len(ranked) <= BOARD_TOP else f"Top {BOARD_TOP} of {len(ranked)}"
            embed.add_field(name=name, value=top or "-", inline=False)
            embed.add_field(name="Totals", value=self.totals(), inline=False)
        embed.set_footer(text=f"Host: {self.host.display_name} · 📍 My position shows your place · N7X Bot")
        return fit_embed(embed)

    async def post(self):
        self.message = await self.session.channel.send(embed=self.embed(), view=self.view, allowed_mentions=NO_PING)
        self.last_edit = time.monotonic()

    async def refresh(self):
        """Ask for an update. Updates are batched: at most one edit every BOARD_MIN_INTERVAL seconds."""
        if not self.message or self.finished or self.pending:
            return
        wait = self.last_edit + BOARD_MIN_INTERVAL - time.monotonic()
        if wait > 0:
            self.pending = asyncio.create_task(self._edit_later(wait))
            return
        await self._edit()

    async def _edit_later(self, wait):
        await asyncio.sleep(wait)
        self.pending = None
        if not self.finished:
            await self._edit()

    async def _edit(self):
        self.edits += 1
        self.last_edit = time.monotonic()
        try:
            await self.message.edit(embed=self.embed(), view=self.view, allowed_mentions=NO_PING)
        except discord.HTTPException as error:
            log.warning("Scoreboard update failed (%s)", error.status)   # keep playing; next update retries

    async def finish(self, final_text, cancelled=False):
        """Turn the board into the final card and disable the Host panel button."""
        if self.finished:
            return
        self.finished = True
        if self.pending:
            self.pending.cancel()
            self.pending = None
        self.view.panel.disabled = True
        self.view.my_position.disabled = True
        self.view.stop()
        if not self.message:
            return
        self.edits += 1
        try:
            await self.message.edit(embed=self.embed(final_text, cancelled), view=self.view, allowed_mentions=NO_PING)
        except discord.HTTPException as error:
            log.warning("Final scoreboard update failed (%s)", error.status)

    # -------------------------------------------------------- host panel
    def can_control(self, member):
        return member.id == self.host.id or is_mod(member)

    def panel_embed(self):
        """What the host sees. Only WHO answered, never WHAT they picked."""
        view = self.session.current_view
        embed = discord.Embed(title="🎛️ Host panel", color=COLOR_BOARD)
        embed.add_field(name="Round", value=self.round_label, inline=False)
        if view is None or view.closed:
            embed.add_field(name="Status", value="Between rounds: nobody to wait for right now.", inline=False)
        else:
            answered = [p for p in view.humans.values() if p in view.responses]
            pending = [p for p in view.humans.values() if p not in view.responses]
            left = max(0, int(view.ends_at - time.monotonic()))
            # Big rounds: counts, plus the first names only (so the panel stays readable).
            embed.add_field(name=f"✅ Answered ({len(answered)})",
                            value=(clip_list(p.display_name for p in answered) if len(answered) <= MAX_NAMES
                                   else f"{len(answered)} players") or "-")
            embed.add_field(name=f"⏳ Pending ({len(pending)})",
                            value=clip_list(p.display_name for p in pending) or "-")
            embed.add_field(name="⏱️ Time left", value=f"{left}s", inline=False)
        embed.set_footer(text="Picks stay hidden, even from the host.")
        return fit_embed(embed)

    def pending_players(self):
        view = self.session.current_view
        if view is None or view.closed:
            return []
        return [p for p in view.humans.values() if p not in view.responses]


class PanelButton(discord.ui.View):
    """The 🎛️ Host panel button on the scoreboard."""

    def __init__(self, board):
        super().__init__(timeout=None)
        self.board = board

    @discord.ui.button(label="Host panel", emoji="🎛️", style=discord.ButtonStyle.secondary)
    async def panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.board.can_control(interaction.user):
            await interaction.response.send_message("Only the host or a mod can use this.", ephemeral=True)
            return
        if self.board.finished:
            await interaction.response.send_message("This game is over.", ephemeral=True)
            return
        await interaction.response.send_message(embed=self.board.panel_embed(),
                                                view=HostControls(self.board, interaction.user), ephemeral=True)

    @discord.ui.button(label="My position", emoji="📍", style=discord.ButtonStyle.secondary)
    async def my_position(self, interaction: discord.Interaction, button: discord.ui.Button):
        found = self.board.position(interaction.user)
        if not found:
            await interaction.response.send_message("You're not playing in this game.", ephemeral=True)
            return
        rank, player = found
        await interaction.response.send_message(
            f"You: #{rank} of {len(self.board.players)} · score **{self.board.score[player]}** · "
            f"{STATUS[self.board.status[player]]}", ephemeral=True)


class ControlBase(discord.ui.View):
    """Shared check for every private control: still allowed, and the game still running."""

    def __init__(self, board, timeout=120):
        super().__init__(timeout=timeout)
        self.board = board

    async def interaction_check(self, interaction):
        if not self.board.can_control(interaction.user):
            await interaction.response.send_message("Only the host or a mod can use this.", ephemeral=True)
            return False
        if self.board.finished:
            await interaction.response.edit_message(content="This game is over.", embed=None, view=None)
            return False
        return True


class HostControls(ControlBase):
    """Private controls for the host or a mod."""

    def __init__(self, board, user):
        super().__init__(board)
        self.user = user

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.board.panel_embed(), view=self)

    @discord.ui.button(label="Skip waiting", emoji="⏭️", style=discord.ButtonStyle.primary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        skipped = await self.board.session.skip(interaction.user)
        text = (f"⏭️ Skipped: {skipped} pending player(s) count as timed out." if skipped is not None
                else "Nobody to wait for right now.")
        await interaction.response.edit_message(content=text, embed=self.board.panel_embed(), view=self)

    @discord.ui.button(label="Remove a player", emoji="🚫", style=discord.ButtonStyle.secondary)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        # A menu holds 25 names: players still pending in this round come first (the usual AFK suspects).
        pending = [p for p in self.board.pending_players() if p in self.board.active()]
        choices = (pending + [p for p in self.board.active() if p not in pending])[:25]
        if not choices:
            await interaction.response.edit_message(content="Nobody to remove.", view=self)
            return
        await interaction.response.edit_message(content="Who should be removed? (counts as a loss, no XP)",
                                                embed=None, view=RemovePicker(self.board, choices))

    @discord.ui.button(label="Cancel game", emoji="🛑", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="⚠️ Cancel the whole game? No results will be saved.",
                                                embed=None, view=ConfirmCancel(self.board))


class RemovePicker(ControlBase):
    def __init__(self, board, choices):
        super().__init__(board, timeout=60)
        self.choices = {str(index): player for index, player in enumerate(choices)}
        select = discord.ui.Select(placeholder="Pick a player", options=[
            discord.SelectOption(label=player.display_name[:100], value=key) for key, player in self.choices.items()])
        select.callback = self.picked
        self.select = select
        self.add_item(select)

    async def picked(self, interaction: discord.Interaction):
        player = self.choices.get(self.select.values[0])
        result = await self.board.session.remove(player, interaction.user)
        text = {"removed": f"🚫 Removed {player.display_name}.",
                "cancelled": "🛑 That would leave fewer than 2 players, so the game was cancelled.",
                "gone": "That player is already out of the game."}[result]
        await interaction.response.edit_message(content=text, view=None)


class ConfirmCancel(ControlBase):
    def __init__(self, board):
        super().__init__(board, timeout=60)

    @discord.ui.button(label="Yes, cancel the game", emoji="🛑", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="🛑 Game cancelled.", view=None)
        await self.board.session.cancel(interaction.user)

    @discord.ui.button(label="No, keep playing", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="👍 The game continues.", view=None)
