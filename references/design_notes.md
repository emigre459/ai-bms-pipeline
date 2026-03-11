# Purpose

Herein I document the various decisions, tradeoffs, and considerations associated with the buildout of this pipeline. Please see below the various discussion points requested.


# Key Tradeoffs Made
## Stage 1 
### BMS Image Classifier

The current image classifier gate (`is_bms_screenshot`) is intentionally kept simple because this project is a prototype focused on proving end-to-end viability (image -> structured extraction) rather than maximizing classifier precision/recall in production.

Why classifier outputs can be flaky in this version:
- The gate is a single LLM vision decision on a fuzzy boundary (what counts as "BMS-relevant").
- Borderline screenshots (partial UI, floor plans, weak telemetry context, blurry captures) are inherently ambiguous.
- Structured-output fallbacks can introduce response-shape variability when model or schema constraints are hit.
- There is no confidence calibration layer or deterministic pre-filter before the LLM call.

- Prototype classifier quality metric (fill in later):
  - Current classifier F1 on labeled validation set: **[PLACEHOLDER - TO BE MEASURED]**


# What this could become with more time
## Stage 1
### BMS Image Classifier
Known ways to improve classifier reliability:
- Move from boolean-only output to `label + confidence + evidence` and threshold by risk preference.
- Add deterministic pre-checks (OCR + HVAC keyword/units scoring) before LLM classification.
- Use stronger rubric prompts and include positive/negative few-shot examples.
- Add self-consistency voting (multi-pass classification) for borderline cases.
- Build a labeled eval set and tune thresholds to maximize target metrics (for this stage, likely high recall to avoid dropping valid BMS images).
- Add a low-confidence routing path (retry, alternate prompt/model, or human review queue).

### BMS Data Extraction
- Expert analyst review and spot-checking (along with creation of a ground truth dataset) would go a long way for properly interpreting borderline data (e.g. an image that says "76.1 F" at the top of the screen with the rest of the building metadata *might* be the OAT but might not be)
- Setting up a NoSQL database (to capture the time series and nested nature of the data being extracted) like MongoDB or Firestore to capture structured data instead of output JSON files would enable greater post-analysis speed and just generally more consistency (e.g. disallowing wrongly-typed structured data outputs from the LLM beyond the response schema constraints I'm already imposing)
- Add allowable values (for constrained fields) as explict lists in the YAML configs instead of just capturing them as inline comments. This will allow more consistency since right now we have to rely on on-demand LLM calls (e.g. via Cursor) to populate the pydantic schemas from the YAML comments

## Stage 2
- Add allowable values (for constrained fields) as explict lists in the YAML configs instead of just capturing them as inline comments. This will allow more consistency

# Evaluating the system's production-readiness
## Stage 1

## Stage 2
