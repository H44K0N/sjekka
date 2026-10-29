# How AI is used in Sjekka

AI plays two parts: **in the app**, Google's Gemini reads receipts and names flyer offers; **in building it**, Claude Code (Anthropic) has written most of the code since October 2026, directed by Haakon. This page says what the AI did, what a person decided, how the AI's work was checked, and what went wrong.

## In short

- **In the app,** Gemini does two narrow jobs: it reads each receipt photo into fixed fields, and it gives each flyer offer the product name a receipt would use. Everything else, including every calculation, is ordinary code.
- **In building it,** Haakon described what he wanted in a sentence or two; Claude Code wrote the code, the tests and the docs, opened pull requests and watched them deploy. Haakon decided what to build, judged the results and said when to ship.
- **How much:** on 24 October 2026 alone, 32 pull requests were merged: about 5,500 new lines in 58 files, and the test suite grew from 110 to 349 tests.
- **How it was kept honest:** every change went through a pull request and automated checks, bugs got a test that fails without the fix, and the mistakes are recorded below.
- **In the films,** the voice-over is AI-generated (Chatterbox, running locally); the music is royalty-free tracks by human composers. The about page says so.

## Building Sjekka by describing it

Most of Sjekka from PR #22 on was built by describing outcomes, not code. Haakon wrote a sentence or two; Claude Code read the code and the live site, did the work, wrote the tests, opened the PRs and reported back. These are Haakon's own prompts (typos tidied), next to what each became.

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
| "Take on the persona of 5 distinctly different probable users ... rate each finding from 1 to 10 ... focus on root causes and assumptions ... If a wider change can fix multiple issues in one change, make the change", given at bedtime | Five personas (a student on a phone in dark mode, a parent building an 18-item list, a senior on an iPad with large text, a data donor re-uploading a Trumf history, a sceptic opening a shared list) walked through a copy of the production database. 52 screenshots and about 25 findings came down to six root causes, each fixed in its own PR, merged overnight (#60–#68). The worst was the offers page's whole point: offers judged "real" against another brand, a pack a quarter of the size, or a price 20 months old. The flows were then run again on fresh production data to confirm the fixes. |
| "They don't tell a story. Frame it as a story: the large chains want you to spend as much as possible on groceries. We are here to declare war on the big chains" | The promo films on the about page: an English voice-over, promo signs piling up in a zigzag, and the reveal of Sjekka as the answer to one question (#46). |

### The decisions that were Haakon's

The judgement calls stayed with Haakon, and several changed the outcome:

- **Asking for a checkpoint** before four PRs were merged at once (the `checkpoint-2026-10-24` tag), so any of it could be rolled back in one step.
- **Questioning the data.** The 169 kr pizza looked wrong; checking it turned up the yoghurt bug next to it.
- **Asking for root causes, not symptoms.** The first fix for hardware in the grocery offers asked Gemini to filter it out. Asking why it was there at all found the Megaflis mix-up in the chain matching.
- **Product taste.** Oda's logo in its orange ("it's their colour"), the logos without white badges, centred in the ranking, and the story and music for the films.
- **Knowing when to stop.** When the list suggested "Kokosmelk, Sjokomelk, Litago melk" for "Melk", Claude offered to add a list of base products. Haakon: "Those are still melk, so I think that's OK." No extra code.

### What made it work

The prompts were short, but the way of working around them did the heavy lifting:

- **One idea, one pull request.** Each change was its own branch and PR with its own description, so every step could be reviewed, merged or rolled back on its own.
- **Automated checks as the gate.** Nothing reached production without lint, the full test suite (including browser and accessibility tests), a coverage floor and a smoke test of the image. When CI was red, nothing merged.
- **A test for every bug.** "Add a test so it cannot happen again" became the rule: the test is checked to fail without the fix, then pass with it.
- **Checkpoints before big steps.** A git tag before merging several PRs at once meant one command could undo them.
- **Reviewing by looking, not by reading code.** Haakon judged the work as a customer would: the live site, screenshots of each journey, the videos themselves, and the before-and-after numbers in each PR.
- **Rules written down.** [`CLAUDE.md`](../CLAUDE.md) holds the project's rules (never call Gemini in tests, migrations for schema changes, Norwegian text, the design), so every AI session starts from the same ground.

### Where the AI was held back

The AI didn't have free rein, and the limits held:

- **Merging and deploying needed Haakon's go-ahead.** Claude Code's permission checks refused to merge or deploy without it, even when Claude had asked earlier in the conversation.
- **The production server stayed off limits.** Claude's SSH user can't read the database or run Docker; a direct log-reading attempt was refused by the permission checks. When a question needed production data (was the 169 kr pizza misread?), Haakon ran the read-only query with his own password and pasted the result.
- **Access stayed with Haakon.** Server root, the Cloudflare dashboard and the Google AI Studio account were his alone; when the work needed them, he did those steps himself.

### Who did what

**Haakon decided** what to build and in which order, the design (match tischbein.dev, no emoji, no gradients), the trade-offs (keeping Cloudflare's bot protection even though it trips a CI check, running the flyer import in production), the story and sound of the films, and when each change shipped.

**Haakon did by hand** anything needing root or dashboard access: running the server setup with sudo, SSH settings, Cloudflare tunnel routes and rate limits, and queries on the production database.

**Claude did** the reading and the writing: it read the code and the live site, proposed and ranked improvements, wrote the code, tests and docs, opened the PRs, watched CI and deploys, and traced failures to their cause.

### Guardrails on the AI's work

- **Every change goes through a PR and CI:** lint, shellcheck, 349 tests including browser and accessibility tests, a 95 % coverage floor, and a smoke test of the Docker image.
- **Tests are checked against deliberate bugs.** Claude broke the code on purpose (disabled the deploy rollback, loosened input checks, broke the unit-price maths, reintroduced an HTML-injection bug, restored the old chain pattern) to confirm the tests catch it.
- **Deploys roll back by themselves** if the new container isn't healthy.
- **Commits are marked.** Every commit Claude worked on carries a `Co-Authored-By: Claude …` line, and every PR says it was generated with Claude Code, so `git log` shows what was AI-assisted.

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
- Claude could not reproduce the shopping-list dropdown bug in an emulated browser (the tap added the item), and said so and asked where it happened rather than guessing. Haakon's answer (the dropdown, on his phone) pointed at the real cause: on a phone, focus leaves the field before the click, and hiding the list on that blur let the click fall through to the product link underneath. A test now forces that event order.
- The first dinners page named a chain cheapest on its lowest total, which was often made of guesses: "fish fingers at Spar" had two of three prices estimated. The walkthrough as the sceptic caught it; a chain now needs mostly real prices to be named. The same walkthrough found that one portion's plan said 66 kr a portion next to dinners at 8 kr (whole packages versus what the food uses), and a race where tapping "swap" during a reload lost the tap; the page now says both numbers and dims results while they reload.
- The reworked promo first piled up invented red "TILBUD" signs. Haakon found them cheap and AI-looking. They were replaced with crops of this week's real flyers, from the catalogues the app already reads. Claude kept one invented sign for "deals that aren't really deals", since a real chain's offer can't be called fake without checking it.
- The first promo films had no voice and synthesised music, and Haakon found them flat ("they don't tell a story") and the music "too haunting". Claude can't hear audio, so it had judged the music only by measurements. They were remade with a voice-over and a real royalty-free track (#46, #47).
- When several PRs were merged in parallel, two of them conflicted in the same test file and the merge stopped without saying so. It was caught by checking the PRs' state, and they were rebased and merged one at a time.

- To work on several PRs at once, Claude made extra git worktrees with a symlink to the project's Python environment. `.gitignore` listed `.venv/`, which ignores folders but not links, so a `git add -A` committed the link (#50); pulling it replaced the real environment, twice. Only the local setup was affected: `.gitignore` now says `.venv`, the link is gone from the repo, and the environment was rebuilt from `requirements-dev.txt`.

## Technical details: Gemini in the app

Gemini is treated as an unreliable component: its output is constrained, checked, and never trusted with arithmetic.

**Reading receipts.** Every uploaded photo is sent to Gemini (`GEMINI_MODEL`, default `gemini-2.5-flash`), which returns the store, date and each line.

- **Fixed output shape.** Gemini must answer against a Pydantic schema (`app/schema.py`), so categories and units can only be values from fixed lists. The answer is validated again before anything is saved; an invalid answer fails the job instead of storing bad data.
- **No arithmetic by the model.** Gemini reads prices off the receipt; the price per 100 g, per 100 ml or per item is computed in Python (`worker.unit_price`). Known misreadings are corrected in code too: a multipack like "4x150g" stored as one cup is repaired to the whole pack.
- **Normalised names.** `KJØTTDEIG ` and `kjøttdeig` are stored as `Kjøttdeig`, so the same product groups together.
- **Duplicates.** A receipt with the same store, date and lines as one already saved isn't stored twice.
- **Failures.** Rate limits and outages are retried with backoff; anything else fails the job with the error saved.
- **Cost and abuse.** Every upload costs a call, so uploads are rate-limited per client and per day.
- **Privacy.** The upload page says photos go to Google, and that it's the free tier, where Google may train on them and staff may read them (the paid tier says it doesn't). The server never serves them and deletes each once read; Gemini's raw answer is kept so the data can be reprocessed without the photo.

**Naming flyer offers.** Every six hours the app fetches new flyers from eTilbudsavis (`app/tjek.py`). Prices, sizes and pack counts come from the flyer data; Gemini gets one text-only call per flyer to give each offer a receipt-style name and brand, and to flag what isn't groceries (those are skipped). The same schema checks apply, and the raw answer is kept on the flyer.

**Tests never call Gemini.** It's replaced with fakes, so tests are free, fast and deterministic. How well Gemini reads real receipts still has to be checked with real receipts.

Known limits: Gemini can misread blurry or cut-off receipts, and slightly different names for the same product ("Kjøttdeig" and "Kjøttdeig storfe") aren't merged.

## Change log

Add a line here for each AI-assisted change.

| When | Work | AI involvement |
|---|---|---|
| 27–28 Apr 2026 | The original app: upload, database, Gemini reading, job queue, explore page | Built by Haakon with help from AI; not marked in the commits |
| 15 Oct 2026 | Stopped tracking `.env`, moved the sample database | Not recorded. **TODO (Haakon).** |
| 18 Oct 2026 | CI/CD pipeline, Docker image, VPS deployment (#1) | Claude Code (Claude Opus 5.5) |
| 23 Oct 2026 | #2–#21 | Claude Code (Claude Opus 5.5) |
| 24 Oct 2026 | #22–#54: shopping list, flyer offers, UI rework, two customer walkthroughs, promo films | Claude Code (Claude Opus 5.5), directed by Haakon in short prompts |
| 24–26 Oct 2026 | #55–#93: receipt files and Trumf histories, a five-persona walkthrough on production data | Claude Code (Claude Opus 5.5); #60–#68 done overnight, unattended, with permission to merge |

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
| #17 | Explore page: sorting and shareable views | |
| #18 | Link previews, a 404 page, caching for static files | |
| #19 | Weekly dependency updates (Dependabot) | |
| #20 | Automated accessibility checks (axe-core) on every page | |
| #21 | Sort menu: tap to reveal instead of text links | |
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
| #54 | This document: an overview in plain words, real prompts and decisions, how the work was done and limited; README and CLAUDE.md made easier to read | Haakon: "showcase how easy it is"; the mistakes section stays |
| #55 | Receipts as files: PDF, CSV or JSON, including whole purchase histories split into parts; files kept 12 months to develop against | Haakon picked digital receipts from the data-source research, said a year of Trumf receipts is the real use case, and asked to keep the raw files; `.gitignore` fixed after a `.venv` link was committed |
| #56 | Trumf histories read from the file: exact prices, dates and weights; Gemini only names the lines | From the first real upload: Gemini read 93 % of 221 receipts exactly, but the file needs no reading at all |
| #57 | A photo and a Trumf export of the same shop count once; the copy already saved is removed | Rule checked on production data first: it found exactly the one known duplicate, and nothing near it |
| #58 | Names that match: Gemini reuses known product names everywhere, brands kept out of names, chains under one name, florists out of comparisons; answers no longer cut off | From the first real upload: 482 receipt names and 864 offer names shared only 105; one part failed with Gemini's answer cut short |
| #59 | The first Trumf history read again with the parser | Dry-run on production first: it touches only that history's 258 receipts |
| #60 | Offers judged only against recent receipts (180 days) for the same brand and a similar size | Persona walkthrough: on production, 8 "real" offers and 118 "lowest known" were mostly wrong; now 1 and 45 |
| #61 | List prices compared like for like: weighed against weighed, at the most common size, and labelled "for 1 l"; ties on estimates called ties; splits only on known prices | "Epler billigst hos Joker 5,47 kr" was one weighed apple against 29,90 kr a kilo |
| #62 | Enter adds what was typed; suggestions ranked by closeness and how many stores sell them; the total bar goes when the list is cleared; a shared list says where it came from | "melk" + Enter had put Melkeglede (sweets) on the list |
| #63 | Receipts keep their printed total; the same total recognises a history uploaded again | Re-uploading the friend's history saved 2 receipts twice; checked on a fresh production copy: 304 totals filled, nothing removed |
| #64 | The product page says how old the cheapest price is, and leads with the unit price when sizes differ | "Billigst nå" on a price from February 2025 |
| #65 | Pages seen before fold their introduction; the front page shows 48 products at a time | The search was 1000 px down on a phone; the front page was 32 000 px tall |
| #66 | Smaller truths: the thank-you text after reading, upload errors in the box, offers worded differently listed once, the about page on estimates | |
| #67 | "Mente du" ranks a word and a compound ending alike, the commoner first | Found when re-running the walkthrough: "Melk" offered Litago melk, not Lettmelk |
| #68 | A product in one store shows "Pris nå", without repeating the price per item | Found when re-running the walkthrough |
| #70 | One name per product and one spelling per brand: "Grandiosa", "Pizza grandiosa" and "Pizza" are one Pizza with Grandiosa as its brand, at save and for what was saved before; old names in saved lists and links still find the product. List prices use the size people buy over all purchases, and never scale a multipack down to one bottle | 344 names on receipts, 179 of them on one receipt only; the same pizza had a third of its prices on each of three products. The user chose generic products with brands inside: "this is the cheapest of all pizzas right now" |
| #71 | A chain is priced by the cheapest brand it sells now (each brand's latest price from the last 180 days), not by whatever was bought there last | Followed #70: with brands inside one product, "the latest line" was often a family pack or a dear brand |
| #72 | Packs are counted in pieces ("EGG 12STK" is 12 stk) and compared per piece | A 6-pack of eggs looked cheaper than a 12-pack; Gemini gave piece counts only about half the time |
| #73 | Product cards and the product page show the same choice as the shopping list, and a pack given by weight whose name says how many pieces it holds counts as pieces when the product is counted in pieces (Coop's "12 PK … 816 g" eggs) | Searching "grandiosa" showed a 1 kg pizza at 169 kr. The user asked for these two after #72 |
| #74 | Tests run on every core and the image is built alongside them; CI went from about 3 minutes to under 2 | Haakon asked how long CI takes, then "if they don't sacrifice quality, do it" |
| #75 | The films reworked: a marketing story that picks the desktop or phone version, a film on how the app works and one on how it was built with AI; all made from code (real app captures moved by a timeline, a new voice with the best of five takes, sound design, captions) | Haakon: "rework the videos, let's try to make them studio grade like from a tier 1 marketing team", and after the first draft: "the offer signs look a little cheap and AI generated, could we clip some real world Norwegian style offers?" |
| #76 | The privacy page and the upload page say that Sjekka uses Gemini's free tier, where Google may train on uploads | Haakon: update the privacy policy to reflect that we use Gemini's cheaper tier, which lets Google train on them. The page had said the opposite |
| #77 | Every file a page points at (films, captions, posters, fonts, logos, scripts, styles) is linked by a hash of its contents and kept for a year; a stylesheet's hash covers its fonts | Haakon, after Cloudflare kept an old film under the same name: "change the video names so the cache updates instantly. We should probably handle this automatically for all media. And other cached items" |
| #78 | Shopping list, first part of the rework: tapping a suggestion in the search dropdown adds it and stays on the page (on phones it opened the product page), the field stays focused for the next one, and a Foreslått panel of tap-to-add chips (offers first, then what most stores sell) sits under the list, as one swipeable row on phones | Haakon: "the shopping list doesn't work as a user might think ... clicking items in the suggested drop list brings you to the product page, which makes it tedious to go back a page to add more items"; priorities chosen: add many items without leaving, and a clearer result |
| #79 | The shopping list's answer is kept in view on phones: a bar under the heading shows the cheapest store, the total, and the saving over the next best store, and stays at the top as you scroll; the verdict compares with the next best store first and the most expensive second; the front page's categories fold behind a "Kategori" pill | Haakon: "Yeah build that" (Claude's recommendation after he picked "clearer result"), and mid-way: "the categories on the main page take too much space, especially on mobile. Make it collapsed by default" |
| #80 | A dinners page (`/middag.html`): 25 whole dinners priced from receipts and flyer offers, cheapest per portion first, a plan for the week that picks dinners sharing ingredients, a swap per day, one tap to put the trip on the shopping list. Walked through as the five earlier users, twice | Haakon: "A page with meal suggestions, based on known tilbuds and cheap prices. Whole dinners comprised of cheap ingredients. Be creative with this one, giving you full creative freedom. When finished, go through as the previous 5 users and get feedback on the user experience. Then iterate again." |
| #81 | The phone menu in two rows: the logo and the upload button, then the four pages spread across the width, each easy to tap | Haakon, with a screenshot of the new "middag" link squeezed in: "Top nav bar is a little crowded." |
| #82 | Dinners say what "cheapest" was compared with: "billigst av 2 butikker med nok priser", and each dinner and the week plan show their cost at the best-known chains | Haakon: "Why is there mostly kiwi entries in the dinner page? Seems like something must have gone wrong or is kiwi consistently cheaper for dinners?" Nothing was broken: the receipts are mostly Kiwi, so Kiwi was the only chain with enough real prices to be named, and the page claimed more than it knew |
| #83 | A logo: the name in bold, bigger than the menu, with a champagne full stop; the same "s." as the tab and home-screen icon (drawn as a shape, since an icon can't load the font) and on the link preview | Haakon: "the logo at the top of the page is the same size as the rest of the nav bar. Lets create a couple logos and i can choose the right one for the brand". Five were drawn in the real header; Haakon chose "A. As is" |
| #84 | A multi-buy says what it asks: "8 for 100 kr" with a "Må kjøpe 8 · 12,50 kr stk" tag on the offers and product pages, and the shopping list prices one can at its usual price and offers "Ta 8" instead of pretending one costs 12,50 | Haakon: "it doesnt really come across clearly enough that this only applies if you buy 8x500ml boxes of battery energy drink. Figure this out in a better way." |
| #85 | Receipt reading keeps every word of a line in the variant, a word the till cut short too, and adds none; product pages show the receipt's own text under each price | Haakon: a "Coca-Cola" from Joker was Coca-Cola Creamy Vanilla with sugar. The line said "COCA-COLA ORIGINAL CREA": the model kept "Original" and dropped the half word, and since the line had no size the Joker row showed an older Coca-Cola Zero |
| #86 | A product is generic ("Brus") and holds items (Coca-Cola Zero, Pepsi Max). The shopping list can hold either "billigste brus" or one item, each priced from its own lines; a product page lists its items with their own pages; "Billigst nå" names what it is and ignores packs far bigger than usual | Haakon: "i do like the idea of having a feature for \"whats the cheapest pizza\", but in this case it might have joined the vanilla coke with coke zero entry. Figure out a better and more intuitive way forward." |
| #87 | The films' player captions start off, since the words are already in the picture, and the sound starts at half | Haakon: "since the videos are already captioned in the actual video, can we make video player captions off by default? also maybe default volume at 50%?" |
| #89 | The front page has a card per item ("Ahlgrens Biler Original", product "Godteri" under it), searchable by product and category; sorted by newest, a card shows its newest price, so the dates are in order; an offer on a card without a brand only when it's cheaper per kilo or litre | Haakon, on a card titled "Godteri": "this product should be more sensibly named Ahlgrens Biler Original" and "Every product should get its own card, sortable by category … Searchable" |
| #90 | Prices per amount are shown per kilo and per litre (kept per 100 g/ml) | Haakon: "per unit should be shown as x kr / l for liquids and x kr / kg for solids. This is what norwegians are more accustomed to" |
| #91 | The reader keeps the line's own word when the name is more general ("PULVERPADDER 120G" → Godteri, variant Pulverpadder) and only takes a brand written on that line; a card with no brand or variant shows the receipt's words when they say more than its name | Haakon, on cards "Godteri" and "Smak Br.Garlic": "Smak? And what is this godteri? Far too little explanation?" |
| #92 | Each saved line records the naming rules that named it; when the rules change, the idle worker names it again from the receipt text, so better rules mend existing cards (names only, never prices or sizes; every change logged) | Haakon: "Doing manual edits on existing cards should never be a priority in this project. We should rather focus on the system that creates these cards to keep producing high quality cards" |
| #93 | Saved lines are reviewed, not named again: Gemini sees how each was named and keeps it unless it breaks a rule; a review changing over 10 % of a batch saves nothing and stops. The first pass of #92 changed 467 of 1,379 lines, mostly swaps between names that would both do, and was undone from a copy taken just before | Haakon: "do all three" (undo the pass, review mode, a brake) |
