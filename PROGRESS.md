# Progress

## Current state (2026-06-05)

- Pipeline (`gtm_pipeline.py`): Planner -> Generator (mock + live) -> Evaluator
  with a revision loop, retargeted to the **post-meeting client memo**. Tests
  green: `python gtm_pipeline_test.py` -> 15/15 passing.
- UI (`gtm_eval_ui.py`): thin Streamlit layer. Inputs are your company, the
  client, and the meeting notes. Shows the brief, the round-by-round
  generate/evaluate, the result, and the decision trace.
- Built to mirror the harness PDFs' three-agent architecture and to plug into
  the context graph (grounding seam + decision-trace output).

## Completed

- [x] Planner turns your company + client + meeting notes into a brief
      (grounding terms, figures from the notes, must include / must avoid).
- [x] Evaluator with five hard-threshold criteria and evidence-backed feedback:
      addressed-to-client, grounded-in-the-meeting, no-fabrication, next-steps,
      format.
- [x] Generator mock with demo scenarios that inject fabrication, generic/
      ungrounded, and missing-next-steps flaws.
- [x] Feedback loop: round history carries evaluator feedback to the generator;
      converges and is tested.
- [x] Live agent behind a flag: `use_live` calls Claude (GTM_MODEL, default
      claude-sonnet-4-6), feeds prior feedback on a revision, and falls back to
      the mock (with a reason) when the key/SDK is absent or the call fails.
- [x] `ground(claim, brief)` grounding seam: checks figures against the meeting
      notes now; context-graph grounding deferred.
- [x] `build_decision_trace(...)`: every evaluation emits a durable trace meant
      to feed the context graph's event clock. Shown as JSON in the UI.
- [x] README, AGENTS.md, requirements.txt, this progress file.

## To enable live mode

    pip install anthropic
    setx ANTHROPIC_API_KEY "sk-ant-..."   # then reopen the terminal

Then flip the "Use live agent" toggle (it defaults on once available). No code
change needed. NOTE: the first real live run is still unverified (no key was
available when this was built).

## Demo scenarios (in the UI dropdown)

- Clean memo -> passes round 1 (100%).
- Invents commitments not in the notes -> fails no-fabrication, fixed round 2.
- Generic, ignores the meeting -> fails grounded-in-the-meeting, fixed round 2.
- Missing next steps -> fails next-steps, fixed round 2.
- All three flaws at once -> fails three criteria, converges within the limit.

## Next steps

1. Get the company ANTHROPIC_API_KEY and do the first real live memo run.
2. Back the `ground()` seam with the real context graph (state/event clocks,
   relationship graph) instead of just the meeting notes.
3. Wire decision traces to actually append to the context graph's event clock.
4. Review Jeff's harness when pushed; map our seams onto his structure.

## Open questions for the team

- Client-facing memo (built) vs internal AE deal-memo — confirm the target.
- Which context graph (Mebbian's or homegrown) backs the grounding seam?
