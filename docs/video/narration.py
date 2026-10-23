"""The voice-over, one cue per line, in the order the video plays them.

`text` is shown as the caption; `say`, when given, is what the voice reads
(spelled so an English voice pronounces it right: Sjekka sounds like "Shecka",
Rema like "Remma"). Spell acronyms normally: "A P I" is read worse than "API".
"""

VOICE = "af_heart"  # Kokoro's highest-rated English voice
SPEED = 1.0

CUES = {
    "title": dict(
        text="This is Sjekka. Snap a photo of a grocery receipt, and it becomes part of a shared price database for everyone in Norway.",
        say="This is Shecka. Snap a photo of a grocery receipt, and it becomes part of a shared price database for everyone in Norway.",
    ),
    "explore": dict(
        text="The explore page shows the latest price of every product, pulled from receipts people have shared.",
    ),
    "search": dict(
        text="Search by product, or by brand.",
    ),
    "filter": dict(
        text="Prices are compared per 100 grams, per 100 millilitres or per item, so you can see what's actually cheap.",
        say="Prices are compared per hundred grams, per hundred millilitres, or per item, so you can see what's actually cheap.",
    ),
    "upload": dict(
        text="Uploading takes seconds. The photo is checked right in the browser before anything is sent.",
    ),
    "thanks": dict(
        text="The receipt is queued instantly. The photo stays private: the server never shows it to anyone. This copy comes from your own browser.",
    ),
    "arch1": dict(text="Behind the scenes, it starts in the browser, on a phone or a laptop."),
    "arch2": dict(text="Traffic comes in through a Cloudflare tunnel, so the server doesn't expose a single open port."),
    "arch3": dict(text="FastAPI checks every photo, fixes its rotation, and rate-limits uploads, because each one costs an AI call."),
    "arch4": dict(text="The upload becomes a job in a queue, so the uploader gets an answer right away."),
    "arch5": dict(text="A background worker picks up the next job, and backs off and retries if the AI service is busy."),
    "arch6": dict(text="Google's Gemini reads the receipt, and has to answer in a strict format: the store, the date, and every line."),
    "arch7": dict(text="That answer is validated, unit prices are computed in code instead of trusted to the model, and duplicate receipts are skipped."),
    "arch8": dict(text="Then the explore page reads the new prices straight from the database."),
    "result": dict(text="And here's the receipt we just uploaded. Its prices are already live."),
    "mince": dict(
        text="Gemini wrote “kjøttdeig” in lowercase, but Sjekka cleans up the name, so it joins the product that was already there, with today's price from Rema.",
        say="Gemini wrote the name for minced meat in lowercase, but Shecka cleans it up, so it joins the product that was already there, with today's price from Remma.",
    ),
    "multipack": dict(text="Multipacks are split, so you always see the price of a single item."),
    "pipeline1": dict(
        text="Every change ships the same way: a pull request, then linting and 110 tests, including real browser tests. The image is built, smoke-tested, and deployed with an SSH key that can do exactly one thing.",
        say="Every change ships the same way. A pull request, then linting and a hundred and ten tests, including real browser tests. The image is built, smoke-tested, and deployed with an S S H key that can do exactly one thing.",
    ),
    "pipeline2": dict(text="If the new version isn't healthy, it rolls back on its own. And the database is backed up every night."),
    "end": dict(
        text="Sjekka. Built by Haakon, together with Claude Code. Har du sjekka i dag? Have you checked today?",
        say="Shecka. Built by Haakon, together with Claude Code. Have you checked today?",
    ),
}
