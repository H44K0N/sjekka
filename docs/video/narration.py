"""The voice-over, one cue per line, in the order the video plays them.

Each line is both the caption and what the voice reads, so they always match.
The voice gets the respellings in PRONOUNCE (an English voice reads
"Sjekka" wrong); everything else should be written so it reads naturally
aloud. Avoid words that depend on context, like "live" (verb or adjective),
and spell acronyms normally: "API" is read better than "A P I".
"""
import re

VOICE = "af_heart"  # Kokoro's highest-rated English voice
SPEED = 1.0

PRONOUNCE = {"Sjekka": "Shecka", "Rema": "Remma"}

CUES = {
    "title": "This is Sjekka. Snap a photo of a grocery receipt, and it becomes part of a shared price database for everyone in Norway.",
    "explore": "The explore page shows the latest price of every product, pulled from receipts people have shared.",
    "search": "Search by product, or by brand.",
    "filter": "Prices are compared per 100 grams, per 100 millilitres, or per item, so you can see what's actually cheap.",
    "upload": "Uploading takes seconds. The photo is checked right in the browser before anything is sent.",
    "thanks": "The receipt is queued instantly. The photo is only sent to Gemini to be read, and deleted once that's done. This preview comes from your own browser.",
    "arch1": "Behind the scenes, it starts in the browser, on a phone or a laptop.",
    "arch2": "Traffic comes in through a Cloudflare tunnel, so the server doesn't expose a single open port.",
    "arch3": "FastAPI checks every photo, fixes its rotation, and rate-limits uploads, because each one costs an AI call.",
    "arch4": "The upload becomes a job in a queue, so the uploader gets an answer right away.",
    "arch5": "A background worker picks up the next job, and backs off and retries if the AI service is busy.",
    "arch6": "Google's Gemini reads the receipt, and has to answer in a strict format: the store, the date, and every line.",
    "arch7": "That answer is validated, unit prices are computed in code instead of trusted to the model, and duplicate receipts are skipped.",
    "arch8": "Then the explore page reads the new prices straight from the database.",
    "result": "And here's the receipt we just uploaded. Its prices are already on the site.",
    "mince": "Gemini wrote the name for minced meat in lowercase, but Sjekka cleans it up, so it joins the product that was already there, with today's price from Rema.",
    "multipack": "Multipacks are split, so you always see the price of a single item.",
    "pipeline1": "Every change ships the same way: a pull request, then linting and 110 tests, including real browser tests. The image is built, smoke-tested, and deployed with an SSH key that can do exactly one thing.",
    "pipeline2": "If the new version isn't healthy, it rolls back on its own. And the database is backed up every night.",
    "end": "Sjekka. Built by Haakon, together with Claude Code. Have you checked today?",
}


def spoken(text):
    """The line as the voice should read it."""
    for word, sound in PRONOUNCE.items():
        text = re.sub(rf"\b{word}\b", sound, text)
    return text
