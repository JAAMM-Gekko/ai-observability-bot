"""PostgreSQL + pgvector FAQ retrieval (matches extraction_updated_postgresql.py)."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import asyncpg
from openai import AsyncOpenAI

if TYPE_CHECKING:
    pass

RAG_OPENAI_EMBEDDING_MODEL = os.getenv("RAG_OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")


def default_database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://chatbot:chatbot_password@localhost:5432/chatbot_db",
    )


def default_tenant_id() -> uuid.UUID:
    return uuid.UUID(os.getenv("RAG_TENANT_ID", "704bd8d9-2791-4f6b-ba69-7f7cf065ba88"))


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(str(x) for x in vec) + "]"


async def embed_query(client: AsyncOpenAI, text: str) -> list[float]:
    resp = await client.embeddings.create(input=text, model=RAG_OPENAI_EMBEDDING_MODEL)
    return list(resp.data[0].embedding)


async def search_similar_chunks(
    pool: asyncpg.Pool,
    tenant_id: uuid.UUID,
    query_embedding: list[float],
    top_k: int = 3,
) -> list[str]:
    lit = vector_literal(query_embedding)
    rows = await pool.fetch(
        """
        SELECT c.chunk_text
        FROM embedding e
        INNER JOIN knowledge_base_chunk c ON c.document_chunk_id = e.document_chunk_id
        WHERE e.tenant_id = $1
        ORDER BY e.embedding <=> $2::vector
        LIMIT $3
        """,
        tenant_id,
        lit,
        top_k,
    )
    return [r["chunk_text"] for r in rows]


def chunk_text_to_qa(chunk_text: str) -> tuple[str, str]:
    """Chunk text is stored as 'Question: ...\\nAnswer: ...'."""
    text = (chunk_text or "").strip()
    if "\n" in text and "Question:" in text and "Answer:" in text:
        q_part, rest = text.split("Answer:", 1)
        question = q_part.replace("Question:", "", 1).strip()
        answer = rest.strip()
        return question or "N/A", answer or text
    return "N/A", text


async def count_chunks_for_tenant(pool: asyncpg.Pool, tenant_id: uuid.UUID) -> int:
    row = await pool.fetchrow(
        "SELECT COUNT(*) AS n FROM knowledge_base_chunk WHERE tenant_id = $1",
        tenant_id,
    )
    return int(row["n"]) if row else 0
