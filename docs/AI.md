# How AI is used in Sjekka

AI shows up in two places: **in the product**, where Google Gemini reads receipt photos, and **in development**, where Claude Code (Anthropic) has written and changed much of the code since October 2026. This document records both, so it's clear what the AI did, what a person decided, and how the AI's work is checked.

Keep it up to date: add a line to the log below for each AI-assisted change.

## In the product: Gemini reads the receipts

Every uploaded photo is sent to Gemini (`GEMINI_MODEL`, default `gemini-2.5-flash`), which returns the store, date and each line item as structured data. This is the only AI in the running app.

The model is treated as an unreliable component, so its output is constrained and checked:

- **Fixed output shape.** Gemini must answer against a Pydantic response schema (`app/schema.py`), so categories and units can only be values from fixed lists. The answer is validated again before anything is saved; an invalid answer fails the job instead of storing bad data.
- **No arithmetic by the model.** Gemini reads prices off the receipt, but the price per 100 g/ml or per item is computed in Python (`worker.unit_price`).
- **Normalised names.** `KJØTTDEIG ` and `kjøttdeig` are stored as `Kjøttdeig` so the same product groups together.
- **Duplicates.** A receipt with the same store, date and lines as one already saved is not stored twice.
- **Failures.** Rate limits and outages are retried with backoff; anything else fails the job with the error saved, rather than retrying forever.
- **Cost and abuse.** Every upload costs a Gemini call, so uploads are rate-limited per client and per day.
- **Privacy.** Photos are sent to Google's Gemini API to be read, and the upload page says so. The server never serves them and deletes each photo once it has been read. Gemini's raw answer is kept, so the data can be reprocessed later without the photo.
- **Tests never call Gemini.** It's replaced with fakes, so tests are free, fast and deterministic. Extraction quality itself still has to be checked with real receipts.

Known limits: Gemini can misread blurry or cut-off receipts, and similar products with different names ("Kjøttdeig" vs "Kjøttdeig storfe") aren't merged yet.

## In development: Claude Code

### Phases

