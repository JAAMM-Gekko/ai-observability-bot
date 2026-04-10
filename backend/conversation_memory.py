"""
Sliding Window + Compression Memory for multi-turn conversation context.

Each session maintains:
- A fixed-size window of recent (user, assistant) turns kept verbatim.
- A running LLM-generated summary that incrementally absorbs turns evicted
  from the window, so the model always has access to older context without
  unbounded token growth.
"""

import os
from collections import deque
from typing import Dict, Tuple, Optional

from openai import AsyncOpenAI

_aclient: Optional[AsyncOpenAI] = None


def _get_aclient() -> AsyncOpenAI:
    global _aclient
    if _aclient is None:
        _aclient = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _aclient


WINDOW_SIZE = int(os.getenv("MEMORY_WINDOW_SIZE", "5"))
SUMMARY_MAX_TOKENS = int(os.getenv("MEMORY_SUMMARY_MAX_TOKENS", "300"))
COMPRESS_MODEL = os.getenv("MEMORY_COMPRESS_MODEL", "gpt-4o-mini")


class _SessionMemory:
    __slots__ = ("summary", "recent_turns", "summary_turn_count")

    def __init__(self, window_size: int = WINDOW_SIZE) -> None:
        self.summary: str = ""
        self.recent_turns: deque[Tuple[str, str]] = deque(maxlen=window_size)
        self.summary_turn_count: int = 0


class ConversationMemoryManager:
    """Per-session sliding-window memory with incremental LLM compression."""

    def __init__(self, window_size: int = WINDOW_SIZE) -> None:
        self.window_size = window_size
        self._sessions: Dict[str, _SessionMemory] = {}

    def _ensure_session(self, session_id: str) -> _SessionMemory:
        if session_id not in self._sessions:
            self._sessions[session_id] = _SessionMemory(window_size=self.window_size)
        return self._sessions[session_id]

    async def add_turn(
        self, session_id: str, user_msg: str, bot_response: str
    ) -> None:
        """Append a turn.  If the window is already full the oldest turn is
        evicted and folded into the running summary."""
        mem = self._ensure_session(session_id)

        evicted = None
        if len(mem.recent_turns) == self.window_size:
            evicted = mem.recent_turns[0]  # will be popped by deque maxlen

        mem.recent_turns.append((user_msg, bot_response))

        if evicted is not None:
            await self._compress(mem, evicted)

    async def get_context(self, session_id: str) -> str:
        """Return a formatted string containing the summary (if any) and the
        recent conversation window, ready for prompt injection."""
        mem = self._sessions.get(session_id)
        if mem is None:
            return ""

        parts: list[str] = []

        if mem.summary:
            parts.append(
                f"Summary of earlier conversation ({mem.summary_turn_count} turns):\n"
                f"{mem.summary}"
            )

        if mem.recent_turns:
            lines: list[str] = []
            for user_msg, bot_msg in mem.recent_turns:
                lines.append(f"User: {user_msg}")
                lines.append(f"Assistant: {bot_msg}")
            parts.append("Recent conversation:\n" + "\n".join(lines))

        return "\n\n".join(parts)

    def reset(self, session_id: str) -> None:
        """Clear all memory for a session."""
        self._sessions.pop(session_id, None)

    def get_stats(self, session_id: str) -> dict:
        """Return lightweight stats useful for OTEL span attributes."""
        mem = self._sessions.get(session_id)
        if mem is None:
            return {"window_turns": 0, "summary_turns": 0, "summary_len": 0}
        return {
            "window_turns": len(mem.recent_turns),
            "summary_turns": mem.summary_turn_count,
            "summary_len": len(mem.summary),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    async def _compress(mem: _SessionMemory, evicted: Tuple[str, str]) -> None:
        """Fold *evicted* turn into the running summary via an LLM call."""
        evicted_text = f"User: {evicted[0]}\nAssistant: {evicted[1]}"

        if mem.summary:
            prompt = (
                "You are a conversation summarizer. Below is an existing summary of "
                "an ongoing customer-support chat, followed by a new exchange that must "
                "be incorporated.\n\n"
                f"Existing summary:\n{mem.summary}\n\n"
                f"New exchange to incorporate:\n{evicted_text}\n\n"
                "Produce an updated summary that is concise (under 150 words), "
                "preserves all key topics, product names, and unanswered questions."
            )
        else:
            prompt = (
                "You are a conversation summarizer. Summarize the following "
                "customer-support exchange in under 100 words. Preserve key topics, "
                "product names, and any unanswered questions.\n\n"
                f"{evicted_text}"
            )

        try:
            client = _get_aclient()
            response = await client.chat.completions.create(
                model=COMPRESS_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=SUMMARY_MAX_TOKENS,
            )
            mem.summary = response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[ConversationMemory] Compression failed: {e}")
            mem.summary += f"\n[Turn {mem.summary_turn_count + 1}] {evicted_text}"

        mem.summary_turn_count += 1
