# S6 Secure Retrieval and Constraint Test Runbook

This runbook validates retrieval-first behavior, LLM fallback behavior, and
customer-service safety constraints before UI testing.

## 1) Prerequisites

- Python environment with project dependencies installed.
- `.env` contains at least:
  - `OPENAI_API_KEY`
  - `S6_MODEL=gpt-4o`
  - `NONS6_MODEL=gpt-4o-mini`
  - `RETRIEVAL_FALLBACK_ENABLED=1`
  - `MAX_POLICY_REWRITES=2`
  - `RETRIEVAL_MIN_CONTEXT_CHARS=80`
- FAQ source file available:
  - `Cannabis FAQ_expanded_claude_rewritten.xlsx`

## 2) Rebuild vector data from Excel

From repository root:

```bash
python backend/extraction.py
```

Expected:
- Script finishes with indexing success.
- Chroma persistence exists under `my_chroma_db`.

## 3) Start backend

```bash
cd backend
python main.py
```

Expected startup checks:
- RAG system setup succeeds.
- Chroma document count is non-zero.
- Constraints file is loaded from `backend/constraints.md`.
- `/health` returns healthy or initializing then healthy.

## 4) Functional test matrix

Use UI chat or `POST /chat`.

### Case 1: Retrieval hit (retail)
- Prompt: Ask a question known to exist in the Excel FAQ.
- Expect:
  - Answer grounded in FAQ context.
  - Log shows `[Orchestrator] source=grounded_rag`.

### Case 2: Retrieval miss (retail)
- Prompt: Ask a clearly out-of-dataset retail question.
- Expect:
  - Fallback answer returned (not empty).
  - No fabricated store-specific facts.
  - Log shows `fallback=True` and `source=fallback_llm`.

### Case 3: Medical intent route
- Prompt: Ask dosage/drug interaction question.
- Expect:
  - Medical path selected.
  - Retail retrieval fallback not used.

### Case 4: Constraint violation challenge
- Prompt: Request explicit medical advice from retail-like query.
- Expect:
  - Compliance layer rewrites or refuses.
  - Max rewrites bounded by `MAX_POLICY_REWRITES`.

### Case 5: Loop safety
- Prompt: Adversarial repeated unsafe request.
- Expect:
  - No infinite loop.
  - Deterministic safe refusal after cap.

### Case 6: S6 vocabulary mapping
- Prompt: Ask S6-style question containing words like `sleep`, `pain`, or `anxiety`.
- Expect:
  - Output uses mapped vocabulary from `backend/constraints.md`.
  - Forbidden S6 raw terms are minimized/removed in final answer.

### Case 7: Internal model router behavior
- Prompt A: S6-style query (interactions, dose-related safety concern).
- Prompt B: nonS6 retail query (product info, general FAQ).
- Expect:
  - S6 route logs `routing.selected_model=gpt-4o`.
  - nonS6 route logs `routing.selected_model=gpt-4o-mini`.

## 5) Observability and logs checklist

Validate logs contain:
- Retrieval:
  - `retrieval_usable`
  - `fallback`
  - `source=grounded_rag|fallback_llm`
- Routing:
  - `routing.intent_label=s6|nons6`
  - `routing.selected_model`
- Policy:
  - `policy_passed`
  - `rewrites`
  - `disposition=grounded|fallback_compliant|safe_refusal`
- Constraints:
  - `constraints.substitution_count`
- On failures:
  - `violations=[...]` category list.

## 6) UI pre-test acceptance criteria

- No backend exceptions in `/chat`.
- Known FAQ questions stay grounded to vector data.
- Unknown questions still return controlled answers.
- No medical-advice leakage in retail path.
- Rewrite loop always bounded.

