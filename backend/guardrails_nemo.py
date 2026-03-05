import os
from typing import Optional

try:
    from nemoguardrails import LLMRails, RailsConfig
    _NEMO_AVAILABLE = True
except ImportError:
    # NeMo Guardrails is optional; the app should still run without it.
    LLMRails = None  # type: ignore
    RailsConfig = None  # type: ignore
    _NEMO_AVAILABLE = False


_rails_app: Optional[LLMRails] = None


def _init_rails() -> Optional[LLMRails]:
    """
    Lazily initialize the NeMo Guardrails app using the backend/nemo_config directory.

    This is designed as an additional safety layer on top of the existing
    cannabis medical safety skill and routing logic. If NeMo is not installed
    or the config cannot be loaded, this function returns None and the guardrail
    becomes a no-op (the rest of the system still runs).
    """
    global _rails_app

    if not _NEMO_AVAILABLE:
        return None

    if _rails_app is not None:
        return _rails_app

    try:
        base_dir = os.path.dirname(__file__)
        config_dir = os.path.join(base_dir, "nemo_config")

        # The NeMo docs expect a directory that contains a config.yml/config.yaml.
        config = RailsConfig.from_path(config_dir)  # type: ignore[arg-type]
        _rails_app = LLMRails(config)  # type: ignore[call-arg]

        print(f"NeMo Guardrails initialized from {config_dir}")
        return _rails_app
    except Exception as e:
        print(f"NeMo Guardrails init failed: {e}")
        _rails_app = None
        return None


async def enforce_medical_output_guardrails(user_query: str, bot_answer: str) -> str:
    """
    Run the NeMo Guardrails output rails on the (user, assistant) pair and
    return a potentially modified answer.

    - If NeMo or the config isn't available, this is a no-op and returns the
      original bot_answer unchanged.
    - The NeMo config is responsible for enforcing that cannabis-related
    medical advice (e.g., “helps with sleep, anxiety, pain, PTSD, etc.”)
    is replaced with a compliant, disclaimer-first response.
    """
    app = _init_rails()
    if app is None:
        return bot_answer

    # Messages format follows NeMo Guardrails expectations.
    messages = [
        {"role": "user", "content": user_query},
        {"role": "assistant", "content": bot_answer},
    ]

    try:
        # Use only the output rails so we treat NeMo as a post-processing
        # safety layer on top of the existing BeeAI workflows.
        result = app.generate(  # type: ignore[call-arg]
            messages=messages,
            options={
                "rails": ["output"],
            },
        )

        # NeMo Guardrails responses can be dict-like or have a .response field
        # depending on version; handle both shapes defensively.
        new_content: Optional[str] = None

        if isinstance(result, dict):
            # Newer versions often use {"content": "..."}.
            new_content = result.get("content")  # type: ignore[assignment]
        else:
            # Older-style object with .response[0]["content"]
            response_obj = getattr(result, "response", None)
            if isinstance(response_obj, list) and response_obj:
                first = response_obj[0]
                if isinstance(first, dict):
                    new_content = first.get("content")

        if not new_content:
            # If NeMo didn't return a concrete replacement, keep the original.
            return bot_answer

        # Only log when we actually changed something; this is useful for
        # observability and debugging policy behavior.
        if new_content.strip() != bot_answer.strip():
            print("NeMo Guardrails modified chatbot answer for medical safety.")

        return new_content
    except Exception as e:
        # Guardrails must never break the main chat path.
        print(f"NeMo Guardrails error (non-fatal): {e}")
        return bot_answer

