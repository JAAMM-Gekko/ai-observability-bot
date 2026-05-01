"""
Persona experiment agent module.

Provides a self-contained chat function for persona experiments that reuses
the existing RAG infrastructure (ChromaDB + embeddings) but applies a custom
system prompt loaded from a persona config file.
"""

import json
import os
from pathlib import Path
from typing import Optional

import openai

PERSONAS_DIR = Path(__file__).resolve().parent.parent / "personas"

_persona_cache: dict[str, dict] = {}


def load_persona(persona_id: str) -> Optional[dict]:
    """Load and cache a persona config from the personas/ directory."""
    if persona_id in _persona_cache:
        return _persona_cache[persona_id]

    config_path = PERSONAS_DIR / f"{persona_id}.json"
    if not config_path.exists():
        return None

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    _persona_cache[persona_id] = config
    return config


def clear_persona_cache():
    """Clear cached persona configs (useful after editing a persona file)."""
    _persona_cache.clear()


def _retrieve_faq_context(query: str, top_k: int = 5) -> str:
    """Retrieve relevant FAQ context from ChromaDB using the shared RAG components."""
    from agent import _embedding_model, _chroma_collection, _setup_rag_system

    if _embedding_model is None or _chroma_collection is None:
        _setup_rag_system()

    from agent import _embedding_model, _chroma_collection
    if _embedding_model is None or _chroma_collection is None:
        return ""

    try:
        query_embedding = _embedding_model.encode(query).tolist()
        results = _chroma_collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
        )
        documents = results.get("documents", [[]])[0]
        if not documents:
            return ""
        return "\n\n---\n\n".join(documents)
    except Exception as e:
        print(f"[PersonaAgent] RAG retrieval error: {e}")
        return ""


async def run_persona_chat(
    query: str,
    persona_id: str,
    session_id: str = "",
    conversation_history: list[dict] | None = None,
) -> str:
    """
    Run a chat turn using the specified persona.

    Uses the persona's system prompt with RAG-retrieved FAQ context,
    calling OpenAI directly for simplicity and isolation.
    """
    persona = load_persona(persona_id)
    if persona is None:
        return f"Persona '{persona_id}' not found."

    faq_context = _retrieve_faq_context(query)

    system_prompt = persona["system_prompt"]
    if faq_context:
        system_prompt += (
            "\n\n### RETRIEVED FAQ CONTEXT (use this to answer when relevant):\n"
            + faq_context
        )

    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    if conversation_history:
        messages.extend(conversation_history)

    messages.append({"role": "user", "content": query})

    model = persona.get("model", "gpt-4o")

    try:
        client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
            max_tokens=600,
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        print(f"[PersonaAgent] OpenAI call failed: {e}")
        return "Sorry, I'm having trouble right now. Try again in a sec?"
