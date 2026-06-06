# GTM Outreach Prototype

A flat, linear prototype of the **Planner -> Generator -> Evaluator** three-agent
architecture, applied to GTM outreach. A target prospect goes in; a pitch comes
out only after it clears an independent evaluator checkpoint that flags
hallucinations and missing context before anything could reach a customer.

## Project overview

- `gtm_pipeline.py` — the logic: Planner, Generator (mock), Evaluator, and the
  feedback loop. No UI dependency, fully unit tested.
- `gtm_eval_ui.py` — a thin Streamlit layer that renders the pipeline.
- `gtm_pipeline_test.py` — tests for the pipeline (the evaluator is the
  load-bearing piece, so it is the most tested).

## How to run

    python -m streamlit run gtm_eval_ui.py

(The bare `streamlit` command is not on PATH here; `python -m streamlit` is the
reliable form. Streamlit 1.56 is installed under Python 3.14.)

## How to verify

    python gtm_pipeline_test.py        # standalone, no pytest required
    python -m pytest gtm_pipeline_test.py   # also works if pytest is installed

A change is "done" when the tests pass — not when the code merely runs.

## The architecture

- **Planner** turns the prospect into a *brief* (the contract): what the pitch
  must include, what it must avoid, and the criteria it will be judged against.
- **Generator** writes a draft against the brief. It is mocked and injects a
  known flaw per demo scenario so the evaluator has something real to catch.
- **Evaluator** scores the draft against five criteria, each a hard threshold:
  personalization, factual grounding (no hallucination), correct product,
  call to action, and format/length. Any failure rejects the draft with
  specific, actionable feedback.
- **Loop**: a rejected draft's feedback goes back to the generator, which
  revises and resubmits, until it passes or the round limit is hit.

The generator and evaluator are deliberately separate: the agent that writes the
pitch does not approve its own work.

## Hard constraints

- Pitch only `Sandman`. Any other product name is a hallucination and must fail.
- No unverifiable metrics or invented statistics.
- The evaluator — never the generator — decides whether a draft is done.

## The two swap seams (for integrating real components)

1. **Live agent**: replace the body of `generate_draft(brief, ...)` in
   `gtm_pipeline.py` with a real model call. Keep the signature; the Planner and
   Evaluator are untouched.
2. **Evaluator state**: `evaluate_draft()` returns an `Evaluation` of
   `CriterionResult(id, label, passed, feedback)` objects. That shape is the
   contract to line up against another implementation's state tracking.

## How this maps to the harness PDFs

- Three-agent architecture (Planner / Generator / Evaluator): the spine of this
  prototype.
- Evaluator with hard per-dimension thresholds and evidence-backed feedback:
  `evaluate_draft()`.
- Externalized termination judgment (harness decides "done"): the `passed`
  gate on `Evaluation`, not a generator self-assessment.
- Definition of done verifiable by command: `python gtm_pipeline_test.py`.
- Logic offloaded to a tested module; entry layer is a thin orchestration spec:
  `gtm_pipeline.py` vs `gtm_eval_ui.py`.
- State tracking for cross-session handoff: `PROGRESS.md`.

## Open question for integration

When Jeff's harness lands, the first decision is whether it *calls* this
Planner/Generator/Evaluator as components, or *replaces* the loop orchestration
and keeps only the GTM-specific brief and evaluator criteria. The two swap seams
above are designed so either path is a small change.
