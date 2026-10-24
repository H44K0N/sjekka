# Sjekka

> *Har du sjekka i dag?*

**Sjekka tells you where your grocery list is cheapest**, using real prices from receipts people share and the offers in this week's flyers. Try it at **https://sjekka.tischbein.dev** (Norwegian), or watch the 45-second film on the [about page](https://sjekka.tischbein.dev/om.html).

---

## What you can do

- **Look up a price.** Search for a product and see what it cost in each store, from real receipts, with its price history.
- **Price your whole shopping list.** Write down what you need and see what the list costs in every chain, cheapest first. Missing prices are estimated, and marked as estimates.
- **See if an offer is really an offer.** This week's flyer offers from ten chains, each checked against what the product cost before the sale.
- **Share a receipt.** Take a photo, or upload a receipt you have as a file (a PDF, or a CSV or JSON export from a store app, bank or online shop). Sjekka reads the store, the items and the prices, and every receipt makes the prices better for everyone.

It's free, needs no account, and sets no cookies of its own.

## How it works

```
Receipt photo ──► Gemini reads it ──► prices per store ──┐
                                                          ├──► search, product pages,
Weekly flyers ──► Gemini names each offer ──► offers ─────┘     shopping list, offer check
```

1. **Receipts.** A photo (shrunk first) or a receipt file is uploaded and put in a queue. Google's Gemini reads the store, date and each line into fixed fields. Sjekka checks the answer, works out comparable prices (per 100 g, per 100 ml or per item), and deletes the photo.
2. **Flyers.** Every six hours Sjekka fetches new grocery flyers from eTilbudsavis. Prices and sizes come from the flyer; Gemini only gives each offer the same product name a receipt would use, so the two can be compared.
3. **Comparing.** An offer is "real" when it's at least 15 % below what receipts show the chain usually charged. A shopping list is priced per chain from the latest receipts and current offers; where a chain has no price for a product, it's estimated from how expensive that chain is on products both sell.

## Privacy, in short

Receipt photos go to Google's Gemini to be read, then are deleted from the server. What's kept is what's printed on the receipt (store, date, items, prices), never who uploaded it. The pages load nothing from other sites and set no cookies of their own (Cloudflare, which protects the site, may set one). The full notice is on [personvern](https://sjekka.tischbein.dev/personvern.html) (`static/personvern.html`).

## Where it stands

- **Few receipts so far**, so most offers can only show the flyer's own claim, and many list prices are estimates. Every receipt uploaded improves that, which is why the offers page asks for them.
- **Names can split a product in two.** Gemini sometimes names the same product slightly differently ("Kjøttdeig" and "Kjøttdeig storfe"), and the list matches names exactly; an item without prices suggests similar names.
- **Store location** is only the chain, not the branch or region.
- **Ideas:** regional prices, flagging low-confidence readings for review, open access to the data.

---

## For developers

Python and FastAPI, SQLite, plain HTML/CSS/JS with no build step, Docker on a VPS behind Cloudflare. How the code is laid out, and the rules for changing it, are in [CLAUDE.md](CLAUDE.md).

### Run it locally

```bash
cp .env.example .env              # add your Gemini API key
pip install -r requirements-dev.txt
uvicorn app.main:app              # http://127.0.0.1:8000
```

Data goes to `./data/`. For a database with something in it, copy `test_data/sjekka.db` (one Kiwi receipt, six items) to `./data/sjekka.db`. To browse without calling Gemini, start it with `RUN_WORKER=0`.

### Settings

All are environment variables (see `app/config.py`); only the API key is required.

| Setting | Default | What it does |
|---|---|---|
| `GEMINI_API_KEY` | none | Google AI Studio key, for reading receipts and naming offers |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Which Gemini model to use |
| `DATA_DIR` | `./data` | Where the database, photos and logs go (`UPLOAD_DIR`, `LOG_DIR` and `DATABASE_URL` can override each) |
| `RUN_WORKER` | `1` | `0` turns off the background worker: no receipts read, no flyers fetched |
| `FLYER_REFRESH_HOURS` | `6` | How often new flyers are fetched; `0` turns it off |
| `UPLOADS_PER_IP_PER_HOUR` | `10` | Upload limit per client, since every upload costs a Gemini call |
| `UPLOADS_PER_DAY` | `200` | Upload limit for everyone together |

### Tests

Run the same checks as CI before pushing:

