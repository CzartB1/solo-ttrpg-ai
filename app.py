"""
app.py — Solo TTRPG Alpha
Run: python app.py → http://localhost:7861
"""

import json
import gradio as gr
from gradio import ChatMessage

from session_state import (
    new_session, save_session, load_session, latest_session,
    list_sessions, add_history, get_recent_history,
    push_snapshot, pop_snapshot,
    add_ghost, dismiss_ghost, promote_ghost, get_active_ghosts,
    append_scene_note, get_scene_notes, session_summary,
)
from opening import (
    generate_opening, extract_ghosts, build_narrator_prompt_suffix
)
from interpreter import interpret, VERB_TREE
from llm_interface import narrate, summarize_history
from world_bible import (
    list_worlds, create_world, load_master,
    list_entities, load_entity, save_entity, delete_entity,
    delete_world, COLLECTIONS, load_index
)
from retrieval import retrieve_context, tag_referenced_entities
from state_changes import apply_mechanical_changes


# ══════════════════════════════════════════════════════════════════════════════
# GLOBALS
# ══════════════════════════════════════════════════════════════════════════════

_session: dict = {}          # active session state
_last_mechanical: dict = {}  # last mechanical result (for retry/continue)
_last_player_input: dict = {}
_last_state_changes: list = []


def _has_session() -> bool:
    return bool(_session)


def _world_slug() -> str:
    return _session.get("world_slug", "") if _session else ""


# ══════════════════════════════════════════════════════════════════════════════
# STATUS / DISPLAY HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def get_status() -> str:
    if not _session:
        return "*No active session. Start a new game.*"
    p  = _session["player"]
    sc = _session["scene"]
    cond = ", ".join(p["conditions"]) if p["conditions"] else "none"
    return (
        f"**{p['name']}** | ❤️ {p['hp']}/{p['hp_max']} HP | "
        f"💰 {p['gold']} gold | 🩹 {cond}\n\n"
        f"📍 **{sc.get('location_name','?')}**\n{sc.get('description','')}"
    )


def get_inventory() -> list[str]:
    if not _session:
        return []
    return _session["player"]["inventory"]


def get_ghost_md() -> str:
    if not _session:
        return "*No active session.*"
    ghosts = get_active_ghosts(_session)
    if not ghosts:
        return "*No ghost entries yet.*"
    lines = []
    for g in ghosts:
        icon = {"character":"👤","location":"📍","faction":"⚔️",
                "item":"🎒","concept":"💡"}.get(g["type"], "👻")
        lines.append(
            f"**{icon} {g['name']}** `{g['type']}` *(turn {g['invented_turn']})*\n"
            f"> {g['context'][:120]}{'…' if len(g['context'])>120 else ''}\n"
            f"`id: {g['id']}`"
        )
    return "\n\n---\n\n".join(lines)


def get_world_options() -> list[str]:
    return [f"{w['slug']} — {w['title']}" for w in list_worlds()]


def _mechanic_display(result: dict) -> str:
    if not result.get("mechanical"):
        return ""
    rtype = result.get("type", "")
    if rtype == "attack":
        if result["hit"]:
            return (f"🎲 `Hit! {result['hit_roll']}+{result['hit_bonus']}"
                    f"={result['hit_total']} vs {result['difficulty']} "
                    f"| 💥 {result['damage']} dmg`")
        return (f"🎲 `Miss. {result['hit_roll']}+{result['hit_bonus']}"
                f"={result['hit_total']} vs {result['difficulty']}`")
    if rtype in ("skill_check","stealth_check","persuasion_check"):
        icon = "✅" if result["success"] else "❌"
        return (f"🎲 `{icon} {result['degree'].title()} | "
                f"{result['rolled']}+{result['bonus']}={result['total']} "
                f"vs {result['difficulty']}`")
    if rtype == "use_item":
        return f"🎒 `{result['item']}: {result.get('effect','')}`"
    if rtype == "examine":
        icon = "✅" if result["success"] else "❌"
        return (f"🔍 `{icon} {result['degree'].title()} | "
                f"{result['rolled']}+{result['bonus']}={result['total']} "
                f"vs {result['difficulty']}`")
    if rtype == "rest":
        return f"💤 `Rested. +{result['hp_gain']} HP`"
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# NEW GAME FLOW
# ══════════════════════════════════════════════════════════════════════════════

def do_start_new_game(world_choice, player_name, player_bio,
                      start_loc_id, model_name, backend, api_key):
    global _session

    if not world_choice:
        return (gr.update(), gr.update(),
                "❌ Select a world first.",
                gr.update(), gr.update(), gr.update())

    slug = world_choice.split(" — ")[0].strip()
    master = load_master(slug)
    if not master:
        return (gr.update(), gr.update(),
                f"❌ World '{slug}' not found.",
                gr.update(), gr.update(), gr.update())

    name = player_name.strip() or "Traveller"
    bio  = player_bio.strip()
    loc  = start_loc_id.strip()

    model_str = model_name.strip() or (
        "tinyllama" if backend == "ollama"
        else "mistralai/mistral-7b-instruct:free"
    )

    # Create session
    _session = new_session(slug, name, bio, loc)

    # Generate opening scene
    opening = generate_opening(
        slug, name, bio, loc, model_str, backend, api_key
    )

    # Populate session scene from opening
    sc = _session["scene"]
    sc["location_id"]   = opening.get("location_id", "")
    sc["location_name"] = opening.get("location_name", "An Unknown Place")
    sc["description"]   = opening.get("description", "")
    sc["npcs_present"]  = opening.get("npcs_present", [])

    narration = opening.get("opening_narration", "Your adventure begins.")

    # Ghost any invented entities from opening
    for npc in sc["npcs_present"]:
        if npc and not _entity_in_bible(slug, npc):
            add_ghost(_session, npc, "character",
                      f"Present at {sc['location_name']} when the adventure began.")

    _session["opening_done"] = True

    chat = [ChatMessage(role="assistant", content=narration)]

    return (
        chat,
        get_status(),
        f"✅ Session started in **{master.get('title',slug)}**.",
        gr.update(choices=get_inventory()),
        get_ghost_md(),
        gr.update(visible=False),   # hide new game panel
    )


