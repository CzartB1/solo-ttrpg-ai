"""
app.py
Solo TTRPG — Alpha
Run with: python app.py  →  open http://localhost:7861
"""

import gradio as gr
from gradio import ChatMessage
from game_state import new_game, save_game, load_game, add_history
from interpreter import interpret, VERB_TREE
from llm_interface import narrate, summarize_history
from world_bible import (
    list_worlds, create_world, save_master, load_master,
    list_entities, load_entity, save_entity, delete_entity,
    duplicate_entity, delete_world, COLLECTIONS
)
from retrieval import retrieve_context, tag_referenced_entities, load_index
from author_assist import (
    generate_fields, suggest_truths,
    get_fillable_fields, set_nested, FILLABLE_FIELDS
)

# ── Global state ──────────────────────────────────────────────────────────────

_state        = new_game("Traveller")
_world_slug   = ""          # active world
_edit_entity  = {}          # entity currently open in editor
_edit_col     = ""          # collection of entity being edited


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_inventory() -> list[str]:
    return _state["player"]["inventory"]


def get_status() -> str:
    p  = _state["player"]
    sc = _state["scene"]
    cond = ", ".join(p["conditions"]) if p["conditions"] else "none"
    return (
        f"**{p['name']}** | ❤️ {p['hp']}/{p['hp_max']} HP | "
        f"💰 {p['gold']} gold | 🩹 {cond}\n\n"
        f"📍 **{sc['location']}**\n{sc['description']}"
    )


def _mechanic_display(result: dict) -> str:
    if not result.get("mechanical"):
        return ""
    rtype = result.get("type", "")
    if rtype == "attack":
        if result["hit"]:
            return f"🎲 `Hit! {result['hit_roll']}+{result['hit_bonus']}={result['hit_total']} vs {result['difficulty']} | 💥 {result['damage']} dmg — {result.get('status','')}`"
        return f"🎲 `Miss. {result['hit_roll']}+{result['hit_bonus']}={result['hit_total']} vs {result['difficulty']}`"
    if rtype in ("skill_check", "stealth_check", "persuasion_check"):
        icon = "✅" if result["success"] else "❌"
        return f"🎲 `{icon} {result['degree'].title()} | {result['rolled']}+{result['bonus']}={result['total']} vs {result['difficulty']}`"
    if rtype == "use_item":
        return f"🎒 `{result['item']}: {result.get('effect','')}`"
    if rtype == "examine":
        icon = "✅" if result["success"] else "❌"
        return f"🔍 `{icon} {result['degree'].title()} | {result['rolled']}+{result['bonus']}={result['total']} vs {result['difficulty']}`"
    if rtype == "rest":
        return f"💤 `Rested. +{result['hp_gain']} HP — {result['status']}`"
    return ""


def _run_action(verb: str, subject: str, modifiers: str, items: list,
                backend: str, model_name: str, api_key: str,
                history: list, bypass: bool = False):
    global _state, _world_slug

    player_input = {
        "verb":      verb,
        "subject":   subject.strip(),
        "modifiers": modifiers.strip(),
        "items":     items or [],
    }

    mechanical_result = (
        {"type": "narrative", "mechanical": False}
        if bypass
        else interpret(_state, verb=verb, subject=subject.strip(),
                       modifiers=modifiers.strip(), items=items or [])
    )

    mech_display = _mechanic_display(mechanical_result)

    model_str = model_name.strip() or (
        "tinyllama" if backend == "ollama"
        else "mistralai/mistral-7b-instruct:free"
    )

    # Retrieve world context
    full_input = f"{verb} {subject} {modifiers}".strip()
    world_ctx  = retrieve_context(_world_slug, _state, full_input,
                                  _state.get("history", []))

    narration = narrate(
        _state,
        player_input=player_input,
        mechanical_result=mechanical_result,
        model=model_str,
        bypass=bypass,
        backend=backend,
        api_key=api_key,
        world_context=world_ctx,
    )

    # Tag referenced entities for recency buffer
    index = load_index(_world_slug) if _world_slug else []
    referenced = tag_referenced_entities(full_input, index)

    add_history(_state, player_input, mechanical_result, narration)
    # Attach entity tags to the last history entry
    if referenced and _state["history"]:
        _state["history"][-1]["entities_referenced"] = referenced

    if _state["turn"] % 8 == 0 and _state["turn"] > 0:
        _state["summary"] = summarize_history(
            _state, model=model_str, backend=backend, api_key=api_key
        )

    action_label = verb
    if subject.strip():
        action_label += f" → {subject.strip()}"
    if modifiers.strip():
        action_label += f" *({modifiers.strip()})*"
    if items:
        action_label += f" 🎒 {', '.join(items)}"

    bot_content = f"{mech_display}\n\n{narration}".strip() if mech_display else narration

    history = history or []
    history.append(ChatMessage(role="user",      content=action_label))
    history.append(ChatMessage(role="assistant", content=bot_content))

    return history, get_status(), gr.update(choices=get_inventory())


