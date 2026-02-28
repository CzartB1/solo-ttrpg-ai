"""
author_assist.py
AI-powered authoring assistant for the world bible.
Fills empty fields in entity forms based on existing content + world context.
"""

import json
from world_bible import compress_master, load_master
from llm_interface import _call_ollama, _call_openrouter

# ── Field definitions per entity type ────────────────────────────────────────
# Maps entity type → list of (dot-notation path, human label)

FILLABLE_FIELDS = {
    "character_named": [
        ("physicality.appearance",        "Physical appearance"),
        ("physicality.distinguishing",     "Distinguishing feature"),
        ("physicality.dress",              "How they dress"),
        ("physicality.movement",           "How they move"),
        ("physicality.voice",              "Voice / speech pattern"),
        ("personality.demeanor",           "Demeanor"),
        ("personality.values",             "Values"),
        ("personality.flaw",               "Flaw / vice"),
        ("personality.at_their_best",      "At their best"),
        ("personality.at_their_worst",     "At their worst"),
        ("psyche.core_fear",               "Core fear"),
        ("psyche.core_desire",             "Core desire"),
        ("psyche.wound",                   "Wound / backstory"),
        ("psyche.coping_mechanism",        "Coping mechanism"),
        ("psyche.under_pressure",          "Under pressure they become…"),
        ("psyche.breaking_point",          "Breaking point"),
    ],
    "character_minor": [
        ("role",   "Role in the world"),
        ("notes",  "Notes"),
    ],
    "character_archetype": [
        ("notes",  "Notes (behavior, appearance, how they respond)"),
    ],
    "location_named": [
        ("appearance.visual",       "Visual appearance"),
        ("appearance.atmosphere",   "Atmosphere (smell, sound, feel)"),
        ("appearance.lighting",     "Lighting"),
        ("appearance.sound",        "Ambient sound"),
        ("purpose.function",        "Function / what it is"),
        ("purpose.narrative_role",  "Narrative role in the story"),
        ("history.relevant_past",   "Relevant history"),
        ("history.echoes",          "What still echoes from the past"),
        ("reputation.general",      "General reputation"),
    ],
    "location_minor": [
        ("notes", "Notes"),
    ],
    "faction_named": [
        ("goal.public",                    "Public goal"),
        ("goal.true",                      "True goal"),
        ("method.primary",                 "Primary method"),
        ("method.secondary",               "Secondary method"),
        ("method.will_not_do",             "What they will not do"),
        ("reputation.general",             "General reputation"),
        ("resources.has",                  "Resources they have"),
        ("resources.lacks",                "Resources they lack"),
        ("structure.leadership",           "Leadership"),
        ("structure.internal_conflict",    "Internal conflict or tension"),
        ("reach",                          "Geographic reach"),
        ("membership_feel",                "What kind of people join"),
    ],
    "faction_minor": [
        ("notes", "Notes"),
    ],
    "item_advanced": [
        ("appearance.visual",              "Visual appearance"),
        ("appearance.feel",                "How it feels"),
        ("purpose.original",               "Original purpose"),
        ("purpose.current_use",            "Current use"),
        ("inner_workings.core_function",   "Core function"),
        ("inner_workings.limitations",     "Limitations"),
        ("inner_workings.cost",            "Cost or side effects"),
        ("reputation.general",             "General reputation"),
        ("origin",                         "Origin"),
        ("quirk",                          "Quirk"),
        ("attunement",                     "Attunement or requirements"),
    ],
    "item_basic": [
        ("appearance", "Appearance"),
        ("note",       "Note"),
    ],
    "concept": [
        ("notes_for_ai",  "Explanation for the AI"),
        ("world_impact",  "How it affects the world and people"),
    ],
}


# ── Main generation call ──────────────────────────────────────────────────────

def generate_fields(world_slug: str, collection: str, entity_data: dict,
                    fields_to_fill: list[str], history: list,
                    model: str, backend: str, api_key: str) -> dict:
    """
    Generate content for specified empty fields.
    Returns a dict of {field_path: generated_value}.
    fields_to_fill: list of dot-notation paths e.g. ["physicality.appearance"]
    """
    if not fields_to_fill:
        return {}

    world_context  = compress_master(world_slug)
    history_snippet = _format_history(history)
    existing        = _format_existing(entity_data)
    field_list      = "\n".join(f"- {f}" for f in fields_to_fill)

    prompt = f"""You are helping a player author an NPC/entity for a solo TTRPG world bible.
Your job is to fill in missing fields in a way that is:
- Consistent with what the player has already written
- Appropriate for the world's genre, tone, and truths
- Specific and vivid, not generic
- 1-3 sentences per field maximum

{world_context}

RECENT SESSION CONTEXT:
{history_snippet if history_snippet else "(none yet)"}

EXISTING ENTRY (authored by player):
{existing}

COLLECTION: {collection}

Fill in ONLY the following empty fields.
Return a valid JSON object where keys are the exact field paths listed below
and values are your generated strings.
Do not include markdown, code fences, or explanation — only the JSON object.

FIELDS TO FILL:
{field_list}

JSON:"""

    raw = _call_llm(prompt, model, backend, api_key, max_tokens=600, temperature=0.75)
    return _parse_json_response(raw)


