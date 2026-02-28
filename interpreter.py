"""
interpreter.py
Maps the hierarchical verb selection to mechanics functions.

VERB_TREE structure:
  { Category: { Action: { "hint": str, "func": callable } } }

The "hint" is shown in the UI to guide the player on what to fill in.
The "func" key is resolved at dispatch time to avoid circular imports.
"""

from mechanics import (
    attack, stealth_check, persuasion_check,
    use_item, examine, rest, skill_check
)


# ── Verb tree ─────────────────────────────────────────────────────────────────
# Each leaf has: hint (shown in UI) and a mechanic key (resolved in interpret())

VERB_TREE = {

    "⚔️ Combat": {
        "Attack":       {"hint": "Target: who to hit | Modifier: recklessly, carefully | Item: your weapon"},
        "Disarm":       {"hint": "Target: who to disarm | Modifier: from behind, swiftly"},
        "Grapple":      {"hint": "Target: who to grab | Modifier: from behind, off-guard"},
        "Defend":       {"hint": "Target: who's attacking you | Modifier: shield raised, braced"},
        "Flee":         {"hint": "Target: leave toward which exit | Modifier: desperately, quietly"},
    },

    "🥷 Stealth": {
        "Sneak":        {"hint": "Target: past whom / toward what | Modifier: slowly, in shadows"},
        "Hide":         {"hint": "Target: behind what | Modifier: quickly, carefully"},
        "Pick Pocket":  {"hint": "Target: whose pocket | Modifier: casually, while distracted"},
        "Tail":         {"hint": "Target: who to follow | Modifier: at a distance, in a crowd"},
    },

    "🗣️ Social": {
        "Persuade":     {"hint": "Target: who | Modifier: your angle — logic, appeal, flattery"},
        "Intimidate":   {"hint": "Target: who | Modifier: quietly, with a weapon drawn, staring"},
        "Deceive":      {"hint": "Target: who | Modifier: the lie you're telling"},
        "Seduce":       {"hint": "Target: who | Modifier: tone — playful, direct, tender, bold"},
        "Flatter":      {"hint": "Target: who | Modifier: what you compliment"},
        "Bribe":        {"hint": "Target: who | Item: what you're offering"},
        "Threaten":     {"hint": "Target: who | Modifier: what you threaten them with"},
        "Negotiate":    {"hint": "Target: who | Modifier: your opening offer or position"},
    },

    "🔍 Exploration": {
        "Examine":      {"hint": "Target: object, person, or area to inspect"},
        "Search":       {"hint": "Target: area or object to search | Modifier: methodically, hastily"},
        "Listen":       {"hint": "Target: what/who to listen to | Modifier: through the door, from afar"},
        "Track":        {"hint": "Target: who or what to track | Modifier: fresh trail, old trail"},
        "Unlock":       {"hint": "Target: what to unlock | Item: key or lockpick"},
        "Move To":      {"hint": "Target: where to go | Modifier: carefully, quickly, stealthily"},
    },

    "🧪 Interaction": {
        "Use Item":     {"hint": "Item: select below | Target: on whom / on what"},
        "Pick Up":      {"hint": "Target: what to pick up"},
        "Drop":         {"hint": "Target: what to drop | Modifier: quietly, dramatically"},
        "Give":         {"hint": "Target: to whom | Item: what to give"},
        "Craft":        {"hint": "Target: what to make | Item: materials used"},
    },

    "🧘 Recovery": {
        "Rest":         {"hint": "No target needed — take a short rest to recover HP"},
        "Tend Wounds":  {"hint": "Target: yourself or an ally | Item: bandage, potion"},
        "Meditate":     {"hint": "Modifier: focus on what — calm, clarity, a memory"},
    },

    "💬 Dialogue": {
        "Ask":          {"hint": "Target: who | Modifier: what you want to know"},
        "Tell":         {"hint": "Target: who | Modifier: what information you share"},
        "Lie":          {"hint": "Target: who | Modifier: what false thing you say"},
        "Confess":      {"hint": "Target: who | Modifier: what you admit"},
        "Taunt":        {"hint": "Target: who | Modifier: what you say to provoke"},
        "Compliment":   {"hint": "Target: who | Modifier: what you praise"},
    },

}

