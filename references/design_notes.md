# Purpose

Herein I document the various decisions, tradeoffs, and considerations associated with the buildout of this pipeline. Please see below the various discussion points requested.


# Key Tradeoffs Made
## Stage 1 
### BMS Image Classifier

The current image classifier gate (`is_bms_screenshot`) is intentionally kept simple because this project is a prototype focused on proving end-to-end viability (image -> structured extraction) rather than maximizing classifier precision/recall in production.

Why classifier outputs can be flaky in this version:
- The gate is a single LLM vision decision on a fuzzy boundary (what counts as "BMS-relevant").
- Borderline screenshots (partial UI, floor plans, weak telemetry context, blurry captures) are inherently ambiguous.
- There is no confidence calibration layer or deterministic pre-filter before the LLM call.

In truth, the classifier is a very rough "first filter" but can result in structured data that is essentially all nulls when returns a false positive. The analysis stage is useful as it runs a secondary effective filter based on the structured results (e.g. more than X nulls will result in it assuming the original image was not operationally-relevant and that it is a false positive from stage 1).

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

## General
To enhance user-friendliness for both analysts and their audiences (e.g. building owners), it would make a lot of sense to add a proper front end to this system (e.g. React.ts) that enables both visualization of the pipeline steps (tracing input image to output analysis and each step in between) for human evaluation, but a chat interfacewith an expert analyst agent that includes web searching at a minimum as an agent tool. Being able to ask more information about source citations (e.g. for electricity cost data) or even throw an idea in for new savings opportunities that were never explored before (e.g. adding another element to `analysis_domains.md` and an expert agent to own it that could be called upon as needed based on the conversation). Generally speaking for agentic AI, some of the most interesting innovations yet to be realized are in the UI/UX. GitHub Copilot was such a success early on not just because it enabled "hyper autocomplete" but because it didn't try to provide value initially through a chat box alone: it provided inline support and accleration of existing workflows before helping define entirely new workflows. 

# Evaluating the system's production-readiness
## Stage 1

## Stage 2
