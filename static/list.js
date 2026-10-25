// The shopping list, kept in this browser only: [{ name, count, brand?, variant? }].
// An entry with a brand is that item ("Brus", Coca-Cola, Zero); one without
// is the cheapest of the product, whichever brand that is. Functions take
// either a product name or such an entry.
// Storage can be missing or blocked (private windows), so every access is
// guarded and the list then simply lasts until the tab closes.
const ShoppingList = (() => {
    const KEY = "sjekka.handleliste";
    let memory = [];

    function read() {
        try {
            const items = JSON.parse(localStorage.getItem(KEY) || "[]");
            return Array.isArray(items) ? items.filter(i => i && typeof i.name === "string" && i.count > 0) : [];
        } catch {
            return memory;
        }
    }
    function write(items) {
        memory = items;
        try { localStorage.setItem(KEY, JSON.stringify(items)); } catch { /* kept in memory */ }
        showCount();
    }

    const entry = what => typeof what === "string" ? { name: what } : what;
    // How the server and a shared link name an entry: "Brus" or "Brus|Coca-Cola|Zero"
    const keyOf = what => {
        const e = entry(what);
        return e.brand ? `${e.name}|${e.brand}|${e.variant || ""}` : e.name;
    };
    const fromKey = key => {
        const [name, brand, variant] = key.split("|");
        return brand ? { name, brand, variant: variant || null } : { name };
    };
    // What a person calls it: "Coca-Cola Zero", "Tine lettmelk", or the product
    const label = what => {
        const e = entry(what);
        return e.brand ? `${e.brand} ${e.variant || e.name.toLowerCase()}` : e.name;
    };
    const find = (items, what) => {
        const key = keyOf(what).toLowerCase();
        return items.find(i => keyOf(i).toLowerCase() === key);
    };

    function add(what, count = 1) {
        const items = read();
        const existing = find(items, what);
        if (existing) existing.count = Math.min(existing.count + count, 99);
        else {
            const { name, brand, variant } = entry(what);
            items.push(brand ? { name, count: Math.min(count, 99), brand, variant: variant || null } : { name, count: Math.min(count, 99) });
        }
        write(items);
    }
    function setCount(what, count) {
        const items = read();
        const item = find(items, what);
        if (!item) return;
        item.count = Math.max(1, Math.min(count, 99));
        write(items);
    }
    function remove(what) {
        const key = keyOf(what).toLowerCase();
        write(read().filter(i => keyOf(i).toLowerCase() !== key));
    }
    const has = what => Boolean(find(read(), what));

    // The round + on product cards: puts the product on the list, or takes it off
    function toggle(what) {
        const name = label(what);
        const button = document.createElement("button");
        button.type = "button";
        button.className = "list-toggle";
        button.setAttribute("aria-label", `Legg ${name} på handlelisten`);
        const show = () => button.setAttribute("aria-pressed", String(has(what)));
        button.addEventListener("click", () => {
            if (has(what)) remove(what); else add(what);
            show();
        });
        show();
        return button;
    }

    // The header link shows how many products are on the list
    function showCount() {
        const n = read().length;
        for (const badge of document.querySelectorAll("[data-list-count]")) {
            badge.textContent = n ? String(n) : "";
            badge.hidden = !n;
        }
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", showCount);
    else showCount();
    // Another tab changed the list
    addEventListener("storage", e => { if (e.key === KEY) showCount(); });

    return { read, write, add, setCount, remove, has, toggle, showCount, keyOf, fromKey, label };
})();
