"""Tests for the GTM pipeline.

The evaluator is the load-bearing logic, so it is the most heavily tested part.
Runs under pytest, and also standalone: `python gtm_pipeline_test.py`.
"""

import gtm_pipeline as p

SELLER = dict(
    seller_company="Oaisis",
    product="Sandman",
    value_prop="connects to Slack, email, and GitHub and delegates tasks across your team",
)
PROSPECT = "Nexus Logistics"
DESCRIPTION = (
    "They manage thousands of supply chain shipments, but their dispatchers "
    "waste hours manually checking emails for container status updates."
)


def _brief():
    return p.plan_brief(SELLER["seller_company"], SELLER["product"],
                        SELLER["value_prop"], PROSPECT, DESCRIPTION)


def _eval_for(scenario):
    """Generate a first-round draft for a scenario and evaluate it."""
    brief = _brief()
    draft = p.generate_draft(brief, [], scenario)  # empty history = round 1
    return brief, draft, p.evaluate_draft(draft, brief)


def _run(scenario):
    return p.run_pipeline(SELLER["seller_company"], SELLER["product"],
                          SELLER["value_prop"], PROSPECT, DESCRIPTION, scenario, 3)


def test_planner_carries_seller_and_extracts_context():
    brief = _brief()
    assert brief.seller_company == "Oaisis"
    assert brief.product == "Sandman"
    assert brief.target_company == "Nexus Logistics"
    assert "container" in brief.pain_keywords
    assert "their" not in brief.pain_keywords  # stopword filtered out
    assert brief.context_summary.startswith("They manage")


def test_draft_is_built_from_seller_inputs():
    _, draft, _ = _eval_for("Clean draft (passes first round)")
    assert "Sandman" in draft.text          # product name
    assert "Oaisis" in draft.text           # seller signoff
    assert "Nexus Logistics" in draft.text  # prospect


def test_custom_seller_flows_through():
    brief = p.plan_brief("Acme Co", "Beacon", "watches your fleet in real time",
                         PROSPECT, DESCRIPTION)
    draft = p.generate_draft(brief, [], "Clean draft (passes first round)")
    ev = p.evaluate_draft(draft, brief)
    assert "Beacon" in draft.text
    assert "Acme Co" in draft.text
    assert ev.passed
    offering = next(r for r in ev.results if r.id == "correct_offering")
    assert offering.passed and "Beacon" in offering.label


def test_clean_draft_passes_every_criterion():
    _, _, ev = _eval_for("Clean draft (passes first round)")
    assert ev.passed
    assert ev.score == 100


def test_hallucination_is_caught():
    _, _, ev = _eval_for("Hallucinated product and stats")
    assert not ev.passed
    fact = next(r for r in ev.results if r.id == "factual_grounding")
    assert not fact.passed
    assert "Prairie" in fact.feedback
    assert "5,000" in fact.feedback
    # Product is still named, so that criterion should still pass.
    assert next(r for r in ev.results if r.id == "correct_offering").passed


def test_generic_draft_fails_personalization():
    _, draft, ev = _eval_for("Generic, no personalization")
    assert not ev.passed
    person = next(r for r in ev.results if r.id == "personalization")
    assert not person.passed
    assert PROSPECT not in draft.text


def test_weak_cta_is_caught():
    _, _, ev = _eval_for("Weak call to action")
    assert not ev.passed
    cta = next(r for r in ev.results if r.id == "call_to_action")
    assert not cta.passed


def test_clean_draft_is_within_word_budget():
    _, draft, _ = _eval_for("Clean draft (passes first round)")
    assert len(draft.text.split()) <= p.MAX_WORDS


def test_allowlisted_meeting_length_is_not_flagged():
    # "10-minute" must not be treated as an unverifiable statistic.
    assert p._unverifiable_numbers("Open to a quick 10-minute brief?") == []
    assert "5,000" in p._unverifiable_numbers("trusted by 5,000 firms")


def test_loop_converges_for_each_single_flaw():
    for scenario in ("Hallucinated product and stats",
                     "Generic, no personalization",
                     "Weak call to action"):
        _, rounds = _run(scenario)
        assert rounds[0][1].passed is False, scenario
        assert rounds[-1][1].passed is True, scenario
        assert len(rounds) == 2, scenario  # one revision is enough


def test_use_live_falls_back_to_mock_when_unavailable():
    # With no API key / SDK, requesting live must safely fall back to the mock
    # and still produce a valid draft -- never crash.
    ok, _ = p.live_generation_available()
    brief = _brief()
    draft = p.generate_draft(brief, [], "Clean draft (passes first round)", use_live=True)
    if not ok:
        assert draft.source == "mock"
        assert "Sandman" in draft.text


def test_mock_draft_is_tagged_as_mock():
    _, draft, _ = _eval_for("Clean draft (passes first round)")
    assert draft.source == "mock"


def test_live_call_failure_falls_back_to_mock():
    # If the live agent is "available" but the call itself raises, the pipeline
    # must degrade to the mock and record why -- never crash the app.
    brief = _brief()
    orig_available, orig_live = p.live_generation_available, p._live_generate

    def boom(_brief, _history):
        raise RuntimeError("simulated API failure")

    p.live_generation_available = lambda: (True, "")
    p._live_generate = boom
    try:
        draft = p.generate_draft(brief, [], "Clean draft (passes first round)", use_live=True)
    finally:
        p.live_generation_available, p._live_generate = orig_available, orig_live

    assert draft.source.startswith("mock")
    assert "live call failed" in draft.source
    assert "Sandman" in draft.text  # still a valid draft


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
