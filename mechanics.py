"""
mechanics.py
Pure deterministic game functions. No AI involved here.
Each function takes game state + parameters and returns a structured result dict.
"""

import random
from game_state import apply_damage, heal_player


# ── Dice ─────────────────────────────────────────────────────────────────────

def roll(sides: int, n: int = 1) -> list[int]:
    return [random.randint(1, sides) for _ in range(n)]

def roll_total(sides: int, n: int = 1) -> int:
    return sum(roll(sides, n))


# ── Core resolution ──────────────────────────────────────────────────────────

def skill_check(state: dict, skill: str, difficulty: int = 12, modifier: int = 0) -> dict:
    """
    Generic skill check. Roll 1d20 + skill bonus + modifier vs difficulty.
    Returns full result for the narrator.
    """
    player   = state["player"]
    bonus    = player["skills"].get(skill, 0)
    rolled   = roll_total(20)
    total    = rolled + bonus + modifier
    success  = total >= difficulty
    margin   = total - difficulty

    # Determine degree of success/failure
    if margin >= 5:
        degree = "strong success"
    elif margin >= 0:
        degree = "marginal success"
    elif margin >= -4:
        degree = "marginal failure"
    else:
        degree = "strong failure"

    return {
        "type":       "skill_check",
        "skill":      skill,
        "rolled":     rolled,
        "bonus":      bonus,
        "modifier":   modifier,
        "total":      total,
        "difficulty": difficulty,
        "success":    success,
        "degree":     degree,
        "margin":     margin,
    }


def attack(state: dict, target: str, weapon: str = "fist", modifier: int = 0) -> dict:
    """
    Attack a target. Rolls to hit then rolls damage.
    Applies damage to the target in game state.
    """
    player    = state["player"]
    npcs      = state["scene"]["npcs"]
    hit_bonus = player["skills"].get("attack", 0)

    # Resolve target key (fuzzy: accept partial names)
    target_key = _resolve_npc(target, npcs)

    # Difficulty to hit: base 10, +2 if target unfriendly/armed
    difficulty = 10
    if target_key and npcs[target_key]["disposition"] in ("unfriendly", "hostile"):
        difficulty = 12

    # To-hit roll
    hit_roll  = roll_total(20)
    hit_total = hit_roll + hit_bonus + modifier
    hit       = hit_total >= difficulty

    # Damage
    weapon_damage = _weapon_damage(weapon)
    dmg_rolls  = roll(weapon_damage["sides"], weapon_damage["dice"])
    dmg_total  = sum(dmg_rolls) + weapon_damage["bonus"]

    status_msg = ""
    if hit and target_key:
        status_msg = apply_damage(state, dmg_total, target_key)
    elif hit:
        status_msg = f"Hit! {dmg_total} damage dealt."

    return {
        "type":       "attack",
        "weapon":     weapon,
        "target":     target,
        "target_key": target_key,
        "hit_roll":   hit_roll,
        "hit_bonus":  hit_bonus,
        "hit_total":  hit_total,
        "difficulty": difficulty,
        "hit":        hit,
        "damage":     dmg_total if hit else 0,
        "dmg_rolls":  dmg_rolls if hit else [],
        "status":     status_msg,
    }


def stealth_check(state: dict, difficulty: int = 13, modifier: int = 0) -> dict:
    """Attempt to hide or move unseen."""
    result = skill_check(state, "stealth", difficulty, modifier)
    result["type"] = "stealth_check"
    if result["success"]:
        if "hidden" not in state["player"]["conditions"]:
            state["player"]["conditions"].append("hidden")
    else:
        # Remove hidden if failed
        state["player"]["conditions"] = [
            c for c in state["player"]["conditions"] if c != "hidden"
        ]
    return result


def persuasion_check(state: dict, target: str, approach: str = "neutral",
                     difficulty: int = 13, modifier: int = 0) -> dict:
    """
    Attempt to persuade, intimidate, or deceive an NPC.
    On success, improves NPC disposition.
    """
    skill = "deception" if approach == "deception" else "persuasion"
    result = skill_check(state, skill, difficulty, modifier)
    result["type"]   = "persuasion_check"
    result["target"] = target
    result["approach"] = approach

    target_key = _resolve_npc(target, state["scene"]["npcs"])
    if target_key:
        npc = state["scene"]["npcs"][target_key]
        if result["success"]:
            npc["disposition"] = _improve_disposition(npc["disposition"])
            result["new_disposition"] = npc["disposition"]
        else:
            if result["degree"] == "strong failure":
                npc["disposition"] = _worsen_disposition(npc["disposition"])
                result["new_disposition"] = npc["disposition"]

    return result


