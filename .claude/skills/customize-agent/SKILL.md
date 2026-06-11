---
name: customize-agent
description: Guide the user through customizing their agent. Use when the user says "customize my agent", "customize the agent", "set up my agent's persona", "configure the agent", "make my agent like X", or otherwise wants to shape this agent's identity, persona, methodology, conversation structure, voice, boundaries, or knowledge-base behavior. Walks a checklist one item at a time and writes to settings.json + agents/<projectName>/customization.json, then re-renders system_prompt.txt via ./render.
---

# Customize your agent

You are helping a user customize the voice-enabled AI agent in this repo. You are running in their **agent project repository** (the cloned template).

Your job is to walk the user through a checklist, one item at a time, and persist their answers as structured JSON in `agents/<projectName>/customization.json`. After each edit, you re-render the system prompt by running `./render`. The user is **not** expected to know how to write a system prompt — frame everything as goal-oriented questions about how they want the agent to behave. The Jinja2 templates assemble the prompt from your JSON.

The goal is that a user who answers thoughtfully gets a system prompt comparable in depth to a hand-crafted one (audience, methodology grounding, structured conversation flow, an interaction loop with self-critique, voice traits, boundaries, knowledge-base instructions, and a language anchor) — without ever having to read or write the prompt themselves.

---

## Answer quality — push back on weak inputs

The quality of the generated agent is bounded by the quality of the user's answers. Apply this gate to **every** item.

Before accepting an answer and writing it into `customization.json`, score it **internally** against the criteria in `gold-standards/shared_quality_rubric.md` (Specificity, Use-Case Fit, Coherence, Voice Quality, Safety and Boundaries). You don't need to run the full ten-criterion rubric on each answer — just spot-check the criteria that the item is meant to satisfy. **Do not show numeric scores to the user.** The rubric is a private review tool.

### When an answer is weak

If the user's answer is **sparse, generic, ambiguous, or off-target**, do NOT silently accept it. Push back in plain language (never quote rubric scores or criterion names at the user). Pick one:

1. **Ask for more.** *"That's a start — can you give me one concrete example? Right now this is on the generic side, so the agent will sound generic too. Even one sentence of detail makes a real difference."*
2. **Offer a menu.** *"I can take that two ways: (a) {interpretation-A}, or (b) {interpretation-B}. Which is closer? Or describe it your way."*
3. **Show what 'good' looks like.** Briefly quote one or two sentences from the matching gold standard (e.g. `gold-standards/sales_coach.md` for `useCase === "sales_coach"`, `sme.md` for `useCase === "sme"`). *"For comparison, the sales-coach gold standard says: '{audience}: enterprise sellers at a systems integrator working financial-services deals'. That level of detail makes a real difference."*
4. **Let them proceed with a flag.** If the user says they're fine with the weak answer, accept it but record it internally: *"OK — noting this is light on specifics. We can revisit later by re-running this item. Moving on."*

Signals an answer is weak:

- Single-word or single-phrase response ("sales", "helpful", "yes") when the question asks for a description.
- Generic placeholders ("users", "customers", "people", "help them with stuff").
- The same boilerplate that's already in the starter customization ("a helpful, friendly assistant").
- Empty / whitespace / "idk" / "whatever" / "you decide".
- Contradicts an earlier answer or `settings.json` (e.g. claims audience = executives but earlier picked `purpose: coach` with sellers).

If the user genuinely doesn't know, **don't bluff a default**. Ask one orienting question ("who's the most likely first 10 users?", "what's the one thing this agent must get right?") and infer from that.

### Final review at exit (silent, agentic)

