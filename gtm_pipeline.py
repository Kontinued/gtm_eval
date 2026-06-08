"""Planner -> Generator -> Evaluator pipeline for GTM outreach.

This is the logic layer. It has no Streamlit dependency so it can be unit
tested and reused. The UI (gtm_eval_ui.py) is a thin layer over these functions.

Three roles, one straight line:

  Planner    Expands a seller and a target prospect into a pitch brief: what
             the pitch must include, what it must avoid, and the criteria it
             will be judged against.

  Generator  Writes a pitch draft against the brief. Two implementations behind
             one interface: a deterministic MOCK (default, used for demos and
             tests) and a LIVE agent (a real Claude call, enabled by a flag).
             The Evaluator and the loop do not care which one ran.

  Evaluator  Scores the draft against the brief's criteria, each with a hard
             threshold. If any point in the criteria fails, the draft is
             rejected with specific feedback. The Evaluator, not the Generator,
             decides whether a draft is done.

The Generator and Evaluator are deliberately separate: the agent that writes the
pitch does not get to approve its own work. When a draft fails, the Evaluator's
feedback is handed back to the Generator (via the round history), which revises
and resubmits, until the draft passes or the round limit is hit.

State objects (Brief, Draft, Evaluation) are explicit dataclasses so the
pipeline state is easy to inspect and to line up against another implementation.
"""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import os
import re

# A pitch must stay tight. The Evaluator enforces this as a hard threshold.
MAX_WORDS = 130

# Default model for the live generator. Override with the GTM_MODEL env var.
LIVE_MODEL = os.environ.get("GTM_MODEL", "claude-sonnet-4-6")

# Demo-only stand-ins for "a product that isn't the seller's". The mock
# Generator injects one of these as a hallucination; the Evaluator flags the
# same set. With a live generator and a real source of truth, this is replaced
# by checking claims against approved facts (see PROGRESS.md / AGENTS.md).
DEMO_WRONG_PRODUCTS = ["Prairie", "Sandstorm", "Sandbox"]

# Words shorter than this are too generic to count as "referencing the context".
_MIN_KEYWORD_LEN = 5
_STOPWORDS = {"their", "there", "about", "these", "those", "which", "would",
              "could", "every", "while", "still", "thing", "things"}

# The defect ids the mock Generator knows how to inject (and later remove).
DEFECT_GENERIC = "generic"
DEFECT_HALLUCINATION = "hallucinated_product"
DEFECT_WEAK_CTA = "weak_cta"

# Maps a failed Evaluator criterion back to the defect the mock should fix.
# This is how the mock's feedback loop converges: a flagged criterion tells the
# Generator exactly which defect to remove on the next round.
_CRITERION_TO_DEFECT = {
    "personalization": DEFECT_GENERIC,
    "factual_grounding": DEFECT_HALLUCINATION,
    "call_to_action": DEFECT_WEAK_CTA,
}

# Named demo scenarios -> the defects the mock Generator starts with. These only
# affect the mock; the live agent writes freely and the Evaluator judges it.
SCENARIOS = {
    "Clean draft (passes first round)": set(),
    "Hallucinated product and stats": {DEFECT_HALLUCINATION},
    "Generic, no personalization": {DEFECT_GENERIC},
    "Weak call to action": {DEFECT_WEAK_CTA},
    "All three flaws at once": {DEFECT_GENERIC, DEFECT_HALLUCINATION, DEFECT_WEAK_CTA},
}


# ---------------------------------------------------------------------------
# State objects
# ---------------------------------------------------------------------------

@dataclass
class Brief:
    """Planner output: the contract the draft is built and judged against."""
    seller_company: str    # who is selling (your company)
    product: str           # the offering's name
    value_prop: str        # one-line description of what the offering does
    target_company: str    # who is being sold to
    context_summary: str   # the prospect's stated situation
    pain_keywords: list
    must_include: list
    must_avoid: list