def use_item(state: dict, item: str, target: str = "") -> dict:
    """
    Use an item from inventory.
    Handles a small set of known consumables; everything else passes through.
    """
    inventory = state["player"]["inventory"]
    item_lower = item.lower()

    if item_lower not in [i.lower() for i in inventory]:
        return {
            "type":    "use_item",
            "item":    item,
            "success": False,
            "effect":  "Item not in inventory.",
        }

    # Known item effects
    if item_lower == "ration":
        hp_gain = roll_total(4) + 1
        msg = heal_player(state, hp_gain)
        _remove_item(state, item)
        return {"type": "use_item", "item": item, "success": True,
                "effect": f"Restored {hp_gain} HP. {msg}", "consumed": True}

    if item_lower == "torch":
        return {"type": "use_item", "item": item, "success": True,
                "effect": "The torch flickers to life, casting warm light.",
                "consumed": False}

    if item_lower == "dagger" and target:
        return attack(state, target, weapon="dagger")

    # Generic: item is used narratively, no mechanical effect
    return {
        "type":    "use_item",
        "item":    item,
        "target":  target,
        "success": True,
        "effect":  "Used. The narrator will describe the outcome.",
        "consumed": False,
    }


def examine(state: dict, subject: str) -> dict:
    """
    Examine a subject. Rolls perception to reveal extra detail.
    """
    result = skill_check(state, "perception", difficulty=10)
    result["type"]    = "examine"
    result["subject"] = subject

    # Check if subject is a known NPC
    target_key = _resolve_npc(subject, state["scene"]["npcs"])
    if target_key:
        npc = state["scene"]["npcs"][target_key]
        result["known_info"] = f"{npc['name']}, disposition: {npc['disposition']}, HP: {npc['hp']}"
    elif subject.lower() in [o.lower() for o in state["scene"]["objects"]]:
        result["known_info"] = f"'{subject}' is present in the scene."
    else:
        result["known_info"] = "Subject not explicitly registered in scene."

    return result


def rest(state: dict) -> dict:
    """Short rest. Recover some HP."""
    hp_gain = roll_total(6) + 2
    msg     = heal_player(state, hp_gain)
    return {
        "type":    "rest",
        "hp_gain": hp_gain,
        "status":  msg,
    }


# ── Internal helpers ─────────────────────────────────────────────────────────

def _resolve_npc(name: str, npcs: dict) -> str | None:
    """Fuzzy match a player-typed name to an NPC key."""
    name_lower = name.lower()
    for key, npc in npcs.items():
        if name_lower in key.lower() or name_lower in npc["name"].lower():
            return key
    return None


def _weapon_damage(weapon: str) -> dict:
    table = {
        "dagger":    {"dice": 1, "sides": 4, "bonus": 1},
        "sword":     {"dice": 1, "sides": 8, "bonus": 2},
        "axe":       {"dice": 1, "sides": 6, "bonus": 2},
        "staff":     {"dice": 1, "sides": 6, "bonus": 0},
        "fist":      {"dice": 1, "sides": 4, "bonus": 0},
        "crossbow":  {"dice": 1, "sides": 8, "bonus": 1},
    }
    return table.get(weapon.lower(), {"dice": 1, "sides": 4, "bonus": 0})


def _improve_disposition(d: str) -> str:
    order = ["hostile", "unfriendly", "neutral", "friendly", "allied"]
    idx = order.index(d) if d in order else 2
    return order[min(idx + 1, len(order) - 1)]

def _worsen_disposition(d: str) -> str:
    order = ["hostile", "unfriendly", "neutral", "friendly", "allied"]
    idx = order.index(d) if d in order else 2
    return order[max(idx - 1, 0)]

def _remove_item(state: dict, item: str):
    inv = state["player"]["inventory"]
    for i, it in enumerate(inv):
        if it.lower() == item.lower():
            inv.pop(i)
            return
