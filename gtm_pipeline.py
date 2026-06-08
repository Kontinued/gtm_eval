"""Planner -> Generator -> Evaluator pipeline for post-meeting client memos.

This is the logic layer. It has no Streamlit dependency so it can be unit
tested and reused. The UI (gtm_eval_ui.py) is a thin layer over these functions.

The task: after a booked meeting, draft the follow-up memo to the client
(recap + agreed next steps) and run it through a strict evaluator checkpoint
before it is sent. Three roles, one straight line:

  Planner    Turns the meeting (your company, the client, and the meeting
             notes) into a brief: what the memo must include, what it must
             avoid, and the criteria it is judged against.

  Generator  Writes the memo against the brief. Two implementations behind one
             interface: a deterministic MOCK (default, for demos and tests) and
             a LIVE agent (a real Claude call, enabled by a flag).

  Evaluator  Scores the memo against five hard-threshold criteria. The most
             important one is grounding: a client memo must not invent
             commitments, dates, or figures that were not in the meeting. The
             Evaluator, not the Generator, decides whether the memo can be sent.

When a memo fails, the Evaluator's feedback is handed back to the Generator (via
the round history), which revises and resubmits, until it passes or the round
limit is hit. Every evaluation emits a DecisionTrace -- the durable record that
feeds the context graph.

For this task the meeting notes are a real source of truth, so ground() actually
checks the memo's figures against the notes; grounding against the wider context
graph is still deferred (see ground()).
"""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import os
import re

# A client memo can run a little longer than a cold email, but not by much.
MAX_WORDS = 180

# Default model for the live generator. Override with the GTM_MODEL env var.
LIVE_MODEL = os.environ.get("GTM_MODEL", "claude-sonnet-4-6")

# Demo-only stand-ins for "a product that isn't yours". The mock injects one as
# a hallucination; the Evaluator flags the same set.
DEMO_WRONG_PRODUCTS = ["Prairie", "Sandstorm", "Sandbox"]

# Words shorter than this are too generic to count as "referencing the meeting".
_MIN_KEYWORD_LEN = 5
_STOPWORDS = {"their", "there", "about", "these", "those", "which", "would",
              "could", "every", "while", "still", "thing", "things", "today",
              "great", "really", "thanks"}

# The defect ids the mock Generator knows how to inject (and later remove).
DEFECT_GENERIC = "generic"            # ignores the meeting, generic filler
DEFECT_FABRICATION = "fabrication"    # invents a commitment/figure not in notes
DEFECT_NO_NEXTSTEPS = "no_next_steps"  # drops the agreed next steps

# Maps a failed Evaluator criterion back to the defect the mock should fix.
_CRITERION_TO_DEFECT = {
    "grounded_in_meeting": DEFECT_GENERIC,
    "no_fabrication": DEFECT_FABRICATION,
    "next_steps": DEFECT_NO_NEXTSTEPS,
}

# Named demo scenarios -> the defects the mock Generator starts with. These only
# affect the mock; the live agent writes freely and the Evaluator judges it.
SCENARIOS = {
    "Clean memo (passes first round)": set(),
    "Invents commitments not in the notes": {DEFECT_FABRICATION},
    "Generic, ignores the meeting": {DEFECT_GENERIC},
    "Missing next steps": {DEFECT_NO_NEXTSTEPS},
    "All three flaws at once": {DEFECT_GENERIC, DEFECT_FABRICATION, DEFECT_NO_NEXTSTEPS},
}


# ---------------------------------------------------------------------------
# State objects
# ---------------------------------------------------------------------------

@dataclass
class Brief:
    """Planner output: the contract the memo is built and judged against."""
    your_company: str
    product: str
    client_company: str
    client_contact: str
    meeting_notes: str
    note_points: list      # the substantive lines pulled from the notes
    note_keywords: list    # grounding terms (notes minus entity names)
    note_numbers: list     # figures present in the notes (for grounding)
    must_include: list
    must_avoid: list


