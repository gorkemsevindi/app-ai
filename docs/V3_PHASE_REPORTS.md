# V3 phase reports

Each phase follows the reporting checklist in spec §24.

## Phase 1: Template V3 (variable person slots, ingestion, remix, ranked feed)

**Feature flags:**
- `template_remix`: consumer remix. Disabled by default; returns 403 `feature_disabled` until enabled.
- `ranking`: optional weight overrides.
- Ingestion works while both flags are off.

### Files changed

| Area | Files |
|---|---|
| API | `app/models.py`, `app/services/templates_v3.py` (new), `app/routers/templates_v3.py` (new), `app/routers/templates.py`, `app/schemas.py`, `app/services/multiperson.py`, `app/services/generation.py`, `app/main.py` |
| Migrations | `alembic/versions/0003_templates_v3.py` |
| Tests | `tests/test_templates_v3.py` |
| Mobile | `src/lib/api.ts`, `src/app/(tabs)/index.tsx`, `src/app/template/[id].tsx`, `src/components/TemplateCard.tsx`, `src/locales/{en,tr}.json`, `.npmrc` |

### Migration 0003 (additive, reversible; upgrade → downgrade → upgrade verified, `alembic check` clean)

**Changes:**
- `templates`: + `creator_id`, `visibility`, `moderation_status`, `commercial_rights`.
  - Server defaults make every existing template `public` + `approved`.
- `template_versions`: + `source_video_id`, `config`, `credit_rule`, `published_at`.
  - A DB trigger makes published recipes immutable.
- New tables: `template_person_slots` and `template_metrics_daily`.

**Drift fix:** `fk_templates_current_version`, declared in 0001, was never created by Alembic. It is now added `NOT VALID`. Validate it in production after a data check.

### APIs

| Endpoint | Type | Notes |
|---|---|---|
| `GET /feed?category=&locale=` | New | Server-ranked. Categories include `new` and `multi_person`. Card fields: `use_count`, `est_credits`, `person_slots`, `est_seconds`, `safety_badge`, `creator`, `mode` |
| `POST /generations/estimate` | New | Credit quote, balance, `confirm_required` |
| `POST /generations/remix` | New | `Idempotency-Key` header; assignments are `{slot_id, profile_id}` |
| `GET /templates/{id}` | Additive | + `mode`, `person_slots`, `est_credits` |
| `GET /templates` | Changed | Now excludes non-public / non-approved templates; legacy templates are unaffected thanks to the defaults |
| `POST /admin/templates/{id}/source-video` (+ `/complete`, GET status) | New, admin | Ingestion: rights evidence first, then upload, then analysis (reuses the multi-person analysis job) |
| `POST /admin/templates/{id}/versions-v3` | New, admin | Define slots on the analysed tracks |
| `POST /admin/templates/{id}/publish` | New, admin | Publish a version |
| `POST /admin/templates/{id}/moderation` | New, admin | Review / takedown |
| `POST /admin/templates/metrics/refresh` | New, admin | Idempotent daily aggregation; run from cron |

### Behaviour

**Remix jobs:**
- A remix is a `multi_replace` job on the template's analysed clip, using the same queue, leases, retry/refund and worker as user-video replacement.
- Each job stores `template_version_id` and the slot → track mapping.
- Output provenance records `template_version_id`, so every output traces back to the exact version.

**Pricing:**
- Formula: `ceil((base + per_extra_slot × (n−1)) × resolution_multiplier)`.
- `base` defaults to the template's `credit_cost`. All parts are overridable per version (`credit_rule`) or globally (`template_remix.pricing`).
- Above `confirm_above_credits`, the client must echo the quoted price (`confirmed_credits`), otherwise it gets 409.

**Ranking:**
- The explainable score from spec §16, with Laplace-smoothed rates, freshness decay, a cold-start boost, a creator-diversity cap and a report-rate safety cut-off.
- Weights live only in the `ranking` flag; responses carry no score or weights.

### Environment variables

None new.

### Tests

| Check | Result |
|---|---|
| API | **41/41 passed** (35 existing + 6 new). Ruff clean |
| Worker | 6/6 passed. Ruff clean |
| Mobile | `npm ci` works again, `tsc` clean, tests 3/3 |

The new tests cover:
- Legacy templates are unchanged.
- Rights and evidence gates.
- Non-selectable tracks and duplicate slots are rejected.
- Invisible until published; the immutability trigger works.
- The flag gate.
- Price per slot count.
- IDOR: a foreign profile returns 404.
- Idempotent replay with a single debit.
- The confirmation threshold.
- Required slots.
- Takedown removes the template from feed, detail and remix.
- Metrics upsert idempotency.
- Ranking order and the safety cut-off; weights are not exposed.
- **Real worker e2e:** ingest → mock analysis → publish → 2-slot remix → completed output traceable to its version.

**Bug found and fixed:** `apply_analysis` overwrote `source_videos.analysis`, which dropped ingestion metadata. It now merges.

### Security and privacy

- Template source clips are owned by the ingesting admin account and keep the existing private storage plus signed URLs.
- Rights attestation is audited (`template.rights_attested`).
- Remix assignments only accept the requester's own consented, ready profiles; foreign ids get the same 404.
- The minor / NSFW / violence analysis flags still block whole clips and make tracks non-selectable.

### Estimated compute cost

Same as multi-person replacement: per-person sequential passes, so cost is linear in slots × seconds. See `docs/V3_AUDIT_AND_PLAN.md` §8.

### Known limitations

- The admin console has no UI yet; it is API only.
- Metrics refresh must be scheduled (cron).
- `paid_conversion` uses queue class (Pro) as a proxy until store verification (Phase 5).
- Template thumbnail and preview media are still URLs set by admins.

### Next step

Phase 2: audio-driven lip-sync and expression preservation.
