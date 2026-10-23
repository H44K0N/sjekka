// Store chains as recognisable badges: the chain's logo where a free one
// exists (Wikimedia Commons, public domain text logos), otherwise its colours.
// Chain names come from Gemini, so matching is loose ("REMA 1000", "Rema").
const CHAINS = [
    { match: /rema/i, logo: "/chains/rema-1000.svg" },
    { match: /kiwi/i, logo: "/chains/kiwi.png" },
    { match: /meny/i, logo: "/chains/meny.png" },
    { match: /prix/i, logo: "/chains/coop-prix.png" },
    { match: /mega/i, logo: "/chains/coop-mega.svg" },
    { match: /joker/i, logo: "/chains/joker.png" },
    { match: /spar/i, logo: "/chains/spar.svg" },
    // Extra's current logo isn't freely licensed: its colours instead
    { match: /extra/i, colors: ["#d8001d", "#ffe100"] },
    // Others (Bunnpris, Obs, Oda…) show as plain text until we have a logo
];

function chainBadge(name) {
    const badge = document.createElement("span");
    badge.className = "chain";
    badge.title = name || "Ukjent butikk";
    const chain = name && CHAINS.find(c => c.match.test(name));
    if (chain?.logo) {
        const img = document.createElement("img");
        img.src = chain.logo;
        img.alt = name;
        badge.classList.add("logo");
        badge.append(img);
    } else if (chain?.colors) {
        badge.classList.add("colored");
        badge.style.background = chain.colors[0];
        badge.style.color = chain.colors[1];
        badge.textContent = name;
    } else {
        badge.textContent = name || "Ukjent butikk";
    }
    return badge;
}