@dataclass
class Draft:
    """Generator output: one memo attempt."""
    round: int
    text: str
    source: str           # "mock" or "live" -- which generator produced it
    active_defects: list  # mock only; [] for live. Demo transparency.


@dataclass
class CriterionResult:
    """One Evaluator check: did it clear its threshold, and the evidence."""
    id: str
    label: str
    passed: bool
    feedback: str


@dataclass
class Evaluation:
    """Evaluator output: per-criterion results plus the overall gate."""
    results: list  # list[CriterionResult]

    @property
    def passed(self):
        return all(r.passed for r in self.results)

    @property
    def score(self):
        return round(100 * sum(r.passed for r in self.results) / len(self.results))

    @property
    def failed(self):
        return [r for r in self.results if not r.passed]


@dataclass
class GroundingResult:
    """Outcome of checking one claim against a source of truth.

    `supported` is True/False once a source can answer, or None when nothing can
    be checked. For a memo the meeting notes ARE a source of truth, so figures
    are checked against them here. The caller does not change when broader
    context-graph grounding is added.
    """
    claim: str
    supported: bool | None
    source: str
    note: str


@dataclass
class DecisionTrace:
    """A durable record of one generate-and-evaluate decision.

    The artifact that feeds the context graph: not just the verdict, but the
    entities it touched, the criteria applied, the grounding attempted, and the
    rationale -- i.e. *why* the memo was allowed (or not) to be sent. Each trace
    is meant to append to the event clock as a first-class record.
    """
    timestamp: str
    decision: str        # "approve_send" | "reject_send"
    verdict: str         # "approved" | "rejected"
    entities: list       # entities this decision touched
    client_company: str
    draft_round: int
    draft_source: str
    score: int
    criteria: list       # [{id, passed, feedback}]
    grounding: list      # [{claim, supported, source, note}]
    rationale: str


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

def _keywords(text):
    """Substantive words from a block of text."""
    seen = []
    for raw in text.split():
        word = raw.lower().strip(".,:;!?\"'()-")
        if (len(word) >= _MIN_KEYWORD_LEN and word.isalpha()  # drop contractions/numbers
                and word not in _STOPWORDS and word not in seen):
            seen.append(word)
    return seen


def _numbers(text):
    """Figures present in a block of text (used as grounding ground-truth)."""
    return re.findall(r"\b\d[\d,]*%?\b", text)


def _note_points(notes):
    """The substantive lines of the meeting notes, for display in the brief."""
    points = []
    for line in notes.splitlines():
        line = line.strip().lstrip("-*").strip()
        if line:
            points.append(line)
    return points


def plan_brief(your_company, product, client_company, client_contact, meeting_notes):
    """Turn the meeting into the brief the memo is judged against."""
    your_company = your_company.strip()
    product = product.strip()
    client_company = client_company.strip()
    client_contact = client_contact.strip()

    # Entity names appear in every memo, so they don't count as "grounding".
    entity_tokens = set()
    for name in (your_company, product, client_company, client_contact):
        for tok in name.lower().split():
            entity_tokens.add(tok)

    note_keywords = [k for k in _keywords(meeting_notes) if k not in entity_tokens]

    return Brief(
        your_company=your_company,
        product=product,
        client_company=client_company,
        client_contact=client_contact,
        meeting_notes=meeting_notes.strip(),
        note_points=_note_points(meeting_notes),
        note_keywords=note_keywords,
        note_numbers=_numbers(meeting_notes),
        must_include=[
            f"Address {client_contact} at {client_company}",
            "Recap what was actually discussed in the meeting",
            "Restate the agreed next steps",
            "Stay professional and concise",
        ],
        must_avoid=[
            "Commitments, dates, or figures that were not in the meeting",
            f"Any product name other than {product}",
            f"More than {MAX_WORDS} words",
        ],
    )


# ---------------------------------------------------------------------------
# Generator -- one interface, two implementations
# ---------------------------------------------------------------------------

