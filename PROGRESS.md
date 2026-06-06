# Progress

## Current state (2026-06-01)

- Pipeline (`gtm_pipeline.py`): Planner -> Generator (mock) -> Evaluator with a
  revision loop. Tests green: `python gtm_pipeline_test.py` -> 9/9 passing.
- UI (`gtm_eval_ui.py`): thin Streamlit layer over the pipeline. Runs with
  `python -m streamlit run gtm_eval_ui.py`.
- Built to mirror the harness PDFs' three-agent architecture so it lines up with
  Jeff's harness when it lands. Jeff's code has not been pushed yet.

## Completed

- [x] Planner derives a brief (context summary, grounding keywords, must
      include / must avoid) from the prospect inputs.
- [x] Seller (company, product, value prop) is an input, carried on the Brief.
- [x] Evaluator with five hard-threshold criteria and evidence-backed feedback.
- [x] Generator mock with demo scenarios that inject hallucination, missing
      context, and weak-CTA flaws.
- [x] Feedback loop: the round history carries evaluator feedback back to the
      generator; loop converges and is tested.
- [x] Live agent wired behind a flag: `use_live` calls Claude (model from
      GTM_MODEL, default claude-sonnet-4-6) and feeds prior feedback in on a
      revision. Falls back to the mock when the API key / SDK is absent, so the
      demo never breaks. UI has a "Use live agent" toggle.
- [x] AGENTS.md entry doc; this progress file.

## To enable live mode

    pip install anthropic
    setx ANTHROPIC_API_KEY "sk-ant-..."   # then reopen the terminal

Then flip the "Use live agent" toggle. No code change needed.

## Demo scenarios (in the UI dropdown)

- Clean draft -> passes round 1 (100%).
- Hallucinated product and stats -> fails factual grounding, fixed on round 2.
- Generic, no personalization -> fails personalization, fixed on round 2.
- Weak call to action -> fails CTA, fixed on round 2.
- All three flaws at once -> fails three criteria, converges within the limit.

## Next steps

1. Review Jeff's harness when pushed; decide call-vs-replace (see AGENTS.md open
   question). Map our two swap seams onto his structure.
2. Replace the mock `generate_draft()` with the live agent.
3. Grow the evaluator criteria toward real GTM judgment (deliverability,
   tone, claim-by-claim grounding against a source-of-truth on the prospect).
4. Point the generator at real prospect sourcing (the "first client = us" goal).

## Open questions for the team

- Is the priority GTM-for-ourselves (dogfood) or Sandman-as-a-product? They
  imply different next builds.
- What is the source of truth the evaluator should ground claims against
  (CRM, the company brain, enrichment data)?
