"""Tests for the client-memo pipeline.

The evaluator is the load-bearing logic, so it is the most heavily tested part.
Runs under pytest, and also standalone: `python gtm_pipeline_test.py`.
"""

import gtm_pipeline as p

YOUR = "Oaisis"
PRODUCT = "Sandman"
CLIENT = "Nexus Logistics"
CONTACT = "Dana"
NOTES = (
    "- Dana walked us through how dispatchers spend ~3 hours a day checking "
    "email for container status.\n"
    "- They manage around 3,000 active shipments at any time.\n"
    "- Main frustration: no single place to see status; updates are scattered "
    "across carrier emails.\n"
    "- Dana liked that Sandman pulls tracking data automatically and flags "
    "exceptions.\n"
    "- Agreed: we'll send a short security overview and set up a pilot scoping "
    "call next week."
)


def _brief():
    return p.plan_brief(YOUR, PRODUCT, CLIENT, CONTACT, NOTES)


def _eval_for(scenario):
    brief = _brief()
    draft = p.generate_draft(brief, [], scenario)  # empty history = round 1
    return brief, draft, p.evaluate_draft(draft, brief)


def _run(scenario):
    return p.run_pipeline(YOUR, PRODUCT, CLIENT, CONTACT, NOTES, scenario, 3)


def test_planner_parses_the_notes():
    brief = _brief()
    assert "container" in brief.note_keywords
    assert "dispatchers" in brief.note_keywords
    assert "sandman" not in brief.note_keywords   # entity name excluded
    assert "3,000" in brief.note_numbers
    assert brief.note_points                       # non-empty


def test_clean_memo_passes_every_criterion():
    _, _, ev = _eval_for("Clean memo (passes first round)")
    assert ev.passed
    assert ev.score == 100


def test_clean_memo_addresses_the_client():
    _, draft, _ = _eval_for("Clean memo (passes first round)")
    assert "Dana" in draft.text
    assert "Nexus Logistics" in draft.text


def test_fabricated_figure_is_caught():
    _, _, ev = _eval_for("Invents commitments not in the notes")
    assert not ev.passed
    fact = next(r for r in ev.results if r.id == "no_fabrication")
    assert not fact.passed
    assert "40" in fact.feedback


def test_generic_memo_fails_grounding():
    _, _, ev = _eval_for("Generic, ignores the meeting")
    assert not ev.passed
    grounded = next(r for r in ev.results if r.id == "grounded_in_meeting")
    assert not grounded.passed


def test_missing_next_steps_is_caught():
    _, _, ev = _eval_for("Missing next steps")
    assert not ev.passed
    steps = next(r for r in ev.results if r.id == "next_steps")
    assert not steps.passed


def test_clean_memo_is_within_word_budget():
    _, draft, _ = _eval_for("Clean memo (passes first round)")
    assert len(draft.text.split()) <= p.MAX_WORDS


def test_ground_checks_figures_against_the_notes():
    brief = _brief()
    assert p.ground("Sandman will cut workload by 40% next month", brief).supported is False
    assert p.ground("They manage around 3,000 shipments", brief).supported is True
    assert p.ground("It was great to connect", brief).supported is None


def test_use_live_falls_back_to_mock_when_unavailable():
    ok, _ = p.live_generation_available()
    draft = p.generate_draft(_brief(), [], "Clean memo (passes first round)", use_live=True)
    if not ok:
        assert draft.source == "mock"
        assert "Sandman" in draft.text


def test_live_call_failure_falls_back_to_mock():
    brief = _brief()
    orig_available, orig_live = p.live_generation_available, p._live_generate

    def boom(_brief, _history):
        raise RuntimeError("simulated API failure")

    p.live_generation_available = lambda: (True, "")
    p._live_generate = boom
    try:
        draft = p.generate_draft(brief, [], "Clean memo (passes first round)", use_live=True)
    finally:
        p.live_generation_available, p._live_generate = orig_available, orig_live

    assert draft.source.startswith("mock")
    assert "live call failed" in draft.source


def test_mock_draft_is_tagged_as_mock():
    _, draft, _ = _eval_for("Clean memo (passes first round)")
    assert draft.source == "mock"


def test_decision_trace_records_approval():
    brief, draft, ev = _eval_for("Clean memo (passes first round)")
    trace = p.build_decision_trace(brief, draft, ev, timestamp="2026-06-05T00:00:00Z")
    assert trace.verdict == "approved"
    assert trace.decision == "approve_send"
    assert "Sandman" in trace.entities
    assert trace.client_company == "Nexus Logistics"
    assert len(trace.criteria) == 5
    assert trace.timestamp == "2026-06-05T00:00:00Z"


def test_decision_trace_records_rejection_and_fabrication_grounding():
    brief, draft, ev = _eval_for("Invents commitments not in the notes")
    trace = p.build_decision_trace(brief, draft, ev)
    assert trace.verdict == "rejected"
    assert trace.decision == "reject_send"
    # the fabricated figure should show up as unsupported in the grounding record
    assert any(g["supported"] is False for g in trace.grounding)


def test_loop_converges_for_each_single_flaw():
    for scenario in ("Invents commitments not in the notes",
                     "Generic, ignores the meeting",
                     "Missing next steps"):
        _, rounds = _run(scenario)
        assert rounds[0][1].passed is False, scenario
        assert rounds[-1][1].passed is True, scenario
        assert len(rounds) == 2, scenario  # one revision is enough


def test_cost_ceiling_stops_the_loop():
    # A flawed scenario would normally take 2 rounds, but a tiny token budget
    # (the cost ceiling) stops it after round 1. Mock drafts report an estimated
    # token count so this is exercisable without a live call.
    _, rounds = p.run_pipeline(YOUR, PRODUCT, CLIENT, CONTACT, NOTES,
                               "Invents commitments not in the notes",
                               max_rounds=3, token_budget=10)
    assert len(rounds) == 1
    assert rounds[-1][1].passed is False


def test_loop_converges_when_all_flaws_present():
    _, rounds = _run("All three flaws at once")
    assert rounds[0][1].passed is False
    assert len(rounds[0][1].failed) == 3
    assert rounds[-1][1].passed is True
    assert len(rounds) <= 3


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {test.__name__}: {exc!r}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    raise SystemExit(1 if failures else 0)
