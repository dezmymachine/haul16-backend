# HAUL16: a brand abuse takedown desk for VASPs

Pronounced "Hall 16". Working brief for the SEC Ghana x ALX Ghana Virtual & Digital Assets Hackathon.

- **Prepared:** 30 October 2026
- **Audience:** Claude Code, to brainstorm architecture and a build plan with the team
- **Status:** working draft. Challenge anything tagged `[ASSUMPTION]` or `[UNVERIFIED]`.

---

## 0. How to use this document

**What we want from you (Claude Code):**

1. Read the whole brief and restate the product in your own words. List what is unclear or contradictory.
2. Ask the team for the build window and the team mix (backend, ML, frontend, domain, business) before planning anything.
3. Propose an architecture, a module breakdown and a build order that fits the time we have.
4. Name the three riskiest technical assumptions and propose a quick spike for each.
5. Only then scaffold code.

**Guardrails that apply to everything built from this brief:**

- All filings and notices are **dry run**. The demo never sends anything to a real registrar, host, platform, telco or regulator.
- Demos use a **fictional brand**. Never name a real company as a victim or as an impersonator unless it has authorised that.
- A **human approves every notice** before it is filed (see section 10, wrongful takedown liability).
- **Evidence first.** Every claim in a notice must trace to captured evidence. Unknown is a valid answer. Never label anything "safe".
- **Minimise personal data.** Phone numbers, wallet strings and screenshots are personal data in Ghana (section 5.4).

**Tags used throughout:**

| Tag | Meaning |
|---|---|
| `[VERIFIED]` | Confirmed from a primary source, or from several independent sources, during our research |
| `[REPORTED]` | Single secondary source, or press report of an official notice. Check the original before relying on it |
| `[ASSUMPTION]` | Our estimate or hypothesis, not data |
| `[UNVERIFIED]` | Could not be confirmed. Treat as a question |

---

## 1. One-page summary

**Name:** HAUL16

**One line:** When someone clones your digital asset business (fake site, fake social page, fake support line, fake Google listing), HAUL16 finds it, proves it, files the takedowns across every channel, and shows you how fast each one came down.

**The user:** the compliance, risk or brand lead at a VASP (licensed or SEC sandbox participant) or fintech in Ghana.

**The problem:** the owner finds out about a clone from a customer who lost money. The response today is a post on X, an email to a platform, and a hope. There is no tracking, no proof for the regulator or the bank partner, and no idea how long anything takes.

**The product (five jobs):** Watch, Triage, Prove, Act, Show. Section 6.

**Who pays:** VASPs and fintechs first. Then banks, telcos, utilities and other exposed brands. `[ASSUMPTION]` Pricing and willingness to pay are untested. Section 7.

**Why this and not a registry or a checker:** in the brand protection market, vendors get paid for the removal, not for detecting or listing. Three Ghanaian crowdsourced registries already exist. Section 5.

**What we build for the hackathon:** a four-minute dry-run demo on a fictional brand: onboard, detect, open a case, review drafted notices, start the takedown clock, export a regulator-ready report. Section 8.

**What we do not build:** a public registry, a consumer scam checker, anything that moves money, real filings, deepfake detection, a "verified" badge for VASPs.

---

## 2. Context: the event and the judging lens

**Event:** SEC Ghana x ALX Ghana Virtual & Digital Assets Hackathon. 30 innovators, part of the African Virtual Asset Summit programme. `[VERIFIED]`
**AVAS 2026:** 2 to 3 November 2026, Kempinski Hotel Gold Coast City, Accra. Banks, financial intelligence units and VASPs are expected. `[VERIFIED]`

**What the organisers' slides say** (photographed in the room, 29 October 2026):

- Five opportunity areas: investor protection and education, fraud detection and market integrity, regulatory technology and compliance, transparency and digital asset infrastructure, access and financial literacy.
- "Pick one area, one user and one problem. Depth beats breadth."
- A problem is worth solving when it is **frequent, painful, measurable and adoptable**.
- A credible prototype shows a working demo on **test data**, security and privacy by design, a realistic path to adoption, and is **clear about what it is not**.
- Area 2 (fraud): "Validate first: who receives your alert, and what can they actually do with it?" A credible prototype shows clear detection logic with low false alarms and **a path to hand alerts to the right authority**.
- Never hold real user funds in a prototype. Answers should trace back to an official source.

**How HAUL16 maps:** Area 2 is the home. The answer to "who receives the alert and what can they do with it?" is the brand owner, the registrar or platform, the telco fraud desk, and the CSA. HAUL16 is the layer that turns an alert into an executed removal.

**A note on the speaker:** the hackathon's keynote slide names Kofi Genfi as Founder & CEO of Vaulta Digital Assets. Vaulta is on the SEC's March 2026 sandbox list. `[VERIFIED]` That makes it a natural design-partner conversation and the exact customer persona. Do not assume he is a judge.

**Team belief, unverified:** other teams' ideas have no direct revenue stream. Treat our revenue advantage as a hypothesis until we have a design partner.

---

## 3. How we got here