```bash
ruff check .
shellcheck deploy/*.sh docs/video/*.sh
playwright install chromium      # once; or set CHROMIUM_PATH=/usr/bin/chromium
pytest --cov=app
```

About 350 tests, and none of them call Gemini: it's replaced with fakes, so they're free, fast and give the same result every time. CI also requires the browser tests and at least 95 % coverage.

<details>
<summary>What each test file covers</summary>

| File | Covers |
|---|---|
| `test_upload.py`, `test_upload_edge_cases.py` | Upload checks, formats (HEIC too), rotation, resizing, rate limits |
| `test_receipt_files.py` | Receipts as PDF, CSV or JSON: recognised by content, checked, stored as UTF-8 |
| `test_api.py` | `/products`, `/stats`, pages, caching and content-hashed scripts, the about page and its films |
| `test_jobs.py` | `/jobs/{id}`, which the thank-you page follows |
| `test_worker.py`, `test_worker_helpers.py` | Saving receipts, unit prices, multipacks, loose produce, names, duplicates, retries |
| `test_pipeline.py` | Upload → queue → worker → API, and recovery after a crash |
| `test_extractor.py` | The Gemini request and checking its answer |
| `test_flyers.py`, `test_tjek.py` | Flyer import (files and eTilbudsavis), sizes and multi-buys, chain matching against real flyer brands, verdicts, duplicates, `/offers` |
| `test_basket.py` | Pricing a shopping list: chain names, latest prices, size-adjusted comparison, estimates, the two-store split, `POST /basket` |
| `test_migrations.py`, `test_export.py` | Adding columns to the live database; exporting Gemini's raw answers |
| `test_deploy_scripts.py` | `deploy.sh` (checks, health check, rollback) with a fake `docker`, and `backup.sh` |
| `test_browser.py` | Every page in Chromium: search, filters, product pages, the shopping list, offers, upload, the phone layout, and accessibility checks (axe-core, WCAG 2.1 AA) |

</details>

### Deployment

Production runs at https://sjekka.tischbein.dev: one Docker container on a VPS, reached through a Cloudflare tunnel. Merging to `main` deploys it.

<details>
<summary>The pipeline, the server, and changing them</summary>

`.github/workflows/pipeline.yml` runs on every pull request and every push to `main`:

1. **Lint and test**: `ruff check`, `shellcheck` and `pytest`.
2. **Build and smoke test**: builds the image, starts it and checks the main endpoints. On `main` it's pushed to `ghcr.io/h44k0n/sjekka`, tagged with the commit and `latest`.
3. **Deploy** (`main` only): logs in to the VPS as `sjekka-deploy`, whose key can only run `/opt/sjekka/deploy.sh`. That script starts the new image and puts the old one back if the container doesn't become healthy. The last step checks the public URL; Cloudflare's bot protection challenges GitHub's runners, so that check usually only logs a warning.

The server was set up with `deploy/setup-vps.sh` (Docker, the deploy user, `/opt/sjekka`, the restricted key, the nightly backup). `compose.yaml` and `deploy/` reach the server only through that script, so re-run it after changing them:

```bash
scp -r compose.yaml deploy vps:/tmp/sjekka-setup/
ssh -t vps 'sudo bash /tmp/sjekka-setup/deploy/setup-vps.sh /tmp/sjekka-setup/ci-deploy-key.pub'
```

Data lives in `/opt/sjekka/data` and secrets in `/opt/sjekka/.env`; the pipeline touches neither. Changing `.env` takes `docker compose up -d`, not just a restart.

Dependabot opens a PR every Monday for new versions of Python packages, the base image and GitHub Actions. Each runs the full pipeline and is deployed only once merged.

Files the browser keeps: scripts and stylesheets are linked as `/name.js?v=<hash of the file>`, so a deploy can never pair a new page with an old script. Logos, fonts and the films are kept for a week or longer, so give them a new file name when they change.

</details>

<details>
<summary>Flyer import</summary>

The app fetches new flyers by itself every `FLYER_REFRESH_HOURS`: it lists the flyers from eTilbudsavis (Tjek) around Oslo, keeps the ten grocery chains it knows (`CHAINS` in `app/basket.py`), and imports the ones it hasn't seen, with one Gemini text call per flyer. Gemini names each offer like a receipt line and flags anything that isn't groceries, which is skipped. A flyer that fails is tried again on the next round. Chains publish overlapping flyers (a weekly and a weekend one), so an offer repeated across them is listed once.

