# Solo TTRPG Alpha

A local-LLM powered solo tabletop RPG engine.  
No cloud APIs, no subscription. Runs entirely on your machine.

---

## What It Does

- Structured action input: **Verb → Subject → Modifiers → Items**
- Deterministic dice rolls and rules resolution (no AI guessing at math)
- Local LLM (via Ollama) handles all narration
- Character sheet, inventory, and scene state tracked persistently
- Save/load support
- Manual bypass toggle for pure narrative moments

---

## Setup

### 1. Install Ollama
Download from https://ollama.com and install it.

Then open a terminal and run:
```bash
ollama serve
```

Pull a model (pick one):
```bash
ollama pull mistral        # recommended — fast and good
ollama pull llama3         # stronger narrative quality, slower
ollama pull openhermes     # good for roleplay flavor
```

### 2. Install Python dependencies
Requires Python 3.10+

```bash
pip install -r requirements.txt
```

### 3. Run the game
```bash
python app.py
```

Then open your browser at: **http://localhost:7860**

---

## How to Play

1. **Verb** — Choose what kind of action from the dropdown
2. **Subject** — Type who or what you're acting on
3. **Modifiers** — Optional: add flavor like "carefully" or "from behind"
4. **Items Used** — Select items from your inventory if relevant
5. Hit **Take Action**

The game will show you the dice result, then the narrator's prose.

Use **Bypass Mechanics** if you want a purely narrative moment with no roll.

---

## File Structure

```
app.py           — Gradio UI
game_state.py    — Character sheet, scene, history management
mechanics.py     — All dice rolls and rules functions
interpreter.py   — Routes verb input to the right mechanic
llm_interface.py — Ollama API calls for narration
requirements.txt — Python dependencies
savegame.json    — Created when you save (auto)
```

---

## Changing the Model

In the UI, edit the **Ollama Model** field to any model you have pulled.
Or change `DEFAULT_MODEL` in `llm_interface.py`.

---

## Extending It

- Add new verbs: edit `VERBS` list in `interpreter.py` and add a branch in `interpret()`
- Add new items: edit `_weapon_damage()` and the item effect block in `mechanics.py`
- Add new NPCs or locations: edit `new_scene()` in `game_state.py`
- Adjust difficulty: tweak the `difficulty` defaults in `interpreter.py`
