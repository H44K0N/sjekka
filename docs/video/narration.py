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

# The desktop video: the features, then how it's built
CUES = {
    "title": "This is Sjekka. It turns grocery receipts into a shared price database for Norway, and helps you find the cheapest way to shop.",
    "explore": "The explore page shows the latest price of every product, from receipts people have shared.",
    "search": "Search by product or brand. Prices are compared per 100 grams, per 100 millilitres, or per item, so you can see what's actually cheap.",
    "product": "Every product has its own page: where it's cheapest right now, and how the price has moved in each store.",
    "add": "One click puts it on your shopping list. So does the plus on any product.",
    "list": "The shopping list works out what everything on it costs in each chain, and where you save the most.",
    "estimate": "When a store has no price for something, it's estimated from how expensive that chain is on the products they share. Open a store to see every line.",
    "offers": "The offers page pulls this week's flyers from every chain, and checks each offer against what receipts say the product normally costs.",
    "real": "Some are real deals. Others are the usual price with a red sign. The details are one click away.",
    "upload": "And all of it comes from receipts. Uploading one takes seconds, and the photo is checked right in the browser first.",
    "thanks": "The receipt is queued instantly. The photo is only sent to Gemini to be read, and deleted once that's done.",
    "arch1": "Behind the scenes, it starts in the browser, on a phone or a laptop.",
    "arch2": "Traffic comes in through a Cloudflare tunnel, so the server doesn't expose a single open port.",
    "arch3": "FastAPI checks every photo, fixes its rotation, and rate-limits uploads, because each one costs an AI call.",
    "arch4": "The upload becomes a job in a queue, so the uploader gets an answer right away.",
    "arch5": "A background worker picks up the next job, and backs off and retries if the AI service is busy. Every six hours it also imports the chains' new flyers.",
    "arch6": "Google's Gemini reads the receipt, and has to answer in a strict format: the store, the date, and every line.",
    "arch7": "That answer is validated, unit prices are computed in code instead of trusted to the model, and duplicate receipts are skipped.",
    "arch8": "Then the site reads the new prices straight from the database.",
    "result": "And here's the receipt we just uploaded. Its prices are already on the site, and multipacks are split, so you see the price of a single item.",
    "pipeline1": "Every change ships the same way: a pull request, then linting and over 250 tests, including real browser tests. The image is built, smoke-tested, and deployed with an SSH key that can do exactly one thing.",
    "pipeline2": "If the new version isn't healthy, it rolls back on its own. And the database is backed up every night.",
    "end": "Sjekka. Built by Haakon, together with Claude Code. Have you checked today?",
}

# The phone video: just the features, the way most people will use them
MOBILE = {
    "m_intro": "This is Sjekka on your phone: real grocery prices from shared receipts, so you can shop for less.",
    "m_explore": "Every product, with its latest price and where it was bought.",
    "m_add": "Tap the plus to put things on your shopping list.",
    "m_list": "Your list shows which chain is cheapest for all of it, and how much you save.",
    "m_offers": "This week's flyer offers are checked against real receipts, so you can tell which deals are real.",
    "m_upload": "Done shopping? Snap the receipt, and your prices help everyone else.",
    "m_end": "Sjekka. Have you checked today?",
}

def spoken(text):
    """The line as the voice should read it."""
    for word, sound in PRONOUNCE.items():
        text = re.sub(rf"\b{word}\b", sound, text)
    return text
