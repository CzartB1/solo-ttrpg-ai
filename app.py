"""
app.py
Gradio frontend for the solo TTRPG engine.
Run with: python app.py
Then open http://localhost:7860 in your browser.

Requirements:
  pip install gradio requests

And Ollama must be running locally with a model pulled:
  ollama serve
  ollama pull mistral
"""

import gradio as gr
from gradio import ChatMessage
from game_state import new_game, save_game, load_game, state_summary
from interpreter import interpret, VERBS
from llm_interface import narrate, summarize_history

# ── Global state (single-session for alpha) ───────────────────────────────────
# In a real app this would be per-user session storage.

_state = new_game("Traveller")


def get_inventory() -> list[str]:
    return _state["player"]["inventory"]


def get_status() -> str:
    p  = _state["player"]
    sc = _state["scene"]
    cond = ", ".join(p["conditions"]) if p["conditions"] else "none"
    return (
        f"**{p['name']}** | HP: {p['hp']}/{p['hp_max']} | "
        f"Gold: {p['gold']} | Conditions: {cond}\n\n"
        f"📍 **{sc['location']}**\n{sc['description']}"
    )


def submit_action(verb, subject, modifiers, items, bypass_toggle, backend, model_name, api_key, history):
    global _state

    if not verb:
        return history, get_status(), gr.update(choices=get_inventory())

    player_input = {
        "verb":      verb,
        "subject":   subject.strip(),
        "modifiers": modifiers.strip(),
        "items":     items if items else [],
    }

    # ── 1. Interpret & run mechanics ──────────────────────────────────────────
    if bypass_toggle:
        mechanical_result = {"type": "narrative", "mechanical": False}
    else:
        mechanical_result = interpret(
            _state,
            verb=verb,
            subject=subject.strip(),
            modifiers=modifiers.strip(),
            items=items if items else [],
        )

    # ── 2. Build mechanic display string ──────────────────────────────────────
    mech_display = _mechanic_display(mechanical_result)

    # ── 3. Narrate ────────────────────────────────────────────────────────────
    model_str = model_name.strip() or (
        "tinyllama" if backend == "ollama"
        else "mistralai/mistral-7b-instruct:free"
    )
    narration = narrate(
        _state,
        player_input=player_input,
        mechanical_result=mechanical_result,
        model=model_str,
        bypass=bypass_toggle,
        backend=backend,
        api_key=api_key,
    )

    # ── 4. Update history ─────────────────────────────────────────────────────
    from game_state import add_history
    add_history(_state, player_input, mechanical_result, narration)

    # Periodic summarization (every 8 turns)
    if _state["turn"] % 8 == 0 and _state["turn"] > 0:
        _state["summary"] = summarize_history(
            _state, model=model_str, backend=backend, api_key=api_key
        )

    # ── 5. Format chat message ────────────────────────────────────────────────
    action_str = f"**{verb}** {subject}"
    if modifiers:
        action_str += f" *({modifiers})*"
    if items:
        action_str += f" — using: {', '.join(items)}"

    user_msg  = action_str
    bot_msg   = f"{mech_display}\n\n{narration}" if mech_display else narration

    history = history or []
    history.append(ChatMessage(role="user",      content=user_msg))
    history.append(ChatMessage(role="assistant", content=bot_msg))

    return history, get_status(), gr.update(choices=get_inventory())


def new_game_action(player_name):
    global _state
    _state = new_game(player_name.strip() or "Traveller")
    intro = (
        f"*A new adventure begins for {_state['player']['name']}...*\n\n"
        f"{_state['scene']['description']}"
    )
    return [ChatMessage(role="assistant", content=intro)], get_status(), gr.update(choices=get_inventory())


def save_action():
    save_game(_state)
    return "Game saved."


def load_action():
    global _state
    loaded = load_game()
    if loaded:
        _state = loaded
        return "Game loaded.", get_status(), gr.update(choices=get_inventory())
    return "No save file found.", get_status(), gr.update(choices=get_inventory())