When the user types `done` (or you've completed the last item), run a **full rubric pass internally** before declaring success. **Do not print the rubric, scores, or a score table to the user.** Hold the scores in your own head and act on them.

1. Re-render with `./render` so you're scoring the current output. Read the final rendered `agents/<projectName>/system_prompt.txt`, the `settings.json` agent fields (`title`, `greeting`, `description`, `description2`), and any sample questions.
2. Score the ten criteria from `gold-standards/shared_quality_rubric.md` (Coherence, Specificity, Use-Case Fit, Method and Interaction Design, Grounding, Voice Quality, Adaptation, Safety and Boundaries, Prompt Maintainability, Behavioral Testability). Use the gold standard matching `customization.json.useCase` (sales_coach → `gold-standards/sales_coach.md`, sme → `gold-standards/sme.md`) as the structural reference.
3. Check against the rubric's **Scoring Threshold**: avg ≥ 4.0, every individual criterion ≥ 4 on Coherence / Specificity / Use-Case Fit / Grounding / Voice Quality / Safety and Boundaries, no criterion at 1.
4. **If the prompt clears the bar**, proceed directly to the "When the user is done" closing message. Don't volunteer that you scored it.
5. **If the prompt falls below the bar**, act agentic: pick the **single weakest criterion** that the user can plausibly improve with one sentence of input, then ask **one** focused clarifying question — in plain language, *not* in rubric vocabulary. Examples (translate the gap into a human question; do not quote the criterion name):
   - Specificity weak on audience → *"One more thing before we wrap — when you picture the typical user, what's their role and what are they trying to get out of a session? Even one sentence sharpens this a lot."*
   - Voice Quality weak (replies sound written) → *"Quick check: should this agent sound more like a quick chat with a colleague, or more like reading from a doc? Anything you want it to never say?"*
   - Safety and Boundaries weak (no refusal pattern) → *"What kinds of questions should this agent decline or redirect — e.g. HR topics, anything outside its domain, prompt-extraction attempts?"*
   - Grounding weak (no source policy) → *"When the agent doesn't have a confirmed answer in its knowledge base, what should it do — say so honestly, fall back to general knowledge, or refuse?"*
   - Method and Interaction Design weak (no conversation shape) → *"How should a typical session flow — should the agent move the user through stages, or just respond to whatever they bring?"*
6. Take the user's answer, revise the relevant fields in `customization.json` (and `settings.json` fields if affected), re-run `./render`, and re-score silently. You may do **at most two** silent revision passes per `done`. After two passes, stop — don't badger the user.
7. If after two passes the prompt still falls short, mention it once, naturally, in the closing message — *"I tightened a couple of things. There's still room to sharpen {one or two areas in plain English, e.g. 'the audience description' or 'how the agent should handle off-topic questions'}; you can re-run me anytime to revisit."* Don't dump scores or criterion names.
8. **Flag unchanged starter defaults.** Check `settings.json.checklist` for items still set to `false`. For each unchecked item, check whether the corresponding fields in `customization.json` still match the starter pack values (i.e., the user never touched them). If any do, mention them naturally in the closing message — *"A few sections are still using starter-pack defaults: {list item names, e.g. 'methodology', 'voice traits', 'boundaries'}. They're fine for initial testing — come back and customize them when you have more specifics about your use case."* Keep it to one sentence; don't list the actual default values.

Do **not** copy the rubric into `customization.json` or the rendered system prompt. Do **not** print the score table. The rubric is an internal review tool that drives the one-question revision loop above.

---

## What gets customized

| Surface | Where it's stored | How it ships |
|---|---|---|
| **App title** (header + browser tab) | `customization.json` → `frontendCopy.title` **and** `settings.json` → `agent.title` | Build arg `VITE_APP_TITLE` (re-deploy) |
| **Greeting** (first thing the agent says) | `customization.json` → `frontendCopy.greeting` **and** `settings.json` → `agent.greeting` | Env var `AGENT_GREETING` (re-deploy) |
| **Landing-page copy** (Overview paragraphs + CTA button) | `customization.json` → `frontendCopy.description`, `frontendCopy.description2`, `frontendCopy.buttonLabel` **and** `settings.json` → `agent.description`, `agent.description2`, `agent.buttonLabel` | Build args (re-deploy) |
| **System prompt** (persona, methodology, conversation, voice, boundaries, KB) | `customization.json` (structured fields) | Rendered to `agents/<projectName>/system_prompt.txt` by `./render`, mounted into container, read by backend |

`customization.json` is the source of truth for prompt content. `system_prompt.txt` is a rendered artifact — never edit it directly; your changes will be overwritten on the next render.

`frontendCopy.*` is written to BOTH `customization.json` (long-term source of truth) and `settings.json` (what `deploy.js` currently reads for Docker build args). The double-write is a transitional measure; long-term, `deploy.js` will read frontend copy from `customization.json` directly.

After making changes, the user must run `./deploy` for them to take effect.

---

## On invocation, follow this script EXACTLY

### Step 1 — Read context

Read `settings.json` at the repo root. If it doesn't exist, tell the user to run `./setup` first and stop.

Read `agents/<projectName>/customization.json`. There are three cases:

1. **customization.json exists** (normal case) — proceed. Note the `useCase` field; you'll use it to pick the matching gold standard for rubric scoring.
2. **customization.json missing, system_prompt.txt exists** (legacy / pre-data-first cartridge) — offer to bootstrap: *"You have a custom system prompt at `agents/<projectName>/system_prompt.txt` but no `customization.json`. The data-first model is now how this template handles customization — your edits go in JSON and the prompt is rendered. Would you like me to extract your current configuration into the data-first format?"* If they say yes, read `system_prompt.txt` and extract structured field values (persona, audience, tone, methodology, boundaries, knowledge-base prose) into a fresh `customization.json`. Pick `useCase` based on `settings.json.agent.purpose` (coach → `sales_coach`, otherwise → `sme`). If they say no, exit and let them keep the existing prose path. Explain that `./deploy` will continue using the existing `system_prompt.txt`, but this skill only edits the data-first JSON model; they can come back later to bootstrap `customization.json`.
3. **Both missing** — tell the user to run `./setup` first; it will copy a starter customization.json from `templates/starters/` and run the initial render.

Make sure the `checklist` object in `settings.json` has all the keys listed in the checklist below. Add any missing keys with value `false` (not done) or `null` (n/a — only for items gated on `agent.digitalTwin`). Persist `settings.json`.

### Step 2 — Show the checklist

Render it like this, using the actual state from `settings.json`. Use `[x]` for `true`, `[ ]` for `false`, and **omit** lines whose value is `null`.

```
Here's your agent customization checklist. Pick a number to work on, paste a description / writeup and I'll synthesize a draft, or type 'done' to exit.

APP IDENTITY (what users see on the page)
  [ ] 0. App title and greeting

WHO IS THIS AGENT?
  [ ] 1. Goals, audience, success
  [ ] 2. Personality and tone
  [ ] 3. Methodology or framework to ground in

HOW DOES IT TALK?
  [ ] 4. Conversation structure
  [ ] 5. Interaction loop (reflect / elicit / self-critique / feedback)
  [ ] 6. Voice traits — catchphrases, metaphors, fillers

GUARDRAILS
  [ ] 7. Boundaries and refusals
  [ ] 8. Knowledge base usage rules

DIGITAL TWIN (only shown if agent.digitalTwin is true)
  [ ] 9. Provide source material for phrase extraction
  [ ] 10. Review and finalize extracted phrases

VOICE PROFILE
  [ ] 11. Custom voice setup
```

Ask: *"Which item would you like to work on? Or paste a description / writeup of how this agent should behave and I'll draft the whole thing for you."*

### Step 3 — Run the chosen item or the import shortcut

If the user types a number, run that item (below). If they paste a description / writeup / Slack thread / doc snippet, run **"Import shortcut"** (below). After every item completes (or after a bulk update), **re-render** (see "Updating customization.json safely" below), then **always re-display the full checklist** with current `[x]`/`[ ]` state and the prompt: *"Which item would you like to work on next? Or type 'done' to exit."*

Items 9, 10, 11 are placeholders — tell the user those flows are coming soon and loop back.

---

## Where the prompt comes from

Unlike the previous prose-direct model, you no longer assemble `system_prompt.txt` by hand. The prompt is rendered from:

- **`agents/<projectName>/customization.json`** — the structured customer data you edit (persona, goals, methodology, session structure, interaction loop, voice, boundaries, knowledge-base policy, frontend copy).
- **`templates/system_prompt/<useCase>.j2`** — the platform-owned Jinja2 template that defines the prompt's structure, fixed sections (response guidelines, language protocol, etc.), and where your JSON values appear.

Available templates today: `sales_coach.j2`, `sme.j2`. The `useCase` field in `customization.json` selects the template. The schema (`schemas/customization.schema.json`) is the contract for what fields exist and what they mean.

Each item in this checklist writes to specific fields. When in doubt about what a field controls, check `templates/system_prompt/<useCase>.j2` for the field's reference (e.g. `{{ persona.tone }}`) — that's where it lands in the rendered prompt.

---

## Item 0 — App title and greeting

Ask, in order:

1. **"What should the app be called? (shown as the page title and in the header — e.g. 'Sales Coaching Assistant', 'Onboarding Helper')"**
2. **"What should the agent say first when someone opens the app? (one short sentence — e.g. 'Hi! Ready to practice some sales calls?')"**

Update `customization.json`:
- `persona.name` ← title (this is what appears in the rendered system prompt as "You are {name}")
- `frontendCopy.title` ← title
- `frontendCopy.greeting` ← greeting
- `frontendCopy.buttonLabel` ← `"Talk to {title}"`

Update `settings.json` (double-write for backward compat — `deploy.js` reads frontend copy from `settings.json` for Docker build args):
- `agent.title` ← title
- `agent.greeting` ← greeting
- `agent.buttonLabel` ← `"Talk to {title}"`
- `checklist.appIdentity = true`

Run `./render` and confirm.

Confirm: *"✓ Set title to '{title}' and greeting to '{greeting}'. These take effect on the next ./deploy."*

---

## Item 1 — Goals, audience, success

Ask, in order:
1. **"In one sentence, what should this agent help its users accomplish?"**
2. **"Who is the typical user? Be specific — role, context, what they care about. (e.g. 'an enterprise seller at a systems integrator working a deal with a financial-services customer')"**
3. **"What does a successful interaction look like? What should the user walk away with?"**

Update `customization.json`:
- `persona.audience` ← the audience description from question 2
- `persona.role` ← inferred from the agent's purpose (e.g. "sales coaching assistant", "subject matter expert", "onboarding guide") — pick a short noun phrase
- `goals.outcome` ← the goal from question 1, phrased so "Help the user {outcome}" reads naturally (e.g. "improve a real customer conversation")
- `goals.successDefinition` ← the success description from question 3
- `frontendCopy.description` ← `"{title} is an AI-powered assistant that helps {audience} {outcome}."`
- `frontendCopy.description2` ← `"Start a conversation to {accomplish-goal-as-imperative}. {success-rephrased-as-benefit}."`

Update `settings.json` (double-write):
- `agent.description` ← same as `frontendCopy.description`
- `agent.description2` ← same as `frontendCopy.description2`
- `checklist.goalsAndInteractionStyle = true`

Keep both description strings ≤ 200 chars and natural. Don't mention "Azure AI Foundry" or any platform brand.

Run `./render` and confirm.

---

## Item 2 — Personality and tone

Show the four dispositions and ask the user to pick one (or describe their own):

1. **Warm and supportive** — encourages, validates, kind tone
2. **Direct and neutral** — matter-of-fact, no fluff, information-first
3. **Challenging and redirecting** — respectfully pushes back, asks probing questions, "tough love"
4. **Playful and casual** — lighthearted, uses humor where appropriate

Then ask: **"Anything else about the agent's voice or style? (e.g. avoids jargon, always formal, never uses emojis)"**

Update `customization.json`:
- `persona.tone` ← a short adjective phrase distilled from the disposition + style notes (e.g. "supportive, candid, and direct" or "clear, grounded, practical, and concise"). This phrase is woven into the "You are X" line of the rendered prompt — keep it sentence-fragment-grammatical.

If the user provided notable style rules (e.g. "never uses emojis"), capture them as one-line entries in `voiceProfile.customTraits` (e.g. `{"label": "Punctuation", "value": "Avoids unpronounceable punctuation; never uses emojis."}`). Item 6 will refine voice traits further.

Set `checklist.baselinePersonalityAndTone = true`.

Run `./render` and confirm.

---

## Item 3 — Methodology or framework

Ask: **"Should this agent ground its answers in a specific methodology, framework, or body of knowledge? (e.g. Bloom's Taxonomy, GROW model, ITIL, SPIN selling, your company's onboarding handbook). Say 'no' if it should answer from general knowledge."**

If the user says no / skip, clear methodology fields and set `checklist.methodology = true`.
- `methodology.name` ← `""`
- `methodology.summary` ← `""`
- `methodology.keyPrinciples` ← `[]`

If they name something, ask:
1. **"Briefly, what is it and why does it matter for this agent?"** (one paragraph)
2. **"Are there 3-7 key concepts, stages, or principles I should bake into the prompt? (you can paste a table or list, or describe in your own words)"**

Update `customization.json`:
- `methodology.name` ← framework name
- `methodology.summary` ← the one-paragraph summary. Multi-line markdown is fine (the renderer treats this field as a free-form block; tables, sub-headers, and bullet lists all render).
- `methodology.keyPrinciples` ← array of one-line principle strings

**Sales Coach extra:** If `useCase === "sales_coach"`, also ask: **"Are there situations you want the agent to flag with a specific coaching question? (e.g. 'when the seller is feature-focused, ask: what outcome is your customer trying to achieve?')"** Capture as `coachingPrompts: [{trigger, prompt}, ...]`. Each entry is `{"trigger": "Feature-focused", "prompt": "What outcome is your customer trying to achieve?"}`. If the user says "use the defaults" or skips, leave the existing entries alone.

Set `checklist.methodology = true`.

Run `./render` and confirm.

---

## Item 4 — Conversation structure

Ask: **"How should a typical interaction be shaped? Pick the closest:**
1. **Open** — no fixed structure; the agent responds to whatever the user brings.
2. **Staged** — the agent moves the user through clear stages (e.g. opening → discovery → guidance → next steps).
3. **Roleplay / scenario** — the agent simulates a customer, interviewer, or other counter-party.
4. **Hybrid** — describe in your own words.**"

Update `customization.json.sessionStructure`:

**For Open:**
- `sessionStructure.type` ← `"open"`
- `sessionStructure.stages` ← `[]`
- `sessionStructure.description` ← `""`

**For Staged:** ask **"Tell me the stages — names and what happens in each. (e.g. 1. Orientation: ask how much time and what they want from the session)"**
- `sessionStructure.type` ← `"staged"`
- `sessionStructure.stages` ← array of `{"name": "...", "description": "..."}` entries in order
- `sessionStructure.description` ← `""`

**For Roleplay:** ask **"Who is the agent simulating, and what should the user practice?"** Synthesize a short prose block (the agent stays in character, lets the user drive, breaks character only on explicit request).
- `sessionStructure.type` ← `"roleplay"`
- `sessionStructure.stages` ← `[]`
- `sessionStructure.description` ← the synthesized block (rendered verbatim under `# Conversation structure` / `# Coaching session structure` in the prompt)

**For Hybrid:** take the user's description verbatim.
- `sessionStructure.type` ← `"hybrid"`
- `sessionStructure.stages` ← `[]`
- `sessionStructure.description` ← the user's description

Set `checklist.conversationStructure = true`.

Run `./render` and confirm. Sales Coach replaces its default 4-stage block; SME inserts a new `# Conversation structure` section before `# Explanation model`.

---

## Item 5 — Interaction loop (optional, recommended for coaches)

This is the secret sauce. It produces a self-correcting conversation that doesn't just parrot or lecture.

Ask: **"Should the agent run a feedback loop on every turn? (recommended for coaching, training, or skill-development agents). If yes, the agent will reflect, ask a deepening question, have the user generate a solution, then critique it together. If no, the agent will just answer questions."**

If no:
- `interactionLoop.enabled` ← `false`

If yes:
- `interactionLoop.enabled` ← `true`

Then ask one follow-up: **"Any specific phrasing you want the agent to use in the elicit / self-critique steps? (e.g. 'walk me through how you'd...', 'if you were the customer hearing that...')"**
- `interactionLoop.customPhrasing` ← the user's phrasing string, or `""` if they say no/skip. This gets inlined as a guidance paragraph after the 5-step loop in the rendered prompt.

The 5-step loop content itself (Reflect → Clarify → Elicit → Self-critique → Feedback) is platform-owned in the template, with phrasing flavored for the use case (selling concepts for sales_coach, source material grounding for sme). Don't try to write the loop steps into JSON — just toggle `enabled` and optionally supply `customPhrasing`.

Set `checklist.interactionLoop = true`.

Run `./render` and confirm. Sales Coach shows/hides `# Interaction loop` + `# Coaching effectiveness`. SME shows/hides its teaching-flavored `# Interaction loop` after `# Interaction patterns`.

---

## Item 6 — Voice traits

Ask:
1. **"Are there 3-5 short phrases or fillers you want the agent to use as acknowledgments? (e.g. 'got it', 'I hear you', 'super', 'that's cool')"**
2. **"How does it emphasize key points? (e.g. 'repeats key words to drive the point home', 'pauses before the headline number')"**
3. **"How does it organize ideas? (e.g. 'uses numbered frameworks', 'frames everything as before/after')"**
4. **"Any other speaking-style rules? (e.g. 'closes with a sincere thank-you', 'never uses jargon')"**

Update `customization.json.voiceProfile`:
- `acknowledgments` ← array of short strings (from question 1)
- `emphasisStyle` ← one short sentence (from question 2)
- `framingStyle` ← one short sentence (from question 3)
- `customTraits` ← array of `{"label": "...", "value": "..."}` rows (from question 4 — one row per rule)

These render as a markdown table under `# Voice and delivery` in the prompt: one row per trait, with `customTraits` rows appended after the standard three.

Set `checklist.voiceTraits = true`.

Run `./render` and confirm.

---

## Item 7 — Boundaries and refusals

Ask: **"What topics or requests should the agent refuse or redirect? The base prompt already covers HR, unsafe content, confidential data, and prompt-extraction. List anything else you want added or any topic-specific phrasing you want — one bullet per addition. (e.g. 'Politics: respond \"I can't help with that\" and redirect.', 'Puzzles/riddles: do not engage.', 'Internal company info not in the KB: say so honestly.')"**

Update `customization.json.boundaries.customAdditions` — array of one-line strings. Each string is appended verbatim after the standard refusal categories in the rendered `# Boundaries` section. If the user says "nothing to add", leave it as `[]`.

Set `checklist.boundaries = true`.

Run `./render` and confirm. Note: the base refusal categories (HR, unsafe content, confidential data, prompt-extraction) are platform-owned in the template — your additions extend them, never replace them.

---

## Item 8 — Knowledge base usage rules

Ask:
1. **"What kinds of questions should trigger a knowledge base search? (e.g. 'specific Azure features, capabilities, or implementation details', 'pricing or licensing claims', 'partner-program details')"**
2. **"How should the agent weave retrieved content into responses? (e.g. 'summarize the useful insight and cite the source briefly', 'paraphrase and avoid reading verbatim')"**
3. **"What exact phrasing should it use when nothing is found?"**

Update `customization.json.knowledgeBase`:
- `triggerDescription` ← answer 1 (free-form prose; multi-line OK)
- `incorporationStyle` ← answer 2 (short sentence)
- `noResultResponse` ← answer 3 (exact phrasing, rendered as a quoted line in the prompt)

Set `checklist.knowledgeBase = true`.

Run `./render` and confirm.

---

## Items 9, 10, 11 — placeholders

If picked: *"That flow isn't implemented yet — coming in a future update. For now I can help with items 0-8."* Then return to the checklist.

---

## Import shortcut

If instead of picking a number the user pastes a description, writeup, transcript, doc, Slack thread, or freeform brief, use it to **populate `customization.json` end-to-end**.

1. Acknowledge: *"Got it — let me extract a customization from this. I'll show you the JSON I came up with, render it, and you can tell me what to refine."*
2. Read everything they pasted plus `settings.json` (for project name, audience hints, purpose, etc.) and `customization.json` (so you know the schema and current values).
3. Extract structured field values, NOT prose. For each schema field (see `schemas/customization.schema.json` for the full contract), pull the best signal from the user's input. If a field has no signal in the input, leave the existing value (don't overwrite with empty or with a fabricated default).
4. Show the proposed JSON diff (or a summary of what changes) in chat.
5. Ask: *"Want me to write this to `agents/<projectName>/customization.json` and render? Or refine specific fields first?"*
6. On approval, write `customization.json`, also propagate `frontendCopy.*` into `settings.json.agent.*` (double-write), run `./render`, and show the rendered diff in `system_prompt.txt`. Set every applicable `checklist.*` key to `true`.
7. Re-display the full checklist with updated `[x]`/`[ ]` state and the prompt: *"Which item would you like to work on next? Or type 'done' to exit."*

