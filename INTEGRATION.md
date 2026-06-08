# Integrating `gtm_eval` into the prairie harness

How this prototype's evaluator slots into `oaisis-dev/prairie`. Short version:
`gtm_eval` is a working prototype of the **output verifier** — the piece that
judges the GTM agent's *runtime output* before it ships — plus the
**faithfulness/grounding** signal and the **decision traces** that feed the
context graph.

## Two different evaluators (do not conflate)

- `prairie/docs/harness/evaluator-rubric.md` scores engineering **sprints** (the
  dev work) A–D. That is harness QA of the code being built.
- `gtm_eval` scores the agent's **output** (the client memo) pass/fail and gates
  "send". That is product/content verification at runtime.

Same Planner → Generator → Evaluator pattern, at two levels. This proposal is
about the second one.

## Where it maps in the build order (`gtm-agent-context.md`)

| gtm_eval piece | harness slot |
| --- | --- |
| `run_pipeline` generate → evaluate → revise loop (with `max_rounds` stop) | **GTM005** "executor + **verifier** loop with stop conditions" |
| `evaluate_draft` 5 hard-threshold criteria | the **verifier** in GTM005 |
| `ground(claim, brief)` (figures vs notes now; context graph later) | **faithfulness** metric in **GTM006** / component 6 |
| `build_decision_trace(...)` (entities, criteria, grounding, rationale) | **provenance** evidence (GTM003) → context graph event clock |

## Proposed `features.md` row (paste-ready)

```
| GTM007 | Output verifier: score generated client memos on grounding + fabrication, gate send, emit decision traces | `python3 -m pytest tests/test_output_verifier.py` | not_started | prototype: Kontinued/gtm_eval |
```

(Or fold into GTM005 as its verifier half + GTM006 as its faithfulness check —
team's call. Keeping it as its own small feature matches the "one session"
sizing rule.)

## Output rubric (the content criteria — distinct from the sprint rubric)

Each is a hard threshold; all must pass for a memo to be sent.

| Criterion | Pass condition |
| --- | --- |
| Addressed to the client | Names the client company and contact |
| Grounded in the meeting | References what was actually discussed in the notes |
| No fabrication | Every figure traces to the notes; no invented commitments or products |
| Clear next steps | Restates the agreed next steps |
| Format | Subject line present, within the word budget |

## Verification

`python3 -m pytest tests/test_output_verifier.py` (the current
`gtm_pipeline_test.py`, 15 tests). It asserts, with evidence: a clean memo
passes; a fabricated figure is caught; a generic memo fails grounding; missing
next steps is caught; `ground()` checks figures against the notes; the loop
converges; the decision trace records approve/reject with grounding.

## Contracts (so state tracking lines up with the harness)

- `evaluate_draft(draft, brief) -> Evaluation` of
  `CriterionResult(id, label, passed, feedback)`.
- `ground(claim, brief) -> GroundingResult(claim, supported, source, note)` —
  source of truth is the meeting notes today, the context graph later; callers
  don't change.
- `build_decision_trace(...) -> DecisionTrace` — the durable record meant to
  append to the event clock (and could write to `quality.md` / `diagnostics.md`).
- `generate_draft(brief, history, scenario, use_live)` — mock or live Claude
  behind one seam.

## Open questions for the team

1. Output verifier (content) vs `evaluator-rubric.md` (sprints): confirm they
   stay separate layers.
2. Scoring shape: keep pass/fail per criterion, or adopt the harness's A–D
   grading so both evaluators read the same?
3. Where do decision traces append — the event clock, and in what schema?
4. Grounding source of truth beyond the notes: the context graph — Mebbian's or
   homegrown?
5. GTM005 requires a **cost ceiling**; add a token/round budget to the loop.
