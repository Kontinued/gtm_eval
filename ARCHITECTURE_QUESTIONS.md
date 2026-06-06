# GTM Agent — Things to Resolve

Open questions for the next architecture session. The current whiteboard is
directional (context graph, RAG, memory, orchestration/retrieval agents, MCP
tools, recursive LLMs). These are the decisions that should be made *before*
we sink weeks into any one subsystem. Ordered by leverage.

Context: that whole architecture is the **generator** — the live system that
will replace the mock in our prototype's `generate_draft()`. The prototype's
Planner/Generator/Evaluator loop and its evaluator are the verification layer
that gates the generator's output before it sends.

---

## 1. The GTM motion (job before tech)

- What does our ideal GTM motion actually do, step by step?
  (source → research → qualify → personalize → send → follow up → log)
- Which single step are we automating first?
- We are our own first customer — whose real GTM process are we copying?

**Why it matters:** the board is component-first with no defined motion to
serve. Risk: building retrieval/memory infra before deciding what it's for.

## 2. The thinnest end-to-end slice

- What is the minimal path — one data source, simple retrieval, one response,
  one eval — that takes a single prospect all the way to a verified, sendable
  pitch?
- Can we get that one loop running before scaling any subsystem?

**Why it matters:** four polished but disconnected components teach us less than
one thin slice that actually runs end to end. Avoid horizontal builds.

## 3. Data reality (the retrieval inputs)

- Do we have auth, coverage, and acceptable data quality from Slack / email /
  GitHub / "other info" today?
- How fresh does the context need to be, and who owns each source connection?

**Why it matters:** stale or junk context makes the generator hallucinate no
matter how good the agents are. This is usually what actually sinks "company
brain" projects.

## 4. The evaluator and "good enough to send"

- Where does the verification layer sit? (Proposed: between the response agent
  and "send" — pass → send, fail → feedback back to the orchestration agent.)
- What must the context graph contain so the evaluator can ground a claim
  against it as a source of truth?
- What is the concrete definition of "good enough to send" for a GTM pitch?
- Who owns this layer? (It is the one box on the board with no owner; the
  prototype already implements it.)

**Why it matters:** for an agent that emails real prospects, this is the gate
that stops a hallucinated pitch from costing a deal. It is also the stopping
condition for the recursive LLM loop.

## 5. Tool consolidation

- The board lists three overlapping stores: vector DB, Supermemory, HydraDB.
  What does each one buy us that the others do not?
- Can memory collapse to the minimum? Same question for MLflow and the
  Agents SDK — load-bearing or incidental?

**Why it matters:** our thesis is "integrate, don't boil the ocean." Every store
is auth, sync, and maintenance we carry. Three memory systems is the opposite.

## 6. Cost ceiling, termination, and a failure log

- What does one run cost, and what stops a recursive run?
- Can we stand up the simplest diagnostic log now — task → succeeded/failed →
  which layer caused the failure?

**Why it matters:** recursion without a gate burns money and loops. The failure
log is how we find the real bottleneck instead of guessing (per the harness
notes' diagnostic loop).

## 7. Interfaces between owners

- What exact data shape does the retrieval agent hand the orchestration agent?
  The response agent hand the evaluator?
- Can we agree these hand-off shapes early?

**Why it matters:** this is the "does our state tracking line up" problem at team
scale (Jeff, Logan, Sachin, Konark working in parallel). Agree the contracts
early or integration will hurt.

---

## If we look at only one thing first

Map the actual GTM motion (#1) and force it through a thin end-to-end slice (#2).
Most of the rest — which memory store, where the eval sits, what data we need —
gets *answered* by running one real prospect through the whole thing, and stays a
whiteboard debate until we do.
