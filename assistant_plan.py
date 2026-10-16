"""
assistant_plan.py - Turns the AI's JSON into a checked, ordered plan (no Discord code here).

The AI must reply with:
    {
      "language": "en",
      "confidence": 0.95,
      "actions": [
        {"tool": "timeout_member", "args": {"member": "<@123>", "minutes": 30, "reason": "spam"}, "delay_minutes": 0},
        {"tool": "remove_timeout", "args": {"member": "<@123>"}, "delay_minutes": 20}
      ],
      "alternatives": []
    }

Everything is checked here. If ANY step is wrong or the AI isn't sure enough,
the whole plan is refused - nothing runs.
"""

import json
import re
from dataclasses import dataclass, field

import mod_tools
from assistant_text import normalize_language

# --- Settings you can tweak ---
MAX_STEPS = 3
MAX_DELAY_MINUTES = 7 * 24 * 60   # 7 days
MIN_CONFIDENCE = 0.9              # below this, the bot asks instead of acting

# The fixed list of tools: their arguments (True = required) and the mod_tools action they use.
TOOLS = {
    "show_warnings":     {"args": {"member": True},                                   "action": "show_warnings"},
    "add_warning":       {"args": {"member": True, "reason": True},                   "action": "warn"},
    "clear_warnings":    {"args": {"member": True, "number": False, "reason": False}, "action": "clear_warnings"},
    "timeout_member":    {"args": {"member": True, "minutes": True, "reason": True},  "action": "timeout"},
    "remove_timeout":    {"args": {"member": True, "reason": False},                  "action": "remove_timeout"},
    "kick_member":       {"args": {"member": True, "reason": True},                   "action": "kick"},
    "cancel_scheduled":  {"args": {"member": True},                                   "action": "cancel_scheduled"},
    "summarize_channel": {"args": {"channel": False},                                 "action": "summarize"},
}
READ_ONLY_TOOLS = {"show_warnings", "summarize_channel"}

MENTION_PATTERN = re.compile(r"^<@!?(\d{15,25})>$")
CHANNEL_PATTERN = re.compile(r"^<#(\d{15,25})>$")
ID_PATTERN = re.compile(r"^\d{15,25}$")


class PlanProblem(Exception):
    """The plan can't run. `key` is a sentence in assistant_text.py, `values` fill its {placeholders}."""

    def __init__(self, key, language="en", **values):
        super().__init__(key)
        self.key = key
        self.language = language
        self.values = values


class Unsure(Exception):
    """The AI's confidence is below MIN_CONFIDENCE. Ask the mod, offering the AI's alternatives."""

    def __init__(self, language, alternatives):
        super().__init__("unsure")
        self.language = language
        self.alternatives = alternatives


@dataclass
class Step:
    tool: str
    args: dict             # member_id, channel_id, minutes, number, reason (only what applies)
    delay: int             # minutes after Confirm
    number: int = 0        # position in the order steps run: 1, 2, 3
    depends_on: int = None  # number of an earlier step that must succeed first
    info: dict = field(default_factory=dict)  # display-only details (never saved or executed)

    @property
    def member_id(self):
        return self.args.get("member_id")


@dataclass
class Plan:
    language: str
    confidence: float
    steps: list
    alternatives: list = field(default_factory=list)

    @property
    def is_read_only(self):
        return len(self.steps) == 1 and self.steps[0].tool in READ_ONLY_TOOLS


def format_minutes(minutes):
    """90 -> '1h 30m', 1440 -> '1d'."""
    if minutes <= 0:
        return "0m"
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = [f"{days}d" if days else "", f"{hours}h" if hours else "", f"{mins}m" if mins else ""]
    return " ".join(part for part in parts if part)


# =====================================================================
# 1. Strict JSON check
# =====================================================================