def generate_draft(brief, history, scenario, use_live=False):
    """Produce the next memo draft.

    `history` is the list of prior (Draft, Evaluation) pairs (empty on round 1);
    it carries the Evaluator's feedback so the Generator can revise. `scenario`
    drives the mock's injected defects and is ignored by the live agent.

    Dispatches to the live agent when `use_live` is set and available; otherwise
    falls back to the mock. If the live call itself fails it also falls back,
    recording why, rather than crashing the app.
    """
    if use_live and live_generation_available()[0]:
        try:
            return _live_generate(brief, history)
        except Exception as exc:  # noqa: BLE001 - any live failure must degrade gracefully
            draft = _mock_generate(brief, scenario, history)
            draft.source = f"mock (live call failed: {type(exc).__name__})"
            return draft
    return _mock_generate(brief, scenario, history)


def live_generation_available():
    """(ok, reason) -- whether the live agent can run right now."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "ANTHROPIC_API_KEY is not set"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "the 'anthropic' package is not installed (pip install anthropic)"
    return True, ""


_LIVE_SYSTEM = (
    "You are an account executive writing a concise, professional post-meeting "
    "follow-up memo to a client. Ground every statement in the meeting notes you "
    "are given: do NOT invent commitments, dates, figures, or outcomes that are "
    "not in the notes, and never mention any product other than the named one. "
    "Recap what was actually discussed and restate the agreed next steps. Keep it "
    f"under {MAX_WORDS} words. Output only the memo, starting with 'Subject:'."
)


def _live_prompt(brief, history):
    """Build the user message: the meeting context, plus prior feedback."""
    lines = [
        f"Write a follow-up memo from {brief.your_company} to {brief.client_contact} "
        f"at {brief.client_company}.",
        f"Product discussed: {brief.product}.",
        "",
        "Meeting notes (the only source of truth -- do not go beyond them):",
        brief.meeting_notes,
        "",
        "Must include:",
        *[f"- {m}" for m in brief.must_include],
        "Must avoid:",
        *[f"- {m}" for m in brief.must_avoid],
    ]
    if history:
        last_draft, last_eval = history[-1]
        lines += [
            "",
            "Your previous memo was REJECTED. Previous draft:",
            last_draft.text,
            "",
            "Fix these specific problems and resubmit:",
            *[f"- {r.label}: {r.feedback}" for r in last_eval.failed],
        ]
    return "\n".join(lines)


def _live_generate(brief, history):
    """Live agent: a real Claude call. This is the swap that makes the
    Evaluator face real, unpredictable output."""
    import anthropic

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=LIVE_MODEL,
        max_tokens=700,
        temperature=0.6,  # professional, grounded copy
        system=[{"type": "text", "text": _LIVE_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": _live_prompt(brief, history)}],
    )
    text = "".join(block.text for block in message.content if block.type == "text").strip()
    return Draft(round=len(history) + 1, text=text, source="live", active_defects=[])


def _mock_generate(brief, scenario, history):
    """Deterministic mock: composes a memo from the brief and injects the
    scenario's defects, removing any the Evaluator has already flagged."""
    active = set(SCENARIOS.get(scenario, set()))
    for _, evaluation in history:
        active = _apply_feedback(active, evaluation)
    return _compose_mock_memo(brief, active, len(history) + 1)