| Stage | Idea | Outcome |
|---|---|---|
| 1 | Consumer licence and scam checker (WhatsApp, USSD) | Dropped. Crowded, B2C is hard to monetise, thin official data behind it |
| 2 | B2B counterparty and internal fraud screening for payment providers | Built as a prototype. Kept as a possible module (section 8.4) |
| 3 | Fraud registry: crawl the web, crowdsource reports, scam MoMo numbers | Dropped as the core. Three Ghanaian registries already exist (section 5.2) |
| 4 | **Takedown and brand protection for VASPs** | **Chosen.** The money in this market is in the removal |

**Names:** "Check Before You Send" was dropped because TrustCheck already uses it as a tagline. "Unclone" was a working name. **HAUL16** is the chosen name.

**Key finding that drove the pivot:** in the brand protection market, vendors sell detection plus takedown, with revenue from the removal, using registrar and host relationships. A list of incidents is cheap. A brand pays when someone makes the fake thing go away.

---

## 4. The problem, from the owner's seat

You run a licensed or sandbox VASP in Ghana. Someone clones you. It hits four ways.

| Surface | What it looks like | Evidence |
|---|---|---|
| **Fake sites and apps** | Lookalike domains and cloned platforms that collect deposits | SEC named 23 unlicensed online entities in July 2026 and warned about YEPBIT Exchange and BonChat `[REPORTED]`. These are unlicensed schemes, not documented clones of a specific licensed VASP `[UNVERIFIED: no documented clone of a Ghanaian sandbox VASP found]` |
| **Fake social pages, ads and deepfakes** | Cloned profiles, paid ads, doctored endorsement video | **GoldBod**, an SEC sandbox participant, warned on 3 Sept 2026 about a **fake LinkedIn page with 24K followers**. The page was unverified and "bod" was spelt "board" `[VERIFIED]` ([GhanaFact](https://ghanafact.com/scam-alerts-fake-press-release-how-bog-ecg-gra-others-fought-digital-misinformation-in-september/)). BoG and SEC warned on 2 Sept 2026 about a doctored video of the President endorsing "Daily Wealth Guide" `[VERIFIED]` |
| **Fake support lines and collection numbers** | WhatsApp, Telegram and MoMo numbers posing as you or your support | In Côte d'Ivoire, police dismantled a network posing as Wave customer service (April 2026) `[REPORTED]`. MTN Ghana warns customers about impersonation by SMS and WhatsApp `[VERIFIED]` |
| **Fake Google Maps and business listings** | Cloned listings with a scammer's number | TrustGH traced nearly 1,000 numbers tied to cloned Google Business Profiles of Ghanaian food chains `[REPORTED]`. Whether VASPs face this surface is `[ASSUMPTION]` |

**Why the owner can't just shout about it:** a joint BoG and SEC notice of 20 February 2026 reportedly told VASPs, including sandbox participants, to stop mass marketing unless authorised, and advocacy for virtual assets is a regulated activity `[REPORTED, from team research; verify]`. So VASPs have less room to publicly counter a clone, and scammers fill the information gap.

**The owner's current response** (inferred from how brands behave, not from interviews `[ASSUMPTION]`): public warning posts, individual reports to platforms, customer support firefighting, no case tracking, no proof for the regulator.

---

## 5. What we learned

### 5.1 How takedown works

A complete takedown is a multi-channel operation. Source: [Doppel's step-by-step guide](https://www.doppel.com/blog/phishing-site-takedown) (a vendor, so read for mechanics, not claims).

1. **Collect evidence first:** URL, IP, hostname, timestamped screenshots, a SHA-256 of the page source, WHOIS and DNS records preserved before they change.
2. **Registrar:** file an abuse report. ICANN enforces abuse obligations for gTLD registrars (.com, .net, .org). **Country domains follow their own authorities and processes.**
3. **Hosting provider:** file with its abuse desk. Identify the host from the IP. CDNs like Cloudflare hide the origin.
4. **Browser blocklists and anti-phishing feeds:** Google Safe Browsing (manual form, or the Web Risk Submission API for volume), Microsoft SmartScreen, APWG.
5. **Distribution channels:** Meta (Brand Rights Protection portal supports bulk reporting), X, LinkedIn, TikTok, ad networks, Google Search results, WhatsApp and Telegram in-app reporting.
6. **Monitor for re-hosting:** the same operator re-registers lookalikes and re-hosts the same kit. Infrastructure and kit fingerprints are a stabler signal than individual domains.

**Why vendors are fast:** pre-approved API or direct-contact workflows with registrars, hosts, platforms and telcos, which bypass public abuse queues, and campaign-level correlation so one action removes connected infrastructure. Netcraft claims 75% of its takedowns go through API or direct channels `[REPORTED, competitor blog]`.

**Speed claims (all vendor-published, treat as marketing):** Netcraft median 33 minutes to 1.9 hours depending on the source, Bolster 75% in under 60 seconds, Doppel median under 10 hours, manual abuse reporting 10 to 12 days. A Doppel post puts the manual average at 58 days. `[REPORTED]`

**Pricing:** custom, unpublished, scaled to brand count and surfaces, billed annually. `[REPORTED]` We have no price anchor for Ghana. `[UNVERIFIED]`

**Where the moat is:** relationships with registrars, hosts and platforms, evidence quality, and volume. Not detection. We cannot claim vendor-grade speed. Our first honest number is whatever we measure ourselves.

### 5.2 Competition

**Global vendors** (Netcraft, Bolster, Doppel, Red Points, PhishFort for crypto and Web3): sold to enterprises, quote-based pricing, global coverage. `[UNVERIFIED]` whether any covers Ghana's local channels (telco fraud desks, CSA, NCA). Doppel says it automates removals across registrars, social platforms, ad networks and telcos.

**Ghanaian crowdsourced registries** (the idea we dropped as a core):

| Registry | What it is | Scale |
|---|---|---|
| **TrustGH** (trustgh.com) | Public scam verification: search a phone number for risk level and reports. Ran an OSINT investigation of cloned Google Business Profiles | Nearly 5,000 reported suspicious numbers `[REPORTED]` |
| **Transparent Turtle** | Public registry of community-flagged numbers, businesses and reviews | Not stated |
| **TrustCheck** | Community scam reports and business reviews. Tagline: "Check Before You Send" | 275 users, 60 brands listed `[REPORTED]` |

**Takeaway:** none of these *executes removals*. They list. That is the gap HAUL16 targets, and it is also why the registry alone was not worth building.

### 5.3 Ghana facts to ground the story

| Fact | Status |
|---|---|
| Banks, SDIs and PSPs reported **24,778 fraud cases in 2025**, up 48% from 16,733. PSPs reported 24,124 of them. Value at risk about GH¢101m | `[VERIFIED]` ([BoG 2025 Fraud Report](https://www.bog.gov.gh/wp-content/uploads/2026/07/FRAUD-REPORT-2025.pdf)) |
| Banks and SDIs recovered about 5% of value at risk (GH¢3.7m of GH¢68.2m). This is banks and SDIs only, not PSPs | `[VERIFIED]` same report |
| CSA: **352 online investment fraud cases and GH¢3.43m lost in H1 2026**. Schemes advertise on social media and WhatsApp groups and take payment mainly by mobile money, and change identities frequently | `[VERIFIED]` ([CSA alert via Citi](https://www.citinewsroom.com/2026/07/online-investment-scams-cost-ghanaians-gh%c2%a23-4m-in-six-months-csa/), [Graphic](https://www.graphic.com.gh/news/general-news/scammers-target-mobile-money-users---csa-warns-of-sharp-rise-in-fraudulent-investment-schemes.html)) |
| Deputy Minister said CWPC Digital Marketing led to reported losses of about 1.14 million (currency not stated in the report) from 174 reports in July 2026, and called for bank, fintech and telco collaboration | `[REPORTED]` |
| SEC named 23 unlicensed online entities (22 July 2026), a separate notice named YEPBIT Exchange and BonChat. **YEPBIT and BonChat are already among the 23, so the total is 23, not 25** | `[VERIFIED]` |
| SEC sandbox: 11 firms admitted 10 March 2026, expanded to **20** in August 2026 (press gives 19 August as the publication date). Full list with pilot activities is on the SEC site (appendix A) | `[VERIFIED]` ([SEC page](https://sec.gov.gh/?p=32564)) |
| BoG directive of 12 June 2026: banks, e-money issuers and PSPs must stop supporting unauthorised crypto foreign-currency wallet services. It **names no platforms** | `[VERIFIED]` |
| SEC Directive Dir/001/06/2026 (23 June 2026): exempt categories reportedly include back-office tools, transaction screening and AML regtech, complaint portals, and awareness-only education | `[REPORTED]` Confirm in writing whether a brand protection tool for VASPs is exempt |
| Brands mostly respond with **public warnings**: MTN customer advisories (May 2026), GoldBod and ECG alerts (Sept 2026), BoG advisories (Oct 2026) | `[VERIFIED]`. Whether any pay for takedown services is `[UNVERIFIED]` |
| **Wave and Paystack:** we found no public description of their takedown processes. Wave's public response to a Sept 2026 wave of fraud was criticised as a single social post | `[REPORTED]` |

### 5.4 Ghana's levers and constraints

**The .gh registry is in turmoil, so do not build on it.**
- On 6 Oct 2026 Google disclosed that attackers compromised the **.gh, .sl and .as registries** and obtained unauthorised HTTPS certificates for Google and other brands. Google's own systems were not breached. `[VERIFIED, multiple outlets]`
- Ownership of .gh is disputed between the long-time operator (Network Computer Systems, successor Ghana Dot Com) and the state's Ghana Domain Name Registry under Act 772. The ministry has inaugurated a board to take full control. `[VERIFIED]`
- One report says domain providers now list .gh registrations as suspended. `[REPORTED, single source]`
- Registration needs a Ghana-registered company and a Ghana-based administrator, so scam sites probably sit mostly on .com, .xyz and similar. `[ASSUMPTION: no data]`
- We found **no published .gh abuse policy**. `[UNVERIFIED]` Treat .gh as a conversation to have, not a dependency.

**Telcos and regulators:**
- MTN Ghana takes fraud reports through `*170#` then 6 (My Wallet) then 4 (Report Fraud), or `mmfraudteam.GH@mtn.com`. `[VERIFIED, MTN support page]` Telecel and AirtelTigo channels: **verify on official pages only**, because third-party guides list conflicting numbers.
- The CSA takes incident reports (292 and an online portal, `[REPORTED]`). The NCA handles SIM and SMS spoofing complaints `[REPORTED]`. What these bodies actually do after a report is opaque. We found no block or takedown rate data. `[UNVERIFIED]`

**The law that shapes the product (Electronic Transactions Act 2008, Act 772, sections 90 to 95):**
- Hosting providers are protected from liability if they remove content on a **valid takedown notice**.
- A notice has required contents, and a provider must act expeditiously.
- **An intermediary is liable for a wrongful takedown**, and a notice that knowingly misrepresents facts creates liability for its author.
- **Implication:** evidence quality is both the product and the legal protection. A human must approve every notice.

**Data protection (Act 843):** processors of personal data must register with the Data Protection Commission, and processing without registration is an offence `[REPORTED]`. HAUL16 will hold screenshots, numbers and handles. Plan registration and a DPIA.

**Cybersecurity Act 1038 / CSA accreditation:** the CSA has told institutions to use cybersecurity service providers that are licensed or accredited. Whether HAUL16 needs accreditation is `[UNVERIFIED]`. Ask a lawyer.

---

## 6. The product: HAUL16

### 6.1 The five jobs

1. **Watch.** The brand registers its official assets once: domains, social handles, phone and MoMo numbers, wallet addresses, logo, licence or sandbox status. HAUL16 then watches for lookalike domains, pages using the name or logo, fake apps, cloned listings, and numbers claiming to be the brand. Customers can also forward anything to a WhatsApp line that answers "Is this us?" against the official-channels list.
2. **Triage (AI).** Separate a true clone from an unrelated business, a legitimate reseller, or a fan page. Extract payment identifiers. Link connected pieces into one **campaign**. Score confidence. Route only novel or ambiguous cases to a human.
3. **Prove.** Build a timestamped **evidence pack**: screenshots, page hash, WHOIS and DNS, the brand's authorisation letter, and the brand's official-asset record.
4. **Act.** Draft the right notice for each channel (registrar, host, Google Safe Browsing, Meta, Google Business Profile, telco fraud desk, CSA), let a human approve, file it, and start a **takedown clock** per case per channel. Keep watching for re-hosting.
5. **Show.** A brand dashboard (open cases, time to removal, repeat offenders) and a **monthly report** the brand can hand to its regulator or bank partner as proof it protects customers.

### 6.2 Legal footing: designated agent

The brand appoints HAUL16 as its **designated agent** through a signed authorisation letter. Platforms act on the rights holder or its authorised agent, and the letter travels with every evidence pack. This is also the commercial unlock: each signed letter is a customer.

### 6.3 Where AI helps, and its limits

| Step | AI role | Risk and control |
|---|---|---|
| Intake | OCR screenshots, transcribe voice notes (Twi, Pidgin, English), extract numbers, links and names | Misreads. Show extracted fields for correction |
| Clone classification | Compare candidate to the brand's official assets, logo and copy | False positives on resellers. Require a reason code, human review when unsure |
| Campaign clustering | Link domains, handles, numbers, wallets and kit fingerprints | False links smear innocents. Show confidence, link only on strong evidence |
| Evidence pack | Assemble the file in each recipient's required format | Wrong facts. Evidence comes from captured data, not model output |
| Notice drafting | Write the abuse report to match each channel's policy | Hallucinated claims. Fixed templates, model fills only verified fields |
| Triage | Route routine cases, escalate odd ones | Over-automation. Human approves every filing |

**Hard rules:**
- Deterministic code captures evidence and decides what is stated. The model only extracts and phrases.
- **Pages the scanner reads are attacker-controlled.** Treat their content as untrusted input. The scanning agent gets no tools or credentials, isolated egress, SSRF protection, no downloads, and a strict output schema (prompt injection defence).
- Every evidence record stores source snapshot hash, rule version and model version so a verdict is reproducible.

### 6.4 Surfaces

- **Brand onboarding:** official asset register, authorisation letter upload.
- **Detection feed:** candidate threats with confidence and reason codes.
- **Case view:** evidence, campaign graph, drafted notices per channel, approval, clock.
- **Takedown clock:** per case per channel: detected, filed, first response, removed. Median time to removal per channel is the headline metric.
- **Brand dashboard and monthly report** (PDF or JSON export).
- **WhatsApp "Is this us?" line** (stretch). `[UNVERIFIED]` WhatsApp Business API requirements and costs.
- **API:** submit a suspicious item, get a case ID and status.

### 6.5 Channel catalogue (what to file where)

| Channel | What to file | Hackathon status |
|---|---|---|
| Registrar | Abuse report citing the policy violation, brand and harm | Draft only |
| Hosting provider | Abuse report referencing the AUP, with HTTP headers and screenshots | Draft only |
| Google Safe Browsing / Web Risk | Phishing URL submission (API supports abuse subtype and targeted brand) | Draft only |
| Microsoft SmartScreen | Single URL report | Draft only |
| Meta (incl. WhatsApp) | Brand Rights Protection report or in-app report | Draft only |
| X, LinkedIn, TikTok, Telegram | Impersonation report via each platform's form | Draft only |
| Google Business Profile | Report of a fake listing. `[UNVERIFIED]` exact process | Draft only |
| Telco fraud desks | Number report with evidence. Verify official channel per telco | Draft only |
| CSA | Incident report. Verify channel | Draft only |
| NCA | Spoofing or SIM abuse complaint. Verify channel | Draft only |

Each channel needs a template, an evidence checklist, a submission method (API, form or email) and an expected-response note. Collect these as data, not code, so channels can be added without changes.

### 6.6 Data model sketch (suggestion)

`Brand` -> `OfficialAsset` (domain, handle, number, wallet, logo). `Threat` (type, surface, source URL, first seen, confidence, reason codes) -> belongs to a `Campaign`. `Identifier` (domain, handle, number, wallet) shared across threats. `Evidence` (screenshot, page hash, WHOIS, DNS, capture time, capture tool version). `Case` -> many `Notice` -> many `Filing` (channel, drafted by, approved by, filed at, response, removed at). `ClockEvent`. `AuditLog` (every state change, who, when).

### 6.7 Evidence record (shape)

```json
{
  "record_id": "haul16-20261030101500-ab12cd34",
  "engine": "haul16-proto-0.1",
  "rules_version": "2026-10-30.1",
  "checked_at": "2026-10-30T10:15:00Z",
  "brand": { "id": "brand_demo", "authorisation_letter_sha256": "..." },
  "threat": {
    "surface": "domain",
    "value": "example-clone.example",
    "first_seen": "2026-10-30T09:40:00Z",
    "classification": "suspected_clone",
    "confidence": "medium",
    "reason_codes": ["logo_match", "brand_name_in_domain", "payment_to_personal_number"]
  },
  "evidence": {
    "page_sha256": "...",
    "screenshot_ref": "evidence/...png",
    "whois_snapshot_ref": "...",
    "dns_snapshot_ref": "...",
    "capture_tool": "sandboxed-browser/0.1"
  },
  "identifiers": { "phones": ["0240000101"], "wallets": [], "domains": [] },
  "campaign_id": "camp_007",
  "notices": [
    { "channel": "registrar", "status": "drafted", "approved_by": null },
    { "channel": "google_safe_browsing", "status": "drafted", "approved_by": null }
  ],
  "limitations": [
    "Dry run. Nothing was filed.",
    "Domain age and page content require the server-side scan."
  ],
  "human_review_required": true
}
```

---

## 7. The business

**Customers, in order:**
1. **VASPs and fintechs.** 20 SEC sandbox participants `[VERIFIED]` and over 100 registered VASPs operating in and from Ghana `[REPORTED, July 2025 registration exercise]`.
2. Banks, telcos, e-money issuers and PSPs (brand abuse and impersonation of their own names).
3. Utilities and other exposed brands (ECG and GoldBod have already had to issue public alerts).
4. Other countries (Nigeria, Kenya) by adding country adapters for local channels.

**Why a VASP would pay** (hypotheses to test in interviews):
- Advertising limits make public rebuttal harder.
- Bank partners and the regulator want proof of consumer protection.
- Sandbox firms reportedly carry KPIs and risk disclosures `[REPORTED, verify]`, and a monthly takedown report fits.
- A cloned brand erodes trust and drives support cost.

**Pricing `[ASSUMPTION]`, no Ghana price evidence:**

| Tier | About | Includes |
|---|---|---|
| Starter | $250/month | Watch, 10 cases a month |
| Growth | $900/month | Unlimited watch, 40 cases, monthly regulator report |
| Enterprise | $2,500+/month | Multi-brand, API, dedicated analyst |
| Overage | about $40/case | Beyond the quota |

**Illustrative sizing `[ASSUMPTION]`, not data:** 30 customers at about $700/month is roughly $250k a year. 100 customers is about $840k a year. This is a beachhead, not a venture-scale number on its own. Expansion to more countries and to banks and telcos is the venture story.

**Cost structure `[ASSUMPTION]`:** analyst minutes per case (AI reduces this), model API, sandboxed browser infrastructure, evidence storage, legal counsel, Data Protection Commission registration, and relationship-building with platforms.

**Honest weaknesses:**
- No willingness-to-pay evidence. Sign design partners and charge from day one.
- Ghanaian brands currently answer with public warnings, not purchased takedowns `[VERIFIED behaviour, unknown spend]`.
- Takedown speed depends on relationships we do not have yet.
- Competitors can add Ghana. The defence is local channels, a measured takedown clock and signed authorisation letters.

---

## 8. Hackathon scope

### 8.1 The demo story (about four minutes)

All on a fictional brand. Everything labelled as dry run.

1. **Onboard the brand:** official domains, handles, numbers, logo, authorisation letter.
2. **Detection feed:** clones appear. Lookalike-domain generation and matching run for real. Page capture and registry lookups need a server, so the demo simulates them and says so.
3. **Open a case:** AI triage, payment identifiers extracted, connected threats linked into one campaign, evidence pack assembled.
4. **Review the drafted notices** for five channels, approve them, and watch the **takedown clock** start.
5. **Export the monthly regulator report.**
6. **Close with the number we will measure**, not claim: median time to removal.

### 8.2 What is real and what is simulated

| Real | Simulated, and labelled |
|---|---|
| Lookalike-domain generation and matching against official assets | Registrar, host and platform responses |
| Evidence pack structure, hashing and export | Page capture at scale, WHOIS and DNS lookups (unless a server is available) |
| AI triage and extraction (with structured output and human review) | Sending any notice |
| Notice drafting from templates | Brand and clone data (fictional) |
| Takedown clock and report | WhatsApp line (stretch) |

### 8.3 Architecture suggestion `[ASSUMPTION]`, team can change it

- **Backend:** Python and FastAPI. SQLite or Postgres.
- **Capture:** sandboxed headless browser (Playwright), view-only, no form submission, isolated network.
- **AI:** an LLM API for structured extraction and notice phrasing only. Rules decide.
- **Front end:** a web app with a case view as the centrepiece.
- **Local-first option:** the earlier prototype ran entirely in the browser. That pattern is worth keeping for any screening module so customer data never leaves the institution.

### 8.4 Reusable assets from the earlier prototype

A working prototype ("Check Before You Send") is published at https://claude.ai/artifact/VFPbqaGjdi7JPHRGLB56np. It contains reusable pieces:

- Name normalisation and fuzzy matching with exact, phrase and similarity tiers, and name-collision cautions.
- A social-engineering signal detector (guaranteed returns, false authority, credential requests, payment to personal numbers, urgency, task and mining lures).
- An evidence record with a data-snapshot hash.
- Batch screening with payee-concentration detection and a staff-override indicator.
- A test harness reporting counts, not percentages.
- Verified Ghana datasets (appendix A).

It is a separate product direction (counterparty screening for payment providers). HAUL16 can reuse its matching and evidence-record code and optionally offer the screening as a module for the same buyers.

### 8.5 Out of scope

A public registry, a consumer scam checker, real filings, on-chain analytics, deepfake detection, a "verified" badge or directory of VASPs (it touches advocacy rules), anything that moves money.

### 8.6 Build order (proportions of the available time)

- 10%: lock the data model, channel catalogue and fictional brand.
- 25%: detection (lookalike generation, matching, reason codes) and triage.
- 25%: case view, evidence pack and notice drafts.
- 15%: takedown clock and the monthly report.
- 15%: demo polish, seeded data, rehearsal.
- 10%: buffer.

### 8.7 Pass and fail

- **Pass:** a full dry-run case from detection to report in under four minutes. Every drafted claim traces to captured evidence. A human-approval step exists and is enforced. The demo states clearly what is simulated.
- **Fail signals:** a notice contains a claim not backed by evidence. Anything could be sent to a real recipient. A real company is named as a victim or impersonator without permission.

---

## 9. Validation plan

**Interviews (aim for 8, in parallel with the build):**
- 3 compliance, risk or brand leads at sandbox VASPs and fintechs (Vaulta is an obvious first conversation).
- 1 telco fraud lead, 1 bank fraud officer, 1 CSA or NCA contact, 2 brand owners with a visible impersonation problem.

**Questions to ask:**
- Has your brand been cloned? On which surfaces? How did you find out?
- What did you do, how long did it take, what did it cost, who owns it?
- Do you spend on takedown or brand protection today, with whom?
- Would you sign an authorisation letter and pay for evidence packs and a monthly report?
- What would make you flinch on price?

**Takedown baseline experiment (cheap, and nobody else will have this):** with a friendly brand's permission, file real reports through the official channels and log time to first response and time to removal per channel. This produces the honest speed number.

**Pass bar:** at least two design partners agree to sign an authorisation letter and pilot. If none can describe a current process or budget, the pain is weak. Pivot to selling the monthly report and evidence file first.

---

## 10. Risks and legal checkpoints

| Risk | Why | Mitigation |
|---|---|---|
| Wrongful takedown | Act 772: intermediaries are liable for wrongful takedown and a knowingly false notice creates liability | Human approval on every notice, evidence-backed claims, fixed templates, lawyer review |
| Defamation | Naming a business or person as a clone | State only what evidence supports. "Suspected" until confirmed. Legitimate-reseller check first |
| Data protection (Act 843) | Screenshots, numbers and handles are personal data | Register with the Data Protection Commission, run a DPIA, minimise, set retention |
| CSA accreditation | May apply to security service providers | Ask a lawyer |
| Platform terms | Crawling social platforms breaks their terms | Stick to public sources and official reporting channels |
| Speed claims | Vendors have years of relationships | Do not claim vendor speeds. Report our measured median |
| `.gh` instability | Registry is hacked and disputed | Do not depend on it |
| Prompt injection | The scanner reads attacker-controlled pages | Isolated, tool-less agent, strict schema, reproducible records |
| False campaign links | Clustering can link innocents | Confidence levels, strong-evidence links only, human review |
| Advertising rules | VASP promotion is restricted | HAUL16 is B2B, promotes no VASP, offers no "verified" badge |
| SEC directive | Exempt categories reportedly cover back-office tools | Get written confirmation that a brand protection tool for VASPs is exempt |

---

## 11. Pitch skeleton (60 to 120 seconds)

**Story (a fictional brand, say so):** "It is Sunday evening. A customer of a licensed exchange sends GH¢500 to a number on a page that looks exactly like the exchange. The real exchange finds out on Monday, from the customer. It posts a warning on X, emails three platforms, and waits. Nobody can tell the regulator how long that clone stayed up."

**Problem:** a cloned brand is a fraud channel, and the owner has no way to find it fast, prove it, remove it, or show how long it took.

**Solution:** HAUL16 watches for clones, builds the evidence, files the takedowns across every channel with a human approving each one, and starts a clock. The brand gets a monthly report to hand to its regulator or bank.

**Why now:** the SEC sandbox now has 20 VASPs, advertising limits make public rebuttal harder, and scam losses are rising (CSA: 352 online investment fraud cases and GH¢3.43m lost in H1 2026).

**Business:** subscription plus per-case, paid by the brand. Ghana is the beachhead, with local channels as the edge.

**Ask:** two design partners who will sign an authorisation letter, and a conversation with the CSA about trusted escalation.

---

## 12. Open questions

For the team:
1. What is the build window, and what is the team mix?
2. Who can get us in front of a VASP compliance or brand lead this week? Vaulta?
3. Can we get a brand to authorise real test filings for the baseline experiment?
4. Who is our Ghanaian legal contact for Act 772, defamation, data protection and CSA accreditation?

For Claude Code to explore:
5. Which channels can we realistically integrate for real versus draft only?
6. What is the best lookalike-domain generation approach we can run without a server?
7. How do we represent and score campaign links so we never smear an innocent party?
8. What is the minimum evidence per channel to make a notice credible?
9. How do we structure the channel catalogue as data so channels can be added without code?
10. What is the cleanest way to show the takedown clock honestly when filings are simulated?

---

## Appendix A: Verified datasets for seeding

**SEC notice of 22 July 2026, 23 entities named as not licensed for capital market activities** `[REPORTED via press of the notice; original list on sec.gov.gh]`. Scope: the SEC states it has not licensed these entities under the Securities Industry Act, 2016 (Act 929). The notice speaks only to capital market activity. It is narrower than "scam".

Afri Hub; BG Wealth; BP Investment; Creative Walker Promotion Company (CWPC); Dallmayr; Expert (Expect) Option; GAIP Securities Learning & Exchange Group; Ghana Vest; Harvest Fund; Infarms/Secure Farm; Kukafor Platform; Mazzuma; Medisyne Trade; NIO Platform; Profit Rise Invest; Quant Vest Stock Exchange (QVSE); QVES; QX Broker/Qumatix; Smart Gain; Yepbit Trading; ZEC ZEC FX; Bonchat; Ultima Cryptocurrency Group.

**Collision cautions (important):** Dallmayr, Mazzuma, KUKA (a CSA alias), Smart Gain, Harvest Fund, Ghana Vest, Afri Hub, BP Investment and NIO Platform are generic or may be shared with unrelated businesses. A name match alone is a lead, not proof. Confirm with a second identifier.

**CSA aliases (alert of 6 July 2026) `[REPORTED]`:** Daily Trade, Ghostore, KUKA, Edollar. The SEC list has a "Kukafor Platform". Name similarity only. No relationship is confirmed. **Darazz** is a genuine company whose identity the CSA says is being misused. It must never be flagged as a scheme.

**SEC sandbox participants and approved pilot activities (SEC list of August 2026, entries 5 to 20 as seen)** `[VERIFIED, https://sec.gov.gh/?p=32564]`:

| # | Participant | Pilot activity |
|---|---|---|
| 5 | WeWire Ghana Limited | Asset Tokenization (Trade Finance) |
| 6 | Mansu Technologies Ltd | Virtual Asset Brokerage |
| 7 | Sage Advance Global Services Ltd | Virtual Asset Brokerage |
| 8 | Mojo Pay Ghana Limited | Virtual Asset Brokerage |
| 9 | Yellow Card Ghana Limited | Virtual Asset Exchange |
| 10 | Hyro Exchange Ltd | Virtual Asset Exchange |
| 11 | WhiteBit Ghana Ltd | Virtual Asset Exchange |
| 12 | KoinKoin Ghana Ltd | Virtual Asset Exchange |
| 13 | Ghana Commodities Exchange | Virtual Asset Exchange (Commodities) |
| 14 | Ghana Gold Board | Real World Asset Custodian (Gold) |
| 15 | XChain Technologies Ltd | Virtual Asset Trading Platform |
| 16 | HanyPay Ghana Ltd | Virtual Asset Trading Platform |
| 17 | The Blue Penguin Company Limited | Virtual Asset Trading Platform |
| 18 | HSB Global Fintech Ltd | Virtual Asset Trading Platform |
| 19 | Mamoru Digital Ghana Ltd | Virtual Asset Trading Platform |
| 20 | BSystems Ltd | Virtual Asset Trading Platform |

Entries 1 to 4 were not captured. Secondary reports name Africoin, Vaulta, GFX Brokers and One Africa among the 20 `[REPORTED]`. Fetch the SEC page to complete the list. Use these names only as test data in a way that does not imply wrongdoing, and do not approach them as customers without a proper introduction.

**Caution on HanyPay:** a name "HanyPay" also appeared in a 2025 Bank of Ghana notice. Whether it is the same entity as the sandbox participant is `[UNVERIFIED]`. Do not use it as a demo case.

---

## Appendix B: Corrections log and caveats

Things said earlier in the project that were wrong or superseded. Do not repeat them.

| Earlier claim | Correction |
|---|---|
| "Money moves from a WhatsApp forward to a MoMo transfer in seconds" as a general Ghana description | Overstated. Supported only as a pattern in reported investment scams (CSA) |
| 25 named unlicensed entities | 23. YEPBIT and BonChat are already in the 23 |
| About 5% fraud recovery in 2025 | Banks and SDIs only, not PSPs |
| Wave and Paystack are "very hard" on impersonation | We found no public evidence of their processes. Wave was criticised for a thin public response in Sept 2026 |
| Sandbox size 11 | 20 since 19 August 2026 |
| A source link in the earlier prototype pointed to an article we could not verify | Replaced with the SEC's own sandbox page. If you see other source links in that prototype, verify them |
| `.gh` as a takedown lever | Registry is compromised and disputed. Not a dependency |

**General caveats:**
- Research was done by web search over a short period. Press-reported figures can differ from regulator originals. Check the regulator source before putting a number on a slide.
- Several third-party guides list conflicting telco and CSA hotline numbers. Use official pages only.
- Pricing, sizing, willingness to pay, and the claim that Ghanaian brands will pay for takedowns are all assumptions.
- Vendor speed and market claims are marketing.

---

## Appendix C: Sources

**Ghana data and regulation**
- Bank of Ghana, 2025 Fraud Report: https://www.bog.gov.gh/wp-content/uploads/2026/07/FRAUD-REPORT-2025.pdf
- CSA H1 2026 investment fraud alert: https://www.citinewsroom.com/2026/07/online-investment-scams-cost-ghanaians-gh%c2%a23-4m-in-six-months-csa/ and https://www.graphic.com.gh/news/general-news/scammers-target-mobile-money-users---csa-warns-of-sharp-rise-in-fraudulent-investment-schemes.html
- SEC 23 unlicensed entities: https://www.graphic.com.gh/business/business-news/beware-of-these-23-unlicensed-investment-platforms-sec-warns-public.html and https://www.modernghana.com/news/1512936/sec-blacklists-23-unlicensed-online-investment.html
- SEC notice on YEPBIT Exchange and BonChat: https://sec.gov.gh/public-notice-warning-against-yepbit-exchange-and-bonchat-platforms-suspected-fraudulent-investment-schemes/
- SEC sandbox participant lists: https://sec.gov.gh/?p=32564 (August, 20 participants) and https://sec.gov.gh/?p=32481 (March, 11) and https://www.mariblock.com/stories/ghanas-sec-expands-crypto-sandbox-to-20-participants
- SEC Directive Dir/001/06/2026: https://sec.gov.gh/directive-to-market-operators-fintech-service-providers-and-persons-owning-and-or-operating-online-investment-and-trading-platforms/
- BoG directive of 12 June 2026: https://www.myjoyonline.com/bank-of-ghana-orders-financial-institutions-to-stop-supporting-foreign-currency-crypto-wallets/
- BoG and SEC deepfake warning (Daily Wealth Guide): https://www.graphic.com.gh/news/general-news/bog-sec-warn-against-fake-mahama-video-promoting-crypto-scam.html
- GoldBod fake LinkedIn page and other September alerts: https://ghanafact.com/scam-alerts-fake-press-release-how-bog-ecg-gra-others-fought-digital-misinformation-in-september/
- Deputy Minister on collaboration: https://www.citinewsroom.com/2026/09/sukparu-urges-banks-fintechs-telcos-to-unite-against-cybercrime/
- Electronic Transactions Act 2008 (Act 772): https://nita.gov.gh/wp-content/uploads/2017/12/Electronic-Transactions-Act-772.pdf
- Data Protection Commission registration: https://dataprotection.org.gh/registration/
- MTN Ghana fraud reporting: https://mtn4186.zendesk.com/hc/en-us/articles/10592692795026-How-do-I-report-Mobile-Money-fraud
- AVAS 2026: https://www.myjoyonline.com/accra-to-convene-africas-regulators-and-markets-on-tokenization-at-avas-2026/

**.gh registry**
- Google-disclosed compromise: https://cybersecuritynews.com/hackers-hijack-domain-registry/
- What site owners should check: https://www.techcityng.com/gh-domain-hijack-what-to-check/
- Ownership dispute background: https://icannwiki.org/.gh and https://techlabari.com/after-ghanas-gh-domain-hack-the-spotlight-falls-back-on-a-tense-fight-between-father-of-the-internet-in-africa-and-the-state/

**Takedown mechanics and vendors (vendor sources, read for mechanics)**
- Doppel guide: https://www.doppel.com/blog/phishing-site-takedown
- Bolster comparisons: https://bolster.ai/blog/brand-monitoring-tools and https://bolster.ai/blog/domain-takedown-services-for-dmca-phishing
- Platform comparison: https://phisheye.com/blog/best-brand-protection-platforms

**Ghanaian registries**
- TrustGH: https://www.trustgh.com/ and https://starrfm.com.gh/trustgh-fake-restaurant-profiles-scam-numbers/
- Transparent Turtle: https://www.transparentturtle.com/
- TrustCheck: https://www.trustcheckapp.com/

**Wave (context)**
- https://afriqueitnews.com/editorial/arnaques-au-mobile-money-les-utilisateurs-attendent-la-voix-de-wave/
- https://www.aip.ci/cote-divoire-aip-un-reseau-de-faux-agents-du-service-client-wave-demantele-par-la-bri-de-san-pedro-police/

**Earlier prototype (separate product direction, reusable code and data)**
- https://claude.ai/artifact/VFPbqaGjdi7JPHRGLB56np
