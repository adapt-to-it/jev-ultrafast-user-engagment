# TypeSafe/Jev research: schema, limits, and mapping for rubrics and element choice

Tags: [D] stated in TypeSafe docs, [I] my inference, [M] marketing or third-party claim.
I read every page as markdown (append `.md` to any docs URL). Index: https://docs.typesafe.ai/llms.txt. The repo's `model.py`, `questions.py`, `docs/design.md`, `README.md`, the rubric JSONs and `judgments.py` were read first.

## 1. Endpoint schema and limits

**Endpoints** [D] (https://docs.typesafe.ai/api, https://docs.typesafe.ai/models)
- `POST https://api.typesafe.ai/v1/systemone` with `Authorization: Bearer`. This is the only evaluation endpoint.
- `GET https://api.typesafe.ai/v1/models` returns `{models:[{name, description, release_date}]}`. It currently lists only the aliases.
- No batch, streaming, seed or logprob endpoints are documented.

**Request** [D]
- `state` is required: a string, a JSON object, or an array. The JS SDK also allows `null` (https://docs.typesafe.ai/sdk/javascript/api/type-aliases/EntryType).
- `model` is required.
- `questions` is a non-empty map of id to question. Ids are for your code and are never sent to the model.

**State** [D] (https://docs.typesafe.ai/concepts/state)
- Arbitrary nested JSON. Examples contain numbers, booleans and arrays inside objects, and objects with named parts are recommended.
- Questions can point at parts by backticked path, for example `` `ticket.messages[0].text` ``.
- Text only. No image, audio or video input (https://docs.typesafe.ai/models).
- English is the primary language. Others are accepted but "not equally well"; test and watch confidence (https://docs.typesafe.ai/models#language-support).

**Question types** [D] (https://docs.typesafe.ai/primitives)
- Only three exist: `choice`, `score`, `noul`.
- There is no multi-label, ranking, extraction or generation type. The documented substitutes are:
  - One Noul per label when several may apply (https://github.com/typesafe-ai/skills/blob/main/skills/typesafe-ai/SKILL.md).
  - A Choice over candidate ids for ranking (https://docs.typesafe.ai/cookbooks/semantic_find).
  - A Choice over code-found spans for extraction (https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook).
- Choice: `criteria` is a map of option to a string, object, array or null. Maximum 255 options. One cookbook says it "works reliably up to roughly 240" (https://docs.typesafe.ai/cookbooks/classification_using_confidence).
- Score: `criteria` is an ordered array of 2 to 10 levels.
- Noul: `criteria` is optional, `{true, false}`.
- `instructions` is a string, object or array on every type. Field names are free (`question`, `focus`, `what`, `not_for`, `examples`, and so on). The model sees them, so keep them short and descriptive (https://docs.typesafe.ai/primitives/advanced).
- Option names and their descriptions are sent to the model (https://docs.typesafe.ai/primitives/choice). The question id is not.

**Questions per request** [D / not documented]
- No numeric cap is documented.
- Requests with 8 Choices, 13 mixed questions, 14 Nouls and 54 mixed questions (a function-call dispatcher) are shown working in one request.
- Sources: https://docs.typesafe.ai/cookbooks/function_calling and https://docs.typesafe.ai/cookbooks/parallel_questions.
- Questions are evaluated in parallel and independently. One answer is never context for another. Batching does not change answers, only cost and time.

**Size** [D] (https://docs.typesafe.ai/models)
- 64k tokens per request in total.
- 32k tokens for `state` plus the longest single question.
- Accuracy drops as irrelevant state grows (https://docs.typesafe.ai/model-jaggedness/jev-1.13).

**Response** [D]
- `{model, answers, usage}`. `model` is the versioned id, for example `jev-1.13.0`.
- `usage` has `input_tokens` and `output_tokens` only.
- Choice answer: `{type, choice, probabilities, confidence}`.
- Score answer: `{type, score, legend, probabilities, confidence}`.
- Noul answer: `{type, noul}`. A Noul has no `confidence`.
- Choice confidence is `(pmax - 1/n) / (1 - 1/n)` (https://docs.typesafe.ai/confidence).
- The repo's `validate_choice` matches this contract. It ignores the `type` field, which is harmless.

**Models** [D] (https://docs.typesafe.ai/models)
- `jev-latest` and `jev-preview` both resolve to `jev-1.13.0` today.
- Aliases move on release. For tuned thresholds the docs advise pinning the versioned id.
- Jev is not fine-tuned per customer. Domain behaviour comes only from state, instructions and criteria.

**Latency** (https://docs.typesafe.ai/concepts/how-to-build-with-system-one)
- [D] "Most queries complete in about 100 ms." The use-case map says "real-time speeds (150ms)".
- [D] Cookbook measurements:
  - 13 questions over a 54k-character article: 0.27 s.
  - 182-option Choice: 0.09 to 0.31 s.
- [M] Homepage https://typesafe.ai shows "Completed in 0.114s". A third-party blog says 70 to 500 ms.
- No SLA is documented.

**Pricing and rate limits** [D] (https://docs.typesafe.ai/models)
- $42 per billion input tokens, which is $0.042 per million. Output tokens are free.
- Limits are 100K tokens/s and 80 requests/s. They are "adjusting dynamically" and can change without notice. Exceeding either returns 429.

**Determinism** [D]
- No temperature or seed parameter exists.
- Docs claim stable answers. In the 13-question cookbook, most answers had a standard deviation of exactly 0 over 5 repeats.
- Evidence of residual noise (https://docs.typesafe.ai/cookbooks/consistency_noul_cookbook and https://docs.typesafe.ai/cookbooks/consistency_choice_cookbook):
  - One Noul spanned 0.43 to 0.53 across 15 repeats, crossing a 0.5 threshold.
  - A borderline post flipped its top label on 2 of 8 Choices.
  - Raw top-label agreement was 90.8%. It rose to 99.2% when the app abstained below top probability 0.60.
- Those cookbooks add a throwaway `uid` field to the state. They say they cannot separate sensitivity to that field from variation on identical requests.

**Calibration** [D, with caveats]
- Jev is trained with RLCD for "calibrated probabilities" (https://docs.typesafe.ai/introduction/machine-learning-primer).
- The docs state that calibration holds across groups of predictions, not for an individual answer. They also say "typed output guarantees the interface, not truth", so validate in your own domain.
- One empirical datapoint (SEC filings, 75 classes): confidence at or above 0.9 was right 90% of the time, below it 40%. The labels there are self-reported.

**Errors** [D]
- The API page lists 401, 422, 429 and 529 (https://docs.typesafe.ai/api#errors).
- The SDK maps 400, 401, 403, 404, 422, 429 and 5xx, and connection and timeout errors (https://docs.typesafe.ai/sdk/python/api/exceptions).
- SDK defaults:
  - Retry on 408, 429 and 5xx with backoff, honouring `Retry-After`.
  - Default timeout 10 s; cookbooks use 30 to 120 s for large states.
- The repo retries only {429, 529, 503} and uses a 25 s timeout. This is compatible, but it does not retry 408 or 502/504.

**Known weaknesses of jev-1.13** [D] (https://docs.typesafe.ai/model-jaggedness/jev-1.13)
- Reads instructions literally.
- Unreliable at counting, arithmetic and date comparison. Keep these in code.
- Loses accuracy with indirection (property of a property), irrelevant state, and contradictory instructions versus criteria.
- Not trained for adversarial content. Page text can steer it.
- Leans toward the first Choice option. Reorder the options and check the answer stays consistent.
- Does not generate text.

## 2. Use cases beyond browser actions, and criteria guidance

**Documented use cases** [D]
- https://docs.typesafe.ai/concepts/use-case-map lists classification, detection, scoring, routing, retrieval, ranking, verification and extraction.
- It also lists moderation and trust-and-safety, guardrails, support triage, e-commerce listing classification, compliance, and "label passages ... using predefined themes".
- Closest cookbooks:
  - Moderation rubric as 8 Choices: https://docs.typesafe.ai/cookbooks/consistency_choice_cookbook
  - Guardrails (Noul hazards plus a harm Score): https://docs.typesafe.ai/cookbooks/llm_guardrails
  - Citation support check: https://docs.typesafe.ai/cookbooks/citation_check
  - Per-passage relevance: https://docs.typesafe.ai/cookbooks/classifying_rag_passages
  - Id-pointing search: https://docs.typesafe.ai/cookbooks/semantic_find
  - Verbatim span selection: https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook
- Tone, frustration and urgency are shown as Score, Choice or Noul on text (https://docs.typesafe.ai/api, https://docs.typesafe.ai/concepts/system-one).

**Browser navigation is not a documented cookbook** [D / I]
- The docs say Jev "does not ... choose its own next action" and warn against agent `while` loops where software can express the flow (https://docs.typesafe.ai/concepts/how-to-build-with-system-one).
- The only adjacent guidance is the skill's "Respond to changing state" (https://github.com/typesafe-ai/skills/blob/main/skills/typesafe-ai/SKILL.md). It says code keeps the goal and observations, a fresh judgment guides the next bounded step, observed facts stay distinct from inferred ones, and freshness is checked before applying a result.
- Closest documented patterns are function calling (an operation Choice plus per-function argument heads, https://docs.typesafe.ai/cookbooks/function_calling) and speculative fan-out (https://docs.typesafe.ai/patterns/fan-out). The repo's operation plus target heads is an extrapolation of these, not a documented browser recipe [I].

**Guidance on writing criteria for classification** [D]
- Describe each option so it separates from its neighbours. Use object descriptions with `what`, `not_for` and `examples`, and the same field names on every option (https://docs.typesafe.ai/primitives/choice#structured-instructions-and-criteria, https://docs.typesafe.ai/primitives/advanced).
- Add `other` or `none of the above` when the list may not cover the input (https://docs.typesafe.ai/primitives/choice).
- Choice probabilities always sum to 1, so something always wins. Add a separate Noul "does any candidate answer this" question to detect no-answer cases (https://docs.typesafe.ai/cookbooks/semantic_find).
- Ask one narrow, literal, atomic question per judgment. Put boundary cases in the criteria and make instructions and criteria agree (https://docs.typesafe.ai/model-jaggedness/jev-1.13).
- Send only the relevant state. Pre-filter candidates in code, because the model cannot choose an omitted value (SKILL.md above).
- Gate on `confidence`: act, confirm, or escalate. Thresholds scale with risk and must be tuned on your data. Examples use 0.5 to 0.6 floors, 0.8 to 0.9 for risky actions, and abstain below top probability 0.60 (https://docs.typesafe.ai/confidence, https://docs.typesafe.ai/patterns/confidence-routing).
- Test criteria on labelled cases. Higher confidence alone does not show better wording (https://docs.typesafe.ai/primitives/score).

## 3. Recommended mappings

### (a) Closed-label rubric over text snippets, with evidence

Shape of one request. This is my design [I] built from documented parts:

```json
{
  "model": "jev-1.13.0",
  "state": {"snippets": {"s1": "<text>", "s2": "<text>"}},
  "questions": {
    "label": {"type": "choice",
      "instructions": {"question": "Which label describes `snippets` ...?",
                       "focus": "Judge only the snippet text."},
      "criteria": {"clear": {"what": "...", "not_for": "...", "examples": ["..."]},
                   "vague": {"...": "..."},
                   "absent": {"...": "..."},
                   "unclear": {"...": "..."}}},
    "evidence_clear": {"type": "choice",
      "instructions": "Assume the label is clear. Which snippet states the return window and one concrete condition?",
      "criteria": {"s1": null, "s2": null, "none": "No snippet does."}}
  }
}
```

- **Label question.**
  - [D] It is a Choice whose keys are your closed labels and whose values are your rubric descriptions. Use object descriptions with `what`, `not_for` and `examples`. Always keep an explicit `unclear` or `absent` label.
  - [I] Map each existing rubric's `labels` directly onto `criteria`, and `question` onto `instructions.question`.
- **Evidence by id, not text.**
  - [D] Make the snippet ids the Choice options with null descriptions, and have code copy the chosen snippet's own text. This is the semantic_find and value-extraction pattern; the model "cannot invent a value" and code owns the string.
  - [D] Cap at 255 ids. The repo's 8 snippets of at most 600 characters are far below that.
  - [I] If a sub-snippet quote is still required, have code pre-split snippets into sentence candidates with ids and choose among those.
- **Evidence depends on the label.**
  - [D] Questions in one request cannot read each other's answers.
  - [I] Use one speculative evidence Choice per non-absent label, each stating its premise ("Assume the label is clear ..."). Consume only the one matching the chosen label. This is the same fan-out discipline as `click_target` and `type_text_target`, and the skill says to state each speculative premise explicitly.
  - [D] The alternative is a second request, which is warranted only when the first answer is needed to build the second.
- **Forced choice.**
  - [D] Pair the evidence Choice with a `none` option and/or a Noul "does any snippet state X". Accept evidence only if the label is not `absent` and the Noul clears a threshold.
  - [I] Do this per rubric.
- **Counting rules** (for example "at least two testimonials with names").
  - [D] Do not ask Jev to count. Ask one Noul per snippet ("Does `snippets.s3` ...") and sum in code (https://docs.typesafe.ai/model-jaggedness/jev-1.13, rerank cookbook).
- **Gating.**
  - [D] If label confidence is below a tuned floor, mark `unclear` and escalate. This matches the decision that Claude may re-judge low-confidence Jev verdicts.
  - [I] Start near 0.6, as in the cookbooks. Tune on a labelled set.
- **Three samples.**
  - [D] Repeated identical Jev calls are largely identical, so 3 samples with a 2/3 vote is mostly redundant, and the report already says samples are not independent raters.
  - [I] Prefer one call plus a confidence gate. For a stability check, add a second Choice with the label order reversed in the same request (the docs advise reordering to test position bias).
- **Language.**
  - [D] The rubrics and snippets are Italian, and the docs say non-English accuracy is lower.
  - [I] Test Italian snippets with English criteria against Italian criteria on a small labelled set before trusting Jev as judge.
- **Perception-type judgments.**
  - [D] Tone, frustration and urgency on text are documented Jev uses, so they are not outside its scope on paper.
  - [I] Keeping emotion, pressure and perceived clarity with Claude is still a defensible default until Italian tests pass. Visual aesthetics cannot go to Jev at all, because there is no image input. Only text or structured proxies can.
- **Rubric versioning.**
  - [I] Bump `rubrics.v1` when routing or labels change, as the constraints require.
  - [D] Log the response's `model` field and pin `jev-1.13.0` once thresholds are tuned.

### (b) "Which observed element leads to the cart, category or product page"

- **Same head structure as the existing loop.**
  - [D] A Choice over indexed candidates with a `none` option, one request, many speculative heads (function-calling and fan-out).
  - [I] One request carries heads such as `cart_link`, `category_link` and `product_link`. Each Choice is keyed by element index, with `none: "No listed element ..."`. Code decides the funnel stage and consumes only the matching head.
- **Per-element criteria.**
  - [D] Objects are allowed as option descriptions.
  - [I] Give each element `{element, role, region/in_card, destination hint}`. Today `model.py` sends label, role, value and state flags but not the link target, even though `snapshot.js` reads `href`. Adding the href path or destination text should help a lot, because the question is about where the link goes. Check that the element's action data actually carries it.
- **Phrase the question literally.**
  - [D] Jev reads literally and is weak on indirection.
  - [I] Ask "Which element is the link or button that opens the shopping cart page?" instead of "which element advances the user toward purchase".
- **Detecting "none".**
  - [D] A Choice always picks something, so add the `none` option and a Noul such as "Does any listed element open the cart page?".
  - [D] The skill-suggestion cookbook combines a Choice with per-candidate "fits" Nouls and a threshold, and can reject all candidates.
- **Candidate cap and pre-filter.**
  - [D] Maximum 255 options, and send only relevant state.
  - [I] The repo caps candidates at 250. Pre-filter with the deterministic lexicon so Jev chooses among the top K (for example 30 to 60) rather than 250. Keep the lexicon crawler first, as decided.
- **Token budget.**
  - [I] Rough worst case is 250 elements in the state plus up to three 250-option target heads. That is probably 20 to 35k tokens, close to the 32k state-plus-longest-question limit. A 20k-token request costs about $0.0008.
  - [I] The page `text` (up to 6000 characters) is probably unnecessary for the crawler fallback and adds distraction. Send url, title, stage and the element table.
- **Verification.**
  - [D] "Typed output guarantees the interface, not truth."
  - [I] After the click, verify the arrival with the deterministic page-type check, never with Jev's choice. Never retry the mutation, and log before observing, as the constraints state.
- **Position bias.**
  - [D] Jev leans toward the first option.
  - [I] Elements are listed in DOM order, which is mostly fine. Add an occasional order-reversed check in offline evaluation.
- **Numbers.**
  - [D] Keep price comparisons in code. Let Jev choose only semantically, for example "which card is the running shoe", after code has filtered by parsed price.

## 4. Not documented (verify live)

- A maximum number of questions per request.
- Whether option keys with arbitrary characters (the repo uses `2:3`) are always accepted.
- Behaviour with mixed Italian and English in one request.
- Server-side caching of identical requests.
- Any SLA for latency.
- The latency of 20k-token requests. The published figures come from short or medium states.

Live results should come from the smoke test only. No live-Jev claim should go into the README until it has been run.

## Main URLs

- https://docs.typesafe.ai/api
- https://docs.typesafe.ai/models
- https://docs.typesafe.ai/concepts/state
- https://docs.typesafe.ai/primitives
- https://docs.typesafe.ai/primitives/choice
- https://docs.typesafe.ai/primitives/score
- https://docs.typesafe.ai/primitives/noul
- https://docs.typesafe.ai/primitives/advanced
- https://docs.typesafe.ai/confidence
- https://docs.typesafe.ai/concepts/how-to-build-with-system-one
- https://docs.typesafe.ai/model-jaggedness/jev-1.13
- https://docs.typesafe.ai/patterns/fan-out
- https://docs.typesafe.ai/patterns/confidence-routing
- https://docs.typesafe.ai/cookbooks/function_calling
- https://docs.typesafe.ai/cookbooks/semantic_find
- https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook
- https://docs.typesafe.ai/cookbooks/skill_suggestion
- https://docs.typesafe.ai/cookbooks/consistency_choice_cookbook
- https://docs.typesafe.ai/cookbooks/consistency_noul_cookbook
- https://docs.typesafe.ai/cookbooks/classification_using_confidence
- https://docs.typesafe.ai/sdk/python/api/exceptions
- https://github.com/typesafe-ai/skills/blob/main/skills/typesafe-ai/SKILL.md

Local copies of the fetched pages are in `/tmp/claude-0/-home-user-jev-ultrafast-user-engagment/9ee195d3-2de4-500b-9be4-a6d170ecb762/scratchpad/ts/` (the `c_*.md` files have JavaScript noise stripped). No repository files were edited.