"""
Persona experiment agent module.

Provides a self-contained chat function for persona experiments that reuses
the existing RAG infrastructure (PostgreSQL + pgvector + OpenAI embeddings)
but applies a custom system prompt loaded from a persona config file.
"""

import json
import os
from pathlib import Path
from typing import Optional

import openai

from faq_vector_store import chunk_text_to_qa, embed_query, search_similar_chunks

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


async def _retrieve_faq_context_async(query: str, top_k: int = 5) -> str:
    """Retrieve relevant FAQ context from PostgreSQL using the shared RAG pool."""
    from agent import _openai_async_rag, _pg_pool, _rag_tenant_id, _setup_rag_system_async

    if _pg_pool is None or _openai_async_rag is None:
        await _setup_rag_system_async()

    from agent import _openai_async_rag as oai, _pg_pool as pool, _rag_tenant_id as tid
    if pool is None or oai is None:
        return ""

    try:
        qe = await embed_query(oai, query)
        chunk_texts = await search_similar_chunks(pool, tid, qe, top_k=top_k)
        if not chunk_texts:
            return ""
        blocks = []
        for chunk_text in chunk_texts:
            q, a = chunk_text_to_qa(chunk_text)
            blocks.append(f"Question: {q}\nAnswer: {a}")
        return "\n\n---\n\n".join(blocks)
    except Exception as e:
        print(f"[PersonaAgent] RAG retrieval error: {e}")
        return ""


from product_tool import is_product_query as _is_product_query


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

    When the user asks a product-related question, the product_tool is
    invoked to supply real inventory data to the LLM.
    """
    persona = load_persona(persona_id)
    if persona is None:
        return f"Persona '{persona_id}' not found."

    faq_context = await _retrieve_faq_context_async(query)

    system_prompt = persona["system_prompt"]
    if faq_context:
        system_prompt += (
            "\n\n### RETRIEVED FAQ CONTEXT (use this to answer when relevant):\n"
            + faq_context
        )

    # Product tool call: if user asks about products, search inventory
    product_context = ""
    if _is_product_query(query):
        from product_tool import run_product_tool_call
        product_context = run_product_tool_call(query)
        print(f"[PersonaAgent] Product tool triggered for query: {query[:60]}...")

    if product_context:
        system_prompt += (
            "\n\n### AVAILABLE PRODUCTS IN STORE (recommend ONLY from these):\n"
            + product_context
            + "\n\n### PRODUCT RECOMMENDATION RULES:\n"
            "1. Only recommend products listed above. Do not invent products.\n"
            "2. If the query is GENERAL (e.g. 'recommend me a vape', 'what edibles do you have'), "
            "briefly introduce ALL matching products as a short list with name, price, and one-line vibe. "
            "Then ask which one they'd like to know more about.\n"
            "3. If the query is SPECIFIC (e.g. 'tell me about the Acapulco Gold'), "
            "go into detail on that one product (vibe, format, who it's for).\n"
            "4. Always use the EXACT product name as listed so it can be matched."
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
