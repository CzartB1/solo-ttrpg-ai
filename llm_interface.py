"""
llm_interface.py
Handles communication with either:
  - Ollama  (local, free, requires ollama running)
  - OpenRouter (cloud, free tier available, requires API key)

BACKENDS:
  "ollama"     → local Ollama instance at localhost:11434
  "openrouter" → https://openrouter.ai (create a free account for an API key)

Recommended Ollama models:
  tinyllama / phi3 / gemma:2b   (small/fast)
  mistral / llama3              (better quality)

Recommended FREE OpenRouter models (no credits needed):
  mistralai/mistral-7b-instruct:free
  meta-llama/llama-3-8b-instruct:free
  google/gemma-3-12b-it:free
  nousresearch/hermes-3-llama-3.1-405b:free

Set your OpenRouter API key in OPENROUTER_API_KEY below,
or pass it through the UI.
"""

import requests
import json
from game_state import state_summary

# ── Config ────────────────────────────────────────────────────────────────────

OLLAMA_URL       = "http://localhost:11434/api/generate"
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = ""   # paste your key here, or enter it in the UI

DEFAULT_BACKEND  = "ollama"
DEFAULT_MODEL    = "tinyllama"


# ── Main narration call ───────────────────────────────────────────────────────

def narrate(state: dict, player_input: dict, mechanical_result: dict,
            model: str = DEFAULT_MODEL, bypass: bool = False,
            backend: str = DEFAULT_BACKEND, api_key: str = "") -> str:
    """
    Send game state + action + mechanical result to the LLM.
    Returns the narrator's prose response as a string.
    bypass=True skips the mechanical result and just narrates freely.
    """
    prompt = _build_prompt(state, player_input, mechanical_result, bypass)

    if backend == "openrouter":
        return _call_openrouter(prompt, model, api_key)
    else:
        return _call_ollama(prompt, model)


def summarize_history(state: dict, model: str = DEFAULT_MODEL,
                      backend: str = DEFAULT_BACKEND, api_key: str = "") -> str:
    """Compress older history into a short summary."""
    if not state["history"]:
        return ""

    history_text = "\n".join(
        f"Turn {h['turn']}: {h['narration']}"
        for h in state["history"]
    )
    prompt = (
        "You are a story archivist. Summarize the following TTRPG session events "
        "into 3-5 sentences, preserving key facts, decisions, and outcomes. "
        "Be concise but specific.\n\n"
        f"{history_text}\n\nSummary:"
    )

    if backend == "openrouter":
        return _call_openrouter(prompt, model, api_key, max_tokens=200, temperature=0.3)
    else:
        return _call_ollama(prompt, model, max_tokens=200, temperature=0.3)


# ── Backend calls ─────────────────────────────────────────────────────────────

def _call_ollama(prompt: str, model: str,
                 max_tokens: int = 300, temperature: float = 0.8) -> str:
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model":  model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "top_p":       0.9,
                    "num_predict": max_tokens,
                }
            },
            timeout=120,
        )
        response.raise_for_status()
        return response.json().get("response", "[No response from model]").strip()

    except requests.exceptions.ConnectionError:
        return (
            "[ERROR] Cannot connect to Ollama. "
            "Make sure Ollama is running ('ollama serve') and you have a model pulled "
            f"('ollama pull {model}')."
        )
    except Exception as e:
        return f"[ERROR] Ollama call failed: {e}"