def do_regenerate_opening(world_choice, player_name, player_bio,
                           start_loc_id, model_name, backend, api_key):
    """Re-run opening generation with same params."""
    global _session
    if not _session:
        return gr.update(), get_status(), "❌ No active session."

    slug = _session["world_slug"]
    name = _session["player"]["name"]
    bio  = _session["player"].get("bio", "")
    loc  = _session["scene"].get("location_id", "")
    model_str = model_name.strip() or "tinyllama"

    opening = generate_opening(slug, name, bio, loc, model_str, backend, api_key)
    sc = _session["scene"]
    sc["location_id"]   = opening.get("location_id", "")
    sc["location_name"] = opening.get("location_name", "An Unknown Place")
    sc["description"]   = opening.get("description", "")
    sc["npcs_present"]  = opening.get("npcs_present", [])
    _session["history"] = []
    _session["turn"]    = 0

    narration = opening.get("opening_narration", "Your adventure begins anew.")
    chat = [ChatMessage(role="assistant", content=narration)]
    return chat, get_status(), "✅ Opening regenerated."


def _entity_in_bible(world_slug: str, entity_id: str) -> bool:
    index = load_index(world_slug)
    return any(e["id"] == entity_id for e in index)


# ══════════════════════════════════════════════════════════════════════════════
# CORE ACTION PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def _run_action(verb: str, subject: str, modifiers: str, items: list,
                backend: str, model_name: str, api_key: str,
                temperature: float, tone_hint: str, length_hint: str,
                history: list, bypass: bool = False):
    global _session, _last_mechanical, _last_player_input

    if not _session:
        return history, get_status(), gr.update(), get_ghost_md()

    # Snapshot for undo
    push_snapshot(_session)

    player_input = {
        "verb":      verb,
        "subject":   subject.strip(),
        "modifiers": modifiers.strip(),
        "items":     items or [],
    }
    _last_player_input = player_input

    mechanical_result = (
        {"type": "narrative", "mechanical": False}
        if bypass
        else interpret(_session, verb=verb, subject=subject.strip(),
                       modifiers=modifiers.strip(), items=items or [])
    )
    _last_mechanical = mechanical_result
    mech_display = _mechanic_display(mechanical_result)

    # Apply mechanical state changes BEFORE narrator call
    state_change_log = apply_mechanical_changes(
        _session, mechanical_result, _world_slug()
    )
    _last_state_changes = state_change_log

    model_str = model_name.strip() or (
        "tinyllama" if backend == "ollama"
        else "mistralai/mistral-7b-instruct:free"
    )

    # World context
    full_input = f"{verb} {subject} {modifiers}".strip()
    world_ctx  = retrieve_context(
        _world_slug(), _session, full_input,
        _session.get("history", [])
    )

    # Ghost suffix (only if world loaded)
    ghost_sfx = ("\n\n" + build_narrator_prompt_suffix()) if _world_slug() else ""

    # Tone/length nudge
    nudge = ""
    if tone_hint and tone_hint != "Default":
        nudge += f" Write in a {tone_hint.lower()} tone."
    if length_hint and length_hint != "Default":
        nudge += f" Keep the response {length_hint.lower()}."
    if nudge:
        ghost_sfx = nudge + ghost_sfx

    raw_narration = narrate(
        _session,
        player_input=player_input,
        mechanical_result=mechanical_result,
        model=model_str,
        bypass=bypass,
        backend=backend,
        api_key=api_key,
        world_context=world_ctx,
        ghost_suffix=ghost_sfx,
    )

    # Extract ghosts
    clean_narration, new_ghosts = extract_ghosts(raw_narration)
    for g in new_ghosts:
        add_ghost(_session, g["name"], g["type"], g["context"])

    # Tag entities
    index = load_index(_world_slug()) if _world_slug() else []
    referenced = tag_referenced_entities(full_input, index)

    add_history(_session, player_input, mechanical_result,
                clean_narration, referenced)

    if _session["turn"] % 8 == 0 and _session["turn"] > 0:
        _session["summary"] = summarize_history(
            _session, model=model_str, backend=backend, api_key=api_key
        )

    # Build chat bubbles
    action_label = verb
    if subject.strip():   action_label += f" → {subject.strip()}"
    if modifiers.strip(): action_label += f" *({modifiers.strip()})*"
    if items:             action_label += f" 🎒 {', '.join(items)}"

    bot_content = (f"{mech_display}\n\n{clean_narration}".strip()
                   if mech_display else clean_narration)

    # Append state change log as a subtle system note
    if state_change_log:
        change_text = "  \n".join(state_change_log)
        bot_content += f"\n\n*— {change_text} —*"

    history = list(history or [])
    history.append(ChatMessage(role="user",      content=action_label))
    history.append(ChatMessage(role="assistant", content=bot_content))

    return history, get_status(), gr.update(choices=get_inventory()), get_ghost_md(), get_gm_scene_md()


# ══════════════════════════════════════════════════════════════════════════════
# QoL ACTIONS
# ══════════════════════════════════════════════════════════════════════════════

def do_continue(backend, model_name, api_key, temperature, history):
    """Append more narration to the last response."""
    global _session
    if not _session or not history:
        return history

    # Find the last assistant content to include as context
    last_narration = ""
    for msg in reversed(history):
        role = msg.role if hasattr(msg, 'role') else msg.get("role", "")
        if role == "assistant":
            last_narration = msg.content if hasattr(msg, 'content') else msg.get("content", "")
            break

    model_str = model_name.strip() or "tinyllama"
    world_ctx = retrieve_context(_world_slug(), _session, "", [])

    prompt = (
        f"{world_ctx}\n\n"
        f"{session_summary(_session)}\n\n"
        f"THE NARRATION SO FAR THIS TURN:\n{last_narration}\n\n"
        "Continue this narration for 2-3 more sentences. "
        "Pick up exactly where it left off — do not repeat anything, "
        "do not add a new paragraph header, just continue the prose."
    )

    from llm_interface import _call_ollama, _call_openrouter
    if backend == "openrouter":
        continuation = _call_openrouter(prompt, model_str, api_key,
                                        max_tokens=200, temperature=float(temperature))
    else:
        continuation = _call_ollama(prompt, model_str,
                                    max_tokens=200, temperature=float(temperature))

    clean, new_ghosts = extract_ghosts(continuation)
    for g in new_ghosts:
        add_ghost(_session, g["name"], g["type"], g["context"])

    # Append to last assistant message
    history = list(history)
    for i in range(len(history) - 1, -1, -1):
        msg = history[i]
        role    = msg.role    if hasattr(msg, 'role')    else msg.get("role", "")
        content = msg.content if hasattr(msg, 'content') else msg.get("content", "")
        if role == "assistant":
            history[i] = ChatMessage(role="assistant", content=content + " " + clean)
            # Update session history narration too
            if _session["history"]:
                _session["history"][-1]["narration"] += " " + clean
            break

    return history


