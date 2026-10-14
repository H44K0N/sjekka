# Sjekka 🧾

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
- Auto-start via systemd on reboot

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
- Paths are hard-coded to `/home/haakon/sjekka` in `app/worker.py` (extraction log) and `util/requeue_photos_as_jobs.py`
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
pip install -r requirements.txt
uvicorn app.main:app
```

`test_data/sjekka.db` is a small sample database: one Kiwi receipt with six items, plus twelve queued jobs whose photos aren't in the repo. Copy it to `./sjekka.db` to start with data. Without it, an empty database is created on first run.

---

## Stack

- **Backend:** Python / FastAPI
- **Extraction:** Google Gemini 2.5 Flash
- **Database:** SQLite (local, on Pi)
- **Frontend:** Plain HTML / CSS / JS
- **Infrastructure:** Nginx on Raspberry Pi 4, Cloudflare Tunnel

---

## Contributing

Private repository. Collaborators by invite only.
