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

Commits Claude worked on carry a `Co-Authored-By: Claude …` trailer, and the PR descriptions say they were generated with Claude Code, so `git log` shows which changes were AI-assisted.

### How the work was split

**Haakon decided:** what to work on and in which order, the visual direction (match tischbein.dev, no emoji, no gradients), keeping Cloudflare's bot protection even though it makes a CI check fail, and when each PR was merged and deployed.

**Haakon did by hand:** anything needing server root or dashboard access: running the VPS setup script with sudo, changing the SSH `AllowUsers` setting, adding Cloudflare tunnel routes, and the Cloudflare rate-limit rule.

**Claude did:** read the code and the server state, proposed and ranked improvements, wrote the code, tests and docs, opened the PRs, watched CI and deploys, and diagnosed failures (for example the SSH `AllowUsers` block and the Cloudflare 403 in CI).

### Guardrails on the AI's work

- **Every change goes through a PR and CI**: lint, shellcheck, 110 tests including browser tests, a 95% coverage floor, and a smoke test of the Docker image.
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
