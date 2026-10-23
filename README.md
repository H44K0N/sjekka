# Sjekka

> *Har du sjekka i dag?*

People throw away receipts every day. Sjekka turns them into a price database — crowdsourced, automatic, and free.

Upload a photo of your grocery receipt. Sjekka extracts the store, items, and prices using AI, and stores them in a searchable database. Over time, patterns emerge: which store is cheapest for chicken mince, how prices vary by region, when deals appear.

---

## How it works

```
Photo uploaded in browser
        ↓
Image compressed and saved
        ↓
Job queued for AI extraction
        ↓
Gemini reads the receipt → structured JSON
        ↓
Normalized and saved to database
        ↓
Visible on the explore page
```

---

## What's built

- Receipt upload via browser (mobile-friendly, camera or gallery)
- Image compression before saving
- AI extraction via Gemini — store, date, items, prices, brands, categories
- Job queue with automatic retry on API failures
- Persistent queue — jobs survive server restarts
- Product normalization: generic names, brands stored separately
- Price-per-unit calculation (per 100g, per 100ml, per stk)
- Multi-pack and deal detection
- Explore page with category filters and search; store chains shown as logos or in their colours
- Product page: latest price per store, cheapest first, with each store's price history on shared axes
- Live counters showing total receipts and prices registered
- Docker image with CI/CD to a VPS (see [Deployment](#deployment))

---

## Known issues and things to improve

- Duplicate products still appear when Gemini uses slightly different canonical names across receipts
- Some items missing quantity/unit (receipt data incomplete or truncated)
- No deduplication logic in the database yet — same product can appear multiple times
- No store location data beyond chain name
- Member/loyalty prices not always flagged correctly
- Product taxonomy is flat — no hierarchy yet

### Security

- Receipt photos are sent to Google's Gemini API to be read, so Google's API terms apply (on the free tier Google may use them to improve its products; the paid tier says it doesn't). The upload page says so. On the server, photos are never served and are deleted once read; photos of failed jobs stay for retrying. The thank-you page shows a small copy kept in the uploader's own browser tab
- Gemini's raw answer and the model name are kept on each job (`jobs.raw_response`, `jobs.model`), so extraction and product matching can be reworked later without the photos
- `/upload` has no authentication. Every upload costs a Gemini call, so uploads are limited to 10 per client per hour and 200 per day (`UPLOADS_PER_IP_PER_HOUR`, `UPLOADS_PER_DAY`). A Cloudflare rate-limiting rule in front would stop floods before they reach the app

### Code

- The `products` and `categories` tables are defined but never used — category is stored as a string on each receipt item

---

## Planned

- Price history per product
- Basket builder — add items, get cheapest store recommendation
- Search with cascading filters (category → product → variant)
- Regional price comparison
- Deal alerts
- Confidence scoring — surface low-confidence extractions for review
- API access to the dataset

---

## Running it

```bash
cp .env.example .env        # then add your Gemini API key
pip install -r requirements-dev.txt
uvicorn app.main:app
```

The database, uploaded photos and extraction log are written to `./data/` (set `DATA_DIR` to change it).

`test_data/sjekka.db` is a small sample database: one Kiwi receipt with six items. Copy it to `./data/sjekka.db` to start with data. Without it, an empty database is created on first run.

Before pushing, run the same checks as CI:

```bash
ruff check .
shellcheck deploy/*.sh docs/video/*.sh
playwright install chromium   # once; or set CHROMIUM_PATH=/usr/bin/chromium
pytest --cov=app
```

The tests never call Gemini; it's replaced with fakes.

| File | What it covers |
|---|---|
| `test_upload.py`, `test_upload_edge_cases.py` | Upload validation, formats (incl. HEIC), EXIF rotation, resizing, rate limits |
| `test_api.py` | `/products`, `/stats`, static pages |
| `test_worker.py`, `test_worker_helpers.py` | Saving receipts, unit prices, name normalisation, duplicates, retries and backoff |
| `test_extractor.py` | The Gemini request and response validation |
| `test_pipeline.py` | Upload → queue → worker → API, the worker loop and crash recovery |
| `test_deploy_scripts.py` | `deploy.sh` (SHA check, health check, rollback) with a fake `docker`, and `backup.sh` |
| `test_browser.py` | The pages in Chromium: rendering, formatting, search, filters, upload flow, phone layout |

Browser tests are skipped if Playwright isn't installed. CI requires them and fails below 95% coverage.

---

## Deployment

Production runs at https://sjekka.tischbein.dev as a Docker container on a VPS, with cloudflared on the host routing the subdomain to `http://localhost:8000`.

`.github/workflows/pipeline.yml` runs on every pull request and every push to `main`:

1. **Lint and test**: `ruff check` and `pytest`.
2. **Build and smoke test image**: builds the Docker image, starts it and checks the main endpoints respond. On `main` the image is pushed to `ghcr.io/h44k0n/sjekka`, tagged with the commit SHA and `latest`.
3. **Deploy** (`main` only): logs in to the VPS as `sjekka-deploy` with a key that can only run `/opt/sjekka/deploy.sh`. That script starts the image for the pushed commit and restores the previous one if the container doesn't become healthy. Finally the job checks the public URL. Cloudflare's bot protection challenges GitHub's runners, so that check usually only logs a warning; `deploy.sh` has already verified the container's health.

The server is set up with `deploy/setup-vps.sh` (Docker, the `sjekka-deploy` user, `/opt/sjekka`, the restricted CI key, the nightly backup). `compose.yaml` and the files in `deploy/` are installed by that script, not by CI, so re-run it after changing any of them:

```bash
scp -r compose.yaml deploy vps:/tmp/sjekka-setup/
ssh -t vps 'sudo bash /tmp/sjekka-setup/deploy/setup-vps.sh /tmp/sjekka-setup/ci-deploy-key.pub'
```

Production data lives in `/opt/sjekka/data` and secrets in `/opt/sjekka/.env`. The pipeline never touches either.

### Backups

`sjekka-backup.timer` runs `deploy/backup.sh` every night at about 03:30. It copies the database with SQLite's online backup API, checks the copy, and keeps 14 days of gzipped copies in `/opt/sjekka/backups`. Those are on the same disk as the database, so copy them elsewhere as well. To restore:

```bash
cd /opt/sjekka && docker compose stop app
gunzip -c backups/sjekka-<date>.db.gz > data/sjekka.db && chown 10001:10001 data/sjekka.db
docker compose start app
```

---

## Stack

- **Backend:** Python / FastAPI
- **Extraction:** Google Gemini (`GEMINI_MODEL`, default `gemini-2.5-flash`) with a response schema
- **Database:** SQLite
- **Frontend:** Plain HTML / CSS / JS
- **Infrastructure:** Docker on a VPS, Cloudflare Tunnel, GitHub Actions

---

## Store logos

The chain logos in `static/chains/` come from Wikimedia Commons. Rema 1000, Kiwi, Meny, Coop Prix, Coop Mega, Joker and Spar are text logos marked public domain. The Extra logo is by Coop Norge, licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) ([source](https://commons.wikimedia.org/wiki/File:Extra_butikklogo.png)), resized. All are trademarks of their chains and are only used to show which store a price is from. Chains without a logo show as text. Matching is in `static/chains.js`.

---

## Showcase video

`docs/video/make.sh` records a narrated 1080p walkthrough of the app and its architecture: the real app and worker on demo data, driven by Playwright, with the Gemini call replaced by a fixed answer. The voice-over is generated locally with [Kokoro](https://github.com/thewh1teagle/kokoro-onnx), an open-source text-to-speech model; the narration in `docs/video/narration.py` sets the pace, since each scene holds until its line is spoken. Setup is in the comment at the top of `make.sh`; without the voice model it makes a silent version. It writes `docs/video/sjekka-showcase.mp4` (not committed). Rerun it after visible changes; facts in the script and slides (like the test count) are in `narration.py` and `slides.html`.

---

## AI use

Gemini reads the receipts, and much of the code since October 2026 was written with Claude Code. [docs/AI.md](docs/AI.md) describes what the AI does, what was decided by a person, and how the AI's work is checked.

---

## Contributing

Private repository. Collaborators by invite only.