| When | Work | AI involvement |
|---|---|---|
| 27–28 Apr 2026 | Original app: upload, database, Gemini extraction, job queue, explore page | Scaffolded by Haakon with help from AI. Not marked in the commits. |
| 15 Oct 2026 | Stopped tracking `.env`, moved the sample database | Not recorded. **TODO (Haakon).** |
| 18 Oct 2026 | CI/CD pipeline, Docker image, VPS deployment (PR #1) | Claude Code (Claude Opus 5.5) |
| 23 Oct 2026 | Everything in PRs #2–#11 (log below) | Claude Code (Claude Opus 5.5) |
| 24 Oct 2026 | PRs #22–#53: shopping list, flyer offers, UI rework, two rounds of customer walkthroughs, promo films | Claude Code (Claude Opus 5.5), directed by Haakon in short prompts (see below) |

Commits Claude worked on carry a `Co-Authored-By: Claude …` trailer, and the PR descriptions say they were generated with Claude Code, so `git log` shows which changes were AI-assisted.

### What directing it looked like

Most of Sjekka from PR #22 on was built by describing outcomes, not code. Haakon wrote a sentence or two; Claude Code read the code and the live site, did the work, wrote the tests, opened the PRs, and reported back. The prompts below are Haakon's own words (typos tidied), next to what came out of each.

| Haakon wrote | What it became |
|---|---|
| "Grocery list that auto checks what store is the cheapest" | The shopping list (#24): what the whole list costs in every chain, missing prices estimated from a per-chain price index, and a two-store split when it's worth the extra stop. Built overnight, unattended. |
| "Price aggregate tool that pulls price info straight from kundeavis", then: "I do want you to use the tilbudsavis API in prod" | Flyer offers from eTilbudsavis, imported every six hours, each one judged against what receipts say it cost before the sale (#26). |
| "Rework the UI, make it look supernice, not too crowded. Flat prices (x,00) don't need decimals. Long info about a product should be collapsed by default to reduce cognitive load" | A calmer interface across every page: short verdict tags, details folded away, "15 kr" instead of "15,00 kr", and a one-column list on phones (#28). |
| "When you fix this, add a test so it cannot happen again" | The offers page had gone blank after a deploy. The cause was Cloudflare caching old JavaScript next to new pages. Every script and stylesheet is now addressed by a hash of its contents, and a test fails if any page links one without it (#29). |
| "Investigate what might have led the pizza to be registered this expensive. Was it a special brand of Grandiosa, i.e. a family pack?" | The pizza was right: a 1 kg Grandiosa. But the same receipt had a real misread (a 4-pack of yoghurt stored as one cup, four times too dear per 100 g). Fixed for receipts and flyers, with old rows repaired (#30, #31, #40). |
| "Think about what implications changing this logic might have. Then fix both issues in separate PRs" | Before any code, the trade-off was written out: storing a 4-pack as "4 for 20 kr" would have suggested you can buy one cup, so a multipack became one package of the full size instead. |
| "Go through a couple of user flows as a user. Screenshot the journey, analyze the images", then: "Do them all! For the smaller changes, bundle them in one PR" | 22 screenshots, 17 findings, and seven PRs merged the same afternoon (#38–#44): loose produce priced per kilo, offers in the list suggestions, honest rounding when most prices are estimates, and a bundle of smaller fixes. |
| "Do another pass as a customer, look for improvements and root causes for unwanted behavior" | Root causes rather than symptoms: Tjek's tile shop *Megaflis* matched the pattern for *Coop Mega*, and chains publish overlapping weekend flyers. Six fixes, merged in one go (#48–#53). |
| "They don't tell a story. Frame it as a story: the large chains want you to spend as much as possible on groceries. We are here to declare war on the big chains" | The promo films on the about page: an English voice-over, promo signs piling up in a zigzag, and the reveal of Sjekka as the answer to one question (#46). |

The judgement calls stayed with Haakon, and several changed the outcome:

- **Asking for a checkpoint** before four PRs were merged at once (the `checkpoint-2026-10-24` tag), so any of it could be rolled back in one step.
- **Questioning the data.** The 169 kr pizza looked wrong; checking it turned up the yoghurt bug next to it.
- **Asking for root causes, not symptoms.** The first fix for hardware in the grocery offers asked Gemini to filter it out. Asking why it was there at all found the Megaflis mix-up in the chain matching.
- **Product taste.** Oda's logo in its orange ("it's their colour"), the logos without white badges, centred in the ranking, and the story and music for the films.
- **Knowing when to stop.** When the list suggested "Kokosmelk, Sjokomelk, Litago melk" for "Melk", Claude offered to add a list of base products. Haakon: "Those are still melk, so I think that's OK." No extra code.

### How the work was split

**Haakon decided:** what to work on and in which order, the visual direction (match tischbein.dev, no emoji, no gradients), keeping Cloudflare's bot protection even though it makes a CI check fail, and when each PR was merged and deployed.

**Haakon did by hand:** anything needing server root or dashboard access: running the VPS setup script with sudo, changing the SSH `AllowUsers` setting, adding Cloudflare tunnel routes, and the Cloudflare rate-limit rule.

**Claude did:** read the code and the server state, proposed and ranked improvements, wrote the code, tests and docs, opened the PRs, watched CI and deploys, and diagnosed failures (for example the SSH `AllowUsers` block and the Cloudflare 403 in CI).

### Guardrails on the AI's work

- **Every change goes through a PR and CI**: lint, shellcheck, 349 tests including browser and accessibility tests, a 95% coverage floor, and a smoke test of the Docker image.
- **Merging and deploying needed an explicit go-ahead.** Claude Code's permission checks blocked merges and production deploys until Haakon said yes, even when Claude had asked earlier.
- **Tests were checked against deliberate bugs.** Claude broke the code on purpose (disabled the deploy rollback, loosened input checks, broke the unit-price maths, reintroduced an HTML-injection bug) to confirm the tests catch it.
- **Deploys roll back automatically** if the new container isn't healthy.
- **`CLAUDE.md`** holds the project rules for future AI sessions (no Gemini in tests, migrations for schema changes, Norwegian UI text, the design).

### Mistakes, and how they were caught

The AI's work wasn't right the first time every time. Recorded so the process can be judged honestly:

- Tests passed locally but failed in CI, because they were run in a way that hid an import-path problem. CI caught it; fixed with `pytest.ini`.
- A coverage data file was committed by accident; spotted in the commit summary and removed.
- Claude first said changing `.env` only needs a container restart. It needs `docker compose up -d`; corrected before it was used.
- Claude took "let's leave it" to mean leaving a failing CI check as it was, when Haakon wanted it fixed later; resolved when Haakon asked.
- shellcheck flagged a notice in the setup script when it was first added to CI; fixed.
- Claude committed the product page directly on `main` and pushed it, skipping the branch and PR. It noticed at once and cancelled the pipeline before the deploy step, so production never got the unreviewed change; the commit was then reviewed as part of PR #15.
- Turning on the flyer import in production ran the 512 MB container out of memory: listing offers loaded every flyer's raw answer. Found from the public site's 502s and fixed the same day by loading that column only when asked for (#27).
- A deploy left the offers page blank for visitors, because Cloudflare served old JavaScript next to new pages. Haakon reported it; the fix and a regression test followed (#29).
- The first promo films had no voice and synthesised music, and Haakon found them flat ("they don't tell a story") and the music "too haunting". Claude can't hear audio, so it had judged the music only by measurements. They were remade with a voice-over and a real royalty-free track (#46, #47).
- When several PRs were merged in parallel, two of them conflicted in the same test file and the merge stopped without saying so. It was caught by checking the PRs' state, and they were rebased and merged one at a time.

## Change log

| PR | Change | Notes |
|---|---|---|
| #1 | CI/CD pipeline and VPS deployment | |
| #2 | Log the HTTP status in the public URL check | Showed the CI failure was Cloudflare's bot challenge |
| #3 | Tolerate the Cloudflare challenge; update all dependencies to latest, Python 3.15 | |
| #4 | Visual overhaul to match tischbein.dev | Direction from Haakon; found and fixed an HTML-injection risk |
| #5 | Upload limits, HEIC support, photo rotation, nightly backups, first tests | |
| #6 | Gemini response schema, one retry layer, duplicate detection | |
| #7 | Full test suite: unit, integration, deploy scripts, browser | |
| #8 | Replace deprecated APIs | Test warnings 197 → 1 |
| #9 | `CLAUDE.md` | |
| #10 | Remove redundant code | Each removal checked for references first |
| #11 | Private receipt photos, sample database cleanup, this document | |
| #12 | Showcase video and the scripts that record it | Gemini simulated in the recording; stated on the end card |
| #13 | AI voice-over for the video (Kokoro text-to-speech) | Script written by Claude; pronunciation checked by transcribing the track with Whisper |
| #14 | Privacy notice, photos deleted after reading, raw Gemini answers kept | Haakon caught that the video called the photos private while they're sent to Google |
| #15 | Product page with per-store prices and history; chain logos on cards; CI uses the runner's Chrome | Logo idea from Haakon; chart colours validated, then a logo-per-row design instead of colour per store. CI: 7–10 min → ~1.5 min |
| #16 | Thank-you page follows the upload live; export of raw Gemini answers | |
| #22 | Privacy page; failed-job photos deleted after 30 days | Haakon asked whether the project was legal; Claude flagged the gaps and wrote the page from what the code does. Not legal advice |
| #23 | Libron for all text, on the site and the portfolio | Haakon's call; merged by Claude on his say-so |
| #24 | Shopping list: what the whole list costs in each chain, gaps estimated from a per-chain price index, two-store split | Built unattended overnight from Haakon's brief ("grocery list that auto checks what store is the cheapest"); reviewed and merged by Haakon |
| #25 | Norwegian that reads like Norwegian, not translated English | Haakon spotted the stiff phrasing; Claude did the pass |
| #26 | Flyer offers: eTilbudsavis imported every 6 hours, "is it really an offer?" verdicts from receipts, offers in the shopping list | Built unattended from Haakon's second brief ("price aggregate tool that pulls price info straight from kundeavis"); he asked for real flyers mid-way, so the eTilbudsavis API was found and tried locally. Haakon chose to run it in production |
| #27 | Fix: listing offers ran the container out of memory | Flyers' raw answers loaded only when asked for |
| #28 | Calmer UI: short verdicts, folded details, whole prices without decimals; English demo videos | From Haakon's brief, quoted above |
| #29 | Scripts and styles addressed by content hash | Haakon: "add a test so it cannot happen again" |
| #30 | Package size on product cards | Prompted by the 169 kr pizza, which was a 1 kg pizza |
| #31 | Multipacks (4x150g) priced for the whole pack, old rows repaired | Found next to the pizza on the same receipt |
| #32 | Tests for every new front-end branch | Haakon asked for a coverage review |
| #33–#37 | Logos for Bunnpris, Obs and Oda; no white badges; centred in the ranking | Haakon's design calls |
| #38 | Gemini flags offers that aren't groceries | Later traced to its root cause in #48 |
| #39 | Small fixes from a walk through the site, bundled | 11 changes in one PR, as Haakon asked |
| #40 | Flyer multipacks told apart from multi-buys | |
| #41 | Offers ordered by what's worth a look, one per product first | |
| #42 | Loose produce priced per kilo | |
| #43 | Offer products suggested on the shopping list | |
| #44 | Totals that are mostly estimates are rounded, and near ties aren't called a win | |
| #45 | About page with two promo films | |
| #46 | Promo films as a story, with an English voice-over | Story from Haakon, quoted above |
| #47 | Royalty-free music ("Motivating Mornings", Mixkit) | Haakon: "get some royalty free music" |
| #48 | Megaflis (a tile shop) no longer filed as Coop Mega | Root cause of the hardware offers |
| #49 | An offer repeated in overlapping flyers listed once | 302 duplicates removed |
| #50 | Front-page search points to matching offers | |
| #51 | Offer-only products can go on the list from their page | |
| #52 | Known names suggested for list items without prices | |
| #53 | The offers page asks for receipts where offers can't be checked | |