---

## Updating customization.json safely

When writing into `customization.json`:
- Read the existing file first.
- Edit ONLY the fields the current item is responsible for. Preserve everything else byte-for-byte.
- Write the file back as pretty-printed JSON (2-space indent, trailing newline).

After every write, run `./render`. The renderer:
- Validates the JSON against `schemas/customization.schema.json` — if validation fails, it prints the missing/invalid field path. Fix and re-run.
- Re-generates `agents/<projectName>/system_prompt.txt` with the AUTO-GENERATED header at the top.

Summarize what changed in the rendered prompt in one natural-language sentence (e.g., "Updated the audience and tone in the rendered prompt" or "Added a 2-stage session structure to the coaching flow"). Do NOT show a raw `git diff` — the full diff is noisy because the renderer regenerates the entire file. The customer can run `git diff agents/<projectName>/system_prompt.txt` themselves if they want the details.

**Never edit `system_prompt.txt` directly.** It is a rendered artifact and will be overwritten on the next render or deploy. If the user wants changes that the schema doesn't express, surface that as feedback — it's either a missing schema field, a missing template hook, or a candidate for the (deferred) override hatch. Don't paper over it with a manual edit.

`frontendCopy.*` writes also propagate to `settings.json.agent.*` (title, greeting, description, description2, buttonLabel). Until `deploy.js` reads frontend copy directly from `customization.json`, the `settings.json` mirror is what reaches the Docker build args.

