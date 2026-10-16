"""
assistant_memory.py - Short-term conversation memory (mod assistant and tagged questions).

For each (server, channel, mod) we keep the last few exchanges:
what THAT mod said to the bot, and what the bot replied.
- Never other members' messages.
- Kept in memory only (gone after a restart), never written to disk or the terminal.
- Forgotten after 5 minutes of silence.

It lets a mod follow up naturally:
    mod: @bot deal with @alt
    bot: What would you like to do with @alt? ...
    mod: @bot timeout him for 30 mins      <- "him" = @alt, the member this mod mentioned earlier
"""

import time

# --- Settings you can tweak ---
MAX_TURNS = 5             # how many of the mod's messages (with the bot's replies) to remember
FORGET_AFTER_SECONDS = 300  # 5 minutes of silence


class ConversationMemory:
    def __init__(self):
        self._turns = {}  # (guild_id, channel_id, mod_id) -> list of turns

    def _fresh_turns(self, key):
        """The remembered turns for this key, or [] if they're too old."""
        turns = self._turns.get(key, [])
        if turns and time.time() - turns[-1]["at"] > FORGET_AFTER_SECONDS:
            self._turns.pop(key, None)
            return []
        return turns

    def get(self, key):
        return list(self._fresh_turns(key))

    def add(self, key, mod_text, bot_text, mentioned_ids):
        """Remember one exchange. mentioned_ids = members the mod explicitly @mentioned (or wrote the ID of)."""
        turns = self._fresh_turns(key)
        turns.append({
            "mod": mod_text[:500],
            "bot": (bot_text or "")[:500],
            "ids": list(mentioned_ids),
            "at": time.time(),
        })
        self._turns[key] = turns[-MAX_TURNS:]

    def remembered_member(self, key):
        """
        The member this mod most recently @mentioned in the memory window.
        Only if that message mentioned exactly ONE member - otherwise it's ambiguous and we return None.
        """
        for turn in reversed(self._fresh_turns(key)):
            if turn["ids"]:
                return turn["ids"][0] if len(turn["ids"]) == 1 else None
        return None

    def as_prompt(self, key, speaker="Moderator"):
        """The remembered conversation as text for the AI (that person's own words + the bot's replies only)."""
        lines = []
        for turn in self._fresh_turns(key):
            lines.append(f"{speaker}: {turn['mod']}")
            if turn["bot"]:
                lines.append(f"Bot: {turn['bot']}")
        return "\n".join(lines)

    def prune(self):
        """Drop every expired conversation (called regularly so memory doesn't grow)."""
        now = time.time()
        for key in [k for k, turns in self._turns.items() if not turns or now - turns[-1]["at"] > FORGET_AFTER_SECONDS]:
            self._turns.pop(key, None)
