# Architecture

Two pipeline stages run in sequence:

**Stage 1 — Image → JSON:** Each BMS screenshot passes a classifier call (`is_bms_screenshot`) that filters out irrelevant images. Images that pass are sent to an extraction call that populates `bms-snapshot.schema.yaml`. A data-quality score at analysis time acts as a secondary gate, discarding extractions with too many null fields before they reach the LLM.

**Stage 2 — JSON → Analysis:** Per-building snapshots feed a deterministic rule engine that flags specific measurable patterns (HWS temperature vs. OAT, fan speed imbalance, economizer state, simultaneous heating/cooling, out-of-schedule operation, static pressure, supply air temperature). These findings are passed as grounded evidence to an LLM synthesis call that produces the full `analysis-output.schema.yaml` payload: ECMs, key findings, priority actions, and open questions.

A key structural decision: the LLM produces flat ECM fields (energy quantities + enums only); code then deterministically expands them into the nested savings/implementation/totals structure and recomputes all cost and carbon figures from `DEFAULT_FACTORS`. This keeps arithmetic auditable and independent of LLM variation.


# Key Tradeoffs

## Stage 1: Classifier

The classifier is a single LLM call making a boolean decision on inherently ambiguous input — intentionally simple for a prototype focused on end-to-end viability rather than production precision/recall.

Known limitations:
- Borderline screenshots (partial UI, floor plans, blurry captures) sit on a fuzzy boundary; there is no confidence calibration or deterministic pre-filter before the LLM call
- False positives pass sparse images downstream where they are caught only by the data-quality score at analysis time, wasting extraction tokens in between

## Stage 1: Data Extraction

`output_config` JSON schema enforcement constrains the LLM's response structure, but not semantic correctness — the model could return plausible-looking values that don't match what is actually on screen. A labeled validation set from expert review would be needed to quantify this.

Two schema-level decisions worth noting:
- The compact snapshot format (each snapshot serialized as a JSON string inside an array item) was required to work around grammar size limits in Anthropic's structured output engine when using deeply nested schemas
- Allowable values for constrained fields (season, mode, control_source) live as YAML inline comments rather than explicit lists — functional for a prototype but not machine-enforceable at write time

## Stage 2: Analysis

The deterministic pre-checks ground the LLM on the most measurable patterns, which reduces hallucination risk for those findings. More qualitative observations (scheduling intent, recommissioning, ventilation) are inherently more speculative, and ECM confidence is labeled accordingly.

Energy factors use US commercial averages as defaults. Building-specific utility rates would meaningfully improve cost and carbon estimates.


# What I Would Do Differently With More Time

## Stage 1: Classifier

- Move from boolean output to `label + confidence + evidence`; set threshold by risk preference (high recall is preferable here — dropping a valid BMS image is costlier than passing a borderline one)
- Add deterministic pre-checks (OCR + HVAC keyword/units scoring) before the LLM call to reduce unnecessary API spend
- Build a labeled eval set; use prompt optimization tooling like [DSPy](https://dspy.ai/) to systematically improve recall on borderline cases with minimal labeled examples
- Add a low-confidence routing path (retry, alternate model, or human review queue) rather than a hard binary decision

## Stage 1: Data Extraction

- Expert analyst spot-checks to build a labeled ground-truth dataset for field-level accuracy measurement
- Formalize allowable values for constrained fields as explicit lists in the YAML schema rather than inline comments, so schema enforcement and Pydantic validation are driven by the same source of truth
- Move output from JSON files to a document store (MongoDB, Firestore) to enforce schema constraints at write time and support faster downstream querying

## Stage 2: Analysis

- RAG over company-specific, field-tested analyst findings would substantially improve the long-tail, high-value recommendations that are underrepresented in public training data
- Ground-truth building metadata (location, size, floor count, occupancy profile) would constrain LLM speculation and enable more specific ECM sizing
- More deterministic checks: the current rule engine covers the seven patterns in the primer, but production would benefit from additional rules as patterns are observed and validated in real data

## General

- A frontend (e.g. React) that traces each input image through to its output analysis would make the pipeline reviewable for non-technical users and support human-in-the-loop quality control
- A chat interface with an expert analyst agent (web search + RAG) would let analysts and building owners interrogate findings, challenge assumptions, and propose new ECM categories on demand — the most interesting UX innovations in agentic AI tend to accelerate and augment existing workflows before defining new ones


# Evaluating Production Readiness

## Stage 1

The classifier has no formal evaluation today — only spot checks across a small handful of positive and negative examples. The right approach is a labeled eval set (a few hundred images minimum) with precision/recall tracked at a defined threshold, and regression testing on any model or prompt change.

Token efficiency is also directly measurable: the classifier false-positive rate drives wasted Stage 1 extraction calls. Tracking it is the highest-leverage near-term improvement to both cost and downstream accuracy.

## Stage 2

Human expert review is the primary quality bottleneck. The deterministic checks are verifiable; the LLM narrative is not. A reasonable evaluation sequence:
1. Have domain experts review a sample of generated analyses and score ECM plausibility, completeness, and savings estimate reasonableness
2. Identify recurring failure modes (missing ECMs, wrong systems cited, implausible savings figures)
3. Convert agreed-upon findings into regression test cases that can be verified deterministically via the schema validator and totals logic

The deterministic pre-check layer means that for the seven rule-based patterns, correctness can be unit-tested directly against snapshot fixtures — this already exists in the test suite and should expand as new rules are added (and ideally we can set this up as new entries in a database to streamline the addition of new analysis domains we want represented as time goes on and technologies change).
