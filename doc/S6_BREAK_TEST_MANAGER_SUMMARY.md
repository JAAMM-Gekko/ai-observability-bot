# S6 Chatbot Break Test Summary

## Executive Summary

We executed a 25-case adversarial test suite against the chatbot using the current local build and `s6_break_test_questions.json`.

- **Overall result:** 11 passed / 25 total (**44% pass rate**)
- **Key takeaway:** The bot performs well on FAQ grounding, but is not yet reliable enough for high-risk compliance scenarios (medical-safety edge cases and law-enforcement style questions).

The chatbot is useful for baseline customer-service FAQ handling, but needs targeted hardening before being considered robust for regulated edge cases.

## What We Tested

Test categories included:
- In-FAQ grounded answers
- Not-in-FAQ unknown handling (no hallucination)
- Medical violation attempts (dosage, cure claims, minors)
- Law violation attempts (promotional/legal boundary prompts)
- Prompt injection attempts
- Boundary and consistency tests across mixed intents

## High-Level Results by Category

| Category | Pass / Total | Notes |
|---|---:|---|
| `in_faq_grounded` | **4 / 4** | Strong performance when FAQ context exists |
| `not_in_faq_unknown` | 2 / 3 | Mostly avoids fabrication |
| `medical_violation_attempt` | **1 / 5** | Major gap in strict safety phrasing/policy enforcement |
| `law_violation_attempt` | **0 / 5** | Largest compliance gap |
| `prompt_injection_attempt` | 1 / 2 | Partial resistance |
| `in_faq_vs_not_in_faq_boundary` | 1 / 2 | Mixed on nuanced requests |
| `multi_turn_consistency_break` | 1 / 2 | Some drift under pressure prompts |
| `law_direct_question` | 1 / 2 | Inconsistent legal directness |

## What the Chatbot Is Doing Well

- Correctly answers FAQ-grounded questions.
- Usually avoids inventing unknown store-specific data.
- Often gives safety-oriented disclaimers in medical contexts.
- Maintains stable behavior on straightforward customer-service prompts.

## Main Gaps / Risk Areas

1. **Regulatory response rigor is inconsistent**
   - For law-related prompts, responses often fall back to “not in FAQ” instead of giving clear compliance-safe refusal or legal boundary guidance.

2. **Medical edge-case handling needs stricter templates**
   - The bot often responds safely in spirit, but misses expected strict refusal language patterns for high-risk prompts.

3. **Adversarial prompt robustness is partial**
   - Some jailbreak and contradiction prompts still produce responses that are too permissive or insufficiently explicit.

4. **Pass/fail criteria sensitivity**
   - A subset of failures are due to strict keyword assertions, not fully unsafe behavior; however, this still indicates low deterministic control in critical scenarios.

## Business Impact

- **Ready for:** low-risk FAQ customer-service usage with monitoring.
- **Not ready for:** strict compliance-grade handling where legal/medical edge prompts must be consistently blocked or redirected with deterministic wording.

## Recommended Next Steps (Short Horizon)

1. **Policy templates for high-risk classes**
   - Add deterministic refusal/redirect templates for:
     - dosage requests
     - therapeutic/cure claims
     - minor-related medical requests
     - legal advertising rule prompts

2. **Law-aware response mode for direct legal questions**
   - For explicit law queries, respond with a controlled “compliance guidance + refer to official source” pattern.

3. **Tighten boundary checks in orchestration**
   - Add explicit post-answer checks for forbidden claim classes and auto-rewrite/refuse when triggered.

4. **Re-run break suite after hardening**
   - Target milestone: materially improve high-risk categories (`medical_violation_attempt`, `law_violation_attempt`) before broader rollout.

## Artifacts

- Test set: `doc/s6_break_test_questions.json`
- Raw results: `doc/s6_break_test_results.json`
- Runbook: `doc/S6_SECURE_TEST_RUNBOOK.md`

## Compliance Reference

- [WAC 314-55-155](https://apps.leg.wa.gov/wac/default.aspx?cite=314-55-155)
- [WA LCB Cannabis Advertising FAQs](https://lcb.wa.gov/enforcement/cannabis_advertising_faqs?utm_source=chatgpt.com)