def do_retry(backend, model_name, api_key, temperature, history):
    """Regenerate the last narration with the same mechanical result."""
    global _session, _last_mechanical, _last_player_input
    if not _session or not history or not _last_mechanical:
        return history, get_ghost_md()

    model_str = model_name.strip() or "tinyllama"
    world_ctx = retrieve_context(_world_slug(), _session, "", [])
    ghost_sfx = ("\n\n" + build_narrator_prompt_suffix()) if _world_slug() else ""

    raw = narrate(
        _session,
        player_input=_last_player_input,
        mechanical_result=_last_mechanical,
        model=model_str,
        bypass=False,
        backend=backend,
        api_key=api_key,
        world_context=world_ctx,
        ghost_suffix=ghost_sfx,
    )
    clean, new_ghosts = extract_ghosts(raw)
    for g in new_ghosts:
        add_ghost(_session, g["name"], g["type"], g["context"])

    mech_display = _mechanic_display(_last_mechanical)
    bot_content  = (f"{mech_display}\n\n{clean}".strip()
                    if mech_display else clean)

    # Replace last assistant message
    history = list(history)
    for i in range(len(history)-1, -1, -1):
        msg = history[i]
        role = msg.role if hasattr(msg, 'role') else msg.get("role","")
        if role == "assistant":
            history[i] = ChatMessage(role="assistant", content=bot_content)
            break

    # Update last history entry narration
    if _session["history"]:
        _session["history"][-1]["narration"] = clean

    return history, get_ghost_md()


def do_undo(history):
    """Roll back one full turn."""
    global _session
    if not _session:
        return history, get_status(), gr.update(), get_ghost_md(), get_gm_scene_md()
    success = pop_snapshot(_session)
    if not success:
        return history, get_status(), gr.update(), get_ghost_md(), get_gm_scene_md()
    # Remove last two chat messages (user + assistant)
    history = list(history)
    if len(history) >= 2:
        history = history[:-2]
    return history, get_status(), gr.update(choices=get_inventory()), get_ghost_md(), get_gm_scene_md()


def do_edit_last(history):
    """Extract the last assistant message content into the edit box."""
    if not history:
        return "", gr.update(visible=True)
    for msg in reversed(history):
        role    = msg.role    if hasattr(msg, 'role')    else msg.get("role", "")
        content = msg.content if hasattr(msg, 'content') else msg.get("content", "")
        if role == "assistant":
            return content, gr.update(visible=True)
    return "", gr.update(visible=True)


def do_save_edit(edited_text, history):
    """Replace the last assistant message with edited content."""
    if not history or not edited_text.strip():
        return history, gr.update(visible=False), ""
    history = list(history)
    for i in range(len(history) - 1, -1, -1):
        msg  = history[i]
        role = msg.role if hasattr(msg, 'role') else msg.get("role", "")
        if role == "assistant":
            history[i] = ChatMessage(role="assistant", content=edited_text.strip())
            if _session and _session["history"]:
                _session["history"][-1]["narration"] = edited_text.strip()
            break
    return history, gr.update(visible=False), ""


def do_copy_last(history) -> str:
    """Return last assistant message content for the copy textbox."""
    if not history:
        return ""
    for msg in reversed(history):
        role    = msg.role    if hasattr(msg, 'role')    else msg.get("role", "")
        content = msg.content if hasattr(msg, 'content') else msg.get("content", "")
        if role == "assistant":
            return content
    return ""


def do_clear_chat():
    return []


def do_add_scene_note(note_text):
    if not _session or not note_text.strip():
        return "❌ No session or empty note.", get_status()
    append_scene_note(_session, note_text.strip())
    return "✅ Note added.", get_status()


# ══════════════════════════════════════════════════════════════════════════════
# SAVE / LOAD
# ══════════════════════════════════════════════════════════════════════════════

def do_save():
    if not _session:
        return "❌ No active session."
    save_session(_session)
    return "✅ Session saved."


def do_load(world_choice):
    global _session
    if not world_choice:
        return gr.update(), get_status(), "❌ Select a world.", gr.update(), get_ghost_md(), get_gm_scene_md()
    slug = world_choice.split(" — ")[0].strip()
    sess = latest_session(slug)
    if not sess:
        return gr.update(), get_status(), f"❌ No saved session for '{slug}'.", gr.update(), get_ghost_md(), get_gm_scene_md()
    _session = sess
    # Rebuild chat from history
    chat = []
    for h in _session.get("history", []):
        verb = h["action"].get("verb", "(action)")
        subj = h["action"].get("subject", "")
        label = verb + (f" → {subj}" if subj else "")
        chat.append(ChatMessage(role="user", content=label))
        chat.append(ChatMessage(role="assistant", content=h["narration"]))
    return (chat, get_status(), "✅ Session loaded.",
            gr.update(choices=get_inventory()), get_ghost_md(), get_gm_scene_md())


# ══════════════════════════════════════════════════════════════════════════════
# GHOST ACTIONS
# ══════════════════════════════════════════════════════════════════════════════

def do_dismiss_ghost(ghost_id):
    if not _session or not ghost_id.strip():
        return get_ghost_md(), "❌ No ghost id."
    dismiss_ghost(_session, ghost_id.strip())
    return get_ghost_md(), "✅ Ghost dismissed."


def do_promote_ghost(ghost_id):
    """Return ghost data as pre-filled JSON for the entity editor."""
    if not _session or not ghost_id.strip():
        return "{}", get_ghost_md(), "❌ No ghost id."
    ghost = promote_ghost(_session, ghost_id.strip())
    if not ghost:
        return "{}", get_ghost_md(), "❌ Ghost not found."

    # Build a pre-filled entity stub
    ctype = ghost["type"]
    col_map = {
        "character": "characters",
        "location":  "locations",
        "faction":   "factions",
        "item":      "items",
        "concept":   "concepts",
    }
    stub = {
        "id":          "",
        "name":        ghost["name"],
        "record_type": ctype,
        "importance":  "minor",
        "notes":       ghost["context"],
    }
    if ctype == "concept":
        stub["notes_for_ai"] = ghost["context"]
        stub["category"]     = "unknown"

    promoted_json = json.dumps(stub, indent=2)
    return promoted_json, get_ghost_md(), f"✅ Open the World Bible tab to save **{ghost['name']}**."


def do_add_manual_ghost(name, gtype, context):
    if not _session:
        return get_ghost_md(), "❌ No active session."
    if not name.strip():
        return get_ghost_md(), "❌ Name required."
    add_ghost(_session, name.strip(), gtype, context.strip())
    return get_ghost_md(), "✅ Ghost added."


# ══════════════════════════════════════════════════════════════════════════════
# WORLD BIBLE HELPERS (reused from before)
# ══════════════════════════════════════════════════════════════════════════════