# ── Submission handlers ───────────────────────────────────────────────────────

def submit_structured(category, subcategory, subject, modifiers, items,
                      backend, model_name, api_key, history):
    verb = subcategory or category or ""
    if not verb:
        return history, get_status(), gr.update(choices=get_inventory())
    return _run_action(verb, subject, modifiers, items,
                       backend, model_name, api_key, history, bypass=False)


def submit_narrative(free_text, backend, model_name, api_key, history):
    if not free_text.strip():
        return history, get_status(), gr.update(choices=get_inventory())
    return _run_action(free_text.strip(), "", "", [],
                       backend, model_name, api_key, history, bypass=True)


def new_game_action(player_name):
    global _state
    _state = new_game(player_name.strip() or "Traveller")
    intro = (
        f"*A new adventure begins for **{_state['player']['name']}**...*\n\n"
        f"{_state['scene']['description']}"
    )
    return (
        [ChatMessage(role="assistant", content=intro)],
        get_status(),
        gr.update(choices=get_inventory()),
    )


def save_action():
    save_game(_state)
    return "✅ Game saved."


def load_action():
    global _state
    loaded = load_game()
    if loaded:
        _state = loaded
        return "✅ Game loaded.", get_status(), gr.update(choices=get_inventory())
    return "❌ No save file found.", get_status(), gr.update(choices=get_inventory())



# ══════════════════════════════════════════════════════════════════════════════
# WORLD TAB helpers
# ══════════════════════════════════════════════════════════════════════════════

def _world_list_md() -> str:
    worlds = list_worlds()
    if not worlds:
        return "*No worlds yet. Create one below.*"
    lines = []
    for w in worlds:
        active = " ✅" if w["slug"] == _world_slug else ""
        lines.append(f"**{w['title']}** ({w['genre']}){active}  —  `{w['slug']}`")
    return "\n\n".join(lines)


def _entity_table(world_slug: str, collection: str) -> list[list]:
    if not world_slug:
        return []
    rows = list_entities(world_slug, collection)
    return [[r["name"], r["importance"], r["status"], r["id"]] for r in rows]


# ── World creation ────────────────────────────────────────────────────────────

def do_create_world(title, genre_primary, genre_tags_str,
                    tone, setting, truths_str, narrator_voice):
    global _world_slug
    if not title.strip():
        return "❌ Title required.", _world_list_md()
    genre_tags = [t.strip() for t in genre_tags_str.split(",") if t.strip()]
    truths     = [t.strip() for t in truths_str.strip().split("\n") if t.strip()]
    slug = create_world(title.strip(), genre_primary, genre_tags,
                        tone, setting, truths, narrator_voice)
    _world_slug = slug
    return f"✅ World **{title}** created and set as active.", _world_list_md()


def do_load_world(slug: str):
    global _world_slug
    slug = slug.strip()
    if not slug:
        return "❌ Enter a world slug.", _world_list_md()
    master = load_master(slug)
    if not master:
        return f"❌ World '{slug}' not found.", _world_list_md()
    _world_slug = slug
    return f"✅ World **{master.get('title',slug)}** loaded.", _world_list_md()


def do_delete_world(slug: str):
    global _world_slug
    slug = slug.strip()
    if not slug:
        return "❌ Enter a world slug.", _world_list_md()
    delete_world(slug)
    if _world_slug == slug:
        _world_slug = ""
    return f"✅ World '{slug}' deleted.", _world_list_md()


def do_suggest_truths(genre_primary, tone, setting, truths_str,
                      model_name, backend, api_key):
    existing = [t.strip() for t in truths_str.strip().split("\n") if t.strip()]
    suggestions = suggest_truths(
        _world_slug, genre_primary, tone, setting, existing,
        model_name.strip() or "tinyllama", backend, api_key
    )
    if not suggestions:
        return truths_str, "❌ Could not generate suggestions."
    new_truths = existing + suggestions
    return "\n".join(new_truths), f"✅ Added {len(suggestions)} suggested truths."


# ── Entity editor ─────────────────────────────────────────────────────────────

def do_load_entity_list(collection):
    rows = _entity_table(_world_slug, collection)
    return gr.update(value=rows)