@dataclass
class Draft:
    """Generator output: one pitch attempt."""
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
        # Hard gate: every criterion must clear its threshold.
        return all(r.passed for r in self.results)

    @property
    def score(self):
        return round(100 * sum(r.passed for r in self.results) / len(self.results))

    @property
    def failed(self):
        return [r for r in self.results if not r.passed]


@dataclass
class GroundingResult:
    """Outcome of checking one factual claim against a source of truth.

    `supported` is True/False once a context graph can answer, or None while no
    graph is connected (grounding deferred). The evaluator that calls ground()
    does not change when the backing source goes from stub to real.
    """
    claim: str
    supported: bool | None
    source: str
    note: str


@dataclass
class DecisionTrace:
    """A durable record of one generate-and-evaluate decision.

    This is the artifact that feeds the context graph: not just the verdict, but
    the entities it touched, the criteria applied, the grounding attempted, and
    the rationale -- i.e. *why* the output was allowed (or not) to ship. Each
    trace is meant to append to the event clock as a first-class record.
    """
    timestamp: str
    decision: str        # "approve_outreach" | "reject_outreach"
    verdict: str         # "approved" | "rejected"
    entities: list       # entities this decision touched
    target_company: str
    draft_round: int
    draft_source: str
    score: int
    criteria: list       # [{id, passed, feedback}]
    grounding: list      # [{claim, supported, source, note}]
    rationale: str


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

def _keywords(description):
    """Substantive words from the description, used to test grounding."""
    seen = []
    for raw in description.split():
        word = raw.lower().strip(".,:;!?\"'()")
        if len(word) >= _MIN_KEYWORD_LEN and word not in _STOPWORDS and word not in seen:
            seen.append(word)
    return seen


def plan_brief(seller_company, product, value_prop, target_company, company_description):
    """Expand a seller and a target prospect into the brief the draft is judged
    against."""
    seller_company = seller_company.strip()
    product = product.strip()
    target_company = target_company.strip()
    summary = company_description.strip().split(".")[0].strip()
    return Brief(
        seller_company=seller_company,
        product=product,
        value_prop=value_prop.strip(),
        target_company=target_company,
        context_summary=summary,
        pain_keywords=_keywords(company_description),
        must_include=[
            f"Address {target_company} by name",
            "Reference their specific, stated situation",
            f"Pitch {product} as the solution",
            "End with one concrete call to action",
        ],
        must_avoid=[
            f"Any product name other than {product}",
            "Unverifiable metrics or invented statistics",
            f"More than {MAX_WORDS} words",
        ],
    )


# ---------------------------------------------------------------------------
# Generator -- one interface, two implementations
# ---------------------------------------------------------------------------

def generate_draft(brief, history, scenario, use_live=False):
    """Produce the next pitch draft.

    `history` is the list of prior (Draft, Evaluation) pairs (empty on round 1);
    it carries the Evaluator's feedback so the Generator can revise. `scenario`
    drives the mock's injected defects and is ignored by the live agent.

    Dispatches to the live agent when `use_live` is set and it is actually
    available (API key + SDK present); otherwise falls back to the mock so the
    demo never breaks. If the live call itself fails (bad key, rate limit,
    network), it also falls back to the mock and records why, rather than
    crashing the app.
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
    "You are a senior SDR writing concise, personalized B2B cold outreach. "
    "Ground every line in the specific facts you are given about the prospect; "
    "do not generalize. Pitch ONLY the named product. Never invent statistics, "
    "customer counts, or capabilities, and never mention any other product. Keep "
    f"the email under {MAX_WORDS} words and end with one concrete call to action. "
    "Output only the email itself, starting with 'Subject:'. No preamble, no notes."
)


def _live_prompt(brief, history):
    """Build the user message: the brief, plus prior feedback on a revision."""
    lines = [
        f"Write a cold outreach email from {brief.seller_company} to {brief.target_company}.",
        f"Product to pitch: {brief.product}.",
        f"What it does: {brief.value_prop}.",
        f"What we know about {brief.target_company}: {brief.context_summary}.",
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
            "Your previous draft was REJECTED. Previous draft:",
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
        max_tokens=600,
        temperature=0.7,  # natural-sounding copy, still grounded by the system prompt
        # Cache the static system prompt across rounds and prospects.
        system=[{"type": "text", "text": _LIVE_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": _live_prompt(brief, history)}],
    )
    text = "".join(block.text for block in message.content if block.type == "text").strip()
    return Draft(round=len(history) + 1, text=text, source="live", active_defects=[])


def _mock_generate(brief, scenario, history):
    """Deterministic mock: composes a pitch from the brief and injects the
    scenario's defects, removing any the Evaluator has already flagged."""
    active = set(SCENARIOS.get(scenario, set()))
    for _, evaluation in history:
        active = _apply_feedback(active, evaluation)
    return _compose_mock_draft(brief, active, len(history) + 1)


