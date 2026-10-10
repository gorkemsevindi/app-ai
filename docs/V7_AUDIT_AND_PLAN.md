# V7 — AI Cinema & Short Drama Factory: audit, gap matrix, plan (Phase 0)

Spec: `docs/MASTER_SPEC_V7_FILM_DRAMA_FACTORY.docx` (V1–V6 retained, V7 appended).

- V7 is built **inside the existing app**: no new backend, router or provider router.
- YourStars/`drama/` is not touched.

User priorities, in order:
1. Users create their own films, series and digital actors.
2. 10–30 minute episodes.
3. Editable dialogue, including fictional profanity/slang (exact mode).
4. Scene-level editing.
5. Story memory.
6. Budget-driven production.

## 1. What V7 reuses (dependency map)

| V7 need | Existing module reused | How |
|---|---|---|
| Scene → shot → render → assembly | `studio.py` (storyboard versions, content-hashed shots, selective re-render, assembly, captions) | One **episode = one Studio project**. Episode storyboards are ordinary Studio versions, so hashing, reuse, edits, undo and identity lock all keep working. |
| Shot planning / AI director | `RuleBasedDirector` / `GeminiDirector` | Long episodes come from a new deterministic **screenplay parser + shot planner**. The V4 director stays for briefs. |
| Provider routing, cost ceilings, kill switch | `router.py`, V5 `policies.py` | Unchanged. Style and scene-type capabilities are new capability tags in the same registry. |
| Characters | V6 `characters.py` / `casting.py` | The production cast is a list of V6 characters; every episode project gets V6 cast snapshots (UUID + locked identity version + lock mode). |
| Credits, reserve/settle/refund, royalties | `credits.py`, `character_market.py` | Unchanged. The budget director adds an **authorization (hard cap)** on top. |
| Edits, revisions | `studio_edits.py` (typed ops, proposal → apply, undo) | New typed ops for dialogue lines; **redo** added; a range edit maps `01:12–01:17` to shots. |
| Learning | V5 learning listener | Production jobs are logged automatically; no content. |
| Moderation | `moderation.py` | New **fiction dialogue policy**. The general screen blocks words like "kill"/"blood" that drama needs. |
| Worker assembler | `worker/studio/assemble.py` | Gains **title-card clips**, so a free animatic preview can be built from the storyboard. |

## 2. Gap matrix

| V7 requirement | Before | Plan |
|---|---|---|
| §1 Four entry flows on one infrastructure | Studio only | `productions` (kind: series / film / stars / life_story), all backed by episode projects; mobile home with 4 cards |
| §2 Formats 30 s → 30+ min, styles, scene override | Studio limits 12 shots / 60 s; one style | Per-project limits (production format), scene `style` / `scene_type` overrides, style capability tags |
| §3 Series / film / stars / life story flows | — | Production wizard endpoints; life-story guided Q&A with chronology, privacy scan, fact/fiction marking, approval |
| §4 Exact Dialogue Mode | Dialogue = {character, text, start} | Dialogue lines with stable ids, emotion / delivery / intensity / pronunciation / voice / language / locked / exact flag; fiction policy; line editor with impact analysis; per-line history |
| §5 Non-destructive range edit | Shot ops, hash reuse, undo | Range → shots mapping, edit plan (changed clips / cost / time / risk), redo, versioned timeline JSON export/import |
| §6 Series memory | — | Story bible (versioned), episode snapshots (canonical world state + deltas), story branches ("don't let X die in ep. 4"), continuity validator with severities, impact preview |
| §7 Budget director | Per-render confirmation | Profiles (economy / standard / cinema_pro as config versions), per-shot cost breakdown, estimate range, feasibility + alternatives, authorization with **hard cap**, currency in minor units |
| §8 Preview-first | — | Free animatic (title cards), 30–60 s pilot render (reused by the full render through hashes), staged full render, episode status machine, cancel + refund, resume = re-render failed shots |
| §9 Pipeline, scene classes, mock labelling | V4–V6 routing | Scene classifier + capability matrix; mock outputs labelled; per-provider limits stay in the router/queue |
| §10 Data model, API, events | — | Tables: `productions`, `production_episodes`, `story_branches`, `story_bibles`, `episode_snapshots`, `continuity_findings`, `cost_estimates`, `budget_authorizations`, `production_events` (outbox), `life_story_sessions`. Endpoints under `/productions`. |
| §11 Mobile | Studio tab | 4 entry cards, production wizard, episode list, dialogue editor, budget approval, progress |
| §12 Rights / consent / rating / store | V6 consent, moderation | Content rating per production (general / teen / mature; mature = adults only), people-consent gate for life stories, publishing off by default |
| §13 Tests A–H | — | Scenario tests with the real worker (mock providers), plus unit tests |

## 3. Adaptations (the repository wins)

- **Scene and shot rows:** scenes and shots live in the immutable versioned storyboard JSON. No `scenes`/`shots` tables, because that keeps V4 hashing and selective re-render intact.
- **Dialogue lines:** dialogue lines are JSON objects with stable ids inside versions. Revision history = version history. A `dialogue_revisions` table is not needed.
- **Seasons:** seasons are an `episode.season` number. A `seasons` table adds nothing yet.
- **Render tables:** `render_jobs`/`render_assets`/`provider_usage` already exist as `generation_jobs` / `studio_shot_renders` / `model_runs`. `timeline_versions` = studio project versions plus the export schema.
- **Endpoint prefix:** `/productions` instead of `/v7/productions`, matching the existing router style (`/studio`, `/characters`).

## 4. Honesty constraints (from §15)

- No video model renders 30 minutes at once. Long episodes are many short shot jobs plus assembly.
- **No TTS, lip-sync or dubbing provider is integrated.** Exact dialogue is guaranteed for stored text and subtitles. Spoken audio and lip-sync are reported as *not available* until a provider that does not rewrite text is configured.
- Real-provider tests are blocked until keys and model ids are configured. Mock results are labelled and never reported as production success.
