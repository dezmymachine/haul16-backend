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

**Going live?** Section [9](#9-going-live-running-the-real-version-not-demo-data) lists everything the real (non-demo)
version needs: data sources and APIs, services, accounts and keys, infrastructure, legal prerequisites and a go-live checklist.

**Product context:** the original working brief (problem, users, scope, architecture decisions, evidence
standards, channels, demo plan, and which claims are `[VERIFIED]`, `[ASSUMPTION]` or `[UNVERIFIED]`) is in
[`docs/HAUL16-brief.md`](docs/HAUL16-brief.md). Read it when a product question is not answered here.

This repo is the **backend** (Python 3.12+, FastAPI, Postgres, Playwright). The operator console is a
separate repo: **[dezmymachine/haul16-frontend](https://github.com/dezmymachine/haul16-frontend)**
(TanStack Start + shadcn/ui). If you are an agent picking this up, read this file fully; the
"Invariants" and "How to extend" sections matter most.

---

## 0. The `live` branch (read this first if you are on it)

`main` is the dry-run demo on SQLite. **`live` turns HAUL16 into a brand-aware agent**: you give it a brand, it builds a sourced,
versioned identity dossier for that brand from everything public, and detection then runs against that dossier instead of a
hand-written `brand.yaml`. Design and rationale: the plan in the repo history and `docs/HAUL16-brief.md`. Status of Milestone 1:

| Area | State on `live` |
|---|---|
| Postgres (Neon-ready) + Alembic migrations | **Done.** `engine/db.py`, `migrations/`, `engine/store.py` ported; all original tests pass on Postgres |
| Settings from env | **Done.** `engine/settings.py`, `.env.example` |
| Durable job queue (Postgres `SKIP LOCKED`) | **Done.** `engine/jobs.py`; runs in the API process (`EMBEDDED_WORKER=true`) or as `python -m engine.worker_main` |
| Capture queue durability | Threats left `queued` by a restart are re-queued on boot (`Watch.recover()`); the in-process asyncio queue itself is kept |
| Brand Profiler (dossier) | **Done.** `engine/brand/` (see below); every collector skips cleanly without its key |
| Brand API (create, profile, verify, review facts, letter upload, activate) | **Done.** `engine/brand/routes.py` |
| Detection reads the dossier | **Done.** `engine/brand/loader.py`; the active brand is chosen with `POST /api/brands/{id}/activate` |
| Brand-scoped reads | **Done.** `?brand_id=` or `X-Brand-Id` header on stats, threats, cases, notices, report, brand |
| LLM adapter | **Done for OpenAI** (`engine/llm.py`); Anthropic is a stub that raises `NotConfigured`, add it in `_chat` |
| Docker / Railway | `Dockerfile`, `railway.json`, `.dockerignore` added. See "Deploying" |
| CT follower as its own process with a saved cursor | **Not done.** CT polling still runs inside the API process (`HAUL16_CT`) |
| urlscan, Meta Ad Library, Telegram/WhatsApp assessment, agent loop, alerts, auth, real senders | **Not started** (Milestones 2 to 5 in the plan) |

### Run it locally
```bash
docker run -d --name haul16-pg -e POSTGRES_USER=haul16 -e POSTGRES_PASSWORD=haul16 -e POSTGRES_DB=haul16 -p 127.0.0.1:55433:5432 postgres:16-alpine
cp .env.example .env            # add keys as you get them; none are required to start
uv sync && uv run playwright install chromium
uv run uvicorn engine.api:app --port 8000     # migrates the database and seeds the fictional demo brand on first start
uv run pytest -q                              # creates and uses a separate haul16_test database
```
Neon: set `DATABASE_URL` to the Neon connection string (it needs `?sslmode=require`) and, for tests, `TEST_DATABASE_URL` to a Neon **branch**.

### The Brand Profiler (`engine/brand/`)
`POST /api/brands/{id}/profile/run` queues a `brand_profile` job; any worker runs `profiler.run_profile`. Collectors, in order, each isolated
(a missing key or vendor outage marks that step `skipped` or `error` and the profile still completes; progress is on the job row, read it with
`GET /api/brands/{id}/profile/status`):

| Step | What it collects | Needs |
|---|---|---|
| `inputs` | what the person typed: domain, LinkedIn/social URLs, extra domains (confirmed, origin `user`) | nothing |
| `dns` | A, NS, MX, SPF, email provider | nothing |
| `rdap` | registrar, creation date, hosting network, abuse contacts | nothing |
| `website` | home page plus about/contact/team/terms/legal pages: text, socials, phones, emails, app links, wallets, JSON-LD organisation, founders | nothing (Firecrawl, if keyed, supplies the page text and `map`) |
| `assets` | logo and favicon files: sha256, perceptual hash (`imagehash`), favicon mmh3. Only the company's own logo (header/nav or named after the brand), never partner logos | nothing |
| `linkedin` | company page facts (followers, employees, tagline, site) and key people | `APIFY_TOKEN`, `APIFY_LINKEDIN_ACTOR`, `APIFY_LINKEDIN_INPUT` |
| `socials` | public Telegram channel previews (no login, no joining); other platforms via an Apify actor | nothing for Telegram; `APIFY_SOCIAL_ACTOR` for the rest |
| `news` | press mentions and "scam/fake/phishing" warnings that name the brand | `SEARCH_API_KEY` (Brave) |
| `dossier` | LLM extraction of legal names, registration numbers, licences, addresses, leadership, official contacts, payment instructions, a summary | `OPENAI_API_KEY` |

Every fact is a row in `brand_facts` (`kind`, `value`, `source_url`, `collector`, `confidence`, `verification`, `review_status`). Verification is
computed in `BrandRepo.upsert_fact`: `verified` (the brand's own site and an independent source agree, or a person entered it), `corroborated`
(own site only), `single_source` (third party only), `conflicting` (two different values for a singular fact such as a registration number).
**Phones, wallets, bank accounts and social handles are never used for detection until a person confirms them** (`needs_confirmation`); the
matcher and scorer read only confirmed numbers and wallets, and domains that are confirmed or backed by the brand's own site
(`BrandRepo.detection_inputs`, `engine/brand/loader.py`). The profile is versioned (`brand_profile_versions`).

Safety built in:
- **SSRF:** every outbound request, including redirects, is checked against private/internal addresses in `engine/clients/base.py` (`new_client`), so a brand "website" cannot point the server at internal services. `base.ALLOW_PRIVATE` is for tests only.
- **LLM:** `engine/llm.py` wraps page text in an untrusted-data guard, validates output against a pydantic schema (one repair retry), logs a hash of every call to `llm_calls`, and `engine/brand/dossier.py` keeps only items that cite a supplied URL **and** literally appear in that page. The model never writes notice text and never sees tools.
- **Ownership:** `POST /api/brands/{id}/verify` checks a DNS TXT record (`_haul16-verify.<domain>` or the domain itself) containing the brand's token. No verified owner, no filings (to be enforced on the file route in the auth milestone).
- **Kill switches:** insert `(collector, FALSE)` into `source_toggles` to disable a collector without a deploy.

### First-run flow and sign-in (Milestone 1.5)
- **Sign-in:** one owner account from `AUTH_OWNER_EMAIL` / `AUTH_OWNER_PASSWORD`. Auth is on whenever a password is set. `POST /api/auth/login` sets an HMAC-signed `HttpOnly`, `SameSite=Lax` cookie;
  a pure ASGI middleware (`engine/auth.py`, so the SSE stream is not buffered) returns 401 for every `/api/*` route except `/api/health` and `/api/auth/*`. Logins are rate-limited (5 a minute per IP).
  The **approver recorded on a notice is the signed-in person**, taken from the session, never from the request body (the body field is only honoured when sign-in is off). `HAUL16_ENV=prod` without
  `AUTH_OWNER_PASSWORD` and `SESSION_SECRET` refuses to start. Multi-user SSO and roles are Milestone 4.
- **No demo brand:** the API starts with no brand (`NO_BRAND`: no seed terms, so nothing matches or is captured) until a real brand is activated. Set `SEED_DEMO_BRAND=true` (development only) or run
  `uv run python -m engine.brand.import_yaml` for the fictional demo brand. `DELETE /api/brands/{id}` removes a brand and everything collected or detected for it.
- **Filing gate:** `POST /api/notices/{id}/file` returns 409 until the brand's domain ownership is verified **and** a signed authorisation letter is on file.
- **Look-alike sweep (`engine/sweep.py`):** `generate_candidates()` builds typo (omission, repetition, transposition, keyboard replacement), homoglyph (Unicode as punycode, `rn`/`m`, `0`/`o`), hyphen, lure-word
  and TLD variants of the brand's domain (cap 600, never the brand's own or allow-listed domains). `run_sweep()` resolves them over DNS (no key), keeps those that exist **and** that the matcher flags, and injects
  the strongest 40 into the normal capture and scoring pipeline (`Watch.inject`, source `lookalike_sweep`), so cases and drafted notices work unchanged. `POST /api/brands/{id}/sweep` and
  `GET /api/brands/{id}/sweep/status`; activating a brand queues the first sweep. It runs as a job in the API process (it needs the `Watch` queue), so keep `EMBEDDED_WORKER=true` on the API service.
- **Setup state:** `GET /api/brands/{id}/setup` returns `{step, complete, steps, next_action, sweep, counts}` computed server-side, so the setup wizard and the Home page always agree on the one next thing to do.
  Order of `next_action`: profile building, details to confirm, start protection, notices to approve, notices to file (or prove ownership first), prove ownership, all clear.

### Brand API
| Method | Path | |
|---|---|---|
| GET/POST | `/api/brands` | list; create (`display_name` plus `website` or `linkedin_url`, optional `socials`, `extra_domains`). Returns the verification record to add |
| GET | `/api/brands/{id}` | brand, verification instructions, latest job |
| POST | `/api/brands/{id}/profile/run` | queue profiling (no duplicate while one is queued/running) |
| GET | `/api/brands/{id}/profile` | facts grouped by kind, people, assets, version summary, `needs_review` and conflict ids |
| GET | `/api/brands/{id}/profile/status` | job progress per step |
| GET | `/api/brands/{id}/setup` | setup step and the single next action (see above) |
| POST/GET | `/api/brands/{id}/sweep`, `/sweep/status` | run and read the look-alike domain sweep |
| DELETE | `/api/brands/{id}` | remove a brand and all its data |
| POST/GET | `/api/auth/login`, `/logout`, `/me` | sign-in (see above) |
| POST | `/api/brands/{id}/verify` | check the DNS ownership record |
| POST | `/api/brands/{id}/facts/{fact_id}/confirm` or `/reject` | human review |
| POST | `/api/brands/{id}/letter` | `{filename, content_base64}`; stores the SHA-256 used in notices |
| POST | `/api/brands/{id}/activate` | point the CT matcher and capture workers at this brand |
Other routes accept `?brand_id=` or the `X-Brand-Id` header and default to the active brand. `GET /api/health` reports `db`, `jobs_pending` and which collectors are configured (booleans only).

### Deploying (Railway + Neon)
Two services from this repo's Dockerfile (branch `live`): **api** (default start command, `HAUL16_ENV=prod`, `EMBEDDED_WORKER=false`) and **worker**
(start command `python -m engine.worker_main`). Set `DATABASE_URL` (Neon), `FRONTEND_ORIGINS` (the Vercel URL), `HAUL16_ENV=prod`, and the collector keys.
`HAUL16_ENV=prod` turns `/demo-sites` and the demo routes off. `GET /api/health` is the health check. The first start applies migrations and seeds the
fictional demo brand if no brand exists; in production create the real brand through the API and activate it.

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

State lives in Postgres (`DATABASE_URL`) and, locally, in `data/` (git-ignored, created on first run):
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
                  store.py (Postgres): threats, identifiers, cases, notices, clock, audit
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
| `engine/store.py` | Postgres store + audit log + case assignment (campaign linking) + counts |
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

## 3. Data model (Postgres, `engine/store.py`; schema in `migrations/`; brand tables in `0002_brand_identity.py`)

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
- **New table/column:** add to the `SCHEMA` string in `store.py`. Add an **Alembic migration** in `migrations/versions/` (they run automatically on start and in tests).

---

## 9. Going live: running the real version (not demo data)

Everything above runs as a **dry-run demo with a fictional brand**. This section is the checklist for turning it
into a real service for a real, consenting brand. Read the legal items first: they gate everything else.

> **Status legend.** `REAL TODAY` = already works against live systems with no key. `NEEDS BUILD` = not implemented
> in this repo; you have to write it. `NEEDS ACCOUNT` = an external signup, key or approval is required.
> Environment variable names marked *(proposed)* **do not exist in the code yet**: today only `HAUL16_PORT` and
> `HAUL16_CT` are read. Introduce a single settings module (for example `pydantic-settings`) when you add them.
> Anything marked `[VERIFY]` is an external process or price that the team has not confirmed. Check the provider's
> own current documentation before relying on it.

### 9.1 What is already real vs what is simulated

| Capability | Today | For live use |
|---|---|---|
| Certificate Transparency watching (5 public logs) | **REAL TODAY**, no key | Keep. Add automatic log-list refresh and lag alerts (9.4) |
| Lookalike matching, scoring, campaign linking | **REAL TODAY** (deterministic) | Tune on real traffic, measure false positives (9.9) |
| Page capture (Playwright/Chromium, SSRF-guarded) | **REAL TODAY** | Run in an isolated sandbox with controlled egress (9.5) |
| Registrar and hosting abuse contacts (public RDAP via `rdap.org`) | **REAL TODAY**, no key | Cache, rate-limit, call registry RDAP servers directly (9.4) |
| Evidence hashing (SHA-256 page and screenshot, favicon mmh3) | **REAL TODAY** | Move files to immutable object storage (9.6) |
| Notice drafting from templates | **REAL TODAY** | Lawyer-review every template (9.2) |
| Human approval, audit log | **REAL TODAY** but the approver is a free-text name | Real login, roles, tamper-evident log (9.7) |
| **Sending a notice** | **SIMULATED** (`filed_dry_run`, nothing leaves the server) | **NEEDS BUILD**: per-channel senders (9.3) |
| **Registrar/host/platform responses and removal** | **SIMULATED** (operator clicks "first response", "removed") | **NEEDS BUILD**: inbound mail parsing and automatic liveness re-checks (9.3, 9.8) |
| Database | SQLite file in `data/` | Postgres (9.6) |
| Job queue | in-process `asyncio.Queue` (lost on restart) | Durable queue (9.6) |
| Brand onboarding | edit `brand.yaml` and restart | Onboarding UI + per-brand storage (9.9) |
| Demo routes (`/api/demo/*`, `/demo-sites`) | on | **Disable or remove in production** (9.12) |

### 9.2 Legal and organisational prerequisites (before any real filing)
These come from the product brief ([`docs/HAUL16-brief.md`](docs/HAUL16-brief.md), section 10) and are not optional.

1. **Signed authorisation letter from each brand** appointing HAUL16 as designated agent. Its SHA-256 is embedded in every notice
   (`authorisation_letter` in `brand.yaml`). No letter, no filing.
2. **Lawyer review** of every template in `channels.yaml` (wrongful-takedown and knowingly-false-notice liability, defamation).
   Keep notices to evidence-backed statements and say "suspected" until confirmed. Check for a legitimate reseller first.
3. **Data protection:** screenshots, phone numbers and handles are personal data. Register with the relevant data protection
   authority, run a DPIA, minimise what you store, and set a retention period (and implement deletion, 9.6). `[VERIFY]` with counsel.
4. **Regulatory confirmation** that a brand-protection tool for virtual-asset providers is permitted (the brief flags a possible
   advertising/marketing directive and possible security-service accreditation). Get it in writing. `[VERIFY]`
5. **Verified recipients.** `channels.yaml` marks telco and CSA recipients `verified: false`. Confirm each address or portal on the
   organisation's official page and only then set `verified: true`. Third-party guides list conflicting numbers.
6. **Platform terms:** do not crawl social platforms against their terms. Use official reporting channels and public sources only.
7. **Do not make vendor-speed claims.** Report the measured median from the takedown clock.

### 9.3 Channels: what each real integration needs

Channel definitions live in `channels.yaml`. Making a channel real means writing a **sender** for its `method` and wiring it to
`POST /api/notices/{id}/file` (replacing the dry-run state change) **after** the existing human-approval check. Keep a global
dry-run switch so a deployment can be put back into safe mode.

| Channel (`id`) | Real method | What you need | Status |
|---|---|---|---|
| `registrar` | Email to the abuse address from RDAP | Outbound email sender (9.3.1). gTLD registrars have ICANN abuse obligations; ccTLDs follow their own processes | `NEEDS BUILD` |
| `hosting` | Email to the network abuse contact from RDAP | Same email sender. CDNs (Cloudflare etc.) hide the origin: expect to file with the CDN, which forwards | `NEEDS BUILD` |
| `google_safe_browsing` | Web Risk **Submission API** (volume) or the manual report form | A Google Cloud project with the Web Risk API enabled and an API key or service account. The Submission API may need allow-listing or approval `[VERIFY]`. Start with the manual form (assisted mode) | `NEEDS ACCOUNT`, `NEEDS BUILD` |
| Microsoft SmartScreen | Single-URL report form | None for the form; no stable public API known `[VERIFY]`. Assisted mode only | not in catalogue yet |
| Meta / WhatsApp | Brand Rights Protection portal or in-app report | A rights-holder or authorised-agent account with Meta `[VERIFY]` | not in catalogue yet |
| X, LinkedIn, TikTok, Telegram | Each platform's impersonation form | A reporter account; some require the rights holder to file or authorise you `[VERIFY]` | not in catalogue yet |
| `telco_mtn` | Email `mmfraudteam.GH@mtn.com`, or USSD `*170#` then 6 then 4 (cited in the brief from MTN's support page) | Email sender; confirm the address is still current | `NEEDS BUILD` |
| `telco_telecel`, `telco_airteltigo` | Official fraud desks | **Verify on each operator's official page only** | `NEEDS BUILD`, `[VERIFY]` |
| `csa` (national cyber security authority) | Incident report (portal/hotline) | Confirm the channel and any trusted-escalation arrangement (the brief lists a CSA conversation as an ask) | `[VERIFY]` |
| NCA | Spoofing/SIM-abuse complaint | Confirm the channel | not in catalogue yet |

**Assisted mode (recommended first step).** Before automating any sender, ship a mode where the approved notice is shown with
a "Copy text", an evidence pack download and a link to the recipient's official form, and a human files it by hand, then
marks it filed. That gives real filings, real clock data and real responses without integration risk. It needs a "filed manually"
event (store `simulated = 0`) and a small UI change; the state machine otherwise stays the same.

#### 9.3.1 Outbound and inbound email (needed by registrar, hosting, telcos)
- **Service:** a transactional email provider (for example Amazon SES, Postmark, SendGrid) or your own SMTP. `NEEDS ACCOUNT`.
- **Domain:** a sending domain you control with **SPF, DKIM and DMARC** configured, or abuse desks will drop the mail.
  Use a role address such as `takedowns@<your-domain>` with a monitored mailbox.
- **Keys/settings** *(proposed)*: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` or a provider `EMAIL_API_KEY`,
  `EMAIL_FROM`, `EMAIL_REPLY_TO`.
- **Attachments:** the authorisation letter and an evidence pack per notice (screenshot, HTML, hashes, RDAP record,
  timestamps) as a zip or PDF. Generating the pack is `NEEDS BUILD`.
- **Inbound:** to detect responses, receive replies by an inbound-email webhook (a provider feature) or poll a mailbox over IMAP,
  match them to a notice (a per-notice `Reply-To` or a `ntc_...` token in the subject), and write a
  `first_response` clock event with `simulated = 0`. `NEEDS BUILD`.
- **Etiquette:** one notice per recipient per site, no repeat-spamming, handle bounces and keep a suppression list.

### 9.4 Data sources and lookup APIs

| Source | Used for | Key? | Notes |
|---|---|---|---|
| Public CT logs (`engine/ct_poll.py`, `LOGS`) | Detect new certificates containing the brand's strings | **No key** | `REAL TODAY`. The log list is hard-coded for 2026 shards. **Automate it**: fetch Google's CT log list (v3 `log_list.json`) on a schedule, add usable RFC 6962 logs, drop retired ones, and alert when a log's backlog (`S.lag`) keeps growing. Respect each log's rate limits |
| RDAP via `https://rdap.org` (`engine/rdap.py`) | Registrar and hosting abuse contacts | **No key** | `REAL TODAY`. The public bootstrap redirector is rate-limited and has no SLA. In production use the IANA RDAP bootstrap data and query registry/RIR RDAP servers directly, add a cache with a TTL, back off on 429, and store the raw RDAP JSON as evidence. Some ccTLDs have no RDAP (the code already degrades to `unknown`) |
| DNS (A/AAAA/NS/MX) | Preserve evidence, find hosting origin | No key for live DNS | Log the exact answers and timestamps. Historic DNS needs a paid provider |
| WHOIS / domain intelligence (WhoisXML API, DomainTools, SecurityTrails, ...) | Registrant data, history, newly-registered feeds | **Paid key**, `NEEDS ACCOUNT` | **Optional.** Mainly for ccTLDs without RDAP. *(proposed)* `WHOIS_API_KEY` |
| Newly-registered-domain feeds | Catch lookalikes before a certificate exists | Often paid or limited free tiers | **Optional.** CT only sees domains that obtained a certificate. Country-code zone files are generally not public |
| The brand's own assets | Favicon hash, numbers, wallets, handles, logos | n/a | Collected at onboarding (9.9). Compute `official_favicon_mmh3` from the brand's real favicon |
| Threat-intel / blocklist feeds (Google Web Risk lookup, PhishTank, OpenPhish, abuse.ch, VirusTotal) | Cross-check a URL before filing; avoid duplicate reports | Varies; some need keys and have usage terms | **Optional.** Extra evidence, never the only reason to file |
| Blockchain explorers (TronScan, Etherscan, Blockstream) | Wallet activity as extra evidence | Free keys, rate-limited | **Optional**, `NEEDS BUILD`. *(proposed)* `TRONSCAN_API_KEY`, `ETHERSCAN_API_KEY` |
| Ghana telco prefix list (`TELCO_PREFIX` in `notices.py`) | Route a phone number to the right telco | n/a | A hint only because of number portability. Verify and keep current |

### 9.5 Page capture at real scale (`engine/capture.py`)
- **Isolation:** run captures in a container or VM per job with no access to your database, secrets or internal network. The in-code
  SSRF guard blocks private addresses, but defence in depth (network policy, egress allow-list, non-root user, seccomp) is expected
  because the page is attacker-controlled.
- **Egress and geography:** some scam kits show different content by country or hide from datacenter IPs. A proxy with local
  (Ghana, ideally mobile-network) egress improves capture quality. `NEEDS ACCOUNT` (proxy provider), *(proposed)* `CAPTURE_PROXY_URL`.
  Have counsel confirm the proxy use.
- **Concurrency and cost:** `Watch(workers=2)` today. A common brand name will queue; size workers, set per-host rate limits and a
  global cap on captures per hour. The `UNIQUE (brand_id, hostname)` constraint already de-dupes.
- **Browsers:** `uv run playwright install --with-deps chromium` in the server image. Pin the Playwright version.
- **Retries:** unreachable hosts retry at 120s, 600s, 1800s (`RETRY_AFTER_S`). Tune from real data and add a later re-check for sites that go live days after the certificate.
- **Prompt-injection safety:** page text is data. If you add any LLM step, give it no tools, a strict output schema and human review.

### 9.6 Infrastructure and storage

| Need | Demo | Live |
|---|---|---|
| Database | SQLite file | **PostgreSQL** (managed). Port the SQL in `engine/store.py` (plain SQL, JSON stored in TEXT columns), add migrations (Alembic), use `jsonb`. `NEEDS BUILD` *(proposed)* `DATABASE_URL` |
| Evidence files | `data/evidence/` on local disk | **S3-compatible object storage** with versioning, encryption at rest and, for evidence, object-lock/WORM. Keep the SHA-256 in the DB and verify on read. `NEEDS BUILD` *(proposed)* `EVIDENCE_BUCKET` plus cloud credentials or an IAM role |
| Job queue | `asyncio.Queue` in the API process | Durable queue (Redis with RQ/arq/Celery, or a Postgres-backed queue) and a **separate worker process**, so API restarts do not drop captures and the CT poller can be scaled independently. `NEEDS BUILD` *(proposed)* `REDIS_URL` |
| CT follower position | starts at each log head on every start | Persist each log's last processed index so a restart does not miss certificates |
| Retention and deletion | none | Scheduled deletion of evidence and personal data past the retention period; export-on-request tooling |
| Backups | none | Automated DB backups, tested restores, evidence bucket replication |
| Hosting | `uvicorn` on a laptop | Container image (Python 3.12 + Chromium), one API instance plus N workers, behind a TLS reverse proxy. Needs long-lived processes: the CT follower and the SSE stream do **not** fit serverless or short-lived functions. Disable proxy buffering for `/api/events` |
| Observability | `print` | Structured logs, metrics (certs/s, queue depth, capture failures, notice counts), alerts on CT lag and failed sends, error tracking (for example Sentry, *(proposed)* `SENTRY_DSN`) |

### 9.7 Authentication, authorisation and audit (currently missing)
- **Today:** no login. The approver is whatever name the browser sends. **Do not expose the current API publicly.**
- **Add:** user login via OIDC/SSO (Auth0, Clerk, Keycloak, Azure AD, Google Workspace) or your own session auth.
  `NEEDS ACCOUNT`, `NEEDS BUILD`. *(proposed)* `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `SESSION_SECRET`.
- **Roles:** at least `analyst` (view, redraft), `approver` (approve/reject/file) and `admin` (brand register, users). Enforce them
  server-side on every mutating route in `engine/api.py`. Take the approver identity from the authenticated session, **not** from the request body.
- **Multi-tenant:** queries are already scoped by `brand_id`; add a user-to-brand membership check.
- **Audit:** keep `audit_log` append-only (revoke UPDATE/DELETE for the app role), record user id, IP and the hash of the approved notice
  text; consider hash-chaining entries so tampering is detectable.
- **Hardening:** restrict CORS (`engine/api.py`) to the real frontend origin (or serve both from one origin), rate-limit `/api/inject`,
  add request size limits and security headers, and CSRF protection if you use cookie sessions.

### 9.8 Closing the loop: a real takedown clock
Today `first_response` and `removed` are clicked by an operator and stored with `simulated = 1`. For live data:
- **First response:** from inbound email matching (9.3.1), or the operator in assisted mode (store `simulated = 0`).
- **Removal:** add a scheduled **liveness checker** that re-captures each filed URL (for example at 15 min, 1 h, 6 h, 24 h, then daily for 14 days)
  and records `removed` when the page returns 404/NXDOMAIN, a registrar/host suspension page, or a browser-warning interstitial.
  Store the evidence of removal. `NEEDS BUILD`.
- **Re-hosting watch:** the same operator reappears on new domains with the same kit. Campaign linking on phone/wallet/telegram already
  catches it while a case is open; consider keeping **closed** cases linkable too.
- **Report honesty:** `/api/report` medians are only meaningful when events are real. The `simulated` column exists: label simulated rows
  and exclude them from headline numbers.

### 9.9 Brand onboarding and tuning
- Replace the single `brand.yaml` with a `brands` table and an onboarding UI/API: display name, seed terms, official domains, handles,
  numbers, wallets, logo plus computed favicon hashes, allow-list (legitimate resellers), **authorisation letter upload** (store the
  PDF, compute its SHA-256), contacts and the approver list.
- **Seed terms are the main false-positive lever.** Short or common words flood the queue. Start narrow, review a week of hits, then widen.
  Track precision (threats confirmed vs notices rejected) and add an allow-list entry for every legitimate site you find.
- Bump `RULES_VERSION` for any rules change and keep earlier verdicts for audit.
- Decide per brand which channels are enabled and which recipients are verified.

### 9.10 Optional: AI-assisted triage (not implemented)
The brief allows AI for triage and extraction (never for sending). The code today is fully deterministic and calls no model. If you add one:
*(proposed)* `ANTHROPIC_API_KEY`; use structured output against a fixed schema; run it tool-less on the **extracted text only**;
treat page content as untrusted; log every prompt and response with the evidence hash; keep the human approval step; and never let a
model write notice text (templates only, invariant 4).

### 9.11 Configuration and secrets summary

Currently read by the code: `HAUL16_PORT`, `HAUL16_CT`. Everything else below is a **proposal**; add it through one settings module
and a committed `.env.example` (no real values). Keep real secrets in a secret manager (AWS/GCP Secret Manager, Vault, Doppler or
your host's secret store), never in git. `.env` is already git-ignored.

| Variable *(proposed)* | Purpose | Needed for live? |
|---|---|---|
| `HAUL16_ENV` (`dev` / `prod`) | Switch demo routes off, enforce auth | yes |
| `HAUL16_DRY_RUN` (`1` / `0`) | Global kill switch for real sending; default `1` | yes |
| `HAUL16_PORT`, `HAUL16_CT` | **existing** | yes |
| `DATABASE_URL` | Postgres connection | yes |
| `REDIS_URL` | Queue and cache | if using Redis |
| `EVIDENCE_BUCKET` plus cloud credentials | Evidence storage | yes |
| `SESSION_SECRET`, `OIDC_*` | Login | yes |
| `SMTP_*` or `EMAIL_API_KEY`, `EMAIL_FROM`, `EMAIL_REPLY_TO` | Send notices | for email channels |
| `GOOGLE_WEB_RISK_API_KEY` or service-account JSON | Web Risk submission | for that channel |
| `CAPTURE_PROXY_URL` | Local egress for capture | optional |
| `WHOIS_API_KEY` | Extra domain intelligence | optional |
| `TRONSCAN_API_KEY`, `ETHERSCAN_API_KEY` | Wallet context | optional |
| `ANTHROPIC_API_KEY` | AI triage | optional |
| `SENTRY_DSN` | Error tracking | recommended |
| `FRONTEND_ORIGIN` | CORS allow-list | yes |

### 9.12 Switching demo features off in production
- Do not mount `/demo-sites`, and disable `POST /api/demo/clone` and `POST /api/demo/reset` when `HAUL16_ENV=prod`. **`demo/reset` deletes all data.**
- Keep `allow_local=True` out of every production route (only fixtures use it).
- Replace the fictional `brand.yaml` and fixtures, and delete the demo `data/`.
- Keep **dry run** on until the first real filing is approved by counsel and the brand. Expose the flag in `GET /api/health` (`dry_run`) and have
  the frontend show a clear **DRY RUN** or **LIVE** indicator from it (today the badge is static text).

### 9.13 Suggested rollout
1. **Pilot, assisted mode:** one consenting brand, real CT detection and capture, real RDAP, a human files every notice by hand. Measure precision and a real clock.
2. **Harden:** Postgres, object storage, durable queue, login and roles, backups, monitoring (9.6, 9.7).
3. **Email senders:** registrar, hosting and telco channels with inbound reply matching (9.3.1).
4. **API channels:** Web Risk submission and platform integrations as approvals arrive.
5. **Automation:** liveness checker, re-hosting watch, onboarding UI, report exports (9.8, 9.9).
6. **Scale:** more brands, more logs, regional proxies, vendor intelligence feeds.

### 9.14 Go-live checklist
- [ ] Signed authorisation letter on file for the brand; its hash matches the one in notices
- [ ] Lawyer-approved templates; a written legitimate-reseller check process
- [ ] Data-protection registration and DPIA done; retention and deletion implemented
- [ ] Written confirmation of the regulatory status of a brand-protection tool `[VERIFY]`
- [ ] Every channel recipient verified on official pages; unverified ones stay `verified: false` and cannot be auto-sent
- [ ] Login and roles on; approver taken from the session; API not reachable without auth
- [ ] Postgres, object storage, durable queue, backups with a tested restore
- [ ] Email domain with SPF/DKIM/DMARC; inbound replies matched to notices
- [ ] CT log-list refresh and lag alerts; RDAP cached and rate-limited
- [ ] Demo routes disabled; fixtures and demo data removed
- [ ] `HAUL16_DRY_RUN` flipped to `0` only after the first approved real filing has been rehearsed end to end
- [ ] Monitoring, alerting and an on-call owner

## 10. Known gaps / ideas

- No auth: the approver is a free-text name sent by the client. A real deployment needs login and role checks.
- Timestamps are ISO-8601 TEXT (not `timestamptz`) to keep the API and frontend format identical; convert in a later migration if needed.
- Single brand per process. `brand.yaml` has no edit UI.
- CT log list is hard-coded in `ct_poll.LOGS` (Google Argon and Xenon 2026h2, Cloudflare Nimbus 2026, Sectigo Elephant and Tiger 2026h2); rotate when logs retire.
- RDAP bootstrap uses `https://rdap.org`; add caching and rate limiting before heavy use.
- Notices go through `str.format_map`: only add placeholders that exist in `facts_for`, or they render `unknown`.
- Telco/CSA recipients are unverified placeholders.

## 11. Related
- Product brief: [`docs/HAUL16-brief.md`](docs/HAUL16-brief.md)
- Frontend: https://github.com/dezmymachine/haul16-frontend
