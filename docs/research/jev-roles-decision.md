# User decision (2026-10-07): bring Jev back as the fast navigator and judge

The user noticed that in the plugin flow Claude replaced Jev as journey pilot and as judge, losing the speed that is the
point of the repo. Decisions (verbatim intent):

1. **Jev navigates and judges.** Jev (TypeSafe choice model) is the default journey pilot whenever TYPESAFE_API_KEY is
   available (CLI and plugin/MCP). Jev also judges the rubric tasks it can handle: closed-label, operationally defined
   questions over extracted snippets. Evidence stays verbatim by having Jev *choose snippet ids* (a choice head over the
   offered snippets), never by generating text.
2. **Claude for feelings, emotions and what Jev cannot understand.** Perception-type judgments (emotional tone, pressure,
   reassurance, perceived clarity of the value proposition, aesthetic/first-impression aspects, anything needing nuanced
   reading) stay with Claude through the existing host-judges protocol (subscription via Claude Code; API fallback).
   Claude is also the fallback when no TypeSafe key is set, and may re-judge Jev's low-confidence verdicts.
3. **Jev as fallback in the audit crawler**: the deterministic lexicon crawler stays first (fast, reproducible); when it
   cannot find the next funnel stage, ask Jev which observed element to click (choice over observed elements, never
   selectors), then continue deterministically.
4. **Live test**: the user will run the live TypeSafe test locally from Claude Code. Provide a ready smoke command/script
   (local fixture shop + optional real URL) that exercises Jev pilot, Jev judge and crawler fallback and prints timings,
   model-call counts and pass/fail. Automated tests stay offline (stand-ins with the same contracts; never call paid APIs).

Constraints: keep AGENTS.md rules (Jev only chooses observed indices / offered snippet ids; never retry mutations; log
before observing; DONE is not proof; versioned anchors/rubrics: bump versions when rubric routing or labels change; keep
README claims consistent and do not claim live Jev results that were not run).

## Additional user guidance (binding): the original library already uses Jev optimally
Reuse the original Jev loop as it is instead of building new layers around it:
- Jev-piloted journeys ARE the original loop: `Agent` + `model.choose` (one TypeSafe request carrying the operation head and
  the per-operation target heads), `questions.py` rules, the TYPE_TEXT text helper and its cache rule, `Browser` freshness
  guards. Engagement code only wraps it for measurement (marks, steps.jsonl, guard filtering, oracle); it must not fork,
  duplicate or re-implement decision logic, prompts or request building.
- The crawler fallback should ask Jev the same way the loop does (a goal such as "open the cart page" over the observed
  element table through `model.choose`/`action_space`, consuming only the CLICK target), not a bespoke question format,
  unless the original contract cannot express it.
- The Jev judge should follow the same request style as `model.choose` (state + choice questions, `post_json`,
  `validate_choice`, one request per page with several heads), with minimal new code; keep the "one round trip" spirit.
- Speed is the point: count TypeSafe requests and keep them to one per decision / per judged page.
