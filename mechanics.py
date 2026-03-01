"""
mechanics.py
Pure deterministic game functions. No AI involved here.
Each function takes game state + parameters and returns a structured result dict.
"""

import random


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
    player  = state["player"]
    bonus   = player["skills"].get(skill, 0)
    rolled  = roll_total(20)
    total   = rolled + bonus + modifier
    success = total >= difficulty
    margin  = total - difficulty

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
    Applies damage to the target's live state.
    """
    player    = state["player"]
    hit_bonus = player["skills"].get("attack", 0)

    # Resolve target key against npcs_live (session_state structure)
    npcs_live = state["scene"].get("npcs_live", {})
    target_key = _resolve_npc(target, npcs_live)

    # Difficulty to hit: base 10, +2 if hostile
    difficulty = 10
    if target_key and npcs_live.get(target_key, {}).get("disposition") in ("unfriendly", "hostile"):
        difficulty = 12

    # To-hit roll
    hit_roll  = roll_total(20)
    hit_total = hit_roll + hit_bonus + modifier
    hit       = hit_total >= difficulty

    if not hit:
        return {
            "type":       "attack",
            "target":     target,
            "target_id":  target_key or "",
            "weapon":     weapon,
            "hit":        False,
            "hit_roll":   hit_roll,
            "hit_bonus":  hit_bonus,
            "hit_total":  hit_total,
            "difficulty": difficulty,
        }

    # Damage roll
    dmg_spec  = _weapon_damage(weapon)
    dmg_total = roll_total(dmg_spec["sides"], dmg_spec["dice"]) + dmg_spec["bonus"]

    # Apply to npcs_live
    sc   = state["scene"]
    live = sc.setdefault("npcs_live", {})
    if target_key not in live:
        live[target_key or target] = {"hp": 10, "disposition": "neutral", "status": "alive"}
    key = target_key or target
    live[key]["hp"] = max(0, live[key]["hp"] - dmg_total)
    defeated = live[key]["hp"] <= 0
    if defeated:
        live[key]["status"] = "defeated"

    status_msg = (f"{target} defeated!" if defeated
                  else f"{target} HP: {live[key]['hp']}")

    return {
        "type":       "attack",
        "target":     target,
        "target_id":  key,
        "weapon":     weapon,
        "hit":        True,
        "hit_roll":   hit_roll,
        "hit_bonus":  hit_bonus,
        "hit_total":  hit_total,
        "difficulty": difficulty,
        "damage":     dmg_total,
        "defeated":   defeated,
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
        state["player"]["conditions"] = [
            c for c in state["player"]["conditions"] if c != "hidden"
        ]
    return result


def persuasion_check(state: dict, target: str, approach: str = "neutral",
                     difficulty: int = 13, modifier: int = 0) -> dict:
    """
    Attempt to persuade, intimidate, or deceive an NPC.
    On success, improves NPC disposition in npcs_live.
    """
    skill  = "deception" if approach == "deception" else "persuasion"
    result = skill_check(state, skill, difficulty, modifier)
    result["type"]     = "persuasion_check"
    result["target"]   = target
    result["approach"] = approach

    # Update disposition in npcs_live (session_state structure)
    npcs_live  = state["scene"].get("npcs_live", {})
    target_key = _resolve_npc(target, npcs_live)
    if target_key:
        live = npcs_live[target_key]
        if result["success"]:
            live["disposition"] = _improve_disposition(live.get("disposition", "neutral"))
        elif result["degree"] == "strong failure":
            live["disposition"] = _worsen_disposition(live.get("disposition", "neutral"))
        result["new_disposition"] = live["disposition"]

    return result


def use_item(state: dict, item: str, target: str = "") -> dict:
    """
    Use an item from inventory.
    Handles a small set of known consumables; everything else passes through.
    """
    inventory  = state["player"]["inventory"]
    item_lower = item.lower()

    if item_lower not in [i.lower() for i in inventory]:
        return {
            "type":    "use_item",
            "item":    item,
            "success": False,
            "effect":  "Item not in inventory.",
        }

    if item_lower == "ration":
        hp_gain = roll_total(4) + 1
        msg = _heal_player(state, hp_gain)
        _remove_item(state, item)
        return {"type": "use_item", "item": item, "success": True,
                "effect": f"Restored {hp_gain} HP. {msg}", "consumed": True}

    if item_lower == "torch":
        return {"type": "use_item", "item": item, "success": True,
                "effect": "The torch flickers to life, casting warm light.",
                "consumed": False}

    if item_lower == "dagger" and target:
        return attack(state, target, weapon="dagger")

    return {
        "type":     "use_item",
        "item":     item,
        "target":   target,
        "success":  True,
        "effect":   "Used. The narrator will describe the outcome.",
        "consumed": False,
    }


def examine(state: dict, subject: str) -> dict:
    """
    Examine a subject. Rolls perception to reveal extra detail.
    """
    result = skill_check(state, "perception", difficulty=10)
    result["type"]    = "examine"
    result["subject"] = subject

    # Check npcs_live (session_state structure)
    npcs_live  = state["scene"].get("npcs_live", {})
    target_key = _resolve_npc(subject, npcs_live)
    if target_key:
        live = npcs_live[target_key]
        result["known_info"] = (
            f"{target_key}, disposition: {live.get('disposition','?')}, "
            f"HP: {live.get('hp','?')}, status: {live.get('status','?')}"
        )
    else:
        # objects is a dict in session_state: {id: {label, flags}}
        objects = state["scene"].get("objects", {})
        match = next(
            (obj["label"] for obj in objects.values()
             if subject.lower() in obj.get("label", "").lower()),
            None
        )
        if match:
            result["known_info"] = f"'{match}' is present in the scene."
        else:
            result["known_info"] = "Nothing specific noted about that."

    return result


def rest(state: dict) -> dict:
    """Short rest. Recover some HP."""
    hp_gain = roll_total(6) + 2
    msg     = _heal_player(state, hp_gain)
    return {
        "type":    "rest",
        "hp_gain": hp_gain,
        "status":  msg,
    }


# ── Internal helpers ─────────────────────────────────────────────────────────

def _heal_player(state: dict, amount: int) -> str:
    """Heal the player in session state. Returns a status string."""
    p = state["player"]
    p["hp"] = min(p["hp_max"], p["hp"] + amount)
    return f"Player HP: {p['hp']}/{p['hp_max']}"


def _resolve_npc(name: str, npcs: dict) -> str | None:
    """Fuzzy match a player-typed name to an NPC key in npcs_live."""
    name_lower = name.lower()
    for key in npcs:
        if name_lower in key.lower():
            return key
    return None


def _weapon_damage(weapon: str) -> dict:
    table = {
        "dagger":   {"dice": 1, "sides": 4, "bonus": 1},
        "sword":    {"dice": 1, "sides": 8, "bonus": 2},
        "axe":      {"dice": 1, "sides": 6, "bonus": 2},
        "staff":    {"dice": 1, "sides": 6, "bonus": 0},
        "fist":     {"dice": 1, "sides": 4, "bonus": 0},
        "crossbow": {"dice": 1, "sides": 8, "bonus": 1},
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
