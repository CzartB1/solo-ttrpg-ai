"""
retrieval.py
Selects and assembles the world bible context block for each LLM call.
Four layers: scene anchor, explicit mention, relational pull, recency buffer.
"""

from world_bible import (
    load_master, load_entity, load_index,
    compress_master, compress_entity, world_path
)

# ── Token budget (approximate — 1 token ≈ 4 chars) ───────────────────────────
BUDGET_CHARS = 6000   # ~1500 tokens for world context


# ── Main retrieval function ───────────────────────────────────────────────────

def retrieve_context(world_slug: str, state: dict,
                     player_input: str, history: list) -> str:
    """
    Assemble world context for this turn.
    Returns a formatted string ready for LLM injection.
    """
    if not world_slug:
        return ""

    index   = load_index(world_slug)
    loaded  = {}   # id → compressed string

    # ── Layer 1: Always loaded ────────────────────────────────────────────────
    master_block = compress_master(world_slug)

    loc_id = state.get("scene", {}).get("location_id", "")
    if loc_id:
        loc_data = load_entity(world_slug, "locations", loc_id)
        if loc_data:
            loaded[loc_id] = compress_entity(loc_data)
            # Load all currently_present characters
            present = loc_data.get("inhabitants", {}).get("currently_present", [])
            for cid in present:
                if cid not in loaded:
                    _load_entity_by_id(world_slug, cid, index, loaded, full=True)

    # ── Layer 2: Explicit mention in player input ─────────────────────────────
    for entry in index:
        names = [entry["name"]] + entry.get("aliases", [])
        if any(n.lower() in player_input.lower() for n in names):
            eid = entry["id"]
            if eid not in loaded:
                _load_entity_by_id(world_slug, eid, index, loaded, full=True)

    # ── Layer 3: Relational pull (one degree, summaries) ─────────────────────
    direct_ids = list(loaded.keys())
    for eid in direct_ids:
        entry = _find_index_entry(eid, index)
        if not entry:
            continue
        col  = entry.get("collection", "")
        data = load_entity(world_slug, col, eid)
        for related_id in _get_relations(data):
            if related_id not in loaded:
                _load_entity_by_id(world_slug, related_id, index, loaded, full=False)

    # ── Layer 4: Recency buffer ───────────────────────────────────────────────
    for eid in _recent_entity_ids(history, n=4):
        if eid not in loaded:
            _load_entity_by_id(world_slug, eid, index, loaded, full=False)

    # ── Apply budget ──────────────────────────────────────────────────────────
    context_blocks = _apply_budget(loaded, master_block)

    if not context_blocks and not master_block:
        return ""

    return (
        "=== WORLD BIBLE ===\n"
        + master_block + "\n\n"
        + "\n\n".join(context_blocks)
        + "\n=== END WORLD BIBLE ==="
    )


# ── Entity loading helpers ────────────────────────────────────────────────────

def _load_entity_by_id(world_slug: str, entity_id: str,
                        index: list, loaded: dict, full: bool):
    entry = _find_index_entry(entity_id, index)
    if not entry:
        return
    col  = entry.get("collection", "")
    data = load_entity(world_slug, col, entity_id)
    if data:
        loaded[entity_id] = compress_entity(data)


def _find_index_entry(entity_id: str, index: list) -> dict | None:
    for entry in index:
        if entry["id"] == entity_id:
            return entry
    return None


def _get_relations(data: dict) -> list[str]:
    """Extract all cross-referenced entity ids from a record."""
    refs = []
    # affiliation
    for a in data.get("affiliation", []):
        refs.append(a)
    # location inhabitants
    inh = data.get("inhabitants", {})
    refs.extend(inh.get("currently_present", []))
    refs.extend(inh.get("permanent", []))
    # faction relations
    for k in data.get("relations", {}).keys():
        refs.append(k)
    # character relationships
    for k in data.get("personality", {}).get("relationships", {}).keys():
        refs.append(k)
    return [r for r in refs if r]


def _recent_entity_ids(history: list, n: int = 4) -> list[str]:
    ids = []
    for turn in history[-n:]:
        ids.extend(turn.get("entities_referenced", []))
    return list(set(ids))


def _apply_budget(loaded: dict, master_block: str) -> list[str]:
    """
    Trim entity blocks to fit within BUDGET_CHARS.
    Priority: named > minor > archetype/basic.
    """
    used  = len(master_block)
    blocks = []

    # Sort by importance: named first
    priority = {"named": 0, "advanced": 0, "minor": 1, "basic": 2,
                "archetype": 3, "": 4}
    items = sorted(loaded.items(),
                   key=lambda kv: priority.get(
                       kv[1].split("\n")[0].split("(")[0].strip().lower(), 4))

    for eid, block in items:
        if used + len(block) + 2 > BUDGET_CHARS:
            break
        blocks.append(block)
        used += len(block) + 2

    return blocks


# ── Tag entity ids to history turns ──────────────────────────────────────────

def tag_referenced_entities(player_input: str, index: list) -> list[str]:
    """
    Returns a list of entity ids mentioned in the player's input.
    Called by app.py when recording history turns.
    """
    referenced = []
    for entry in index:
        names = [entry["name"]] + entry.get("aliases", [])
        if any(n.lower() in player_input.lower() for n in names):
            referenced.append(entry["id"])
    return referenced