def _world_list_md() -> str:
    worlds = list_worlds()
    if not worlds:
        return "*No worlds yet. Create one in the World Bible tab.*"
    lines = []
    for w in worlds:
        active = " ✅" if w["slug"] == _world_slug() else ""
        lines.append(f"**{w['title']}** ({w['genre']}){active} — `{w['slug']}`")
    return "\n\n".join(lines)


def _entity_table(world_slug: str, collection: str) -> list[list]:
    if not world_slug:
        return []
    rows = list_entities(world_slug, collection)
    return [[r["name"], r["importance"], r["status"], r["id"]] for r in rows]


def do_create_world(title, genre_primary, genre_tags_str,
                    tone, setting, truths_str, narrator_voice):
    if not title.strip():
        return "❌ Title required.", _world_list_md(), gr.update()
    genre_tags = [t.strip() for t in genre_tags_str.split(",") if t.strip()]
    truths     = [t.strip() for t in truths_str.strip().split("\n") if t.strip()]
    from world_bible import create_world as _cw
    slug = _cw(title.strip(), genre_primary, genre_tags,
                tone, setting, truths, narrator_voice)
    worlds = get_world_options()
    return (f"✅ World **{title}** created.",
            _world_list_md(),
            gr.update(choices=worlds))


def do_load_world_tab(slug):
    master = load_master(slug.strip())
    if not master:
        return f"❌ World '{slug}' not found.", _world_list_md(), gr.update()
    worlds = get_world_options()
    return (f"✅ World **{master.get('title',slug)}** available.",
            _world_list_md(),
            gr.update(choices=worlds))


def do_delete_world_tab(slug):
    delete_world(slug.strip())
    worlds = get_world_options()
    return "✅ Deleted.", _world_list_md(), gr.update(choices=worlds)


def do_suggest_truths(genre_primary, tone, setting, truths_str,
                      model_name, backend, api_key):
    from author_assist import suggest_truths
    existing = [t.strip() for t in truths_str.strip().split("\n") if t.strip()]
    suggestions = suggest_truths(
        _world_slug(), genre_primary, tone, setting, existing,
        model_name.strip() or "tinyllama", backend, api_key
    )
    if not suggestions:
        return truths_str, "❌ Could not generate suggestions."
    return "\n".join(existing + suggestions), f"✅ Added {len(suggestions)} truths."


def do_load_entity_list(collection):
    return gr.update(value=_entity_table(_world_slug(), collection))


def do_open_entity(collection, evt: gr.SelectData):
    if not _world_slug():
        return "{}"
    rows = _entity_table(_world_slug(), collection)
    if evt.index[0] >= len(rows):
        return "{}"
    entity_id = rows[evt.index[0]][3]
    data = load_entity(_world_slug(), collection, entity_id)
    return json.dumps(data, indent=2)


def do_new_entity(collection, importance):
    stub = {"id":"","name":"","importance":importance,
            "record_type": collection[:-1]}
    return json.dumps(stub, indent=2)


def do_save_entity_json(json_str, collection):
    try:
        data = json.loads(json_str)
    except Exception as e:
        return f"❌ Invalid JSON: {e}", gr.update()
    save_entity(_world_slug(), collection, data)
    return "✅ Saved.", gr.update(value=_entity_table(_world_slug(), collection))


def do_delete_entity_btn(collection, json_str):
    try:
        data = json.loads(json_str)
        eid  = data.get("id","")
        if eid:
            delete_entity(_world_slug(), collection, eid)
    except Exception:
        pass
    return "✅ Deleted.", gr.update(value=_entity_table(_world_slug(), collection)), "{}"


def do_generate_fields(json_str, collection, model_name, backend, api_key):
    from author_assist import generate_fields, get_fillable_fields, set_nested
    try:
        data = json.loads(json_str)
    except Exception as e:
        return json_str, f"❌ Invalid JSON: {e}"
    fields = get_fillable_fields(collection, data)
    if not fields:
        return json_str, "ℹ️ No empty fields to fill."
    model_str = model_name.strip() or "tinyllama"
    generated = generate_fields(
        _world_slug(), collection, data, fields,
        _session.get("history",[]) if _session else [],
        model_str, backend, api_key
    )
    if not generated:
        return json_str, "❌ Generation failed."
    for path, value in generated.items():
        set_nested(data, path, value)
    return json.dumps(data, indent=2), f"✅ Generated {len(generated)} fields."


# ══════════════════════════════════════════════════════════════════════════════
# GM SCREEN HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

def get_gm_scene_md() -> str:
    """Render the current live scene state for the GM screen."""
    if not _session:
        return "*No active session.*"
    sc = _session["scene"]
    lines = [f"**📍 {sc.get('location_name','?')}**"]

    # NPCs present
    present = sc.get("npcs_present", [])
    if present:
        lines.append("\n**👥 Present:**")
        for npc_id in present:
            live = sc.get("npcs_live", {}).get(npc_id, {})
            disp = live.get("disposition", "neutral")
            hp   = live.get("hp", "?")
            stat = live.get("status", "alive")
            lines.append(f"- `{npc_id}` — {disp}, {hp} HP, {stat}")
    else:
        lines.append("\n*No NPCs present.*")

    # Objects with flags
    objects = sc.get("objects", {})
    if objects:
        lines.append("\n**🪑 Objects:**")
        for oid, obj in objects.items():
            flags = obj.get("flags", {})
            flag_str = ", ".join(f"{k}={v}" for k, v in flags.items())
            lines.append(f"- {obj.get('label', oid)}" +
                         (f" `[{flag_str}]`" if flag_str else ""))

    # Scene notes
    notes = get_scene_notes(_session, n=5)
    if notes:
        lines.append("\n**📝 Notes:**")
        for n in notes:
            lines.append(f"- {n}")

    return "\n".join(lines)


def do_gm_add_npc(npc_id: str):
    """Add an NPC id to currently_present."""
    if not _session or not npc_id.strip():
        return get_gm_scene_md(), "❌ Enter an NPC id."
    npc_id = npc_id.strip()
    sc = _session["scene"]
    if npc_id not in sc["npcs_present"]:
        sc["npcs_present"].append(npc_id)
        if npc_id not in sc["npcs_live"]:
            sc["npcs_live"][npc_id] = {"hp":10,"disposition":"neutral","status":"alive"}
    return get_gm_scene_md(), f"✅ {npc_id} added to scene."


