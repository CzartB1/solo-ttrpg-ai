"""
opening.py
Generates the opening scene for a new session from world context.
Also handles ghost extraction from narrator output.
"""

import json
import re
from world_bible import compress_master, load_master, load_index, load_entity
from llm_interface import _call_ollama, _call_openrouter


# ── Opening scene generation ──────────────────────────────────────────────────

def generate_opening(world_slug: str, player_name: str, player_bio: str,
                     starting_location_id: str,
                     model: str, backend: str, api_key: str) -> dict:
    """
    Generate an opening scene for a new session.
    Returns a dict with scene data and opening narration.
    Falls back to a generic opening on failure.
    """
    world_context = compress_master(world_slug)
    master        = load_master(world_slug)

    # Load starting location if specified
    location_hint = ""
    if starting_location_id:
        loc = load_entity(world_slug, "locations", starting_location_id)
        if loc:
            location_hint = (
                f"Starting location: {loc.get('name','')}\n"
                f"{loc.get('appearance',{}).get('atmosphere','')}"
            )

    prompt = f"""You are starting a solo TTRPG session. Generate an opening scene.

{world_context}

PLAYER CHARACTER:
Name: {player_name}
Background: {player_bio if player_bio else "A traveller with a past."}

{location_hint if location_hint else "Choose an appropriate starting location for this world and character."}

Generate a vivid atmospheric opening. Do NOT introduce a plot hook yet — just establish where the player is, what they sense, and the immediate situation.

Return ONLY a valid JSON object with these exact keys:
{{
  "location_id": "slug-of-location or empty string if invented",
  "location_name": "Name of the location",
  "description": "2-3 sentence atmospheric description of the immediate scene",
  "atmosphere": "sensory details — smell, sound, light, temperature",
  "npcs_present": ["npc-id-1", "npc-id-2"],
  "opening_narration": "3-5 sentence opening narration in second person present tense"
}}

If inventing a new location not in the world bible, leave location_id as empty string.
Keep npcs_present as ids from the world bible if known, otherwise use descriptive strings."""

    raw = _call_llm(prompt, model, backend, api_key, max_tokens=500, temperature=0.85)
    data = _parse_json(raw)

    if not data or "opening_narration" not in data:
        return _fallback_opening(world_slug, player_name)

    return data


def _fallback_opening(world_slug: str, player_name: str) -> dict:
    master = load_master(world_slug)
    title  = master.get("title", "an unknown world")
    genre  = master.get("genre", {}).get("primary", "world")
    return {
        "location_id":      "",
        "location_name":    "An Unknown Place",
        "description":      f"You find yourself at the edge of {title}. The {genre} stretches out before you.",
        "atmosphere":       "The air is still. Something feels like it's about to begin.",
        "npcs_present":     [],
        "opening_narration": (
            f"You are {player_name}, and your story begins here. "
            f"The world of {title} surrounds you, full of possibility and danger. "
            "Take a moment to get your bearings."
        ),
    }


# ── Ghost extraction ──────────────────────────────────────────────────────────

GHOST_START = "---GHOSTS---"
GHOST_END   = "---END---"

def extract_ghosts(raw_narration: str) -> tuple[str, list[dict]]:
    """
    Split narrator output into (clean_narration, ghost_list).
    Ghost block format:
        ---GHOSTS---
        [{"name":"...","type":"...","context":"..."}]
        ---END---
    Returns clean narration with ghost block stripped, and list of ghost dicts.
    """
    if GHOST_START not in raw_narration:
        return raw_narration.strip(), []

    parts = raw_narration.split(GHOST_START, 1)
    clean = parts[0].strip()

    ghost_block = parts[1]
    if GHOST_END in ghost_block:
        ghost_block = ghost_block.split(GHOST_END, 1)[0]

    ghosts = _parse_json(ghost_block.strip())
    if not isinstance(ghosts, list):
        ghosts = []

    valid = []
    for g in ghosts:
        if isinstance(g, dict) and g.get("name") and g.get("type"):
            valid.append({
                "name":    g["name"],
                "type":    g.get("type", "character"),
                "context": g.get("context", ""),
            })

    return clean, valid


def build_narrator_prompt_suffix() -> str:
    """
    Suffix appended to narrator prompt instructing ghost extraction format.
    Only used when world is loaded.
    """
    return """
After your narration, if you introduced any NEW named entities (characters, locations, factions, items, or concepts that don't already exist in the world bible), append a ghost block in this exact format:

---GHOSTS---
[{"name":"Entity Name","type":"character|location|faction|item|concept","context":"1-2 sentence description of what this entity is"}]
---END---

Only include genuinely NEW named entities. If you introduced nothing new, omit the ghost block entirely."""


# ── Internal helpers ──────────────────────────────────────────────────────────

def _call_llm(prompt, model, backend, api_key, max_tokens=400, temperature=0.8):
    if backend == "openrouter":
        return _call_openrouter(prompt, model, api_key,
                                max_tokens=max_tokens, temperature=temperature)
    return _call_ollama(prompt, model,
                        max_tokens=max_tokens, temperature=temperature)


def _parse_json(raw: str):
    raw = raw.strip()
    for fence in ["```json", "```"]:
        if raw.startswith(fence):
            raw = raw[len(fence):]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        for start, end in [('{', '}'), ('[', ']')]:
            s = raw.find(start)
            e = raw.rfind(end)
            if s != -1 and e != -1:
                try:
                    return json.loads(raw[s:e+1])
                except Exception:
                    pass
    return None
