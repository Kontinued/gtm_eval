# Integrating gtm_eval into `oaisis-dev/prairie`

How this prototype slots into the GTM agent harness. Bring this to the team.

## What this is, in prairie's terms

`gtm_eval` is a working prototype of the **output verifier** — the
generate → evaluate → revise loop that gates an agent's client-facing output
before it ships. In the prairie build order (`docs/harness/gtm-agent-context.md`)
it maps to:

- **GTM005 — "Executor + verifier loop with stop conditions and a cost ceiling."**
  We have the *verifier* half (the evaluator), the revision loop, and both stop
  conditions (`max_rounds` + a `token_budget` cost ceiling) already implemented
  and tested.
- **GTM006 — "Observability + evaluation … retrieval/faithfulness scoring."**
  Our `no_fabrication` / grounding check is a faithfulness scorer: it verifies
  every claim's figures against the source of truth and refuses ungrounded output.

## The distinction to keep clear (two different evaluators)

There are two "evaluators" in play; they are **not** the same box:

- prairie's `docs/harness/evaluator-rubric.md` scores **engineering sprints**
  (A–D on correctness, test coverage, architecture compliance, …). It judges the
  *dev work*.
- This prototype's evaluator scores the **agent's runtime output** (is the memo
  grounded / safe to send). It judges the *product the agent emits*.

Same Planner→Generator→Evaluator pattern, two levels. This is the runtime one.
It is also distinct from the LangSmith/Langfuse "tracing & eval" box (system
observability) — this is content verification.

## Proposed `features.md` entry

Add to `docs/harness/features.md` (its table format), as the verifier deliverable:

```
| GTM007 | Output verifier: gate agent-drafted client output against grounding + no-fabrication criteria before send; failed drafts revise via evaluator feedback; stop conditions = max_rounds + token-budget cost ceiling | python3 -m pytest tests/test_output_verifier.py | not_started | prototype in gtm_eval |
```

(Or fold it into GTM005's verifier — either works; a separate id keeps the
content-evaluator legible.)

## What pre-aligns with the harness already

- **Evaluator state shape** — `evaluate_draft` returns an `Evaluation` of
  `CriterionResult(id, label, passed, feedback)`; hard-threshold gate. Lines up
  field-for-field against the rubric's per-dimension scoring (we use pass/fail;
  the rubric uses A–D — pick one when we merge).
- **Decision trace** — every evaluation emits `build_decision_trace(...)`
  (entities, criteria, grounding, rationale). This is the durable record meant to
  append to the **context graph's event clock** (component 1 + the decision-trace
  thesis in the Context Graph writeup).
- **Grounding seam** — `ground(claim, brief)` checks claims against a source of
  truth. Today it uses the meeting notes; back it with the prairie context graph
  (state/event clocks, relationship graph) and nothing that calls it changes.
- **Stop conditions** — `max_rounds` + `token_budget` already satisfy the GTM005
  "stop conditions + cost ceiling" requirement.

## Migration steps

1. Move `gtm_pipeline.py` → a module under prairie (e.g. `src/verifier/`), and
   `gtm_pipeline_test.py` → `tests/test_output_verifier.py`.
2. Register **GTM007** in `features.md`; wire its pytest into the `Makefile`
   `check` target so the harness gate runs it.
3. Back `ground()` with the context graph once it exists (component 1 / GTM003).
4. Route `build_decision_trace` output to the event clock.

## Live generator note

The live generator currently calls **Gemini** (`google-genai`, `GEMINI_API_KEY`)
as a pragmatic unblock on available GCP access. Claude via **Bedrock** is the
eventual target per the AWS-first stack; the swap is behind `_live_generate()`
only — the Planner, Evaluator, loop, and seams above do not change.