def _compose_mock_draft(brief, defects, round_number):
    product = brief.product
    target = brief.target_company
    pain_clause = (brief.context_summary[0].lower() + brief.context_summary[1:]
                   if brief.context_summary
                   else "a lot of manual, repetitive work is slowing the team down")

    # Clean baseline, composed entirely from the brief.
    subject = f"{product} for {target}"
    greeting = f"Hi {target} team,"
    context = f"Reading about {target}, one thing stood out: {pain_clause}."
    value = f"{product} {brief.value_prop}."
    cta = "Open to a quick 10-minute brief next week?"
    signoff = f"Best,\nThe {brief.seller_company} team"

    # Inject defects.
    if DEFECT_GENERIC in defects:
        subject = "A smarter way to handle your operations"
        greeting = "Hi there,"
        context = "A lot of teams are still stuck doing this by hand and losing time as a result."
    if DEFECT_HALLUCINATION in defects:
        value += (f" It's already trusted by over 5,000 companies, and works "
                  f"alongside our {DEMO_WRONG_PRODUCTS[0]} analytics suite for "
                  f"end-to-end visibility.")
    if DEFECT_WEAK_CTA in defects:
        cta = "Anyway, that's a bit about what we do."

    text = f"Subject: {subject}\n\n{greeting}\n\n{context}\n\n{value}\n\n{cta}\n\n{signoff}"
    return Draft(round=round_number, text=text, source="mock", active_defects=sorted(defects))


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

_CTA_PATTERNS = [
    r"open to a", r"up for a", r"would you be open",
    r"\d+[\s-]?minute", r"quick (call|chat|brief|demo)",
    r"grab \d+", r"book a", r"schedule a",
    r"next week\?", r"this week\?",
]


def _has_cta(text):
    low = text.lower()
    return any(re.search(p, low) for p in _CTA_PATTERNS)


def _unverifiable_numbers(text):
    """Numbers that aren't on the small allowlist of approved figures."""
    cleaned = re.sub(r"\d+[\s-]?minute(s)?", "", text, flags=re.IGNORECASE)
    return re.findall(r"\b\d[\d,]*%?\b", cleaned)


