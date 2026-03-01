"""
session_state.py
Manages live session state separately from authored world bible records.
Authored records are never modified by gameplay.
Session state overlays live changes on top of them.

File: sessions/<world_slug>/<session_id>.json
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

SESSIONS_DIR = Path("sessions")


# ── Session structure ─────────────────────────────────────────────────────────

def new_session(world_slug: str, player_name: str,
                player_bio: str, starting_location_id: str = "") -> dict:
    return {
        "session_id":           str(uuid.uuid4())[:8],
        "world_slug":           world_slug,
        "created":              datetime.now().isoformat(),
        "last_saved":           datetime.now().isoformat(),

        # Player
        "player": {
            "name":       player_name,
            "bio":        player_bio,
            "hp":         20,
            "hp_max":     20,
            "stats": {
                "strength": 1, "dexterity": 1,
                "intelligence": 1, "charisma": 1, "perception": 1,
            },
            "skills": {
                "attack": 2, "stealth": 1, "persuasion": 1,
                "deception": 0, "perception": 1, "athletics": 1,
            },
            "inventory":  ["torch", "dagger", "ration"],
            "conditions": [],
            "gold":       10,
        },

        # Scene — live overlay on world bible location record
        "scene": {
            "location_id":       starting_location_id,
            "location_name":     "",
            "description":       "",      # overrides world bible on the fly
            "npcs_present":      [],      # list of entity ids currently here
            "npcs_live": {},              # {npc_id: {hp, disposition, status}}
            "objects": {},                # {object_id: {label, flags{}}}
            "notes":   [],                # append-only scene notes
            "ambient": "day",
        },

        # History
        "history":       [],
        "summary":       "",
        "turn":          0,

        # Ghost bar
        "ghosts":        [],              # list of ghost entry dicts

        # State snapshots for undo (last 3)
        "snapshots":     [],

        # Opening scene flag
        "opening_done":  False,
    }


# ── Snapshot for undo ─────────────────────────────────────────────────────────

def push_snapshot(session: dict):
    """Save a lightweight snapshot of mutable state before each turn."""
    import copy
    snap = {
        "turn":    session["turn"],
        "player":  copy.deepcopy(session["player"]),
        "scene":   copy.deepcopy(session["scene"]),
        "ghosts":  copy.deepcopy(session["ghosts"]),
    }
    session["snapshots"].append(snap)
    # Keep only last 3
    if len(session["snapshots"]) > 3:
        session["snapshots"] = session["snapshots"][-3:]


def pop_snapshot(session: dict) -> bool:
    """Restore the most recent snapshot. Returns True if successful."""
    if not session["snapshots"]:
        return False
    snap = session["snapshots"].pop()
    session["turn"]   = snap["turn"]
    session["player"] = snap["player"]
    session["scene"]  = snap["scene"]
    session["ghosts"] = snap["ghosts"]
    # Remove last history entry
    if session["history"]:
        session["history"].pop()
    return True


# ── Ghost bar ─────────────────────────────────────────────────────────────────

def add_ghost(session: dict, name: str, ghost_type: str, context: str):
    """Add a ghost entry. Deduplicates by name."""
    for g in session["ghosts"]:
        if g["name"].lower() == name.lower():
            # Update context if richer
            if len(context) > len(g["context"]):
                g["context"] = context
            return
    session["ghosts"].append({
        "id":           f"ghost-{len(session['ghosts'])+1:03d}",
        "name":         name,
        "type":         ghost_type,
        "context":      context,
        "invented_turn": session["turn"],
        "promoted":     False,
    })


def dismiss_ghost(session: dict, ghost_id: str):
    session["ghosts"] = [g for g in session["ghosts"]
                         if g["id"] != ghost_id]


def promote_ghost(session: dict, ghost_id: str) -> dict | None:
    """Mark ghost as promoted, return its data for the entity editor."""
    for g in session["ghosts"]:
        if g["id"] == ghost_id:
            g["promoted"] = True
            return g
    return None


def get_active_ghosts(session: dict) -> list[dict]:
    return [g for g in session["ghosts"] if not g["promoted"]]


# ── Scene helpers ─────────────────────────────────────────────────────────────

def append_scene_note(session: dict, note: str):
    session["scene"]["notes"].append({
        "turn": session["turn"],
        "text": note.strip(),
    })


def get_scene_notes(session: dict, n: int = 5) -> list[str]:
    return [n["text"] for n in session["scene"]["notes"][-n:]]


def set_object_flag(session: dict, object_id: str,
                    flag: str, value, label: str = ""):
    objs = session["scene"]["objects"]
    if object_id not in objs:
        objs[object_id] = {"label": label or object_id, "flags": {}}
    objs[object_id]["flags"][flag] = value


def get_npc_live(session: dict, npc_id: str) -> dict:
    """Get or create a live NPC state record."""
    live = session["scene"]["npcs_live"]
    if npc_id not in live:
        live[npc_id] = {"hp": 10, "disposition": "neutral", "status": "alive"}
    return live[npc_id]


# ── History ───────────────────────────────────────────────────────────────────

def add_history(session: dict, action: dict, result: dict,
                narration: str, entities_referenced: list = None):
    session["history"].append({
        "turn":               session["turn"],
        "action":             action,
        "result":             result,
        "narration":          narration,
        "entities_referenced": entities_referenced or [],
    })
    session["turn"] += 1
    if len(session["history"]) > 12:
        session["history"] = session["history"][-12:]


def get_recent_history(session: dict, n: int = 4) -> list:
    return session["history"][-n:]


# ── Save / Load ───────────────────────────────────────────────────────────────

def session_path(world_slug: str, session_id: str) -> Path:
    return SESSIONS_DIR / world_slug / f"{session_id}.json"


def save_session(session: dict):
    session["last_saved"] = datetime.now().isoformat()
    p = session_path(session["world_slug"], session["session_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2, ensure_ascii=False)


def load_session(world_slug: str, session_id: str) -> dict | None:
    p = session_path(world_slug, session_id)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def list_sessions(world_slug: str) -> list[dict]:
    d = SESSIONS_DIR / world_slug
    if not d.exists():
        return []
    sessions = []
    for f in sorted(d.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sessions.append({
                "session_id":  data["session_id"],
                "player_name": data["player"]["name"],
                "turn":        data["turn"],
                "last_saved":  data["last_saved"],
            })
        except Exception:
            pass
    return sessions


def latest_session(world_slug: str) -> dict | None:
    sessions = list_sessions(world_slug)
    if not sessions:
        return None
    return load_session(world_slug, sessions[0]["session_id"])


# ── State summary for LLM ─────────────────────────────────────────────────────

def session_summary(session: dict) -> str:
    p  = session["player"]
    sc = session["scene"]
    conditions = ", ".join(p["conditions"]) if p["conditions"] else "none"
    inventory  = ", ".join(p["inventory"])  if p["inventory"]  else "nothing"

    # Live NPCs
    npc_lines = []
    for npc_id in sc.get("npcs_present", []):
        live = sc["npcs_live"].get(npc_id, {})
        disp = live.get("disposition", "neutral")
        stat = live.get("status", "alive")
        npc_lines.append(f"{npc_id} ({disp}, {stat})")
    npcs_str = ", ".join(npc_lines) if npc_lines else "none"

    # Objects with flags
    obj_lines = []
    for oid, obj in sc.get("objects", {}).items():
        flags = obj.get("flags", {})
        flag_str = ", ".join(f"{k}={v}" for k, v in flags.items())
        obj_lines.append(f"{obj.get('label', oid)}" + (f" [{flag_str}]" if flags else ""))
    objs_str = ", ".join(obj_lines) if obj_lines else "none"

    # Scene notes
    notes = get_scene_notes(session, n=4)
    notes_str = ("\n  " + "\n  ".join(notes)) if notes else " none"

    # Recent history
    recent = get_recent_history(session, n=3)
    history_str = ""
    for h in recent:
        history_str += f"\n  Turn {h['turn']}: {h['narration'][:120]}…"

    return f"""=== GAME STATE ===
Location : {sc.get('location_name', 'Unknown')}
Scene    : {sc.get('description', '')}
NPCs     : {npcs_str}
Objects  : {objs_str}
Notes    :{notes_str}

Player   : {p['name']}  HP {p['hp']}/{p['hp_max']}
Bio      : {p.get('bio', '')}
Conditions: {conditions}
Inventory: {inventory}
Gold     : {p['gold']}

Recent history:{history_str if history_str else ' (none yet)'}
=== END STATE ===""".strip()
