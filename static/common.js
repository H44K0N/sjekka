// Helpers shared by the explore and product pages.
const kr = new Intl.NumberFormat("nb-NO", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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
    let unit = "";
    if (p.price_per_unit && (p.unit === "g" || p.unit === "ml")) {
        unit = `${kr.format(p.price_per_unit)} kr / 100 ${p.unit}`;
    } else if (p.unit === "stk" && p.pack_size > 1) {
        unit = `${kr.format(each)} kr / stk`;
    } else if (p.quantity) {
        unit = `${whole.format(p.quantity)} ${p.unit}`;
    }
    return { each: each != null ? `${kr.format(each)} kr` : "–", unit };
}

const perLabel = per => per === "stk" ? "kr / stk" : `kr / ${per}`;

// What a flyer offer's verdict (see app/flyers.py) says, as a line of text
function offerVerdict(o) {
    const v = o.verdict, line = el("p", `deal-verdict ${v.kind}`);
    const pct = v.discount != null ? `${Math.round(Math.abs(v.discount) * 100)} %` : "";
    const usual = v.usual != null ? `${kr.format(v.usual)} ${perLabel(v.per)}` : "";
    switch (v.kind) {
        case "real": line.textContent = `Ekte tilbud: ${pct} under vanlig pris på ${usual}`; break;
        case "small": line.textContent = `Bare ${pct} under vanlig pris på ${usual}`; break;
        case "not": line.textContent = v.discount < 0
            ? `Dyrere enn vanlig pris på ${usual}` : `Omtrent vanlig pris (${usual})`; break;
        case "cheaper_elsewhere":
            line.append("Billigere uten tilbud hos ", chainBadge(v.cheaper_chain), ` ${kr.format(v.cheaper_price)} ${perLabel(v.per)}`);
            break;
        case "cheapest_known": line.textContent = "Lavere enn prisene vi kjenner fra andre butikker"; break;
        case "claimed": line.textContent = `Butikken sier ${pct} under førpris på ${kr.format(v.pre_price)} kr. Ikke sjekket mot kvitteringer ennå`; break;
        default: line.textContent = "Ingen kvitteringer å sammenligne med ennå";
    }
    return line;
}
