# Sjekka

Crowdsourced Norwegian grocery prices: people upload receipt photos, Gemini reads the lines, flyer offers come in from eTilbudsavis, and the site shows where a shopping list is cheapest. Live at https://sjekka.tischbein.dev. The README covers features, settings, deployment and backups; `docs/AI.md` how AI is used.

## Layout

- `app/main.py`: FastAPI app. `POST /upload` (validation, rate limits, EXIF rotation, HEIC), `/products`, `/products/{name}`, `/jobs/{id}` (followed by the thank-you page), `POST /basket`, `/offers`, `/stats`, `/health`, and the static site. Pages are served through `Pages`, which links each local script and stylesheet as `/name.js?v=<content hash>`.
- `app/worker.py`: background loop in the same process. Takes pending jobs, calls Gemini, saves receipts. Retries 408/429/5xx with backoff, detects duplicate receipts, computes `price_per_unit`, normalises names.
- `app/basket.py`: prices a shopping list per chain for `POST /basket` (pure functions, unit-tested). Groups chain names by pattern; keep `CHAINS` in step with `static/chains.js`.
- `app/flyers.py`: flyer offers (kundeavis). Import from Gemini-read files (`util/import_flyer.py`), the verdict on whether an offer beats the chain's usual price, `GET /offers`. `app/tjek.py`: import from the eTilbudsavis API (`util/import_tjek.py`); prices and sizes from Tjek, names from Gemini or matched to known names (`--no-gemini`); `run_refresh` imports new flyers every `FLYER_REFRESH_HOURS` from the app's lifespan. Offers live in `flyers`/`flyer_offers`, apart from receipts, and never feed the chain price index.
- `app/extractor.py`: the prompts and the Gemini calls; `DEFINITIONS` is shared by the receipt and flyer prompts so names match. `app/schema.py`: Pydantic response schema (allowed categories and units).
- `app/models.py`: SQLAlchemy models. `app/config.py`: all env settings.
- `static/`: plain HTML/CSS/JS, one shared `style.css`. No build step. `list.js` holds the shopping list in `localStorage` (guarded; falls back to memory) and fills the header count on every page.
- `deploy/`: `deploy.sh` (CI's forced SSH command), `backup.sh` + systemd timer, `setup-vps.sh`.
- `tests/`: see the table in the README.

## Commands

```bash
pip install -r requirements-dev.txt
ruff check . && shellcheck deploy/*.sh docs/video/*.sh
CHROMIUM_PATH=/usr/bin/chromium pytest --cov=app   # or `playwright install chromium` once
DATA_DIR=/tmp/sjekka RUN_WORKER=0 uvicorn app.main:app   # site without calling Gemini
```

CI (`.github/workflows/pipeline.yml`) runs exactly this, requires the browser tests (`E2E_REQUIRED=1`) and fails below 95% coverage.

## Rules

- **Tests never call Gemini.** Fake `worker.extract_receipt` or `extractor.client.models.generate_content`. Browser tests run the app with `RUN_WORKER=0`.
- **Every change gets tests.** Prefer checking that a new test fails when the code is broken on purpose.
- **Schema changes need a migration.** `create_all` only creates missing tables; it never adds columns to the existing production database. New columns go in `app/migrations.py` (tested in `tests/test_migrations.py`); anything bigger needs a real migration tool.
- **Photos are personal data.** They go to Gemini and are deleted once read; keep the raw answer (`jobs.raw_response`) instead. Never serve them.
- **One app process only.** The worker runs inside it and the upload rate limit is in memory, so never add uvicorn workers or replicas.
- **Timestamps are naive UTC** via `models.utcnow()`, matching the stored data.
- **User-facing text is Norwegian (bokmål)**, including API error messages shown in the UI. Code, comments and commits are English.
- **Link scripts and styles as `/name.js` or `/name.css`** (root-relative, no query) so `Pages` can add the content hash; otherwise Cloudflare can serve an old file next to a new page. A test checks every page.
- **Logos, fonts and films are cached for a week or more.** Give a changed file a new name (`oda-orange.svg`, `sjekka-film-*.mp4`) and update the links.
- **Chain names are matched by pattern** (`CHAINS` in `app/basket.py`, mirrored in `static/chains.js`). Anchor new patterns so other businesses don't match (Megaflis once became Coop Mega), and check them against `TJEK_BRANDS` in `tests/test_tjek.py`.
- **Design mirrors tischbein.dev:** Libron everywhere (no second font), the colour tokens in `style.css`, hairline cards, pill buttons. No gradients, no emoji, no decorative fluff. Build DOM with `textContent`, never `innerHTML` with data.

## Workflow

- Add a line for each change to the log in `docs/AI.md`, and keep its facts (test counts, guardrails) and the README's current. The README leads with plain language for anyone; technical detail goes under the developer sections.
- Branch from `main`, open a PR, wait for CI. Merge (`gh pr merge --rebase`) only when the user says so: merging to `main` deploys to production.
- After a merge, watch the run. Judge the deploy by the **Deploy** step: `deploy.sh` checks container health and rolls back. The "Check public URL" step always gets HTTP 403 from Cloudflare's bot protection (kept on purpose) and only logs a warning.
- Changes to `compose.yaml` or `deploy/` reach the server only when `setup-vps.sh` is re-run with sudo, which the user does: copy the files with `scp -r compose.yaml deploy vps:/tmp/sjekka-setup/` and give them the command from the README.
- Production: `ssh vps`, app in `/opt/sjekka` (data, `.env`, backups), container on `127.0.0.1:8000` behind the Cloudflare tunnel. Your user can't run docker or read the data there; ask the user for anything needing sudo.
- `GEMINI_MODEL` selects the model (default `gemini-2.5-flash`). Changing it in `/opt/sjekka/.env` needs `docker compose up -d`, not a restart.