def _compose_mock_memo(brief, defects, round_number):
    you = brief.your_company
    product = brief.product
    client = brief.client_company
    contact = brief.client_contact

    # Clean baseline, grounded in the meeting. (Subject avoids the literal
    # phrase "next steps" so it can't satisfy the next-steps check on its own.)
    subject = f"Recap from our meeting: {you} & {client}"
    greeting = f"Hi {contact},"
    recap = ("Thanks for the time today. To recap: your dispatchers are losing "
             f"hours to scattered carrier emails for container status, and {product} "
             "can pull that tracking data automatically and flag exceptions.")
    next_steps = ("Next steps:\n- We'll send a short security overview.\n"
                  "- Let's set up a pilot scoping call next week.")
    close = f"Best,\nThe {you} team"

    # Inject defects.
    if DEFECT_GENERIC in defects:
        # Generic everywhere a meeting reference would normally live, so the memo
        # fails grounding while still being a structurally complete memo.
        subject = "Following up on our conversation"
        recap = ("Thanks for the time today. It was a pleasure to connect and "
                 "learn more about your business and priorities.")
        next_steps = ("Next steps:\n- We'll follow up with more details.\n"
                      "- Let's find time to reconnect soon.")
    if DEFECT_FABRICATION in defects:
        recap += (f" Based on today, {product} will cut your dispatch workload by "
                  "40% within the first month and pay for itself by Q3.")
    if DEFECT_NO_NEXTSTEPS in defects:
        next_steps = "Looking forward to staying in touch."

    text = f"Subject: {subject}\n\n{greeting}\n\n{recap}\n\n{next_steps}\n\n{close}"
    return Draft(round=round_number, text=text, source="mock", active_defects=sorted(defects))


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

_NEXT_STEPS_PATTERNS = [
    r"next step", r"action item", r"\bwe'll\b", r"\bi'll\b", r"\blet's\b",
    r"will send", r"will set up", r"follow up", r"follow-up",
]


def _has_next_steps(text):
    low = text.lower()
    return any(re.search(p, low) for p in _NEXT_STEPS_PATTERNS)


def evaluate_draft(draft, brief):
    """Score a memo against the brief. Each criterion is a hard threshold."""
    text = draft.text
    low = text.lower()
    results = []

    # 1. Addressed to the client.
    named = (brief.client_company.lower() in low
             or brief.client_contact.lower() in low)
    fb = (f"Addresses {brief.client_contact} / {brief.client_company}." if named
          else f"Does not address {brief.client_contact} at {brief.client_company}.")
    results.append(CriterionResult("addressed_to_client", "Addressed to the client", named, fb))

    # 2. Grounded in the meeting: references actual discussion points.
    matched = [k for k in brief.note_keywords if k in low]
    grounded = bool(matched)
    fb = (f"References the meeting ('{matched[0]}')." if grounded
          else "Reads generic -- does not reference anything specific that was "
               "discussed. Recap the actual points from the notes.")
    results.append(CriterionResult("grounded_in_meeting", "Grounded in the meeting", grounded, fb))

    # 3. No fabrication: every figure must trace back to the notes; no wrong product.
    grounding = gather_grounding(draft, brief)
    fabricated = sorted({f for g in grounding if g.supported is False
                         for f in re.findall(r"\b\d[\d,]*%?\b", g.claim)
                         if f not in brief.note_numbers})
    wrong = [p for p in DEMO_WRONG_PRODUCTS if p in text]
    clean = not fabricated and not wrong
    if clean:
        fb = "No invented figures or products; claims trace to the notes."
    else:
        issues = []
        if fabricated:
            issues.append(f"states figure(s) not in the notes: {', '.join(fabricated)}")
        if wrong:
            issues.append(f"mentions unapproved product(s): {', '.join(wrong)}")
        fb = ("Memo " + " and ".join(issues) + ". Only state what the meeting "
              "actually covered.")
    results.append(CriterionResult("no_fabrication", "No fabricated commitments", clean, fb))

    # 4. Clear next steps.
    steps = _has_next_steps(text)
    fb = ("States the agreed next steps." if steps
          else "No clear next steps. Restate what was agreed, e.g. the next call.")
    results.append(CriterionResult("next_steps", "Clear next steps", steps, fb))

    # 5. Format and length.
    has_subject = "subject:" in low
    words = len(text.split())
    ok = has_subject and words <= MAX_WORDS
    if ok:
        fb = f"Subject line present, {words} words (limit {MAX_WORDS})."
    else:
        parts = []
        if not has_subject:
            parts.append("missing a subject line")
        if words > MAX_WORDS:
            parts.append(f"too long at {words} words (limit {MAX_WORDS})")
        fb = "Memo is " + " and ".join(parts) + "."
    results.append(CriterionResult("format", "Complete and concise", ok, fb))

    return Evaluation(results=results)