# Flat list for backward compat if anything needs it
VERBS = [sub for cat in VERB_TREE.values() for sub in cat.keys()]


# ── Mechanic dispatch ─────────────────────────────────────────────────────────

def interpret(state: dict, verb: str, subject: str = "",
              modifiers: str = "", items: list = None) -> dict:
    """
    Route a verb + args to the correct mechanic function.
    Returns a result dict with at minimum:
      type, mechanical (bool)
    """
    if items is None:
        items = []

    v   = verb.lower().strip()
    mod = _parse_modifier_bonus(modifiers)

    # ── Combat ────────────────────────────────────────────────────────────────
    if v == "attack":
        weapon = items[0] if items else "fist"
        result = attack(state, target=subject, weapon=weapon, modifier=mod)
        result["mechanical"] = True
        return result

    if v == "disarm":
        result = skill_check(state, "attack",
                             difficulty=_diff(modifiers, 14), modifier=mod)
        result["type"] = "skill_check"
        result["flavor"] = "disarm"
        result["mechanical"] = True
        return result

    if v == "grapple":
        result = skill_check(state, "athletics",
                             difficulty=_diff(modifiers, 13), modifier=mod)
        result["type"] = "skill_check"
        result["flavor"] = "grapple"
        result["mechanical"] = True
        return result

    if v == "defend":
        result = skill_check(state, "athletics",
                             difficulty=_diff(modifiers, 11), modifier=mod)
        result["type"] = "skill_check"
        result["flavor"] = "defend"
        result["mechanical"] = True
        return result

    if v == "flee":
        result = skill_check(state, "athletics",
                             difficulty=_diff(modifiers, 12), modifier=mod)
        result["type"] = "skill_check"
        result["flavor"] = "flee"
        result["mechanical"] = True
        return result

    # ── Stealth ───────────────────────────────────────────────────────────────
    if v in ("sneak", "hide"):
        result = stealth_check(state, difficulty=_diff(modifiers, 13), modifier=mod)
        result["mechanical"] = True
        return result

    if v == "pick pocket":
        result = skill_check(state, "stealth",
                             difficulty=_diff(modifiers, 14), modifier=mod)
        result["type"] = "skill_check"
        result["flavor"] = "pick_pocket"
        result["mechanical"] = True
        return result

    if v == "tail":
        result = skill_check(state, "stealth",
                             difficulty=_diff(modifiers, 13), modifier=mod)
        result["type"] = "skill_check"
        result["flavor"] = "tail"
        result["mechanical"] = True
        return result

    # ── Social ────────────────────────────────────────────────────────────────
    if v == "persuade":
        result = persuasion_check(state, target=subject, approach="persuasion",
                                  difficulty=_diff(modifiers, 13), modifier=mod)
        result["mechanical"] = True
        return result

    if v in ("intimidate", "threaten"):
        result = persuasion_check(state, target=subject, approach="persuasion",
                                  difficulty=_diff(modifiers, 14), modifier=mod)
        result["flavor"] = "intimidate"
        result["mechanical"] = True
        return result

    if v in ("deceive", "lie"):
        result = persuasion_check(state, target=subject, approach="deception",
                                  difficulty=_diff(modifiers, 14), modifier=mod)
        result["mechanical"] = True
        return result

    if v in ("seduce", "flatter", "compliment"):
        # Seduction/flattery uses charisma-flavored persuasion, slightly harder
        result = persuasion_check(state, target=subject, approach="persuasion",
                                  difficulty=_diff(modifiers, 14), modifier=mod)
        result["flavor"] = v
        result["mechanical"] = True
        return result

    if v == "bribe":
        item = items[0] if items else ""
        result = persuasion_check(state, target=subject, approach="persuasion",
                                  difficulty=_diff(modifiers, 12), modifier=mod)
        result["flavor"] = "bribe"
        result["bribe_item"] = item
        result["mechanical"] = True
        return result

    if v == "negotiate":
        result = persuasion_check(state, target=subject, approach="persuasion",
                                  difficulty=_diff(modifiers, 13), modifier=mod)
        result["flavor"] = "negotiate"
        result["mechanical"] = True
        return result

    # ── Exploration ───────────────────────────────────────────────────────────
    if v == "examine":
        result = examine(state, subject=subject)
        result["mechanical"] = True
        return result

    if v == "search":
        result = skill_check(state, "perception",
                             difficulty=_diff(modifiers, 12), modifier=mod)
        result["type"] = "skill_check"
        result["flavor"] = "search"
        result["mechanical"] = True
        return result

    if v == "listen":
        result = skill_check(state, "perception",
                             difficulty=_diff(modifiers, 12), modifier=mod)
        result["type"] = "skill_check"
        result["flavor"] = "listen"
        result["mechanical"] = True
        return result

    if v == "track":
        result = skill_check(state, "perception",
                             difficulty=_diff(modifiers, 14), modifier=mod)
        result["type"] = "skill_check"
        result["flavor"] = "track"
        result["mechanical"] = True
        return result

    if v == "unlock":
        item = items[0] if items else ""
        diff = 10 if "key" in item.lower() else 14
        result = skill_check(state, "dexterity",
                             difficulty=_diff(modifiers, diff), modifier=mod)
        result["type"] = "skill_check"
        result["flavor"] = "unlock"
        result["mechanical"] = True
        return result

    # ── Interaction ───────────────────────────────────────────────────────────
    if v == "use item":
        item = items[0] if items else subject
        result = use_item(state, item=item, target=subject)
        result["mechanical"] = True
        return result

    if v == "give":
        item = items[0] if items else ""
        result = use_item(state, item=item, target=subject)
        result["flavor"] = "give"
        result["mechanical"] = True
        return result

    # ── Recovery ─────────────────────────────────────────────────────────────
    if v == "rest":
        result = rest(state)
        result["mechanical"] = True
        return result

    if v == "tend wounds":
        item = items[0] if items else ""
        result = use_item(state, item=item, target=subject) if item else rest(state)
        result["flavor"] = "tend_wounds"
        result["mechanical"] = True
        return result

    # ── Dialogue & narrative-only ─────────────────────────────────────────────
    # Ask, Tell, Confess, Taunt, Move To, Meditate, Pick Up, Drop, etc.
    # These are pure narrative — no roll — but a soft perception/charisma
    # oracle fires behind the scenes to give the LLM a success/fail hint.
    result = skill_check(state, "charisma", difficulty=10, modifier=mod)
    result["type"] = "narrative"
    result["verb"] = verb
    result["mechanical"] = False   # don't show dice in UI
    result["oracle"] = result["success"]  # whisper a hint to the narrator
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_modifier_bonus(modifiers: str) -> int:
    if not modifiers:
        return 0
    m = modifiers.lower()
    bonus = 0
    if any(w in m for w in ["carefully", "slowly", "cautiously", "tenderly", "gently"]):
        bonus += 1
    if any(w in m for w in ["recklessly", "hastily", "rushing", "aggressively"]):
        bonus -= 1
    if any(w in m for w in ["from behind", "surprise", "unaware", "off-guard"]):
        bonus += 2
    if any(w in m for w in ["hidden", "stealthily", "while invisible"]):
        bonus += 2
    if any(w in m for w in ["bold", "direct", "confidently"]):
        bonus += 1
    if any(w in m for w in ["nervous", "hesitant", "unsure"]):
        bonus -= 1
    return bonus


def _diff(modifiers: str, default: int) -> int:
    if not modifiers:
        return default
    m = modifiers.lower()
    if any(w in m for w in ["easy", "simple", "willing", "friendly"]):
        return max(8, default - 3)
    if any(w in m for w in ["hard", "difficult", "suspicious", "hostile"]):
        return default + 3
    return default
