# HAUL16 Backend

**HAUL16** is a brand-abuse takedown desk. For one protected brand it:

1. **Detects** look-alike hostnames in live **Certificate Transparency (CT) logs**,
2. **Captures** evidence from the suspect page in a locked-down headless browser,
3. **Scores** the site (`high / medium / low / unknown`, never "safe") with deterministic, versioned rules,
4. **Links** sites that share a payment number, wallet or Telegram handle into one **campaign / case**,
5. **Drafts takedown notices** from templates, using only captured evidence,
6. Requires a **named human to approve** each notice, then "files" it as a **dry run** and starts a
   **takedown clock**, and
7. Produces a monthly **report** (time-to-removal by channel).

> **DRY RUN, always.** Nothing is ever sent to a registrar, host, platform, telco or regulator. The
> demo brand "Nkwa Exchange" is fictional. Do not add real sending without legal review, verified
> recipient addresses and an explicit human-approval design.

**Product context:** the original working brief (problem, users, scope, architecture decisions, evidence
standards, channels, demo plan, and which claims are `[VERIFIED]`, `[ASSUMPTION]` or `[UNVERIFIED]`) is in
[`docs/HAUL16-brief.md`](docs/HAUL16-brief.md). Read it when a product question is not answered here.

This repo is the **backend** (Python 3.12+, FastAPI, SQLite, Playwright). The operator console is a
separate repo: **[dezmymachine/haul16-frontend](https://github.com/dezmymachine/haul16-frontend)**
(TanStack Start + shadcn/ui). If you are an agent picking this up, read this file fully; the
"Invariants" and "How to extend" sections matter most.

---

## 1. Quick start

Prerequisites: Python >= 3.12, [`uv`](https://docs.astral.sh/uv/), and Playwright's Chromium.

```bash
uv sync                                  # create .venv and install locked deps
uv run playwright install chromium       # one-time browser download for captures
uv run uvicorn engine.api:app --port 8000
```

Open http://127.0.0.1:8000/docs for the OpenAPI UI, or run the frontend repo against it
(`npm run dev` there proxies `/api` and `/demo-sites` to `:8000`).

| Task | Command |
|---|---|
| Run API with live CT polling | `uv run uvicorn engine.api:app --port 8000` |
| Run API **without** CT polling (offline / tests) | `HAUL16_CT=0 uv run uvicorn engine.api:app --port 8000` |
| Run the tests | `uv run pytest -q` (30 tests, ~20s, uses a real headless Chromium) |
| Follow CT logs only (CLI) | `uv run python -m engine.ct_poll --duration 60` |
| Run the watch loop as a CLI | `uv run python -m engine.worker --help` |
| Capture one URL (CLI) | `uv run python -m engine.capture https://suspect.example` |
| Push a local fixture through the pipeline | `uv run python -m engine.worker --no-ct --inject http://127.0.0.1:8000/demo-sites/clone/ --as-host nkwaex-support.com --allow-local` |

Environment variables (all optional):

| Var | Default | Meaning |
|---|---|---|
| `HAUL16_PORT` | `8000` | Port the demo clone URLs point at (`/api/demo/clone` builds `http://127.0.0.1:$PORT/demo-sites/<variant>/`). Set it if you run uvicorn on another port |
| `HAUL16_CT` | `1` | `0` disables live CT polling (the API still works with injected URLs) |

CORS allows only `http://localhost:5173` and `http://127.0.0.1:5173` (the frontend dev server). Change
it in `engine/api.py` if the frontend runs elsewhere.

State lives in `data/` (git-ignored, created on first run): `haul16.db` (SQLite, WAL),
`evidence/` (screenshots `.png` and page source `.html`), `hits.jsonl` (raw Stage A hits), `logs/`.

### Running both repos together
Check the two repos out side by side (e.g. `haul16-backend/` and `haul16-frontend/`), start the backend
as above, then in the frontend repo `npm install && npm run dev` and open http://127.0.0.1:5173.

---

## 2. Architecture

```
 CT logs (RFC 6962)        manual URL / demo fixture
        |                          |
        v                          v
  engine/ct_poll.py  -->  engine/pipeline.py (Seen + Stage A hit handling)
        |                          |
        +-----------> engine/matcher.py  (Stage A: lookalike candidate filter)
                                   |  Watch.on_hit / Watch.inject
                                   v
                      engine/worker.py  Watch  (queue + capture workers)
                                   |
        capture.py (Stage B) -> rdap.py (enrich) -> score.py (Stage C)
                                   |
                  store.py (SQLite): threats, identifiers, cases, notices, clock, audit
                                   |
                  notices.py + channels.yaml  (draft notices; templates only)
                                   |
                  engine/api.py (FastAPI + SSE)  <---->  frontend
```

| Module | Responsibility |
|---|---|
| `engine/normalize.py` | punycode -> unicode, confusable "skeletons" (homoglyphs, `rn`->`m`, `0`->`o`...), public-suffix splitting, free-host detection |
| `engine/matcher.py` | **Stage A.** `Brand` (loaded from `brand.yaml`), `Matcher.match(host)` -> `Candidate` with reason codes + priority; `RULES_VERSION` |
| `engine/ct_poll.py` | Reads CT logs directly (`get-sth` / `get-entries`), parses certs, feeds hostnames to the pipeline. Per-log follower with backlog tracking |
| `engine/pipeline.py` | `Seen` bounded de-dupe set; `Pipeline` runs the matcher on every hostname, writes `data/hits.jsonl`, calls `on_hit` |
| `engine/capture.py` | **Stage B.** Playwright capture: screenshot, HTML hash, favicon mmh3, title, text excerpt, identifiers. SSRF guard |
| `engine/extract.py` | Regex extraction of Ghana phone numbers, TRON/ETH/BTC wallets, Telegram, WhatsApp; `unofficial()` diffs against the brand register |
| `engine/rdap.py` | Registrar + hosting-network abuse contacts from public RDAP (free, no key). Every field may be `None` |
| `engine/score.py` | **Stage C.** Combine Stage A reasons + page evidence into a `Verdict`; deterministic, versioned |
| `engine/store.py` | SQLite store + audit log + case assignment (campaign linking) + counts |
| `engine/notices.py` | Drafts notices from `channels.yaml` templates, filled **only** from captured evidence |
| `engine/worker.py` | `Watch`: queue, capture workers, retry of unreachable hosts, SSE `publish`; also a CLI |
| `engine/api.py` | FastAPI app, lifespan wiring, SSE stream, all HTTP routes, report maths |

### The three stages
- **Stage A (domain only, cheap):** decides whether a hostname is *worth capturing*. Reason codes and weights (in
  `matcher.py`): `homoglyph_of_brand` 40, `typo_of_brand` 30, `brand_name_exact_label` 30, `brand_plus_lure_word` 25,
  `brand_name_in_domain` 20, `free_hosting_subdomain` 10. Official domains, their subdomains and
  `allowlist_domains` are never flagged. Confidence about a clone **never** comes from the name alone.
- **Stage B (capture):** the page is attacker-controlled. The browser only looks: no clicks, no form submission, no
  downloads, no permissions, hard timeout, and **private/internal addresses are blocked (SSRF guard)** unless
  `allow_local` is set (fixtures only). Page text is data, never instructions.
- **Stage C (scoring):** page reason codes (weights): `logo_or_favicon_match` 35, `payment_identifier_not_official` 35,
  `brand_named_on_page` 20, `lure_language_on_page` 15, plus Stage A priority. "Strong" signals are
  `logo_or_favicon_match`, `payment_identifier_not_official`, `homoglyph_of_brand`.
  - `high`: >= 2 strong signals, or 1 strong + brand named on page + total >= 70
  - `medium`: brand named on page and total >= 40
  - `low`: otherwise; `unknown`: page could not be captured (`page_unreachable`)
  - A stray phone/wallet only counts if the page is posing as the brand (brand named, or favicon match).
  - Every verdict carries `human_review_required = True` and an `evidence` dict (reason code -> the captured fact).

### What happens to a hit (`Watch.process`)
1. Capture `https://host/` then `http://host/` (or the given URL). Enrich with RDAP (failures are non-fatal).
2. Score and save the result. Publish SSE `scored`.
3. If confidence is `medium` or `high` (`OPEN_CASE_AT`): record linking identifiers, `assign_case` (join the open case
   of any threat sharing a phone/wallet/telegram value, else open a new case), draft notices.
   One telco notice per number per case (not one per site showing the number).
4. If `unknown`: retry later (after 120s, 600s, 1800s) because scammers often get the cert before the page is live.
5. Exceptions in one capture never stop the watch; they are written to the audit log as `capture_failed`.

### Campaign linking (important invariant)
Threats are linked **only** on a shared payment **phone, wallet or Telegram handle** (`LINKING_KINDS` in `store.py`).
A shared registrar, host or copied favicon is **not** enough: it would smear innocents.

---

## 3. Data model (SQLite, `engine/store.py`)

| Table | Key columns |
|---|---|
| `threats` | `id`, `brand_id`, `hostname` (UNIQUE per brand), `url`, `source` (`certificate_transparency`, `manual`, `demo_fixture`), `status` (`queued/captured/scored/error`), JSON `stage_a`, `capture`, `verdict`, `enrichment`, `confidence`, `score`, `case_id` |
| `cases` | `id`, `brand_id`, `opened_at`, `status` (`open/closed`), `title` |
| `identifiers` | `(threat_id, kind, value)`, kind in `phone/wallet/telegram`; indexed for linking |
| `notices` | `id`, `case_id`, `threat_id`, `channel`, recipient (+ `recipient_verified`), `subject`, `body`, JSON `evidence_refs`, `status` (`drafted/approved/filed_dry_run/rejected`), approver/timestamps; UNIQUE `(threat_id, channel)` |
| `clock_events` | `case_id`, `notice_id`, `channel`, `event` (`detected/filed/first_response/removed`), `ts`, `simulated` (1 while dry run) |
| `audit_log` | `ts`, `actor`, `action`, `ref`, JSON `detail`: every state change is recorded |

JSON columns are decoded by `Store._row` (`JSON_COLS`). IDs come from `new_id(prefix)` (`case_...`, `ntc_...`).

---

## 4. HTTP API

Base path `/api`. All JSON unless noted. Errors use FastAPI's `{"detail": ...}` and the frontend shows it verbatim,
so keep `detail` messages human-readable.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | `{ok, dry_run, ct_enabled}` |
| GET | `/api/brand` | brand YAML + `authorisation_letter{ref, sha256}` |
| GET | `/api/stats` | `counts{threats, by_confidence, open_cases, notices_drafted, notices_awaiting_approval, notices_filed}`, `ct{status,logs,certs,unique_domains,hits,certs_per_s,uptime_s,backlog}`, `queue` |
| GET | `/api/threats?confidence=&limit=` | light rows (`screenshot_ref`, `title`; no capture/enrichment) |
| GET | `/api/threats/{id}` | full threat |
| GET | `/api/cases` | summaries with `threat_count`, `notice_count`, `filed_count`, `removed_count` |
| GET | `/api/cases/{id}` | `threats`, `identifiers`, `notices`, `clock`, `audit` |
| POST | `/api/cases/{id}/redraft` | re-draft notices; `{drafted: n}` |
| GET | `/api/notices?status=` | cross-case notices incl. `case_title`, `host` (the Approvals inbox). `status` in `drafted/approved/filed_dry_run/rejected` else 422 |
| POST | `/api/notices/{id}/approve` | body `{approver}` (2-80 chars). 409 unless `drafted` |
| POST | `/api/notices/{id}/reject` | body `{approver, reason}` (reason 3-500). 409 unless `drafted`/`approved` |
| POST | `/api/notices/{id}/file` | **dry run**. 409 unless `approved`. Publishes SSE `filed`, starts the clock |
| POST | `/api/notices/{id}/clock` | body `{event: first_response\|removed, note?}`. 409 unless `filed_dry_run`. Always `simulated` |
| POST | `/api/inject` | body `{url, as_host?}` -> `{threat_id, duplicate}`. Private networks blocked (`allow_local=False`) |
| POST | `/api/demo/clone` | body `{variant: clone\|clone2\|decoy}`; serves fixtures from `/demo-sites/` with `allow_local=True` |
| POST | `/api/demo/reset` | wipes threats, cases, identifiers, notices, clock, audit (demo only) |
| GET | `/api/evidence/{name}` | `.png`/`.html` from `data/evidence`; name is regex-validated (no path traversal) |
| GET | `/api/report?month=YYYY-MM` | monthly report: counts, per-channel medians (detect->removed, filed->removed), cases |
| GET | `/api/events` | **SSE** (below) |
| GET | `/api/audit?limit=` | recent audit rows |
| static | `/demo-sites/{clone,clone2,decoy,official}/` | fictional fixture pages |

### Server-Sent Events (`/api/events`)
First message is a `ct_stats` snapshot, then events as they happen; a keepalive comment is sent every 15s. Event shapes
(`type` field): `ct_stats` (every 2s, includes `rate_now`), `hit` (`threat_id, host, priority, reasons, source, ct_log`),
`capturing` (`threat_id, host`), `scored` (`threat_id, host, confidence, score, reasons, case_id?, linked_via?,
notices_drafted?, retry_in_s?`), `filed` (`notice_id, case_id, channel`). The frontend's `src/live.ts` consumes these;
**changing an event shape is a breaking change for the frontend.**

### Human approval is enforced here
`approve/reject/file/clock` validate the state machine server-side (`drafted -> approved -> filed_dry_run`, or
`-> rejected`). The UI mirrors it but is not the safeguard. Never add a route that files without an approved notice.

---

## 5. Configuration files (data, not code)

### `brand.yaml`: the official asset register for ONE brand
Every detection is a **diff against this file**. Keys: `fictional`, `brand_id`, `display_name`, `seed_terms` (lowercase
letters/digits a scammer would put in a domain), `official_domains`, `official_handles`, `official_numbers`,
`official_wallets`, `official_favicon_mmh3`, `authorisation_letter` (path relative to repo root; its SHA-256 goes in every
notice), `allowlist_domains`. Compute a favicon fingerprint with:
`uv run python -c "from engine.capture import favicon_hash; print(favicon_hash(open(PATH,'rb').read()))"`.
Changing it needs an API restart. There is no edit UI yet.

### `channels.yaml`: the notice channel catalogue
Each channel: `id`, `name`, `applies_when`, `method` (email/web form), `recipient` or `recipient_from`, `verified`,
`expected_response`, `evidence_checklist`, `subject`, `body`. Channels shipped: `registrar`, `hosting`,
`google_safe_browsing`, `telco_mtn`, `telco_telecel`, `telco_airteltigo`, `csa`.
`applies_when` values are implemented in `notices.applies()`: `has_url`, `has_registered_domain`, `has_hosting`,
`has_payment_identifier`, `has_phone_mtn`, `has_phone_telecel`, `has_phone_at` (Ghana mobile prefixes in `TELCO_PREFIX`;
number portability makes that a hint, not proof). Template placeholders (all filled from evidence or the register):
`{brand} {brand_domain} {host} {url} {detected_at} {captured_at} {html_sha256} {screenshot_sha256} {screenshot_ref}
{claims} {phones} {wallets} {registrar} {registered_domain} {created} {network} {ip} {letter_ref} {letter_sha256} {case_id}`.
A missing fact renders as the word `unknown`; **never guess or invent a value**. Recipients marked `verified: false`
(telcos, CSA) must be confirmed on the channel's official page before any real filing.

### `fixtures/`
`fixtures/sites/{official,clone,clone2,decoy}` are fictional pages (clone and clone2 share a MoMo number so they join one
case; decoy "Nkwa Farms" scores low). `fixtures/authorisation_letter_demo.txt` is the demo authorisation letter.

---

## 6. Invariants (do not break)

1. **Dry run only.** No code path sends a notice anywhere. `filed_dry_run` is the terminal "filed" state.
2. **Human approval before filing**, enforced in the API, recorded in `audit_log` with the approver's name.
3. **No "safe" verdict.** The lowest outcomes are `low` and `unknown`.
4. **Notices contain only evidence-backed claims.** Claims come from `CLAIM_TEXT` keyed by reason code; unknown facts say
   "unknown". No LLM-generated notice text.
5. **Deterministic, versioned rules.** If you change scoring or matching, bump `RULES_VERSION` in `matcher.py`
   (it is stored on every verdict/candidate) and add tests.
6. **Campaign linking only on phone/wallet/telegram**, never on registrar, host or favicon.
7. **Capture is read-only and sandboxed**: SSRF guard on, `allow_local` only for fixtures, timeouts, no downloads.
8. **Every state change writes to `audit_log`.**
9. **SSE and JSON shapes are a contract** with the frontend (`src/api.ts`, `src/live.ts` there). Change both repos together.
10. Evidence file names are validated by regex before serving. Keep it that way.

---

## 7. Tests

`uv run pytest -q` (config in `pyproject.toml`: `pythonpath = ["."]`).

| File | Covers |
|---|---|
| `tests/test_matcher.py` | Stage A: homoglyphs, typos, lure words, official/allowlisted domains |
| `tests/test_extract.py` | phone/wallet/telegram extraction and `unofficial()` |
| `tests/test_capture_score.py` | serves fixtures locally, captures with real Chromium, scores them; SSRF blocked without the flag; unreachable is `unknown`, not safe |
| `tests/test_worker.py` | full watch loop on fixtures: campaign linking, decoy skipped, notice drafting, retry of unreachable hosts |

Tests that capture need Chromium installed (`uv run playwright install chromium`). There are **no API-level tests** yet (approval state machine, `GET /api/notices`, report); adding them with FastAPI's TestClient and `HAUL16_CT=0` is the best first contribution.

---

## 8. How to extend (recipes)

- **New brand / change the register:** edit `brand.yaml`; restart. For multi-brand support, `brand_id` is already a column
  on `threats`/`cases`, but the API assumes a single loaded `Watch.brand`.
- **New notice channel:** add an entry to `channels.yaml` (and an `applies_when` rule in `notices.applies()` if it needs a
  new condition). Add the label to the frontend `CHANNEL_LABEL` map in `src/ui.tsx`.
- **New reason code:** add a weight (`WEIGHTS` in `matcher.py` or `PAGE_WEIGHTS` in `score.py`), a claim sentence in
  `notices.CLAIM_TEXT`, a label in the frontend `REASON_LABEL`, bump `RULES_VERSION`, add tests.
- **New hostname source** (NRD list, CertStream): feed it into `Pipeline` the same way `ct_poll.follow` does; everything
  downstream is source-agnostic (`source` is just a string stored on the threat).
- **New API route:** add it to `engine/api.py`, return plain dicts from `Store`, validate bodies with Pydantic models,
  raise `HTTPException(409, "<readable reason>")` for state-machine violations, and add the type + method to the
  frontend `src/api.ts`.
- **New table/column:** add to the `SCHEMA` string in `store.py`. There is **no migration system**: `CREATE TABLE IF NOT
  EXISTS` only. For a column on an existing table either delete `data/haul16.db` (dev) or write a small migration.

---

## 9. Known gaps / ideas

- No auth: the approver is a free-text name sent by the client. A real deployment needs login and role checks.
- No migrations (see above). SQLite single-writer; fine for the demo scale.
- Single brand per process. `brand.yaml` has no edit UI.
- CT log list is hard-coded in `ct_poll.LOGS` (Google Argon and Xenon 2026h2, Cloudflare Nimbus 2026, Sectigo Elephant and Tiger 2026h2); rotate when logs retire.
- RDAP bootstrap uses `https://rdap.org`; add caching and rate limiting before heavy use.
- Notices go through `str.format_map`: only add placeholders that exist in `facts_for`, or they render `unknown`.
- Telco/CSA recipients are unverified placeholders.

## 10. Related
- Product brief: [`docs/HAUL16-brief.md`](docs/HAUL16-brief.md)
- Frontend: https://github.com/dezmymachine/haul16-frontend