def parse_plan(text):
    """Check the AI's reply. Returns a Plan, or raises PlanProblem / Unsure."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()  # remove a ```json wrapper only
    try:
        data = json.loads(text)
    except ValueError:
        raise PlanProblem("not_understood")
    if not isinstance(data, dict) or set(data) - {"language", "confidence", "actions", "alternatives"}:
        raise PlanProblem("not_understood")

    language = normalize_language(data.get("language"))

    # Alternatives: only short plain strings, at most 3. They're suggestions, never actions.
    alternatives = []
    if isinstance(data.get("alternatives"), list):
        for item in data["alternatives"][:3]:
            if isinstance(item, str) and item.strip():
                alternatives.append(item.strip()[:150])

    # Confidence: 0..1 (we also accept 0..100 and convert it).
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise Unsure(language, alternatives)
    if 1 < confidence <= 100:
        confidence = confidence / 100
    if not 0 <= confidence <= 1:
        raise PlanProblem("not_understood", language)
    if confidence < MIN_CONFIDENCE:
        raise Unsure(language, alternatives)

    actions = data.get("actions")
    if not isinstance(actions, list) or not actions:
        raise PlanProblem("not_understood", language)
    if len(actions) > MAX_STEPS:
        raise PlanProblem("too_many_steps", language, max=MAX_STEPS)

    steps = []
    for raw in actions:
        try:
            steps.append(parse_step(raw))
        except PlanProblem as problem:
            problem.language = language
            raise

    # Run order: by delay; steps with the same delay keep the order the mod said them in.
    steps = [step for _, step in sorted(enumerate(steps), key=lambda pair: (pair[1].delay, pair[0]))]
    for number, step in enumerate(steps, start=1):
        step.number = number

    plan = Plan(language, float(confidence), steps, alternatives)

    # Showing warnings / summaries, and cancelling scheduled steps, only on their own, right away.
    alone = len(steps) == 1 and steps[0].delay == 0
    if any(step.tool in READ_ONLY_TOOLS for step in steps) and not alone:
        raise PlanProblem("readonly_alone", language)
    if any(step.tool == "cancel_scheduled" for step in steps) and not alone:
        raise PlanProblem("cancel_alone", language)
    return plan


def parse_step(raw):
    """Check one step: its tool, arguments and delay."""
    if not isinstance(raw, dict) or set(raw) - {"tool", "args", "delay_minutes"}:
        raise PlanProblem("not_understood")
    tool = raw.get("tool")
    if tool not in TOOLS:  # includes "unknown", "ban", "add_role"...
        raise PlanProblem("not_understood")

    delay = raw.get("delay_minutes", 0)
    if delay is None:
        delay = 0
    if isinstance(delay, bool) or not isinstance(delay, int) or delay < 0:
        raise PlanProblem("not_understood")
    if delay > MAX_DELAY_MINUTES:
        raise PlanProblem("delay_too_long")

    args = raw.get("args") or {}
    if not isinstance(args, dict):
        raise PlanProblem("not_understood")
    allowed = TOOLS[tool]["args"]
    args = {key: value for key, value in args.items() if value is not None}  # drop nulls
    if set(args) - set(allowed):
        raise PlanProblem("not_understood")

    clean = {}
    for name, required in allowed.items():
        if name not in args:
            if required and name == "reason":
                raise PlanProblem("need_reason")
            if required and name == "member":
                raise PlanProblem("need_mention")
            if required:
                raise PlanProblem("not_understood")
            continue
        value = args[name]

        if name == "member":
            match = MENTION_PATTERN.match(str(value).strip()) or ID_PATTERN.match(str(value).strip())
            if not match:
                raise PlanProblem("need_mention")  # a name instead of a mention
            clean["member_id"] = int(match.group(1) if match.groups() else match.group(0))
        elif name == "channel":
            match = CHANNEL_PATTERN.match(str(value).strip()) or ID_PATTERN.match(str(value).strip())
            if not match:
                raise PlanProblem("need_channel")
            clean["channel_id"] = int(match.group(1) if match.groups() else match.group(0))
        elif name in ("minutes", "number"):
            if isinstance(value, bool) or not isinstance(value, int):
                raise PlanProblem("not_understood")
            if name == "minutes" and not 1 <= value <= mod_tools.MAX_TIMEOUT_MINUTES:
                raise PlanProblem("timeout_range")
            if name == "number" and value < 1:
                raise PlanProblem("not_understood")
            clean[name] = value
        elif name == "reason":
            if not isinstance(value, str) or not value.strip():
                raise PlanProblem("not_understood")
            clean["reason"] = value.strip()[:300]

    return Step(tool, clean, delay)


# =====================================================================
# 2. Rules between steps (uses the server's current state)
# =====================================================================

def check_dependencies(plan, is_timed_out, warning_count, label, pending_count=lambda member_id: 0):
    """
    - Nothing can come after a kick of the same member.
    - remove_timeout needs the member to be timed out now, or an EARLIER step in this plan that times them out.
    - clear_warnings needs warnings now, or an EARLIER step in this plan that warns them.
    Sets step.depends_on, or raises PlanProblem.
    - cancel_scheduled needs the member to have pending scheduled steps.
    is_timed_out(member_id) -> bool, warning_count(member_id) -> int, label(member_id) -> "@name",
    pending_count(member_id) -> int
    """
    lang = plan.language
    for step in plan.steps:
        earlier = [s for s in plan.steps if s.number < step.number and s.member_id == step.member_id]
        kicked = [s for s in earlier if s.tool == "kick_member"]
        if step.member_id and kicked:
            raise PlanProblem("after_kick", lang, member=label(step.member_id), n=kicked[-1].number)

        if step.tool == "remove_timeout":
            timeouts = [s for s in earlier if s.tool == "timeout_member"]
            if timeouts:
                step.depends_on = timeouts[-1].number
            elif not is_timed_out(step.member_id):
                raise PlanProblem("not_timed_out", lang, member=label(step.member_id))

        if step.tool == "cancel_scheduled":
            count = pending_count(step.member_id)
            step.info["count"] = count
            if count == 0:
                raise PlanProblem("no_scheduled", lang, member=label(step.member_id))
            step.info["count"] = count

        if step.tool == "clear_warnings":
            warns = [s for s in earlier if s.tool == "add_warning"]
            if warns:
                step.depends_on = warns[-1].number
            else:
                count = warning_count(step.member_id)
                if count == 0:
                    raise PlanProblem("no_warnings", lang, member=label(step.member_id))
                if step.args.get("number") and step.args["number"] > count:
                    raise PlanProblem("warning_number_high", lang, member=label(step.member_id), count=count)


def overlap_notes(plan):
    """Notes like 'The timeout would run for 30m, but it will be removed after 20m.' -> [(key, values)]"""
    notes = []
    for first in plan.steps:
        if first.tool != "timeout_member":
            continue
        ends_at = first.delay + first.args["minutes"]
        for later in plan.steps:
            if later.number <= first.number or later.member_id != first.member_id:
                continue
            if later.tool == "remove_timeout":
                if later.delay < ends_at:
                    notes.append(("note_removed_early", {"duration": format_minutes(first.args["minutes"]),
                                                         "after": format_minutes(later.delay - first.delay)}))
                else:
                    notes.append(("note_ends_first", {"duration": format_minutes(first.args["minutes"]),
                                                      "n": later.number}))
                break  # after a removal, later steps relate to a new situation
            if later.tool == "timeout_member" and later.delay < ends_at:
                notes.append(("note_replaced", {"n": later.number, "m": first.number}))
                break
    return notes


# =====================================================================
# 3. Human-readable lines (built by code, never by the AI)
# =====================================================================

def describe_step(step, language, label, t):
    """One plan line, e.g. 'Timeout @sam for 30m (starts now)'. `t` is assistant_text.t."""
    who = label(step.member_id) if step.member_id else ""
    if step.tool == "add_warning":
        text = t(language, "warn", member=who)
    elif step.tool == "clear_warnings":
        if step.args.get("number"):
            text = t(language, "clear_one", member=who, number=step.args["number"])
        else:
            text = t(language, "clear_all", member=who)
    elif step.tool == "timeout_member":
        text = t(language, "timeout", member=who, duration=format_minutes(step.args["minutes"]))
    elif step.tool == "remove_timeout":
        text = t(language, "remove_timeout", member=who)
    elif step.tool == "kick_member":
        text = t(language, "kick", member=who)
    elif step.tool == "cancel_scheduled":
        text = t(language, "cancel_scheduled", member=who, count=step.info.get("count", "?"))
    else:
        text = step.tool

    if step.args.get("reason"):
        text += t(language, "reason", reason=step.args["reason"])

    if step.delay == 0:
        timing = t(language, "starts_now" if step.tool == "timeout_member" else "now")
    else:
        timing = t(language, "after", delay=format_minutes(step.delay))
    return f"{text} {timing}"
