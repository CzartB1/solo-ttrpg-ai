"""
world_bible.py
Manages world folders, master files, entity records, and the index.
All world data lives under: worlds/<world-slug>/
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

WORLDS_DIR = Path("worlds")
COLLECTIONS = ["characters", "locations", "factions", "items", "concepts"]


# ── Slug / path helpers ───────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text


def world_path(world_slug: str) -> Path:
    return WORLDS_DIR / world_slug


def entity_path(world_slug: str, collection: str, entity_id: str) -> Path:
    return world_path(world_slug) / collection / f"{entity_id}.json"


def master_path(world_slug: str) -> Path:
    return world_path(world_slug) / "master.json"


def index_path(world_slug: str) -> Path:
    return world_path(world_slug) / "index.json"


# ── World CRUD ────────────────────────────────────────────────────────────────

def list_worlds() -> list[dict]:
    """Return a list of world summary dicts for the world selector UI."""
    if not WORLDS_DIR.exists():
        return []
    worlds = []
    for d in sorted(WORLDS_DIR.iterdir()):
        if d.is_dir() and (d / "master.json").exists():
            master = load_master(d.name)
            worlds.append({
                "slug":          d.name,
                "title":         master.get("title", d.name),
                "genre":         master.get("genre", {}).get("primary", ""),
                "last_modified": master.get("last_modified", ""),
            })
    return worlds


def create_world(title: str, genre_primary: str, genre_tags: list,
                 tone: str, setting: str, truths: list,
                 narrator_voice: str) -> str:
    """
    Scaffold a new world folder and write master.json.
    Returns the world slug.
    """
    slug = _unique_slug(slugify(title))
    wp   = world_path(slug)
    wp.mkdir(parents=True, exist_ok=True)
    for col in COLLECTIONS:
        (wp / col).mkdir(exist_ok=True)

    master = {
        "title":    title,
        "genre":    {"primary": genre_primary, "tags": [t for t in genre_tags if t]},
        "tone":     tone,
        "setting":  setting,
        "truths":   [t for t in truths if t.strip()],
        "narrator_voice": narrator_voice,
        "created":       datetime.now().isoformat(),
        "last_modified": datetime.now().isoformat(),
    }
    _write_json(master_path(slug), master)
    rebuild_index(slug)
    return slug


def save_master(world_slug: str, data: dict):
    data["last_modified"] = datetime.now().isoformat()
    _write_json(master_path(world_slug), data)
    rebuild_index(world_slug)


def load_master(world_slug: str) -> dict:
    p = master_path(world_slug)
    if not p.exists():
        return {}
    return _read_json(p)


def delete_world(world_slug: str):
    import shutil
    wp = world_path(world_slug)
    if wp.exists():
        shutil.rmtree(wp)


# ── Entity CRUD ───────────────────────────────────────────────────────────────

def list_entities(world_slug: str, collection: str) -> list[dict]:
    """Return lightweight summaries of all entities in a collection."""
    col_path = world_path(world_slug) / collection
    if not col_path.exists():
        return []
    entities = []
    for f in sorted(col_path.glob("*.json")):
        data = _read_json(f)
        entities.append({
            "id":         data.get("id", f.stem),
            "name":       data.get("name", f.stem),
            "importance": data.get("importance", data.get("tier", "—")),
            "record_type":data.get("record_type", collection[:-1]),
            "status":     _entity_status(data),
        })
    return entities


def load_entity(world_slug: str, collection: str, entity_id: str) -> dict:
    p = entity_path(world_slug, collection, entity_id)
    if not p.exists():
        return {}
    return _read_json(p)


def save_entity(world_slug: str, collection: str, data: dict) -> str:
    """Save entity data. Returns the entity id."""
    if not data.get("id"):
        data["id"] = slugify(data.get("name", "unnamed"))
    data["id"] = _unique_entity_slug(world_slug, collection, data["id"])
    _write_json(entity_path(world_slug, collection, data["id"]), data)
    rebuild_index(world_slug)
    return data["id"]


def delete_entity(world_slug: str, collection: str, entity_id: str):
    p = entity_path(world_slug, collection, entity_id)
    if p.exists():
        p.unlink()
    rebuild_index(world_slug)


def duplicate_entity(world_slug: str, collection: str, entity_id: str) -> str:
    data = load_entity(world_slug, collection, entity_id)
    data["id"]   = data["id"] + "-copy"
    data["name"] = data["name"] + " (Copy)"
    return save_entity(world_slug, collection, data)


# ── Index ─────────────────────────────────────────────────────────────────────

def rebuild_index(world_slug: str):
    """Scan all entity files and write a lightweight index.json."""
    entries = []
    for col in COLLECTIONS:
        col_path = world_path(world_slug) / col
        if not col_path.exists():
            continue
        for f in col_path.glob("*.json"):
            data = _read_json(f)
            entries.append({
                "id":          data.get("id", f.stem),
                "name":        data.get("name", f.stem),
                "aliases":     data.get("aliases", []),
                "record_type": data.get("record_type", col[:-1]),
                "importance":  data.get("importance", data.get("tier", "")),
                "collection":  col,
                "file":        str(f.relative_to(world_path(world_slug))),
                "tags":        _extract_tags(data),
            })
    _write_json(index_path(world_slug), {"entities": entries})


def load_index(world_slug: str) -> list[dict]:
    p = index_path(world_slug)
    if not p.exists():
        rebuild_index(world_slug)
    return _read_json(p).get("entities", [])


# ── Compression for LLM injection ────────────────────────────────────────────

def compress_master(world_slug: str) -> str:
    """Format master file for LLM context injection."""
    m = load_master(world_slug)
    if not m:
        return ""
    genre = m.get("genre", {})
    genre_str = genre.get("primary", "")
    if genre.get("tags"):
        genre_str += " / " + " / ".join(genre["tags"])
    truths = "\n".join(f"- {t}" for t in m.get("truths", []))
    return (
        f"WORLD: {m.get('title','')} ({genre_str})\n"
        f"TONE: {m.get('tone','')}\n"
        f"SETTING: {m.get('setting','')}\n"
        f"TRUTHS:\n{truths}\n"
        f"NARRATOR VOICE: {m.get('narrator_voice','')}"
    ).strip()


def compress_entity(data: dict) -> str:
    """
    Compress a full entity record to a short context block.
    Secrets are always stripped.
    """
    rtype = data.get("record_type", "")
    name  = data.get("name", "Unknown")
    role  = data.get("role", data.get("type", ""))

    lines = [f"{rtype.upper()}: {name}" + (f" ({role})" if role else "")]

    if rtype == "character":
        imp = data.get("importance", "minor")
        if imp == "named":
            p = data.get("physicality", {})
            if p.get("appearance"): lines.append(f"Appearance: {_trim(p['appearance'])}")
            if p.get("movement"):   lines.append(f"Movement: {_trim(p['movement'])}")
            if p.get("voice"):      lines.append(f"Voice: {_trim(p['voice'])}")
            per = data.get("personality", {})
            if per.get("demeanor"): lines.append(f"Demeanor: {_trim(per['demeanor'])}")
            if per.get("flaw"):     lines.append(f"Flaw: {_trim(per['flaw'])}")
            psy = data.get("psyche", {})
            if psy.get("core_fear"):     lines.append(f"Fears: {_trim(psy['core_fear'])}")
            if psy.get("under_pressure"):lines.append(f"Under pressure: {_trim(psy['under_pressure'])}")
        disp = data.get("disposition_to_player", "")
        if disp: lines.append(f"Disposition: {disp}")
        if data.get("notes"): lines.append(f"Note: {_trim(data['notes'])}")

    elif rtype == "location":
        app = data.get("appearance", {})
        if app.get("atmosphere"): lines.append(f"Atmosphere: {_trim(app['atmosphere'])}")
        inh = data.get("inhabitants", {})
        present = inh.get("currently_present", [])
        if present: lines.append(f"Present: {', '.join(present)}")
        rep = data.get("reputation", {})
        if rep.get("general"): lines.append(f"Reputation: {_trim(rep['general'])}")
        state = data.get("state", "")
        if state: lines.append(f"State: {state}")
        if data.get("notes"): lines.append(f"Note: {_trim(data['notes'])}")

    elif rtype == "faction":
        goal = data.get("goal", {})
        if goal.get("true"): lines.append(f"True goal: {_trim(goal['true'])}")
        method = data.get("method", {})
        if method.get("primary"): lines.append(f"Method: {_trim(method['primary'])}")
        disp = data.get("disposition_to_player", "")
        if disp: lines.append(f"Player standing: {disp}")
        struct = data.get("structure", {})
        if struct.get("internal_conflict"):
            lines.append(f"Tension: {_trim(struct['internal_conflict'])}")
        if data.get("notes"): lines.append(f"Note: {_trim(data['notes'])}")

    elif rtype == "item":
        tier = data.get("tier", "basic")
        app  = data.get("appearance", data.get("appearance", ""))
        if isinstance(app, dict):
            app = app.get("visual", "")
        if app: lines.append(f"Appearance: {_trim(app)}")
        if tier == "advanced":
            iw = data.get("inner_workings", {})
            if iw.get("core_function"): lines.append(f"Function: {_trim(iw['core_function'])}")
            if iw.get("limitations"):   lines.append(f"Limits: {_trim(iw['limitations'])}")
            if data.get("quirk"):       lines.append(f"Quirk: {_trim(data['quirk'])}")
        state = data.get("state", "")
        if state: lines.append(f"State: {state}")

    elif rtype == "concept":
        if data.get("notes_for_ai"):   lines.append(_trim(data["notes_for_ai"], 200))
        if data.get("world_impact"):   lines.append(f"Impact: {_trim(data['world_impact'])}")

    else:
        if data.get("notes"): lines.append(_trim(data["notes"]))

    return "\n".join(lines)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _trim(text: str, max_chars: int = 120) -> str:
    text = text.strip().replace("\n", " ")
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "…"
    return text


def _entity_status(data: dict) -> str:
    return (data.get("status")
            or data.get("state")
            or data.get("typical_disposition")
            or "—")


def _extract_tags(data: dict) -> list[str]:
    tags = []
    aff = data.get("affiliation", [])
    if isinstance(aff, list):
        tags.extend(aff)
    role = data.get("role", "")
    if role:
        tags.extend(role.lower().split())
    return list(set(tags))


def _unique_slug(slug: str) -> str:
    if not (WORLDS_DIR / slug).exists():
        return slug
    i = 2
    while (WORLDS_DIR / f"{slug}-{i}").exists():
        i += 1
    return f"{slug}-{i}"


def _unique_entity_slug(world_slug: str, collection: str, slug: str) -> str:
    if not entity_path(world_slug, collection, slug).exists():
        return slug
    i = 2
    while entity_path(world_slug, collection, f"{slug}-{i}").exists():
        i += 1
    return f"{slug}-{i}"