To import by hand, from `/opt/sjekka`:

```bash
sudo docker compose exec app python util/import_tjek.py                                        # around Oslo again
sudo docker compose exec app python util/import_tjek.py --lat 60.39 --lng 5.32 --radius 30000  # Bergen
sudo docker compose cp kiwi-uke-43.pdf app:/tmp/                                               # or a flyer you have,
sudo docker compose exec app python util/import_flyer.py /tmp/kiwi-uke-43.pdf                  # read by Gemini
```

The eTilbudsavis API is public but undocumented, and we haven't found terms for reuse; the offers page credits it. Tjek's data and Gemini's answer are kept on each flyer (`flyers.raw_response`).

</details>

<details>
<summary>Backups and restoring</summary>

`sjekka-backup.timer` runs `deploy/backup.sh` every night at about 03:30. It copies the database with SQLite's online backup, checks the copy, and keeps 14 days in `/opt/sjekka/backups`. They're on the same disk as the database, so copy them elsewhere too. To restore:

```bash
cd /opt/sjekka && docker compose stop app
gunzip -c backups/sjekka-<date>.db.gz > data/sjekka.db && chown 10001:10001 data/sjekka.db
docker compose start app
```

</details>

<details>
<summary>Security and data handling</summary>

- **Photos** go to Google's Gemini API, so Google's API terms apply (on the free tier Google may use them to improve its products; on the paid tier it says it doesn't). The server never serves them, deletes each one once read, and deletes photos of failed jobs after 30 days. The thank-you page shows a small copy kept only in the uploader's browser tab.
- **Gemini's raw answers** are kept (`jobs.raw_response`, `jobs.model`), so readings can be reworked later without the photos. `util/export_extractions.py` exports them.
- **Uploads** need no login, so they're rate-limited (see Settings). A Cloudflare rate-limit rule in front would stop floods earlier.
- **The privacy notice** (`static/personvern.html`) must stay in step with the code: what's kept, for how long, and who receives what (Google, Cloudflare, the VPS host).
- **Unused tables:** `products` and `categories` exist in the database but nothing uses them; the category is stored on each receipt line.

</details>

<details>
<summary>Videos</summary>

**Promo films** (on the about page, committed in `static/video/`): a 45-second story with an English voice-over, for desktop and phone. `docs/video/promo.sh` records them on demo data. The lines are in `promo_script.py`, voiced with [Kokoro](https://github.com/thewh1teagle/kokoro-onnx), an open-source text-to-speech model that runs locally; the voice is AI-generated, and the about page says so. The music is "Motivating Mornings" by Ahjay Stelino from [Mixkit](https://mixkit.co/free-stock-music/) (royalty free, no credit required), cut by `music.py` so it lifts as Sjekka appears; `sfx.py` makes a few quiet sound effects and `promo_mix.py` puts it together.

**Walkthroughs** (not committed): `docs/video/make.sh` records a longer narrated tour of the app and how it's built, for desktop and phone, with Gemini replaced by a fixed answer. The narration is in `narration.py` and the slides in `slides.html`; update facts like the test count there.

Both drive the real app with Playwright. The phone versions show the site in a phone-width frame scaled up, since Playwright records CSS pixels.

</details>

<details>
<summary>Store logos</summary>

From Wikimedia Commons unless noted. Rema 1000, Kiwi, Meny, Coop Prix, Coop Mega, Joker, Spar and Obs ([source](https://commons.wikimedia.org/wiki/File:Coop_obs_hypermarked.jpg), cropped) are marked public domain. Bunnpris is from Bunnpris' own media bank, licensed with attribution ([source](https://commons.wikimedia.org/wiki/File:Bunnpris_Logo.png)). Extra is by Coop Norge, [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) ([source](https://commons.wikimedia.org/wiki/File:Extra_butikklogo.png)), resized. The Oda wordmark is from the header of [oda.com](https://oda.com/no/), in its orange. All are the chains' trademarks, used only to show which store a price is from. A test checks that every known chain has a logo that loads; matching is in `static/chains.js`.

</details>

---

## AI use

Gemini reads the receipts and names the flyer offers, and most of the code since October 2026 was written with Claude Code, directed by Haakon. [docs/AI.md](docs/AI.md) shows how: real prompts and what they became, what a person decided, how the AI's work was checked, and what went wrong.

## Contributing

Private repository; collaborators by invite only.
