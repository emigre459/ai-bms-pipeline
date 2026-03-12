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

### BMS Image Data Extractor
While this is using a response schema structure to guarantee valid output compared to the original schema provided, I have not had time to validate a meaningfully-sized set of the inputs against their extracted data. Additionally, there is a bug in the current code that makes it error out the full image data extraction step at about 49% completion which I have yet to debug (although I have added enough error checking since then that it may be a non-issue).

## Stage 2

As in the preceding stage, there is work to be done to determine the consistency and quality of the ECMs being generated at this step. Enough deterministic calculations/calculation checks are being done for the numeric fields that I am reasonably confident in them, but my lack of commercial building EE domain expertise made it difficult to determine how accurate each analysis was. The model was directed to the domain knowledge content distilled (in `analysis_domains.md`) from the original task description such that it likely did a decent job adhering to those concepts and some very basic spot checks indicate alignment, but there is a lot more work that would need to be done to validate these findings. 


# What this could become with more time
## General
Overall, it would be beneficial to have ground truth about various building parameters (e.g. known location, known size, number of floors, etc.) that would enable much more granular LLM-generated data checks to protect against hallucinations or wild speculation. There is also the potential to treat uncertainty/confidence in our findings with this pipeline based on "evidence quality". The LLMs are already generating confidence estimates for the generated ECMs using data observability and should continue to do so: if all the data necessary for a finding can be directly observed in a BMS screenshot (or series of screenshots when the building ID is well-known to be the same for all of them), then high confidence is warranted. If findings rely in part on data assumptions (e.g. no OAT visible in a screenshot but day and time can provide a daily average temperature for the area), then a medium level of confidence is possible. If multiple points of inference or estimation are required for a finding to be valid, we can safely categorize it as low-confidence.

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
## General
1. A lot of this needs to be more battle-tested. I have done spot checks of roughly 5-10 images that should be used for this pipeline (AKA they show useful building operational data) and another set of the same size that should *not* be used. Our two stages together seem to do a decent job of ignoring the irrelevant images by the point of analysis, but that's a lot of wasted tokens when our initial classifier should do a much better job much earlier and have a much lower false positive rate. 
2. Overall, the pipeline would benefit from more evals. Where possible and obvious, I have included deterministic checks or even direct calculations of analysis outputs (e.g. for calculating electricity costs) to avoid the random nature of LLM outputs skewing results. But this is simply a starting point. My spot checks during the development process are a very rough "gut check" not a thorough "I've run this through 10,000 images meant to confuse the pipeline and it has an accuracy of 90%". 

## Stage 1
1. Regarding the classification step, this kind of computer vision problem can be reasonably well addressed by training a custom vision model on our exact task (e.g. as has been done for segmentation and classification in self-driving cars). Alternatively we could significantly improve our initial classifier using modern LLMs and a toolkit I've worked with before called [DSPy](https://dspy.ai/) that essentially serves to optimize LLM prompts using (small, 20-30 examples only) query-response pairs. Improving the initial classification step in Stage 1 would significxantly improve token usage and latency (and likely downstream accuracy as well).


## Stage 2
1. Given the level of domain-specificity in terms of energy efficiency recommendations required at this stage, review by a human expert analyst is crucial to better identify the blind spots in the system. The available reasoning models (with web search enabled) are very powerful indeed, but likely the real value of a product like this comes from the long tail of infrequent, but high-value recommendations that are probably poorly represented on the public Internet and in the models' training data. 
    * Depending on the level of documentation of a company choosing to implement this pipeline, it is possible we could supercharge the quality of this project through [RAG](https://en.wikipedia.org/wiki/Retrieval-augmented_generation) tooling that enables enrichment of the process through company-specific (and private) information and analyst findings that have been field-tested with real customers.