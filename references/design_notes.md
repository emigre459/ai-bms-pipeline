# Purpose

Herein I document the various decisions, tradeoffs, and considerations associated with the buildout of this pipeline. Please see below the various discussion points requested.

# Classifier Reliability Notes (Prototype Scope)

The current image classifier gate (`is_bms_screenshot`) is intentionally kept simple because this project is a prototype focused on proving end-to-end viability (image -> structured extraction) rather than maximizing classifier precision/recall in production.

Why classifier outputs can be flaky in this version:
- The gate is a single LLM vision decision on a fuzzy boundary (what counts as "BMS-relevant").
- Borderline screenshots (partial UI, floor plans, weak telemetry context, blurry captures) are inherently ambiguous.
- Structured-output fallbacks can introduce response-shape variability when model or schema constraints are hit.
- There is no confidence calibration layer or deterministic pre-filter before the LLM call.

Known ways to improve classifier reliability:
- Move from boolean-only output to `label + confidence + evidence` and threshold by risk preference.
- Add deterministic pre-checks (OCR + HVAC keyword/units scoring) before LLM classification.
- Use stronger rubric prompts and include positive/negative few-shot examples.
- Add self-consistency voting (multi-pass classification) for borderline cases.
- Build a labeled eval set and tune thresholds to maximize target metrics (for this stage, likely high recall to avoid dropping valid BMS images).
- Add a low-confidence routing path (retry, alternate prompt/model, or human review queue).

# Key Tradeoffs Made

- Prototype classifier quality metric (fill in later):
  - Current classifier F1 on labeled validation set: **[PLACEHOLDER - TO BE MEASURED]**


# What this could become with more time


# Evaluating the system's production-readiness