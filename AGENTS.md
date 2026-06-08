# Client Memo Prototype

A flat, linear prototype of the **Planner -> Generator -> Evaluator** three-agent
architecture, applied to the post-meeting **client memo**. Your company, the
client, and the meeting notes go in; the follow-up memo comes out only after it
clears an independent evaluator checkpoint that ensures it stays grounded in what
was actually discussed (no invented commitments) before it can be sent to the
client.

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

- **Planner** turns the meeting (your company, the client, the notes) into a
  *brief* (the contract): what the memo must include, what it must avoid, the
  grounding terms and figures from the notes, and the criteria it is judged by.
- **Generator** writes a memo against the brief. Mock (default) or a real Gemini
  call behind a flag (Claude/Bedrock is the eventual target, swappable behind
  `_live_generate()`); the mock injects a known flaw per demo scenario so the
  evaluator has something real to catch.
- **Evaluator** scores the memo against five criteria, each a hard threshold:
  addressed-to-client, grounded-in-the-meeting, no-fabrication (no figure or
  commitment absent from the notes), clear-next-steps, and format/length. Any
  failure rejects the memo with specific, actionable feedback.
- **Loop**: a rejected memo's feedback goes back to the generator, which revises
  and resubmits, until it passes or the round limit is hit.
- **Decision trace**: every evaluation emits a `DecisionTrace` (entities,
  criteria, grounding, rationale) meant to append to the context graph.

The generator and evaluator are deliberately separate: the agent that writes the
memo does not approve its own work.

## Hard constraints

- Mention only the named product. Any other product name is a hallucination and must fail.
- No commitments, dates, or figures that were not in the meeting notes.
- The evaluator — never the generator — decides whether a memo can be sent.

## The swap seams (for integrating real components)

1. **Live agent**: `generate_draft(brief, history, scenario, use_live)` dispatches
   to a real Claude call when a key is present, else the mock. Planner and
   Evaluator are untouched.
2. **Evaluator state**: `evaluate_draft()` returns an `Evaluation` of
   `CriterionResult(id, label, passed, feedback)` objects — the contract to line
   up against another implementation's state tracking.
3. **Grounding**: `ground(claim, brief)` checks claims against a source of truth
   (the meeting notes now, the context graph later) without changing callers.
4. **Decision trace**: `build_decision_trace(...)` is the durable record that
   feeds the context graph's event clock.

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