---

## When the user is done

Before showing the closing message, run the **Final review at exit (silent, agentic)** from the "Answer quality" section above: run `./render` so you're scoring the current output, load the gold standard matching `customization.json.useCase`, score the ten rubric criteria internally, and — if the prompt is below the demo-ready bar — ask one targeted clarifying question, write the revision into `customization.json`, re-render, and re-score. Do **not** print scores or a score table to the user.

Once the user confirms they're good to ship:

> All set. Run `./deploy` (or `.\\deploy.ps1` on Windows) to push your changes — title, greeting, and rendered system prompt all take effect on the next deploy. The deploy script re-runs `./render` before the build, so `system_prompt.txt` is always fresh from your `customization.json`.

---

## Rules of engagement

- Always work on **one** checklist item per invocation, then return to the checklist (except for the import shortcut, which fills many fields at once).
- When an item completes, **immediately** update `customization.json`, run `./render`, set the `settings.json` checklist flag, and confirm in chat (e.g. *"✓ Marked 'Conversation structure' as complete."*).
- Never edit `system_prompt.txt` directly — it's a rendered artifact.
- Never overwrite fields in `customization.json` that the current item isn't responsible for.
- If the user types `done`, `exit`, or `quit`, show the "When the user is done" message and stop.
- The user can re-run you anytime to revisit any item.