def suggest_truths(world_slug: str, genre: str, tone: str, setting: str,
                   existing_truths: list,
                   model: str, backend: str, api_key: str) -> list[str]:
    """
    Suggest additional world truths based on genre, tone, and existing truths.
    Returns a list of suggested truth strings.
    """
    existing_block = "\n".join(f"- {t}" for t in existing_truths if t.strip())

    prompt = f"""You are helping a player build a solo TTRPG world.
Generate 5 interesting world truths — declarative facts that define this world.
Each truth should be specific, surprising, or evocative.
Keep each truth to one sentence.

GENRE: {genre}
TONE: {tone}
SETTING: {setting}
EXISTING TRUTHS:
{existing_block if existing_block else "(none yet)"}

Do not repeat existing truths.
Return a JSON array of 5 strings.
Do not include markdown, code fences, or explanation — only the JSON array.

JSON:"""

    raw  = _call_llm(prompt, model, backend, api_key, max_tokens=400, temperature=0.85)
    data = _parse_json_response(raw)
    if isinstance(data, list):
        return [str(t) for t in data]
    return []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _call_llm(prompt: str, model: str, backend: str, api_key: str,
              max_tokens: int = 400, temperature: float = 0.75) -> str:
    if backend == "openrouter":
        return _call_openrouter(prompt, model, api_key,
                                max_tokens=max_tokens, temperature=temperature)
    return _call_ollama(prompt, model,
                        max_tokens=max_tokens, temperature=temperature)


def _format_existing(data: dict) -> str:
    """Flatten entity dict to readable key: value lines, skipping empty values."""
    lines = []
    def _flatten(obj, prefix=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                _flatten(v, f"{prefix}.{k}" if prefix else k)
        elif isinstance(obj, list):
            if obj:
                lines.append(f"{prefix}: {', '.join(str(i) for i in obj)}")
        elif obj and str(obj).strip():
            lines.append(f"{prefix}: {obj}")
    _flatten(data)
    return "\n".join(lines) if lines else "(empty)"


def _format_history(history: list, n: int = 3) -> str:
    recent = history[-n:] if history else []
    return "\n".join(
        f"Turn {h['turn']}: {h.get('narration','')[:100]}"
        for h in recent
    )


def _parse_json_response(raw: str) -> dict | list:
    """Safely parse JSON from LLM output, stripping any markdown wrapping."""
    raw = raw.strip()
    # Strip code fences if present
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.split("\n")[:-1])
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to find the first { or [ and parse from there
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            start = raw.find(start_char)
            end   = raw.rfind(end_char)
            if start != -1 and end != -1:
                try:
                    return json.loads(raw[start:end+1])
                except Exception:
                    pass
        return {}


def get_fillable_fields(collection: str, entity_data: dict) -> list[str]:
    """
    Return list of field paths that are currently empty and can be generated.
    """
    importance = entity_data.get("importance",
                  entity_data.get("tier", "minor"))
    tier       = entity_data.get("tier", "")
    rec_type   = entity_data.get("record_type", "")

    # Determine which field template to use
    if collection == "characters":
        if importance == "named":
            key = "character_named"
        elif rec_type == "archetype":
            key = "character_archetype"
        else:
            key = "character_minor"
    elif collection == "locations":
        key = "location_named" if importance == "named" else "location_minor"
    elif collection == "factions":
        key = "faction_named" if importance == "named" else "faction_minor"
    elif collection == "items":
        key = "item_advanced" if tier == "advanced" else "item_basic"
    elif collection == "concepts":
        key = "concept"
    else:
        return []

    all_fields = FILLABLE_FIELDS.get(key, [])
    empty = []
    for path, _ in all_fields:
        if _get_nested(entity_data, path) in (None, "", [], {}):
            empty.append(path)
    return empty


def _get_nested(data: dict, path: str):
    """Get a value from a nested dict using dot notation."""
    keys = path.split(".")
    cur  = data
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def set_nested(data: dict, path: str, value):
    """Set a value in a nested dict using dot notation, creating keys as needed."""
    keys  = path.split(".")
    cur   = data
    for k in keys[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[keys[-1]] = value