def evaluate_draft(draft, brief):
    """Score a draft against the brief. Each criterion is a hard threshold."""
    text = draft.text
    low = text.lower()
    results = []

    # 1. Personalization: names the prospect AND references their context.
    has_name = brief.target_company.lower() in low
    matched = [k for k in brief.pain_keywords if k in low]
    if has_name and matched:
        fb = f"References {brief.target_company} and their context ('{matched[0]}')."
    else:
        missing = []
        if not has_name:
            missing.append(f"does not address {brief.target_company} by name")
        if not matched:
            missing.append("does not reference their stated situation")
        fb = ("Draft " + " and ".join(missing) + ". Open by naming the company "
              "and tying the first line to their actual problem.")
    results.append(CriterionResult(
        "personalization", "Personalized to the prospect", has_name and bool(matched), fb))

    # 2. Factual grounding: no wrong products, no unverifiable numbers.
    wrong = [p for p in DEMO_WRONG_PRODUCTS if p in text]
    bad_numbers = _unverifiable_numbers(text)
    if not wrong and not bad_numbers:
        fb = "No invented products or unverifiable metrics."
    else:
        issues = []
        if wrong:
            issues.append(f"mentions unapproved product(s): {', '.join(wrong)}")
        if bad_numbers:
            issues.append(f"states unverifiable figure(s): {', '.join(bad_numbers)}")
        fb = ("Draft " + " and ".join(issues) + f". Remove them -- pitch only "
              f"{brief.product} and only claims we can back.")
    results.append(CriterionResult(
        "factual_grounding", "Factually grounded (no hallucination)",
        not wrong and not bad_numbers, fb))

    # 3. Correct offering: pitches the seller's product.
    correct = brief.product in text
    fb = (f"Pitches {brief.product}." if correct
          else f"Does not name {brief.product}. State the product we are selling.")
    results.append(CriterionResult(
        "correct_offering", f"Pitches the correct product ({brief.product})", correct, fb))

    # 4. Call to action: a concrete next step.
    cta = _has_cta(text)
    fb = ("Ends with a concrete ask." if cta
          else "No concrete call to action. Ask for a specific next step, e.g. a "
               "short call next week.")
    results.append(CriterionResult(
        "call_to_action", "Has a clear call to action", cta, fb))

    # 5. Format and length: subject line present, within the word budget.
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
        fb = "Draft is " + " and ".join(parts) + "."
    results.append(CriterionResult(
        "format", "Complete and concise", ok, fb))

    return Evaluation(results=results)


# ---------------------------------------------------------------------------
# Grounding seam: check claims against a source of truth (the context graph)
# ---------------------------------------------------------------------------

def ground(claim, brief):
    """Check whether a factual claim is supported by a source of truth.

    SEAM. Today there is no context graph wired, so this returns "unknown"
    (supported=None) and defers. When the context graph lands, back this with a
    query against the state/event clocks and the relationship graph; everything
    that calls ground() stays the same. This is the grounding counterpart to the
    generate_draft() live-agent seam.
    """
    return GroundingResult(
        claim=claim,
        supported=None,
        source="",
        note="no context graph connected; grounding deferred",
    )


def extract_claims(draft, brief):
    """Pull candidate factual claims (sentences naming an entity or a figure)."""
    text = draft.text.replace("\n", " ")
    claims = []
    for sentence in re.split(r"(?<=[.?!])\s+", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        names_entity = (brief.product in sentence
                        or brief.target_company.lower() in sentence.lower())
        has_figure = bool(re.search(r"\d", sentence))
        if names_entity or has_figure:
            claims.append(sentence)
    return claims


def gather_grounding(draft, brief):
    """Ground every extracted claim. Returns a list of GroundingResult."""
    return [ground(claim, brief) for claim in extract_claims(draft, brief)]


def build_decision_trace(brief, draft, evaluation, timestamp=None):
    """Assemble the durable decision trace for one evaluated draft."""
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    rationale = ("All criteria passed." if evaluation.passed
                 else "Rejected on: " + "; ".join(r.label for r in evaluation.failed))
    return DecisionTrace(
        timestamp=ts,
        decision="approve_outreach" if evaluation.passed else "reject_outreach",
        verdict="approved" if evaluation.passed else "rejected",
        entities=[brief.seller_company, brief.product, brief.target_company],
        target_company=brief.target_company,
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


def run_pipeline(seller_company, product, value_prop, target_company,
                 company_description, scenario, max_rounds=3, use_live=False):
    """Run the full loop. Returns (brief, rounds) where rounds is a list of
    (Draft, Evaluation) pairs, one per generate-and-evaluate cycle."""
    brief = plan_brief(seller_company, product, value_prop,
                       target_company, company_description)
    history = []
    for _ in range(max_rounds):
        draft = generate_draft(brief, history, scenario, use_live=use_live)
        evaluation = evaluate_draft(draft, brief)
        # Stop if the Generator can't make progress (same draft twice).
        stalled = bool(history) and draft.text == history[-1][0].text
        history.append((draft, evaluation))
        if evaluation.passed or stalled:
            break
    return brief, history