def _call_openrouter(prompt: str, model: str, api_key: str,
                     max_tokens: int = 300, temperature: float = 0.8) -> str:
    key = api_key.strip() or OPENROUTER_API_KEY
    if not key:
        return (
            "[ERROR] No OpenRouter API key provided. "
            "Create a free account at https://openrouter.ai and paste your key in the UI."
        )
    try:
        response = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization":  f"Bearer {key}",
                "Content-Type":   "application/json",
                "HTTP-Referer":   "http://localhost:7860",   # required by OpenRouter
                "X-Title":        "Solo TTRPG Alpha",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens":  max_tokens,
                "temperature": temperature,
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

    except requests.exceptions.HTTPError as e:
        if response.status_code == 401:
            return "[ERROR] Invalid OpenRouter API key."
        if response.status_code == 429:
            return "[ERROR] OpenRouter rate limit hit. Wait a moment and try again."
        return f"[ERROR] OpenRouter HTTP error: {e}"
    except Exception as e:
        return f"[ERROR] OpenRouter call failed: {e}"


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(state: dict, player_input: dict,
                  result: dict, bypass: bool) -> str:

    game_ctx = state_summary(state)

    # Describe what the player tried to do
    verb     = player_input.get("verb", "")
    subject  = player_input.get("subject", "")
    mods     = player_input.get("modifiers", "")
    items    = player_input.get("items", [])

    action_desc = f"The player chose to: {verb}"
    if subject:
        action_desc += f" → target: {subject}"
    if mods:
        action_desc += f" → modifiers: {mods}"
    if items:
        action_desc += f" → using: {', '.join(items)}"

    # Describe the mechanical outcome (if any)
    if bypass or not result.get("mechanical"):
        mechanic_desc = "No mechanical resolution — narrate freely based on context."
    else:
        mechanic_desc = _format_result(result)

    # Previous summary if it exists
    summary_block = ""
    if state.get("summary"):
        summary_block = f"\n\nEARLIER SESSION SUMMARY:\n{state['summary']}"

    prompt = f"""You are the narrator of a solo tabletop RPG. Your job is to describe what happens next in vivid, atmospheric prose. Keep responses to 2-4 sentences. Do not invent new NPCs or major plot points unprompted. Stay consistent with the game state provided.

{game_ctx}{summary_block}

PLAYER ACTION:
{action_desc}

MECHANICAL OUTCOME:
{mechanic_desc}

Narrate the result in second person ("You..."). Be specific, immersive, and let the mechanical outcome drive the tone — a strong success should feel triumphant, a strong failure should sting:"""

    return prompt


def _format_result(result: dict) -> str:
    """Convert a mechanics result dict into readable text for the LLM."""
    rtype = result.get("type", "unknown")

    if rtype == "skill_check":
        return (
            f"Skill check ({result['skill']}): "
            f"Rolled {result['rolled']} + {result['bonus']} bonus = {result['total']} "
            f"vs difficulty {result['difficulty']}. "
            f"Result: {result['degree'].upper()}."
        )

    if rtype in ("stealth_check",):
        hidden = "Player is now hidden." if result.get("success") else "Player failed to hide."
        return (
            f"Stealth check: Rolled {result['rolled']} + {result['bonus']} = {result['total']} "
            f"vs {result['difficulty']}. {result['degree'].upper()}. {hidden}"
        )

    if rtype in ("persuasion_check",):
        disp = f" NPC disposition is now: {result.get('new_disposition', 'unchanged')}."
        return (
            f"{'Persuasion' if result['approach'] == 'persuasion' else 'Deception'} check "
            f"on {result['target']}: Rolled {result['rolled']} + {result['bonus']} = {result['total']} "
            f"vs {result['difficulty']}. {result['degree'].upper()}.{disp}"
        )

    if rtype == "attack":
        if result["hit"]:
            return (
                f"Attack with {result['weapon']} on {result['target']}: "
                f"Hit roll {result['hit_roll']} + {result['hit_bonus']} = {result['hit_total']} "
                f"vs {result['difficulty']}. HIT! Dealt {result['damage']} damage. "
                f"{result.get('status', '')}"
            )
        else:
            return (
                f"Attack with {result['weapon']} on {result['target']}: "
                f"Hit roll {result['hit_roll']} + {result['hit_bonus']} = {result['hit_total']} "
                f"vs {result['difficulty']}. MISS."
            )

    if rtype == "use_item":
        return f"Used {result['item']}: {result.get('effect', 'No effect.')}"

    if rtype == "examine":
        return (
            f"Examine {result['subject']}: "
            f"Perception roll {result['rolled']} + {result['bonus']} = {result['total']} "
            f"vs {result['difficulty']}. {result['degree'].upper()}. "
            f"Known info: {result.get('known_info', 'Nothing specific noted.')}"
        )

    if rtype == "rest":
        return f"Short rest: Recovered {result['hp_gain']} HP. {result['status']}"

    return json.dumps(result, indent=2)