def do_gm_remove_npc(npc_id: str):
    """Remove an NPC from currently_present."""
    if not _session or not npc_id.strip():
        return get_gm_scene_md(), "❌ Enter an NPC id."
    npc_id = npc_id.strip()
    sc = _session["scene"]
    sc["npcs_present"] = [n for n in sc["npcs_present"] if n != npc_id]
    return get_gm_scene_md(), f"✅ {npc_id} removed from scene."


def do_gm_update_npc(npc_id: str, disposition: str, hp_str: str, status: str):
    """Update live state of an NPC."""
    if not _session or not npc_id.strip():
        return get_gm_scene_md(), "❌ Enter an NPC id."
    npc_id = npc_id.strip()
    sc = _session["scene"]
    if npc_id not in sc["npcs_live"]:
        sc["npcs_live"][npc_id] = {"hp":10,"disposition":"neutral","status":"alive"}
    live = sc["npcs_live"][npc_id]
    if disposition: live["disposition"] = disposition
    if hp_str.strip().lstrip("-").isdigit(): live["hp"] = int(hp_str)
    if status: live["status"] = status
    return get_gm_scene_md(), f"✅ {npc_id} updated."


def do_gm_set_object_flag(obj_label: str, flag: str, value: str):
    """Add or update an object flag in the current scene."""
    if not _session or not obj_label.strip():
        return get_gm_scene_md(), "❌ Enter an object name."
    sc = _session["scene"]
    oid = obj_label.strip().lower().replace(" ", "-")
    if oid not in sc["objects"]:
        sc["objects"][oid] = {"label": obj_label.strip(), "flags": {}}
    # Coerce value: "true"→True, "false"→False, numbers→int, else str
    val: object = value.strip()
    if val.lower() == "true":   val = True
    elif val.lower() == "false": val = False
    else:
        try: val = int(val)
        except ValueError: pass
    sc["objects"][oid]["flags"][flag.strip()] = val
    return get_gm_scene_md(), f"✅ {obj_label} [{flag}={val}]"


def do_gm_move_scene(location_id: str):
    """Manually move to a location by id — triggers world bible lookup."""
    if not _session or not location_id.strip():
        return get_gm_scene_md(), get_status(), "❌ Enter a location id."
    from state_changes import find_location, transition_to_known_location, transition_to_unknown_location
    dest = location_id.strip()
    if _world_slug():
        loc_data = find_location(_world_slug(), dest)
        if loc_data:
            transition_to_known_location(_session, loc_data)
            return get_gm_scene_md(), get_status(), f"✅ Moved to {loc_data.get('name', dest)}."
    transition_to_unknown_location(_session, dest)
    return get_gm_scene_md(), get_status(), f"✅ Moved to {dest} (not in bible)."


# ══════════════════════════════════════════════════════════════════════════════

def submit_structured(category, subcategory, subject, modifiers, items,
                      backend, model_name, api_key, temperature,
                      tone_hint, length_hint, history):
    if not _session:
        return history, get_status(), gr.update(), get_ghost_md(), get_gm_scene_md()
    verb = subcategory or category or ""
    if not verb:
        return history, get_status(), gr.update(), get_ghost_md(), get_gm_scene_md()
    return _run_action(verb, subject, modifiers, items,
                       backend, model_name, api_key,
                       float(temperature), tone_hint, length_hint,
                       history, bypass=False)


def submit_narrative(free_text, backend, model_name, api_key,
                     temperature, tone_hint, length_hint, history):
    if not _session:
        return history, get_status(), gr.update(), get_ghost_md(), get_gm_scene_md()
    if not free_text.strip():
        return history, get_status(), gr.update(), get_ghost_md(), get_gm_scene_md()
    return _run_action(free_text.strip(), "", "", [],
                       backend, model_name, api_key,
                       float(temperature), tone_hint, length_hint,
                       history, bypass=True)


# ══════════════════════════════════════════════════════════════════════════════
# UI
# ══════════════════════════════════════════════════════════════════════════════

CSS = """
#main-col  { display:flex; flex-direction:column; }
#chatbox   { flex:1 1 auto; }
#ghost-bar { border-left:2px solid #e0e0e0; padding-left:12px; }
.input-area { border-top:1px solid #ddd; padding-top:8px; margin-top:4px; }
"""

_first_cat  = list(VERB_TREE.keys())[0]
_first_subs = list(VERB_TREE[_first_cat].keys())

