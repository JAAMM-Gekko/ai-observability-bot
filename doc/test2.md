# Test2 Summary: Customer Scenario Evaluation

## Scope

Based on `Cannabis FAQ_expanded_claude_rewritten.xlsx`, I generated and executed 20 customer-style prompts across mixed scenarios:

- In-FAQ product education
- Not-in-FAQ store operations
- S6/medical safety prompts
- WA law/compliance prompts
- Prompt-injection attempt
- Mixed FAQ + unknown follow-up

Artifacts:
- Questions: `doc/test2_questions.json`
- Raw results: `doc/test2_results.json`

## Execution

- Endpoint tested: `POST /chat`
- Environment: local backend (venv active)
- Total cases: **20**
- Runtime errors: **0**
- Duration: **65.3s**

## High-Level Outcome

The chatbot performs strongly on FAQ grounding and S6 safety refusal behavior, but still shows weakness in deterministic legal/compliance answers and one potential hallucination in store-policy style content.

### Category-level readout

- **in_chroma_faq (5/5)**: Good grounding signals in all cases.
- **not_in_chroma_store_ops (5/5 executed)**:
  - 3 with safe unknown-handling style.
  - 2 flagged as potential hallucination risk.
- **s6_medical_safety (5/5)**: Strong safety-refusal signal.
- **law_compliance (3/3 executed)**: All 3 flagged as weak legal guidance (too generic “check with store/law”).
- **prompt_injection (1/1)**: Refusal behavior observed.
- **mixed_followup (1/1)**: Executed successfully.

## What the chatbot did well

1. **FAQ Retrieval Behavior**
   - Core educational questions about salve, decarb, full-spectrum, and sublingual use were answered with relevant content.

2. **S6 Safety Posture**
   - For dosage/interaction/minor-risk style prompts, responses consistently avoided explicit dosing and redirected to medical professionals.

3. **Robustness**
   - All 20 requests completed without runtime/API errors.

## Key Gaps Found

1. **Store-policy hallucination risk (medium)**
   - Case `t2_009` produced a specific return-policy style answer that may not be grounded in current FAQ/store source of truth.

2. **Legal compliance answers not deterministic (high for regulated use)**
   - Cases `t2_016`, `t2_017`, `t2_018` were too generic and did not explicitly provide the WA rule outcome when asked directly.

3. **Unknown handling wording inconsistency (low-medium)**
   - Some not-in-FAQ responses are safe in intent but inconsistent in explicitly saying “cannot confirm from FAQ.”

## Cases to review first

- `t2_009` (possible store-policy hallucination)
- `t2_016`, `t2_017`, `t2_018` (law/compliance weakness)

## Recommended Next Actions

1. Add a strict “not-in-FAQ policy” response template for store ops (hours/inventory/returns/promotions) to prevent accidental fabrication.
2. Add legal-response templates for direct WA compliance questions (coupon/giveaway, signage, out-of-state targeting) with explicit rule outcomes.
3. Extend regression assertions so legal prompts require deterministic compliance terms instead of generic redirection.
4. Re-run this same 20-case suite after patching and compare deltas in:
   - legal-compliance quality,
   - hallucination risk count,
   - unknown-handling consistency.

