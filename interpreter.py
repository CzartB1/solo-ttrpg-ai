"""
interpreter.py
Takes the structured form input (verb, subject, modifiers, items)
and routes it to the correct mechanics function.
No LLM needed here — the verb dropdown already tells us the intent.
"""

from mechanics import (
    attack, stealth_check, persuasion_check,
    use_item, examine, rest, skill_check
)


# ── Available verbs (shown in the UI dropdown) ───────────────────────────────

VERBS = [
    "Attack",
    "Sneak",
    "Persuade",
    "Deceive",
    "Examine",
    "Use Item",
    "Rest",
    "Move",       # narrative only — no mechanic
    "Talk",       # narrative only — no mechanic
    "Other",      # bypass — goes straight to narrator
]


# ── Main dispatch ─────────────────────────────────────────────────────────────

def interpret(state: dict, verb: str, subject: str = "",
              modifiers: str = "", items: list[str] = None) -> dict:
    """
    Route a structured player action to the correct mechanic.

    Returns a result dict that always has:
      - 'type'       : what kind of action this was
      - 'mechanical' : True if a mechanic was run, False if narrative-only
      - plus whatever the mechanic function returns
    """
    if items is None:
        items = []

    verb_key = verb.strip().lower()

    # ── Mechanical actions ────────────────────────────────────────────────────

    if verb_key == "attack":
        weapon = items[0] if items else "fist"
        mod    = _parse_modifier_bonus(modifiers)
        result = attack(state, target=subject, weapon=weapon, modifier=mod)
        result["mechanical"] = True
        return result

    if verb_key == "sneak":
        mod    = _parse_modifier_bonus(modifiers)
        diff   = _difficulty_from_modifiers(modifiers, default=13)
        result = stealth_check(state, difficulty=diff, modifier=mod)
        result["mechanical"] = True
        return result

    if verb_key == "persuade":
        mod    = _parse_modifier_bonus(modifiers)
        diff   = _difficulty_from_modifiers(modifiers, default=13)
        result = persuasion_check(state, target=subject, approach="persuasion",
                                  difficulty=diff, modifier=mod)
        result["mechanical"] = True
        return result

    if verb_key == "deceive":
        mod    = _parse_modifier_bonus(modifiers)
        diff   = _difficulty_from_modifiers(modifiers, default=14)
        result = persuasion_check(state, target=subject, approach="deception",
                                  difficulty=diff, modifier=mod)
        result["mechanical"] = True
        return result

    if verb_key == "examine":
        result = examine(state, subject=subject)
        result["mechanical"] = True
        return result

    if verb_key == "use item":
        item   = items[0] if items else subject
        result = use_item(state, item=item, target=subject)
        result["mechanical"] = True
        return result

    if verb_key == "rest":
        result = rest(state)
        result["mechanical"] = True
        return result

    # ── Narrative-only actions (no mechanic, pass through) ────────────────────

    return {
        "type":       "narrative",
        "verb":       verb,
        "subject":    subject,
        "modifiers":  modifiers,
        "items":      items,
        "mechanical": False,
    }


# ── Modifier parsing helpers ──────────────────────────────────────────────────

def _parse_modifier_bonus(modifiers: str) -> int:
    """
    Look for explicit bonus/penalty keywords in the modifier text.
    e.g. 'carefully' → +1, 'recklessly' → -1, 'from behind' → +2
    """
    if not modifiers:
        return 0
    m = modifiers.lower()
    bonus = 0
    if any(w in m for w in ["carefully", "slowly", "cautiously"]):
        bonus += 1
    if any(w in m for w in ["recklessly", "hastily", "rushing"]):
        bonus -= 1
    if any(w in m for w in ["from behind", "surprise", "unaware"]):
        bonus += 2
    if any(w in m for w in ["while hidden", "stealthily"]):
        bonus += 2
    return bonus


def _difficulty_from_modifiers(modifiers: str, default: int = 12) -> int:
    """Allow player to hint at difficulty via modifier text."""
    if not modifiers:
        return default
    m = modifiers.lower()
    if any(w in m for w in ["easy", "simple", "trivial"]):
        return max(8, default - 3)
    if any(w in m for w in ["hard", "difficult", "tricky"]):
        return default + 3
    return default