def do_open_entity(collection, evt: gr.SelectData):
    global _edit_entity, _edit_col
    if not _world_slug:
        return "{}", collection
    rows = _entity_table(_world_slug, collection)
    if evt.index[0] >= len(rows):
        return "{}", collection
    entity_id  = rows[evt.index[0]][3]
    _edit_entity = load_entity(_world_slug, collection, entity_id)
    _edit_col    = collection
    import json
    return json.dumps(_edit_entity, indent=2), collection


def do_save_entity_json(json_str, collection):
    global _edit_entity, _edit_col
    import json as _json
    try:
        data = _json.loads(json_str)
    except Exception as e:
        return f"❌ Invalid JSON: {e}", gr.update()
    save_entity(_world_slug, collection, data)
    _edit_entity = data
    _edit_col    = collection
    return "✅ Saved.", gr.update(value=_entity_table(_world_slug, collection))


def do_new_entity(collection, importance):
    global _edit_entity, _edit_col
    import json as _json
    _edit_entity = {
        "id": "",
        "name": "",
        "importance": importance,
        "record_type": collection[:-1],
    }
    _edit_col = collection
    return _json.dumps(_edit_entity, indent=2)


def do_delete_entity_btn(collection, json_str):
    import json as _json
    try:
        data = _json.loads(json_str)
        eid  = data.get("id", "")
        if eid:
            delete_entity(_world_slug, collection, eid)
    except Exception:
        pass
    return "✅ Deleted.", gr.update(value=_entity_table(_world_slug, collection)), "{}"


def do_generate_fields(json_str, collection, model_name, backend, api_key):
    import json as _json
    try:
        data = _json.loads(json_str)
    except Exception as e:
        return json_str, f"❌ Invalid JSON: {e}"

    fields = get_fillable_fields(collection, data)
    if not fields:
        return json_str, "ℹ️ No empty fields to fill."

    model_str = model_name.strip() or (
        "tinyllama" if backend == "ollama"
        else "mistralai/mistral-7b-instruct:free"
    )
    generated = generate_fields(
        _world_slug, collection, data, fields,
        _state.get("history", []), model_str, backend, api_key
    )
    if not generated:
        return json_str, "❌ Generation failed or returned nothing."

    for path, value in generated.items():
        set_nested(data, path, value)

    return _json.dumps(data, indent=2), f"✅ Generated {len(generated)} fields."


# ══════════════════════════════════════════════════════════════════════════════
# UI LAYOUT
# ══════════════════════════════════════════════════════════════════════════════

CSS = """
#main-col { display: flex; flex-direction: column; }
#chatbox  { flex: 1 1 auto; }
.input-area { border-top: 1px solid #ddd; padding-top: 10px; margin-top: 6px; }
"""

_first_category  = list(VERB_TREE.keys())[0]
_first_subs      = list(VERB_TREE[_first_category].keys())