def _mechanic_display(result: dict) -> str:
    """Short mechanical readout shown above the narration."""
    if not result.get("mechanical"):
        return ""

    rtype = result.get("type", "")

    if rtype == "attack":
        if result["hit"]:
            return f"🎲 `Hit! Roll: {result['hit_roll']}+{result['hit_bonus']}={result['hit_total']} vs {result['difficulty']} | Damage: {result['damage']}`"
        else:
            return f"🎲 `Miss. Roll: {result['hit_roll']}+{result['hit_bonus']}={result['hit_total']} vs {result['difficulty']}`"

    if rtype in ("skill_check", "stealth_check", "persuasion_check"):
        icon = "✅" if result["success"] else "❌"
        return f"🎲 `{icon} {result['degree'].title()} | Roll: {result['rolled']}+{result['bonus']}={result['total']} vs {result['difficulty']}`"

    if rtype == "use_item":
        return f"🎒 `{result['item']}: {result.get('effect','')}`"

    if rtype == "examine":
        icon = "✅" if result["success"] else "❌"
        return f"🔍 `{icon} {result['degree'].title()} | Roll: {result['rolled']}+{result['bonus']}={result['total']} vs {result['difficulty']}`"

    if rtype == "rest":
        return f"💤 `Rested. +{result['hp_gain']} HP | {result['status']}`"

    return ""


# ── Build UI ──────────────────────────────────────────────────────────────────

with gr.Blocks(title="Solo TTRPG Alpha") as demo:

    gr.Markdown("# ⚔️ Solo TTRPG — Alpha")
    gr.Markdown("*A local-LLM powered solo roleplaying engine. Powered by Ollama.*")

    with gr.Row():

        # ── Left column: action form ──────────────────────────────────────────
        with gr.Column(scale=1):
            gr.Markdown("### Your Action")

            verb_dd  = gr.Dropdown(
                choices=VERBS,
                label="Verb",
                value="Examine",
            )
            subject_tb = gr.Textbox(
                label="Subject / Target",
                placeholder="e.g. 'the merchant', 'locked door'",
            )
            modifier_tb = gr.Textbox(
                label="Modifiers",
                placeholder="e.g. 'carefully', 'from behind', 'loudly'",
            )
            items_dd = gr.Dropdown(
                choices=get_inventory(),
                multiselect=True,
                label="Items Used",
                value=[],
            )

            bypass_cb = gr.Checkbox(
                label="🎭 Bypass mechanics (pure narrative)",
                value=False,
            )

            submit_btn = gr.Button("Take Action", variant="primary")

            gr.Markdown("---")
            gr.Markdown("### Settings")

            backend_radio = gr.Radio(
                choices=["ollama", "openrouter"],
                value="ollama",
                label="LLM Backend",
            )
            model_tb = gr.Textbox(
                label="Model",
                value="tinyllama",
                placeholder="Ollama: tinyllama, mistral | OpenRouter: mistralai/mistral-7b-instruct:free",
            )
            api_key_tb = gr.Textbox(
                label="OpenRouter API Key (leave blank for Ollama)",
                placeholder="sk-or-...",
                type="password",
                value="",
            )

            def on_backend_change(backend):
                if backend == "openrouter":
                    return gr.update(value="mistralai/mistral-7b-instruct:free",
                                     placeholder="e.g. mistralai/mistral-7b-instruct:free | meta-llama/llama-3-8b-instruct:free")
                else:
                    return gr.update(value="tinyllama",
                                     placeholder="e.g. tinyllama / phi3 / mistral / llama3")

            backend_radio.change(on_backend_change, inputs=[backend_radio], outputs=[model_tb])

            gr.Markdown("---")
            gr.Markdown("### New / Save / Load")

            player_name_tb = gr.Textbox(
                label="Character Name",
                value="Traveller",
            )
            new_btn  = gr.Button("New Game")
            save_btn = gr.Button("Save Game")
            load_btn = gr.Button("Load Game")
            save_msg = gr.Markdown("")

        # ── Right column: story + status ──────────────────────────────────────
        with gr.Column(scale=2):
            gr.Markdown("### Story")
            chatbot = gr.Chatbot(
                label="",
                height=500,
            )

            gr.Markdown("### Status")
            status_md = gr.Markdown(get_status())

    # ── Wire up events ────────────────────────────────────────────────────────

    submit_btn.click(
        fn=submit_action,
        inputs=[verb_dd, subject_tb, modifier_tb, items_dd,
                bypass_cb, backend_radio, model_tb, api_key_tb, chatbot],
        outputs=[chatbot, status_md, items_dd],
    )

    new_btn.click(
        fn=new_game_action,
        inputs=[player_name_tb],
        outputs=[chatbot, status_md, items_dd],
    )

    save_btn.click(fn=save_action, outputs=[save_msg])

    load_btn.click(
        fn=load_action,
        outputs=[save_msg, status_md, items_dd],
    )


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7861, theme=gr.themes.Soft())
