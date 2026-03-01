"""
state_changes.py
Applies mechanical state changes to the session after a mechanic resolves.
Runs BEFORE the narrator call so the narrator sees updated state.

Flow: interpret() → apply_mechanical_changes() → narrate()
"""

from world_bible import load_entity, load_index
from session_state import append_scene_note


# ── Main dispatcher ───────────────────────────────────────────────────────────

def apply_mechanical_changes(session: dict, result: dict,
                             world_slug: str = "") -> list[str]:
    """
    Mutate session state based on mechanical result.
    Returns a list of change description strings (for GM screen / status display).
    """
    if not result.get("mechanical"):
        return []

    rtype = result.get("type", "")
    changes = []

    # ── Movement ─────────────────────────────────────────────────────────────
    if rtype == "move":
        changes += _handle_move(session, result, world_slug)

    # ── Defeat ───────────────────────────────────────────────────────────────
    elif rtype == "attack" and result.get("defeated"):
        changes += _handle_defeat(session, result)

    # ── Item consumed ─────────────────────────────────────────────────────────
    elif rtype == "use_item" and result.get("consumed"):
        item = result.get("item", "")
        if item:
            changes.append(f"🎒 {item} consumed and removed from inventory.")

    # ── Unlock ────────────────────────────────────────────────────────────────
    elif rtype == "skill_check" and result.get("flavor") == "unlock":
        if result.get("success"):
            changes += _handle_unlock(session, result)

    # ── Pick up ───────────────────────────────────────────────────────────────
    elif rtype == "pick_up" and result.get("success"):
        item = result.get("item", "")
        if item:
            changes.append(f"🎒 {item} added to inventory.")

    # ── Drop ──────────────────────────────────────────────────────────────────
    elif rtype == "drop":
        item = result.get("item", "")
        if item:
            changes.append(f"🎒 {item} dropped.")

    # ── Stealth ───────────────────────────────────────────────────────────────
    elif rtype == "stealth_check":
        if result.get("success"):
            changes.append("🥷 Player is now hidden.")
        else:
            changes.append("🥷 Player failed to hide — hidden condition removed.")

    # ── Rest ──────────────────────────────────────────────────────────────────
    elif rtype == "rest":
        hp = session["player"]["hp"]
        hp_max = session["player"]["hp_max"]
        changes.append(f"💤 Rested. HP: {hp}/{hp_max}.")

    return changes


# ── Movement handler ──────────────────────────────────────────────────────────

def _handle_move(session: dict, result: dict, world_slug: str) -> list[str]:
    destination = result.get("destination", "").strip()
    if not destination:
        return []

    # Try to find destination in world bible
    if world_slug:
        loc_data = _find_location(world_slug, destination)
        if loc_data:
            return _transition_to_known_location(session, loc_data)

    # Destination not in bible — narrative move only, ghost it
    return _transition_to_unknown_location(session, destination)


def _find_location(world_slug: str, destination: str) -> dict | None:
    """Search world bible locations by name or id."""
    index = load_index(world_slug)
    dest_lower = destination.lower()
    for entry in index:
        if entry.get("collection") != "locations":
            continue
        if (dest_lower in entry["name"].lower() or
                dest_lower in entry["id"].lower() or
                any(dest_lower in alias.lower()
                    for alias in entry.get("aliases", []))):
            return load_entity(world_slug, "locations", entry["id"])
    return None


def _transition_to_known_location(session: dict, loc_data: dict) -> list[str]:
    """Move to a world-bible location. Load its data into scene."""
    sc = session["scene"]

    # Save current scene notes to a turn marker before leaving
    # (notes stay in session history, don't need explicit save)

    old_name = sc.get("location_name", "?")
    sc["location_id"]   = loc_data.get("id", "")
    sc["location_name"] = loc_data.get("name", "Unknown")

    # Pull description from appearance
    app = loc_data.get("appearance", {})
    if isinstance(app, dict):
        sc["description"] = (app.get("visual","") + " " +
                             app.get("atmosphere","")).strip()
    else:
        sc["description"] = str(app)

    sc["ambient"] = loc_data.get("appearance",{}).get("lighting","") or "day"

    # Update present NPCs from world bible
    inh = loc_data.get("inhabitants", {})
    sc["npcs_present"] = list(inh.get("currently_present", []))

    # Register any world-bible objects as scene objects
    # (connections become potential exits)
    connections = loc_data.get("connections", [])
    sc["objects"] = sc.get("objects", {})
    for conn in connections:
        dest = conn.get("to","")
        if dest and dest not in sc["objects"]:
            sc["objects"][dest] = {
                "label": dest.replace("-"," ").title(),
                "flags": {}
            }

    # Clear scene notes for new location
    sc["notes"] = []

    return [
        f"📍 Moved to **{sc['location_name']}** (from {old_name}).",
        f"👥 Present: {', '.join(sc['npcs_present']) or 'nobody'}.",
    ]


def _transition_to_unknown_location(session: dict, destination: str) -> list[str]:
    """Move to a location not in the world bible. Let narrator invent it."""
    sc = session["scene"]
    old_name = sc.get("location_name", "?")

    sc["location_id"]   = ""
    sc["location_name"] = destination
    sc["description"]   = ""   # narrator will describe
    sc["npcs_present"]  = []
    sc["notes"]         = []

    return [
        f"📍 Moved to **{destination}** (from {old_name}). "
        f"*(Location not in world bible — narrator will improvise.)*"
    ]


# ── Defeat handler ────────────────────────────────────────────────────────────

def _handle_defeat(session: dict, result: dict) -> list[str]:
    target_id = result.get("target_id","")
    sc = session["scene"]

    # Update live NPC status
    if target_id and target_id in sc.get("npcs_live",{}):
        sc["npcs_live"][target_id]["status"] = "defeated"
        sc["npcs_live"][target_id]["hp"]     = 0

    # Remove from present list
    if target_id and target_id in sc.get("npcs_present",[]):
        sc["npcs_present"] = [n for n in sc["npcs_present"] if n != target_id]

    append_scene_note(session, f"{target_id or result.get('target','')} was defeated here.")

    return [f"💀 {target_id or result.get('target','')} defeated and removed from scene."]


# ── Unlock handler ────────────────────────────────────────────────────────────

def _handle_unlock(session: dict, result: dict) -> list[str]:
    # Find the object being unlocked from subject
    subject = result.get("item","")
    sc = session["scene"]
    for oid, obj in sc.get("objects",{}).items():
        if subject.lower() in obj.get("label","").lower():
            obj["flags"]["locked"] = False
            return [f"🔓 {obj['label']} unlocked."]
    # Object not registered — add it
    label = subject or "object"
    sc.setdefault("objects",{})[label] = {
        "label": label, "flags": {"locked": False}
    }
    return [f"🔓 {label} unlocked."]


# ── Public wrappers for GM screen ─────────────────────────────────────────────

def find_location(world_slug: str, destination: str) -> dict | None:
    return _find_location(world_slug, destination)


def transition_to_known_location(session: dict, loc_data: dict) -> list[str]:
    return _transition_to_known_location(session, loc_data)


def transition_to_unknown_location(session: dict, destination: str) -> list[str]:
    return _transition_to_unknown_location(session, destination)