with gr.Blocks(title="Solo TTRPG Alpha", css=CSS) as demo:

    gr.Markdown("# ⚔️ Solo TTRPG — Alpha")

    with gr.Tabs():

        # ════════════════════════════════════════════════════
        # TAB 1 — GAME
        # ════════════════════════════════════════════════════
        with gr.Tab("🎮 Game"):
            with gr.Row(equal_height=False):

                # Left sidebar
                with gr.Column(scale=1, min_width=240):
                    gr.Markdown("### ⚙️ Settings")
                    backend_radio = gr.Radio(
                        choices=["ollama", "openrouter"],
                        value="ollama", label="LLM Backend",
                    )
                    model_tb = gr.Textbox(
                        label="Model", value="tinyllama",
                        placeholder="tinyllama / mistral / llama3",
                    )
                    api_key_tb = gr.Textbox(
                        label="OpenRouter API Key",
                        placeholder="sk-or-... (blank for Ollama)",
                        type="password", value="",
                    )
                    gr.Markdown("---")
                    gr.Markdown("### 🗂️ Session")
                    player_name_tb = gr.Textbox(label="Character Name", value="Traveller")
                    new_btn  = gr.Button("🆕 New Game")
                    save_btn = gr.Button("💾 Save")
                    load_btn = gr.Button("📂 Load")
                    save_msg = gr.Markdown("")
                    gr.Markdown("---")
                    gr.Markdown("### 📊 Status")
                    status_md = gr.Markdown(get_status())

                # Main column
                with gr.Column(scale=3, elem_id="main-col"):
                    chatbot = gr.Chatbot(label="", height=440, elem_id="chatbox")

                    mode_radio = gr.Radio(
                        choices=["🎲 Structured Action", "💬 Narrative"],
                        value="🎲 Structured Action",
                        label="Input Mode", elem_classes=["input-area"],
                    )

                    with gr.Group(visible=True) as structured_panel:
                        with gr.Row():
                            category_dd = gr.Dropdown(
                                choices=list(VERB_TREE.keys()),
                                value=_first_category, label="Category", scale=1,
                            )
                            subcategory_dd = gr.Dropdown(
                                choices=_first_subs,
                                value=_first_subs[0] if _first_subs else None,
                                label="Action", scale=1,
                            )
                        action_hint = gr.Markdown(
                            value="*" + VERB_TREE[_first_category][_first_subs[0]].get("hint","") + "*"
                            if _first_subs else ""
                        )
                        with gr.Row():
                            subject_tb  = gr.Textbox(label="Subject / Target",
                                                      placeholder="who or what", scale=2)
                            modifier_tb = gr.Textbox(label="How / Modifiers",
                                                      placeholder="carefully, from behind…", scale=2)
                            items_dd    = gr.Dropdown(choices=get_inventory(),
                                                       multiselect=True, label="Items",
                                                       scale=1, value=[])
                        struct_submit = gr.Button("Take Action ▶", variant="primary")

                    with gr.Group(visible=False) as narrative_panel:
                        with gr.Row():
                            free_tb     = gr.Textbox(label="",
                                                      placeholder="Type what you do… (Enter to send)",
                                                      lines=2, scale=5)
                            narr_submit = gr.Button("Send ▶", variant="primary", scale=1)

            # Game tab events
            def switch_mode(mode):
                if mode == "💬 Narrative":
                    return gr.update(visible=False), gr.update(visible=True)
                return gr.update(visible=True), gr.update(visible=False)

            mode_radio.change(switch_mode, [mode_radio], [structured_panel, narrative_panel])

            def on_category_change(category):
                subs = list(VERB_TREE.get(category, {}).keys())
                if subs:
                    hint = VERB_TREE[category][subs[0]].get("hint", "")
                    return gr.update(choices=subs, value=subs[0]), gr.update(value=f"*{hint}*" if hint else "")
                return gr.update(choices=[], value=None), gr.update(value="")

            category_dd.change(on_category_change, [category_dd], [subcategory_dd, action_hint])

            def on_sub_change(category, sub):
                hint = VERB_TREE.get(category, {}).get(sub, {}).get("hint", "")
                return gr.update(value=f"*{hint}*" if hint else "")

            subcategory_dd.change(on_sub_change, [category_dd, subcategory_dd], [action_hint])

            def on_backend_change(backend):
                if backend == "openrouter":
                    return gr.update(value="mistralai/mistral-7b-instruct:free")
                return gr.update(value="tinyllama")

            backend_radio.change(on_backend_change, [backend_radio], [model_tb])

            struct_submit.click(
                fn=submit_structured,
                inputs=[category_dd, subcategory_dd, subject_tb, modifier_tb,
                        items_dd, backend_radio, model_tb, api_key_tb, chatbot],
                outputs=[chatbot, status_md, items_dd],
            )
            narr_submit.click(submit_narrative,
                [free_tb, backend_radio, model_tb, api_key_tb, chatbot],
                [chatbot, status_md, items_dd])
            free_tb.submit(submit_narrative,
                [free_tb, backend_radio, model_tb, api_key_tb, chatbot],
                [chatbot, status_md, items_dd])
            new_btn.click(new_game_action, [player_name_tb], [chatbot, status_md, items_dd])
            save_btn.click(save_action, outputs=[save_msg])
            load_btn.click(load_action, outputs=[save_msg, status_md, items_dd])

        # ════════════════════════════════════════════════════
        # TAB 2 — WORLD BIBLE
        # ════════════════════════════════════════════════════
        with gr.Tab("🌍 World Bible"):

            with gr.Row():
                # ── Left: world list + creation ──────────────────────────────
                with gr.Column(scale=1, min_width=280):

                    gr.Markdown("### 🌐 Worlds")
                    world_list_md = gr.Markdown(_world_list_md())

                    with gr.Row():
                        load_slug_tb = gr.Textbox(label="Load/Delete by slug",
                                                   placeholder="world-slug")
                        load_world_btn  = gr.Button("Load")
                        delete_world_btn = gr.Button("🗑️", variant="stop")

                    world_msg = gr.Markdown("")

                    gr.Markdown("---")
                    gr.Markdown("### ➕ Create New World")

                    wc_title    = gr.Textbox(label="Title", placeholder="The Hollow Meridian")
                    wc_genre    = gr.Dropdown(
                        choices=["fantasy","sci-fi","horror","historical",
                                 "contemporary","western","post-apocalyptic",
                                 "mythological","other"],
                        label="Primary Genre", value="fantasy"
                    )
                    wc_tags     = gr.Textbox(label="Genre Tags (comma separated)",
                                             placeholder="noir, biopunk")
                    wc_tone     = gr.Textbox(label="Tone",
                                             placeholder="grim, atmospheric, morally grey")
                    wc_setting  = gr.Textbox(label="Setting (2-4 sentences)",
                                             lines=3,
                                             placeholder="Far-future Earth, 200 years after…")
                    wc_truths   = gr.Textbox(label="Truths (one per line)",
                                             lines=5,
                                             placeholder="Consciousness can be digitized but the copy is never quite right.\nThe megacorps collapsed 40 years ago.")
                    wc_voice    = gr.Textbox(label="Narrator Voice (optional)",
                                             placeholder="Second person, present tense. Terse sentences.")

                    with gr.Row():
                        create_world_btn  = gr.Button("Create World ✨", variant="primary")
                        suggest_truth_btn = gr.Button("🎲 Suggest Truths")

                    suggest_msg = gr.Markdown("")

                # ── Right: entity database ───────────────────────────────────
                with gr.Column(scale=2):

                    gr.Markdown("### 📚 Entity Database")

                    collection_radio = gr.Radio(
                        choices=COLLECTIONS,
                        value="characters",
                        label="Collection",
                    )

                    entity_table = gr.Dataframe(
                        headers=["Name", "Importance", "Status", "ID"],
                        datatype=["str","str","str","str"],
                        interactive=False,
                        label="",
                        value=_entity_table(_world_slug, "characters"),
                    )

                    with gr.Row():
                        new_entity_imp = gr.Dropdown(
                            choices=["named","minor","archetype",
                                     "advanced","basic"],
                            value="named", label="Tier", scale=1
                        )
                        new_entity_btn  = gr.Button("➕ New Entry", scale=2)
                        dup_entity_btn  = gr.Button("📋 Duplicate", scale=1)
                        del_entity_btn  = gr.Button("🗑️ Delete", variant="stop", scale=1)

                    gr.Markdown("#### ✏️ Entity Editor")
                    gr.Markdown("*Click a row to open. Edit JSON directly, or use AI Assist.*")

                    entity_json = gr.Code(
                        label="Entity JSON",
                        language="json",
                        lines=22,
                        value="{}",
                    )

                    with gr.Row():
                        save_entity_btn  = gr.Button("💾 Save Entity", variant="primary")
                        gen_fields_btn   = gr.Button("✨ AI Fill Empty Fields")

                    entity_msg = gr.Markdown("")

            # ── World Bible events ────────────────────────────────────────────

            create_world_btn.click(
                fn=do_create_world,
                inputs=[wc_title, wc_genre, wc_tags, wc_tone,
                        wc_setting, wc_truths, wc_voice],
                outputs=[world_msg, world_list_md],
            )

            load_world_btn.click(
                fn=do_load_world,
                inputs=[load_slug_tb],
                outputs=[world_msg, world_list_md],
            )

            delete_world_btn.click(
                fn=do_delete_world,
                inputs=[load_slug_tb],
                outputs=[world_msg, world_list_md],
            )

            suggest_truth_btn.click(
                fn=do_suggest_truths,
                inputs=[wc_genre, wc_tone, wc_setting, wc_truths,
                        model_tb, backend_radio, api_key_tb],
                outputs=[wc_truths, suggest_msg],
            )

            collection_radio.change(
                fn=do_load_entity_list,
                inputs=[collection_radio],
                outputs=[entity_table],
            )

            entity_table.select(
                fn=do_open_entity,
                inputs=[collection_radio],
                outputs=[entity_json, collection_radio],
            )

            new_entity_btn.click(
                fn=do_new_entity,
                inputs=[collection_radio, new_entity_imp],
                outputs=[entity_json],
            )

            save_entity_btn.click(
                fn=do_save_entity_json,
                inputs=[entity_json, collection_radio],
                outputs=[entity_msg, entity_table],
            )

            del_entity_btn.click(
                fn=do_delete_entity_btn,
                inputs=[collection_radio, entity_json],
                outputs=[entity_msg, entity_table, entity_json],
            )

            gen_fields_btn.click(
                fn=do_generate_fields,
                inputs=[entity_json, collection_radio,
                        model_tb, backend_radio, api_key_tb],
                outputs=[entity_json, entity_msg],
            )


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7861, theme=gr.themes.Soft())

