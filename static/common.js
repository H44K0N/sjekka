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
