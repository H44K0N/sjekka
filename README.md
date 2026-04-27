# Sjekka 🧾

> *Har du sjekka i dag?*

Sjekka is a crowdsourced grocery price comparison service for Norway. Users upload photos of their receipts, and Sjekka extracts the items and prices — building a real-time, community-driven picture of what things actually cost across Norwegian grocery chains.

---

## The Problem

Norwegian grocery prices vary significantly across chains like Rema 1000, Kiwi, Coop, and Meny — but there's no easy way for consumers to compare them in real time. Existing solutions rely on manual data entry or scraping, which is fragile and legally grey. Meanwhile, millions of receipts are thrown away every day.

---

## The Idea

Every receipt is a data point. Sjekka turns them into a living price database.

- User photographs a receipt
- Sjekka extracts store, items, prices, and date using AI
- Data is normalized into comparable categories (e.g. "GILDE KJØTTDEIG 400G" → *chicken mince, 400g*)
- Users can search and compare prices across chains and regions
- Over time, the dataset reveals trends, seasonal changes, and regional differences

The more people use it, the more accurate and comprehensive it gets.

---

## How It Works

```
[Receipt photo]
      ↓
[Image compression + upload]
      ↓
[Data extraction → structured JSON]
      ↓
[Normalization → product taxonomy]
      ↓
[Postgres database]
      ↓
[Price comparison UI]
```

---

## Current Status

Early development. Core pipeline being built:

- [ ] Receipt image upload + compression
- [ ] AI-powered data extraction (not set in stone, might change)
- [ ] Product taxonomy and normalization
- [ ] Database schema
- [ ] Basic price comparison UI

---

## Product Taxonomy

Products are stored hierarchically to enable flexible filtering:

```
Category → Product → Attributes
Dairy    → Milk    → fat: 3.5%, volume: 1L
Meat     → Chicken → cut: mince, weight: 400g
```

Users can filter as broadly or specifically as they like:

- *"I want milk"* → all milk, all sizes, all fat percentages
- *"I want whole milk, 1.5L"* → fat: 3.5%, volume: 1500ml only

---

## Shopping List & Basket Optimization

Users build a shopping list using cascading dropdowns — each selection narrows the next. Sjekka then answers: **which store is cheapest for your whole basket today?**

---

## Future Possibilities

- **Basket optimizer** — given your list, which single store saves you the most?
- **Price history graphs** — see how prices have trended over weeks and months
- **Deal alerts** — notify me when chicken mince drops below X kr at any Rema
- **Regional breakdown** — Oslo vs Bergen vs Tromsø price comparisons
- **Member price tracking** — flag and track loyalty card discounts separately
- **Receipt splitting** — useful for shared households
- **Carbon footprint** — surface lower-impact alternatives at similar price points
- **API** — let third parties build on the dataset

---

## Stack

- **Backend:** Python / FastAPI
- **Data extraction:** TBD - possible ai integration
- **Database:** PostgreSQL
- **Frontend:** HTML / CSS / HTMX
- **Infrastructure:** Nginx on Raspberry Pi, exposed via Cloudflare Tunnel

---

## Contributing

Private repository. Collaborators by invite only.

---

*Built in Norway, for Norway.*