# ---------------------------------------------------------------------------
# Grounding seam: check claims against a source of truth
# ---------------------------------------------------------------------------

def ground(claim, brief):
    """Check whether a claim is supported by a source of truth.

    For a memo the meeting notes are an available source of truth, so figures in
    the claim are checked against the figures in the notes. Claims without a
    checkable figure return "unknown" (None) -- verifying those needs the wider
    context graph, which is still deferred. This is the grounding counterpart to
    the generate_draft() live-agent seam: when the context graph lands, extend
    this function and nothing that calls it changes.
    """
    figures = re.findall(r"\b\d[\d,]*%?\b", claim)
    if not figures:
        return GroundingResult(claim, None, "",
                               "no checkable figure; deferred to context graph")
    unsupported = [f for f in figures if f not in brief.note_numbers]
    if unsupported:
        return GroundingResult(claim, False, "meeting_notes",
                               f"figure(s) not in the notes: {', '.join(unsupported)}")
    return GroundingResult(claim, True, "meeting_notes", "figures match the meeting notes")


def extract_claims(draft, brief):
    """Pull candidate claims (sentences naming an entity or stating a figure)."""
    text = draft.text.replace("\n", " ")
    claims = []
    for sentence in re.split(r"(?<=[.?!])\s+", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        names_entity = (brief.product in sentence
                        or brief.client_company.lower() in sentence.lower())
        has_figure = bool(re.search(r"\d", sentence))
        if names_entity or has_figure:
            claims.append(sentence)
    return claims


def gather_grounding(draft, brief):
    """Ground every extracted claim. Returns a list of GroundingResult."""
    return [ground(claim, brief) for claim in extract_claims(draft, brief)]


def build_decision_trace(brief, draft, evaluation, timestamp=None):
    """Assemble the durable decision trace for one evaluated memo."""
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    rationale = ("All criteria passed." if evaluation.passed
                 else "Rejected on: " + "; ".join(r.label for r in evaluation.failed))
    return DecisionTrace(
        timestamp=ts,
        decision="approve_send" if evaluation.passed else "reject_send",
        verdict="approved" if evaluation.passed else "rejected",
        entities=[brief.your_company, brief.product, brief.client_company],
        client_company=brief.client_company,
        draft_round=draft.round,
        draft_source=draft.source,
        score=evaluation.score,
        criteria=[{"id": r.id, "passed": r.passed, "feedback": r.feedback}
                  for r in evaluation.results],
        grounding=[asdict(g) for g in gather_grounding(draft, brief)],
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Feedback loop: Planner -> (Generator -> Evaluator)* until done
# ---------------------------------------------------------------------------

def _apply_feedback(active_defects, evaluation):
    """Mock helper: drop the defects the Evaluator flagged this round."""
    remaining = set(active_defects)
    for result in evaluation.failed:
        defect = _CRITERION_TO_DEFECT.get(result.id)
        if defect:
            remaining.discard(defect)
    return remaining


def run_pipeline(your_company, product, client_company, client_contact,
                 meeting_notes, scenario, max_rounds=3, use_live=False):
    """Run the full loop. Returns (brief, rounds) where rounds is a list of
    (Draft, Evaluation) pairs, one per generate-and-evaluate cycle."""
    brief = plan_brief(your_company, product, client_company, client_contact, meeting_notes)
    history = []
    for _ in range(max_rounds):
        draft = generate_draft(brief, history, scenario, use_live=use_live)
        evaluation = evaluate_draft(draft, brief)
        stalled = bool(history) and draft.text == history[-1][0].text
        history.append((draft, evaluation))
        if evaluation.passed or stalled:
            break
    return brief, history
