// The shopping list, kept in this browser only: [{ name, count }].
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

    const find = (items, name) => items.find(i => i.name.toLowerCase() === name.toLowerCase());

    function add(name, count = 1) {
        const items = read();
        const existing = find(items, name);
        if (existing) existing.count = Math.min(existing.count + count, 99);
        else items.push({ name, count: Math.min(count, 99) });
        write(items);
    }
    function setCount(name, count) {
        const items = read();
        const item = find(items, name);
        if (!item) return;
        item.count = Math.max(1, Math.min(count, 99));
        write(items);
    }
    function remove(name) {
        write(read().filter(i => i.name.toLowerCase() !== name.toLowerCase()));
    }
    const has = name => Boolean(find(read(), name));

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

    return { read, write, add, setCount, remove, has, showCount };
})();
