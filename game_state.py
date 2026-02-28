"""
game_state.py
Manages the player character sheet, scene state, inventory, and session history.
All state is stored as a dict and can be saved/loaded as JSON.
"""

import json
import os
from datetime import datetime


# ── Default character template ──────────────────────────────────────────────

def new_character(name: str = "Traveller") -> dict:
    return {
        "name": name,
        "hp": 20,
        "hp_max": 20,
        "stats": {
            "strength":     1,
            "dexterity":    1,
            "intelligence": 1,
            "charisma":     1,
            "perception":   1,
        },
        "skills": {
            "attack":    2,   # bonus on attack rolls
            "stealth":   1,
            "persuasion":1,
            "deception": 0,
            "perception":1,
            "athletics": 1,
        },
        "inventory": ["torch", "dagger", "ration"],
        "conditions": [],     # e.g. ["poisoned", "hidden"]
        "gold": 10,
    }


# ── Default scene template ───────────────────────────────────────────────────

def new_scene() -> dict:
    return {
        "location": "The Rusty Flagon Inn",
        "description": (
            "A dimly lit tavern that smells of stale ale and woodsmoke. "
            "A few rough-looking patrons nurse their drinks. "
            "A nervous merchant sits alone in the corner."
        ),
        "npcs": {
            "bartender": {"name": "Gareth", "disposition": "neutral", "hp": 10},
            "merchant":  {"name": "Silas",  "disposition": "nervous", "hp":  8},
            "patron":    {"name": "Drunk Patron", "disposition": "unfriendly", "hp": 12},
        },
        "objects": ["bar counter", "fireplace", "wooden chair", "locked door"],
        "exits": ["north alley", "upstairs room"],
        "ambient": "evening",
    }


# ── Full game state ──────────────────────────────────────────────────────────

def new_game(player_name: str = "Traveller") -> dict:
    return {
        "player": new_character(player_name),
        "scene":  new_scene(),
        "history": [],        # list of {turn, action, result, narration}
        "summary": "",        # compressed older history
        "turn": 0,
        "created": datetime.now().isoformat(),
    }


# ── State helpers ────────────────────────────────────────────────────────────

def add_history(state: dict, action: dict, result: dict, narration: str):
    state["history"].append({
        "turn":      state["turn"],
        "action":    action,
        "result":    result,
        "narration": narration,
    })
    state["turn"] += 1
    # Keep only the last 10 turns in full; older turns are summarised elsewhere
    if len(state["history"]) > 10:
        state["history"] = state["history"][-10:]


def get_recent_history(state: dict, n: int = 4) -> list:
    return state["history"][-n:]


def apply_damage(state: dict, amount: int, target: str = "player") -> str:
    """Apply damage to player or a named NPC. Returns a status string."""
    if target == "player":
        state["player"]["hp"] = max(0, state["player"]["hp"] - amount)
        hp = state["player"]["hp"]
        if hp == 0:
            return "The player has fallen unconscious!"
        return f"Player HP: {hp}/{state['player']['hp_max']}"
    elif target in state["scene"]["npcs"]:
        npc = state["scene"]["npcs"][target]
        npc["hp"] = max(0, npc["hp"] - amount)
        if npc["hp"] == 0:
            npc["disposition"] = "defeated"
            return f"{npc['name']} has been defeated!"
        return f"{npc['name']} HP: {npc['hp']}"
    return "Target not found."


def heal_player(state: dict, amount: int) -> str:
    p = state["player"]
    p["hp"] = min(p["hp_max"], p["hp"] + amount)
    return f"Player HP: {p['hp']}/{p['hp_max']}"


# ── Save / Load ──────────────────────────────────────────────────────────────

SAVE_PATH = "savegame.json"

def save_game(state: dict, path: str = SAVE_PATH):
    with open(path, "w") as f:
        json.dump(state, f, indent=2)
    print(f"[Game saved to {path}]")

def load_game(path: str = SAVE_PATH) -> dict:
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


# ── State summary for LLM context ───────────────────────────────────────────

def state_summary(state: dict) -> str:
    """Returns a compact text snapshot of the game state for the LLM prompt."""
    p  = state["player"]
    sc = state["scene"]
    conditions = ", ".join(p["conditions"]) if p["conditions"] else "none"
    npcs = ", ".join(
        f"{v['name']} ({v['disposition']})"
        for v in sc["npcs"].values()
    )
    inventory = ", ".join(p["inventory"]) if p["inventory"] else "nothing"
    recent = get_recent_history(state, n=3)
    history_text = ""
    for h in recent:
        history_text += f"\n  Turn {h['turn']}: {h['narration'][:120]}..."

    return f"""
=== GAME STATE ===
Location : {sc['location']}
Scene    : {sc['description']}
NPCs     : {npcs}
Objects  : {', '.join(sc['objects'])}

Player   : {p['name']}  HP {p['hp']}/{p['hp_max']}
Conditions: {conditions}
Inventory: {inventory}
Gold     : {p['gold']}

Recent history:{history_text if history_text else ' (none yet)'}
=== END STATE ===
""".strip()