with gr.Blocks(title="Solo TTRPG Alpha", css=CSS) as demo:

    gr.Markdown("# ⚔️ Solo TTRPG — Alpha")

    with gr.Tabs():

        # ════════════════════════════════════════════
        # TAB 1 — GAME
        # ════════════════════════════════════════════
        with gr.Tab("🎮 Game"):

            with gr.Row(equal_height=False):

                # ── LEFT SIDEBAR ──────────────────────────────────
                with gr.Column(scale=1, min_width=230):

                    gr.Markdown("### ⚙️ Settings")
                    backend_radio = gr.Radio(
                        choices=["ollama","openrouter"],
                        value="ollama", label="LLM Backend")
                    model_tb = gr.Textbox(
                        label="Model", value="tinyllama",
                        placeholder="tinyllama / mistral / llama3")
                    api_key_tb = gr.Textbox(
                        label="OpenRouter API Key",
                        placeholder="sk-or-...", type="password", value="")
                    temp_slider = gr.Slider(
                        minimum=0.1, maximum=1.5, value=0.8, step=0.05,
                        label="🌡️ Temperature")

                    gr.Markdown("---")
                    gr.Markdown("### 🗂️ Session")

                    world_dd = gr.Dropdown(
                        choices=get_world_options(),
                        label="Active World",
                        value=None,
                        allow_custom_value=False,
                    )

                    new_game_btn = gr.Button("🆕 New Game", variant="primary")
                    save_btn     = gr.Button("💾 Save")
                    load_btn     = gr.Button("📂 Load Latest")
                    clear_btn    = gr.Button("🧹 Clear Chat")
                    session_msg  = gr.Markdown("")

                    gr.Markdown("---")
                    gr.Markdown("### 📊 Status")
                    status_md = gr.Markdown(get_status())

                    gr.Markdown("---")
                    gr.Markdown("### 📝 Scene Note")
                    scene_note_tb  = gr.Textbox(
                        label="", placeholder="Add a note to the current scene…",
                        lines=2)
                    add_note_btn   = gr.Button("Add Note")

                # ── NEW GAME PANEL (shown over main column) ───────
                with gr.Column(scale=3, elem_id="main-col"):

                    # New game setup panel
                    with gr.Group(visible=True) as new_game_panel:
                        gr.Markdown("## 🌍 Start a New Adventure")
                        gr.Markdown(
                            "*Select a world and describe your character. "
                            "If you have no worlds yet, create one in the "
                            "**World Bible** tab first.*"
                        )
                        ng_world_dd = gr.Dropdown(
                            choices=get_world_options(),
                            label="World",
                            value=None,
                        )
                        ng_name_tb  = gr.Textbox(
                            label="Character Name", value="Traveller")
                        ng_bio_tb   = gr.Textbox(
                            label="Who are you? (1-2 sentences)",
                            placeholder="A disgraced soldier looking for work in the lower city.",
                            lines=2)
                        ng_loc_tb   = gr.Textbox(
                            label="Starting Location ID (optional)",
                            placeholder="rusty-flagon-inn  (leave blank to let AI choose)")
                        ng_start_btn = gr.Button(
                            "Begin Adventure ▶", variant="primary")
                        ng_msg = gr.Markdown("")

                    # Main game area (hidden until session starts)
                    with gr.Group(visible=False) as game_panel:

                        chatbot = gr.Chatbot(label="", height=420, elem_id="chatbox")

                        # QoL bar
                        with gr.Row(elem_classes=["input-area"]):
                            continue_btn = gr.Button("▶ Continue",    size="sm")
                            retry_btn    = gr.Button("🔄 Retry",       size="sm")
                            undo_btn     = gr.Button("↩ Undo",         size="sm")
                            edit_btn     = gr.Button("✏️ Edit Last",   size="sm")
                            copy_btn     = gr.Button("📋 Copy Last",   size="sm")
                            regen_btn    = gr.Button("🎲 New Opening", size="sm")

                        # Edit last panel (hidden by default)
                        with gr.Group(visible=False) as edit_panel:
                            edit_tb      = gr.Textbox(
                                label="Edit last response",
                                lines=4, placeholder="Edit the narration…")
                            with gr.Row():
                                save_edit_btn   = gr.Button("💾 Save Edit", variant="primary", scale=2)
                                cancel_edit_btn = gr.Button("✕ Cancel",                       scale=1)

                        # Copy last — read-only textbox the browser can copy from
                        copy_tb = gr.Textbox(
                            label="📋 Last response (select all + copy)",
                            lines=3, interactive=False, visible=False)

                        # Tone / length nudges
                        with gr.Row():
                            tone_dd   = gr.Dropdown(
                                choices=["Default","Dramatic","Tense","Melancholic",
                                         "Darkly humorous","Sparse","Vivid"],
                                value="Default", label="Tone", scale=1)
                            length_dd = gr.Dropdown(
                                choices=["Default","Very short","Short","Long"],
                                value="Default", label="Length", scale=1)

                        mode_radio = gr.Radio(
                            choices=["🎲 Structured Action","💬 Narrative"],
                            value="🎲 Structured Action",
                            label="Input Mode")

                        # Structured panel
                        with gr.Group(visible=True) as structured_panel:
                            with gr.Row():
                                category_dd = gr.Dropdown(
                                    choices=list(VERB_TREE.keys()),
                                    value=_first_cat, label="Category", scale=1)
                                subcategory_dd = gr.Dropdown(
                                    choices=_first_subs,
                                    value=_first_subs[0] if _first_subs else None,
                                    label="Action", scale=1)
                            action_hint = gr.Markdown(
                                "*" + VERB_TREE[_first_cat][_first_subs[0]].get("hint","") + "*"
                                if _first_subs else "")
                            with gr.Row():
                                subject_tb  = gr.Textbox(
                                    label="Subject", placeholder="who or what", scale=2)
                                modifier_tb = gr.Textbox(
                                    label="Modifiers", placeholder="carefully, from behind…", scale=2)
                                items_dd    = gr.Dropdown(
                                    choices=get_inventory(), multiselect=True,
                                    label="Items", scale=1, value=[])
                            struct_submit = gr.Button("Take Action ▶", variant="primary")

                        # Narrative panel
                        with gr.Group(visible=False) as narrative_panel:
                            with gr.Row():
                                free_tb     = gr.Textbox(
                                    label="", placeholder="Type what you do… (Enter to send)",
                                    lines=2, scale=5)
                                narr_submit = gr.Button("Send ▶", variant="primary", scale=1)

                # ── RIGHT SIDEBAR: GHOST BAR ──────────────────────
                with gr.Column(scale=1, min_width=220, elem_id="ghost-bar"):
                    gr.Markdown("### 👻 Ghost Entries")
                    ghost_md = gr.Markdown(get_ghost_md())

                    gr.Markdown("##### Manage")
                    with gr.Row():
                        ghost_id_tb   = gr.Textbox(
                            label="Ghost ID", placeholder="ghost-001", scale=3)
                        dismiss_btn   = gr.Button("✕", scale=1, size="sm")
                    promote_btn   = gr.Button("📖 Promote to Bible", variant="primary")
                    promoted_json = gr.Code(
                        label="Promoted Entity (copy to Bible tab)",
                        language="json", lines=6, value="{}", visible=False)
                    ghost_msg     = gr.Markdown("")

                    gr.Markdown("---")
                    gr.Markdown("##### Add Manually")
                    mg_name    = gr.Textbox(label="Name", placeholder="Brennan")
                    mg_type    = gr.Dropdown(
                        choices=["character","location","faction","item","concept"],
                        value="character", label="Type")
                    mg_context = gr.Textbox(
                        label="Context", placeholder="Nervous guard at the east gate.",
                        lines=2)
                    mg_add_btn = gr.Button("Add Ghost")

                    # ── GM SCREEN ──────────────────────────────────
                    gr.Markdown("---")
                    gr.Markdown("### 🎬 GM Screen")
                    gm_scene_md = gr.Markdown(get_gm_scene_md())

                    gr.Markdown("##### 📍 Move Scene")
                    with gr.Row():
                        gm_loc_tb  = gr.Textbox(
                            label="Location ID", placeholder="rusty-flagon-inn",
                            scale=3)
                        gm_move_btn = gr.Button("Go", scale=1, size="sm")

                    gr.Markdown("##### 👥 NPC Controls")
                    with gr.Row():
                        gm_npc_tb     = gr.Textbox(
                            label="NPC ID", placeholder="silas-vorne", scale=3)
                        gm_add_npc    = gr.Button("+ Add", scale=1, size="sm")
                        gm_remove_npc = gr.Button("– Remove", scale=1, size="sm")

                    gr.Markdown("*Update live NPC state:*")
                    gm_npc_edit_id   = gr.Textbox(
                        label="NPC ID", placeholder="silas-vorne")
                    gm_npc_disp      = gr.Dropdown(
                        choices=["","hostile","unfriendly","neutral",
                                 "friendly","allied"],
                        value="", label="Disposition")
                    with gr.Row():
                        gm_npc_hp     = gr.Textbox(label="HP", placeholder="10", scale=1)
                        gm_npc_status = gr.Dropdown(
                            choices=["","alive","injured","defeated","fled"],
                            value="", label="Status", scale=2)
                    gm_update_npc = gr.Button("Update NPC")

                    gr.Markdown("##### 🪑 Object Flags")
                    gm_obj_label = gr.Textbox(label="Object", placeholder="locked door")
                    with gr.Row():
                        gm_flag_key = gr.Textbox(label="Flag", placeholder="locked", scale=1)
                        gm_flag_val = gr.Textbox(label="Value", placeholder="false", scale=1)
                    gm_set_flag_btn = gr.Button("Set Flag")

                    gm_msg = gr.Markdown("")

            # ── GAME TAB EVENTS ───────────────────────────────────

            def refresh_world_dropdowns():
                opts = get_world_options()
                return gr.update(choices=opts), gr.update(choices=opts)

            # New game
            ng_start_btn.click(
                fn=do_start_new_game,
                inputs=[ng_world_dd, ng_name_tb, ng_bio_tb, ng_loc_tb,
                        model_tb, backend_radio, api_key_tb],
                outputs=[chatbot, status_md, ng_msg, items_dd,
                         ghost_md, new_game_panel],
            ).then(
                fn=lambda: (gr.update(visible=False), gr.update(visible=True)),
                outputs=[new_game_panel, game_panel],
            ).then(
                fn=get_gm_scene_md,
                outputs=[gm_scene_md],
            )

            new_game_btn.click(
                fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
                outputs=[new_game_panel, game_panel],
            )

            # Regenerate opening
            regen_btn.click(
                fn=do_regenerate_opening,
                inputs=[ng_world_dd, ng_name_tb, ng_bio_tb, ng_loc_tb,
                        model_tb, backend_radio, api_key_tb],
                outputs=[chatbot, status_md, session_msg],
            ).then(fn=get_gm_scene_md, outputs=[gm_scene_md])

            # Mode switch
            def switch_mode(mode):
                if mode == "💬 Narrative":
                    return gr.update(visible=False), gr.update(visible=True)
                return gr.update(visible=True), gr.update(visible=False)
            mode_radio.change(switch_mode, [mode_radio], [structured_panel, narrative_panel])

            # Category → subcategory
            def on_cat(cat):
                subs = list(VERB_TREE.get(cat,{}).keys())
                hint = VERB_TREE[cat][subs[0]].get("hint","") if subs else ""
                return (gr.update(choices=subs, value=subs[0] if subs else None),
                        gr.update(value=f"*{hint}*" if hint else ""))
            category_dd.change(on_cat, [category_dd], [subcategory_dd, action_hint])

            def on_sub(cat, sub):
                hint = VERB_TREE.get(cat,{}).get(sub,{}).get("hint","")
                return gr.update(value=f"*{hint}*" if hint else "")
            subcategory_dd.change(on_sub, [category_dd, subcategory_dd], [action_hint])

            # Backend switch
            def on_backend(b):
                return gr.update(value="mistralai/mistral-7b-instruct:free"
                                  if b=="openrouter" else "tinyllama")
            backend_radio.change(on_backend, [backend_radio], [model_tb])

            # Submit
            struct_submit.click(
                fn=submit_structured,
                inputs=[category_dd, subcategory_dd, subject_tb, modifier_tb,
                        items_dd, backend_radio, model_tb, api_key_tb,
                        temp_slider, tone_dd, length_dd, chatbot],
                outputs=[chatbot, status_md, items_dd, ghost_md, gm_scene_md],
            )
            narr_submit.click(
                fn=submit_narrative,
                inputs=[free_tb, backend_radio, model_tb, api_key_tb,
                        temp_slider, tone_dd, length_dd, chatbot],
                outputs=[chatbot, status_md, items_dd, ghost_md, gm_scene_md],
            )
            free_tb.submit(
                fn=submit_narrative,
                inputs=[free_tb, backend_radio, model_tb, api_key_tb,
                        temp_slider, tone_dd, length_dd, chatbot],
                outputs=[chatbot, status_md, items_dd, ghost_md, gm_scene_md],
            )

            # QoL
            continue_btn.click(
                fn=do_continue,
                inputs=[backend_radio, model_tb, api_key_tb, temp_slider, chatbot],
                outputs=[chatbot],
            )
            retry_btn.click(
                fn=do_retry,
                inputs=[backend_radio, model_tb, api_key_tb, temp_slider, chatbot],
                outputs=[chatbot, ghost_md],
            )
            undo_btn.click(
                fn=do_undo,
                inputs=[chatbot],
                outputs=[chatbot, status_md, items_dd, ghost_md, gm_scene_md],
            )
            clear_btn.click(fn=do_clear_chat, outputs=[chatbot])

            # Edit last
            edit_btn.click(
                fn=do_edit_last,
                inputs=[chatbot],
                outputs=[edit_tb, edit_panel],
            )
            save_edit_btn.click(
                fn=do_save_edit,
                inputs=[edit_tb, chatbot],
                outputs=[chatbot, edit_panel, edit_tb],
            )
            cancel_edit_btn.click(
                fn=lambda: (gr.update(visible=False), ""),
                outputs=[edit_panel, edit_tb],
            )

            # Copy last
            copy_btn.click(
                fn=do_copy_last,
                inputs=[chatbot],
                outputs=[copy_tb],
            ).then(
                fn=lambda: gr.update(visible=True),
                outputs=[copy_tb],
            )

            # Clear inputs after structured submit
            struct_submit.click(
                fn=lambda: ("", "", []),
                outputs=[subject_tb, modifier_tb, items_dd],
            )
            # Clear free text after narrative submit
            narr_submit.click(
                fn=lambda: gr.update(value=""),
                outputs=[free_tb],
            )
            free_tb.submit(
                fn=lambda: gr.update(value=""),
                outputs=[free_tb],
            )

            # Save / load
            save_btn.click(fn=do_save, outputs=[session_msg])
            load_btn.click(
                fn=do_load,
                inputs=[world_dd],
                outputs=[chatbot, status_md, session_msg, items_dd, ghost_md, gm_scene_md],
            )

            # Ghost bar
            dismiss_btn.click(
                fn=do_dismiss_ghost,
                inputs=[ghost_id_tb],
                outputs=[ghost_md, ghost_msg],
            )
            promote_btn.click(
                fn=do_promote_ghost,
                inputs=[ghost_id_tb],
                outputs=[promoted_json, ghost_md, ghost_msg],
            ).then(
                fn=lambda: gr.update(visible=True),
                outputs=[promoted_json],
            )
            mg_add_btn.click(
                fn=do_add_manual_ghost,
                inputs=[mg_name, mg_type, mg_context],
                outputs=[ghost_md, ghost_msg],
            )

            # GM screen
            gm_move_btn.click(
                fn=do_gm_move_scene,
                inputs=[gm_loc_tb],
                outputs=[gm_scene_md, status_md, gm_msg],
            )
            gm_add_npc.click(
                fn=do_gm_add_npc,
                inputs=[gm_npc_tb],
                outputs=[gm_scene_md, gm_msg],
            )
            gm_remove_npc.click(
                fn=do_gm_remove_npc,
                inputs=[gm_npc_tb],
                outputs=[gm_scene_md, gm_msg],
            )
            gm_update_npc.click(
                fn=do_gm_update_npc,
                inputs=[gm_npc_edit_id, gm_npc_disp, gm_npc_hp, gm_npc_status],
                outputs=[gm_scene_md, gm_msg],
            )
            gm_set_flag_btn.click(
                fn=do_gm_set_object_flag,
                inputs=[gm_obj_label, gm_flag_key, gm_flag_val],
                outputs=[gm_scene_md, gm_msg],
            )
            add_note_btn.click(
                fn=do_add_scene_note,
                inputs=[scene_note_tb],
                outputs=[session_msg, status_md],
            ).then(
                fn=get_gm_scene_md,
                outputs=[gm_scene_md],
            ).then(
                fn=lambda: gr.update(value=""),
                outputs=[scene_note_tb],
            )

        # ════════════════════════════════════════════
        # TAB 2 — WORLD BIBLE
        # ════════════════════════════════════════════
        with gr.Tab("🌍 World Bible"):

            with gr.Row():
                with gr.Column(scale=1, min_width=280):

                    gr.Markdown("### 🌐 Worlds")
                    world_list_md = gr.Markdown(_world_list_md())

                    with gr.Row():
                        wb_slug_tb       = gr.Textbox(
                            label="Slug", placeholder="world-slug")
                        wb_load_btn      = gr.Button("Load")
                        wb_delete_btn    = gr.Button("🗑️", variant="stop")
                    wb_world_msg = gr.Markdown("")

                    gr.Markdown("---")
                    gr.Markdown("### ➕ Create New World")
                    wc_title   = gr.Textbox(label="Title",
                                             placeholder="The Hollow Meridian")
                    wc_genre   = gr.Dropdown(
                        choices=["fantasy","sci-fi","horror","historical",
                                 "contemporary","western","post-apocalyptic",
                                 "mythological","other"],
                        label="Primary Genre", value="fantasy")
                    wc_tags    = gr.Textbox(label="Genre Tags (comma-sep)",
                                             placeholder="noir, biopunk")
                    wc_tone    = gr.Textbox(label="Tone",
                                             placeholder="grim, atmospheric")
                    wc_setting = gr.Textbox(label="Setting",
                                             lines=3,
                                             placeholder="Far-future Earth…")
                    wc_truths  = gr.Textbox(label="Truths (one per line)",
                                             lines=5,
                                             placeholder="Magic costs years off your lifespan.")
                    wc_voice   = gr.Textbox(label="Narrator Voice",
                                             placeholder="Second person, present tense.")
                    with gr.Row():
                        create_world_btn  = gr.Button("Create World ✨", variant="primary")
                        suggest_truth_btn = gr.Button("🎲 Suggest Truths")
                    suggest_msg = gr.Markdown("")

                with gr.Column(scale=2):
                    gr.Markdown("### 📚 Entity Database")
                    collection_radio = gr.Radio(
                        choices=COLLECTIONS, value="characters", label="Collection")
                    entity_table = gr.Dataframe(
                        headers=["Name","Importance","Status","ID"],
                        datatype=["str","str","str","str"],
                        interactive=False, label="",
                        value=_entity_table(_world_slug(), "characters"))
                    with gr.Row():
                        new_entity_imp = gr.Dropdown(
                            choices=["named","minor","archetype","advanced","basic"],
                            value="named", label="Tier", scale=1)
                        new_entity_btn = gr.Button("➕ New",    scale=2)
                        del_entity_btn = gr.Button("🗑️ Delete", variant="stop", scale=1)
                    gr.Markdown("#### ✏️ Entity Editor")
                    entity_json = gr.Code(
                        label="JSON", language="json", lines=22, value="{}")
                    with gr.Row():
                        save_entity_btn = gr.Button("💾 Save Entity", variant="primary")
                        gen_fields_btn  = gr.Button("✨ AI Fill Empty Fields")
                    entity_msg = gr.Markdown("")

            # World Bible events
            create_world_btn.click(
                fn=do_create_world,
                inputs=[wc_title, wc_genre, wc_tags, wc_tone,
                        wc_setting, wc_truths, wc_voice],
                outputs=[wb_world_msg, world_list_md, ng_world_dd],
            )
            wb_load_btn.click(
                fn=do_load_world_tab,
                inputs=[wb_slug_tb],
                outputs=[wb_world_msg, world_list_md, ng_world_dd],
            )
            wb_delete_btn.click(
                fn=do_delete_world_tab,
                inputs=[wb_slug_tb],
                outputs=[wb_world_msg, world_list_md, ng_world_dd],
            )
            suggest_truth_btn.click(
                fn=do_suggest_truths,
                inputs=[wc_genre, wc_tone, wc_setting, wc_truths,
                        model_tb, backend_radio, api_key_tb],
                outputs=[wc_truths, suggest_msg],
            )
            collection_radio.change(
                fn=do_load_entity_list,
                inputs=[collection_radio], outputs=[entity_table])
            entity_table.select(
                fn=do_open_entity,
                inputs=[collection_radio], outputs=[entity_json])
            new_entity_btn.click(
                fn=do_new_entity,
                inputs=[collection_radio, new_entity_imp], outputs=[entity_json])
            save_entity_btn.click(
                fn=do_save_entity_json,
                inputs=[entity_json, collection_radio],
                outputs=[entity_msg, entity_table])
            del_entity_btn.click(
                fn=do_delete_entity_btn,
                inputs=[collection_radio, entity_json],
                outputs=[entity_msg, entity_table, entity_json])
            gen_fields_btn.click(
                fn=do_generate_fields,
                inputs=[entity_json, collection_radio,
                        model_tb, backend_radio, api_key_tb],
                outputs=[entity_json, entity_msg])


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7861, theme=gr.themes.Soft())
