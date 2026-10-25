// Helpers shared by the explore and product pages.
// Prices: "24,90", but a whole amount without the decimals ("15", not "15,00")
const kr = (() => {
    const cents = new Intl.NumberFormat("nb-NO", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const round = new Intl.NumberFormat("nb-NO", { maximumFractionDigits: 0 });
    return { format: v => Math.round(v * 100) % 100 === 0 ? round.format(Math.round(v)) : cents.format(v) };
})();
const whole = new Intl.NumberFormat("nb-NO");

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
}

function daysAgo(dateStr) {
    const date = new Date(dateStr);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    date.setHours(0, 0, 0, 0);
    const diff = Math.round((today - date) / (1000 * 60 * 60 * 24));
    if (Number.isNaN(diff)) return "";
    if (diff <= 0) return "i dag";
    if (diff === 1) return "i går";
    if (diff < 14) return `${diff} dager siden`;
    return date.toLocaleDateString("nb-NO", { day: "numeric", month: "short", year: date.getFullYear() === today.getFullYear() ? undefined : "numeric" });
}

// The last day an offer applies: "t.o.m. søndag", or with the date when it's further off
function until(dateStr) {
    const date = new Date(`${dateStr}T00:00`);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const days = Math.round((date - today) / 864e5);
    if (days <= 0) return "kun i dag";
    if (days === 1) return "t.o.m. i morgen";
    return `t.o.m. ${date.toLocaleDateString("nb-NO", days < 6 ? { weekday: "long" } : { weekday: "long", day: "numeric", month: "short" })}`;
}

// Price of one unit, plus the comparable unit price where there is one.
function prices(p) {
    const each = p.pack_size > 1 ? p.total_price / p.pack_size : p.total_price;
    // Loose produce: the kilo price, and what this one receipt's weight cost
    if (p.by_weight && p.price_per_unit) {
        return { each: `${kr.format(p.price_per_unit * 10)} kr / kg`, unit: `${packSize(p)} for ${kr.format(p.total_price)} kr` };
    }
    let unit = "";
    if (p.price_per_unit && (p.unit === "g" || p.unit === "ml")) {
        unit = `${kr.format(p.price_per_unit)} kr / 100 ${p.unit}`;
    } else if (p.unit === "stk" && p.quantity > 1) {
        // A 12-pack of eggs: what one egg costs
        unit = `${kr.format(each / p.quantity)} kr / stk`;
    } else if (p.unit === "stk" && p.pack_size > 1) {
        unit = `${kr.format(each)} kr / stk`;
    } else if (p.quantity) {
        unit = `${whole.format(p.quantity)} ${p.unit}`;
    }
    return { each: each != null ? `${kr.format(each)} kr` : "–", unit };
}

// A package's size, "600 g", "1 kg", "1,5 l" or "12 stk", so a family
// pizza isn't mistaken for a small one at a big price
function packSize(p) {
    if (p.unit === "stk" && p.quantity > 1) return `${whole.format(p.quantity)} stk`;
    if (!p.quantity || (p.unit !== "g" && p.unit !== "ml")) return "";
    if (p.quantity < 1000) return `${whole.format(p.quantity)} ${p.unit}`;
    return `${whole.format(p.quantity / 1000)} ${p.unit === "g" ? "kg" : "l"}`;
}

// Brand, variant and size on one line, leaving out the size when the variant
// already says it
function describe(p) {
    // Nor for loose produce, whose weight is just what one shopper took
    const size = p.by_weight || /\d\s*(kg|g|ml|cl|dl|l|stk|pk)\b/i.test(p.variant || "") ? "" : packSize(p);
    return [p.brand, p.variant, size].filter(Boolean).join(" · ");
}

const perLabel = per => per === "stk" ? "kr / stk" : `kr / ${per}`;

// What a flyer offer costs: "44,90 kr", "3 for 50 kr" for a multi-buy, or "29,90 kr / kg"
const offerPrice = o => o.pack_size > 1 ? `${o.pack_size} for ${kr.format(o.total_price)} kr`
    : o.by_weight ? `${kr.format(o.per_unit * 10)} kr / kg` : `${kr.format(o.each)} kr`;

// A multi-buy is that price only for whoever takes them all: say so where the
// price is, not only in the flyer's own words under "Detaljer"
function mustBuy(o) {
    if (!(o.pack_size > 1)) return null;
    const tag = el("span", "must-buy", `Må kjøpe ${o.pack_size}`);
    tag.append(el("span", "each", ` · ${kr.format(o.each)} kr stk`));
    tag.title = `Prisen gjelder bare når du kjøper ${o.pack_size}: da koster hver ${kr.format(o.each)} kr`;
    return tag;
}

// A flyer offer's verdict (see app/flyers.py) in a few words, for a tag
function verdictTag(o) {
    const v = o.verdict;
    const pct = v.discount != null ? `${Math.round(Math.abs(v.discount) * 100)} %` : "";
    const text = {
        real: `Ekte tilbud −${pct}`,
        small: `Bare −${pct}`,
        not: v.discount < 0 ? "Dyrere enn vanlig" : "Vanlig pris",
        cheaper_elsewhere: `Billigere hos ${v.cheaper_chain}`,
        cheapest_known: "Lavest kjente pris",
        claimed: `Butikken sier −${pct}`,
    }[v.kind] || "Ikke sjekket ennå";
    return el("span", `verdict-tag ${v.kind}`, text);
}

// The same verdict as a sentence, for the details
function offerVerdict(o) {
    const v = o.verdict, line = el("p", `deal-verdict ${v.kind}`);
    const pct = v.discount != null ? `${Math.round(Math.abs(v.discount) * 100)} %` : "";
    const usual = v.usual != null ? `${kr.format(v.usual)} ${perLabel(v.per)}` : "";
    switch (v.kind) {
        case "real": line.textContent = `${pct} under vanlig pris i butikken, som var ${usual}.`; break;
        case "small": line.textContent = `Bare ${pct} under vanlig pris i butikken, som var ${usual}.`; break;
        case "not": line.textContent = v.discount < 0
            ? `Dyrere enn vanlig pris i butikken, som var ${usual}.` : `Omtrent vanlig pris i butikken (${usual}).`; break;
        case "cheaper_elsewhere":
            line.append("Uten tilbud er den billigere hos ", chainBadge(v.cheaper_chain), ` ${kr.format(v.cheaper_price)} ${perLabel(v.per)}`);
            break;
        case "cheapest_known": line.textContent = "Lavere enn prisene vi kjenner fra andre butikker. Vi har ingen kvittering fra denne butikken å sammenligne med."; break;
        case "claimed": line.textContent = `Kundeavisen sier ${pct} under førpris på ${kr.format(v.pre_price)} kr. Ikke sjekket mot kvitteringer ennå.`; break;
        default: line.textContent = "Ingen kvitteringer å sammenligne med ennå.";
    }
    return line;
}

// Folded-away extra information: a small "Detaljer" that opens in place
function fold(label, ...content) {
    const details = el("details", "fold");
    details.append(el("summary", "", label), ...content);
    return details;
}

// Someone who has seen a page before knows what it's for: its introduction
// folds to the heading, so the search and the prices come first
function foldIntroOnReturn() {
    const intro = document.querySelector(".intro");
    const key = `sjekka.sett${location.pathname}`;
    try {
        if (intro && localStorage.getItem(key)) intro.classList.add("compact");
        localStorage.setItem(key, "1");
    } catch { /* storage blocked: always the full introduction */ }
}
