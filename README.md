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
- Explore page with category filters and search
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

- Uploaded receipt photos are served publicly at `/uploads/<id>.jpg`. Filenames are random UUIDs, but a receipt can show card digits, store and time of purchase
- No authentication or rate limiting on `/upload` — every upload costs a Gemini call, so anyone with the URL can use up the API quota

### Code

- The worker only retries on 503. A 429 (rate limited) marks the job as failed for good
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

`test_data/sjekka.db` is a small sample database: one Kiwi receipt with six items, plus twelve queued jobs whose photos aren't in the repo. Copy it to `./data/sjekka.db` to start with data. Without it, an empty database is created on first run.

Before pushing, run the same checks as CI:

```bash
ruff check .
pytest
```

---

## Deployment

Production runs at https://sjekka.tischbein.dev as a Docker container on a VPS, with cloudflared on the host routing the subdomain to `http://localhost:8000`.

`.github/workflows/pipeline.yml` runs on every pull request and every push to `main`:

1. **Lint and test**: `ruff check` and `pytest`.
2. **Build and smoke test image**: builds the Docker image, starts it and checks the main endpoints respond. On `main` the image is pushed to `ghcr.io/h44k0n/sjekka`, tagged with the commit SHA and `latest`.
3. **Deploy** (`main` only): logs in to the VPS as `sjekka-deploy` with a key that can only run `/opt/sjekka/deploy.sh`. That script starts the image for the pushed commit and restores the previous one if the container doesn't become healthy. Finally the job checks the public URL.

The server is set up with `deploy/setup-vps.sh` (Docker, the `sjekka-deploy` user, `/opt/sjekka`, the restricted CI key). `compose.yaml` and `deploy.sh` are installed by that script, not by CI, so re-run it after changing either file:

```bash
scp -r compose.yaml deploy vps:/tmp/sjekka-setup/
ssh -t vps 'sudo bash /tmp/sjekka-setup/deploy/setup-vps.sh /tmp/sjekka-setup/ci-deploy-key.pub'
```

Production data lives in `/opt/sjekka/data` and secrets in `/opt/sjekka/.env`. The pipeline never touches either.

---

## Stack

- **Backend:** Python / FastAPI
- **Extraction:** Google Gemini 2.5 Flash
- **Database:** SQLite
- **Frontend:** Plain HTML / CSS / JS
- **Infrastructure:** Docker on a VPS, Cloudflare Tunnel, GitHub Actions

---

## Contributing

Private repository. Collaborators by invite only.
