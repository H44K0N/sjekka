// Pieces shared by the technical films: scenes, chapter titles, diagram
// nodes with wires and travelling packets, checklists and subtitles. All
// positions are for a 1920x1080 picture.
import { h, place, words, onVoice, sink, W, H, stage } from "./studio.js";

// A scene: a layer that fades in at `from` and out at `to`
export function scene(film, parent, from, to, { fade = 0.35 } = {}) {
  const el = h("div", "layer", parent);
  gsap.set(el, { opacity: 0 });
  film.span(el, from, to + fade);
  film.tl.to(el, { opacity: 1, duration: fade, ease: "power1.out" }, from);
  film.tl.to(el, { opacity: 0, duration: fade, ease: "power1.in" }, to);
  return el;
}

// "02" and a title at the top left, the words rising
export function chapter(film, parent, num, text, at) {
  const box = h("div", "abs", parent);
  place(box, 120, 112);
  const n = h("div", "chapter", box, num);
  n.style.cssText = "font-size:22px;letter-spacing:.2em;color:var(--accent);margin-bottom:10px";
  const block = words(box, text, "");
  block.style.cssText = "font-size:78px;font-weight:700;letter-spacing:-.03em;line-height:1.02";
  film.tl.fromTo(n, { autoAlpha: 0, x: -16 }, { autoAlpha: 1, x: 0, duration: 0.5, ease: "power3.out" }, at);
  film.tl.fromTo(block.spans, { yPercent: 115 }, { yPercent: 0, duration: 0.8, ease: "expo.out", stagger: 0.06 }, at + 0.05);
  return box;
}

// The line as subtitles at the bottom, a sentence at a time, timed by the
// voice (a sentence's share of the line by its length)
export function subs(film, line, parent) {
  const text = film.cues.at(-1).text;
  const parts = text.match(/[^.!?]+[.!?]?\s*/g).map(p => p.trim()).filter(Boolean);
  const total = parts.reduce((a, p) => a + p.length, 0);
  const span = line.end - line.start;
  let used = 0;
  for (const p of parts) {
    const from = line.start + span * used / total;
    used += p.length;
    const to = line.start + span * used / total;
    const el = h("div", "abs", parent, p);
    el.style.cssText = `left:160px;width:${W - 320}px;top:${H - 118}px;text-align:center;font-size:34px;line-height:1.3;color:#d8d1c5;opacity:0`;
    film.tl.fromTo(el, { autoAlpha: 0, y: 10 }, { autoAlpha: 1, y: 0, duration: 0.3, ease: "power2.out" }, from);
    film.tl.to(el, { autoAlpha: 0, duration: 0.2 }, Math.max(from + 0.4, to - 0.12));
  }
}

// A box with a small label and a line of text
export function node(parent, x, y, w, hgt, label, text, cls = "") {
  const n = h("div", `node ${cls}`, parent);
  place(n, x, y, w, hgt);
  h("div", "k", n, label);
  h("div", "v", n, text);
  return n;
}

export const centre = (x, y, w, hgt) => [x + w / 2, y + hgt / 2];

// A line from a to b that draws itself, with an arrowhead
export function wire(film, parent, a, b, at, dur = 0.6, lit = true) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const len = Math.hypot(dx, dy);
  const line = h("div", "abs", parent);
  line.style.cssText = `left:${a[0]}px;top:${a[1] - 1.5}px;width:${len}px;height:3px;transform-origin:0 50%;` +
    `transform:rotate(${Math.atan2(dy, dx)}rad);background:${lit ? "var(--accent)" : "var(--line-strong)"};opacity:.9`;
  const head = h("div", "abs", parent);
  head.style.cssText = `left:${b[0] - 12}px;top:${b[1] - 9}px;width:0;height:0;border-left:16px solid ${lit ? "var(--accent)" : "#4a453e"};` +
    `border-top:9px solid transparent;border-bottom:9px solid transparent;transform-origin:12px 9px;transform:rotate(${Math.atan2(dy, dx)}rad)`;
  film.tl.fromTo(line, { scaleX: 0 }, { scaleX: 1, duration: dur, ease: "power2.inOut" }, at);
  film.tl.fromTo(head, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.15 }, at + dur - 0.1);
  return [line, head];
}

// A glowing dot (or any element) carried along a path of points
export function packet(film, parent, pts, at, dur, el) {
  const p = el || h("div", "packet", parent);
  gsap.set(p, { x: pts[0][0], y: pts[0][1], autoAlpha: 0 });
  film.tl.to(p, { autoAlpha: 1, duration: 0.1 }, at);
  const legs = pts.slice(1).map((q, i) => Math.hypot(q[0] - pts[i][0], q[1] - pts[i][1]));
  const sum = legs.reduce((a, b) => a + b, 0);
  let t = at;
  pts.slice(1).forEach((q, i) => {
    const d = dur * legs[i] / sum;
    film.tl.to(p, { x: q[0], y: q[1], duration: d, ease: "none" }, t);
    t += d;
  });
  film.tl.to(p, { autoAlpha: 0, duration: 0.15 }, t);
  return p;
}

// Lights a node's outline from `at` for `dur`
export function glow(film, n, at, dur = 1.6) {
  film.tl.to(n, { boxShadow: "0 0 0 2px #c9b48f, 0 0 60px rgba(201,180,143,0.22)", duration: 0.25 }, at)
    .to(n, { boxShadow: "0 0 0 1.5px rgba(236,230,220,0.24), 0 0 0 rgba(0,0,0,0)", duration: 0.4 }, at + dur);
}

// Things that fade up in turn
export function stagger(film, els, at, gap = 0.18, dur = 0.55) {
  film.tl.fromTo(els, { autoAlpha: 0, y: 24 }, { autoAlpha: 1, y: 0, duration: dur, ease: "power3.out", stagger: gap }, at);
}

// A checklist row: a ring that fills with a tick, and a line of text
export function check(parent, x, y, w, text) {
  const row = h("div", "abs", parent);
  row.style.cssText = `left:${x}px;top:${y}px;width:${w}px;display:flex;align-items:center;gap:26px;font-size:38px;line-height:1.2`;
  const ring = h("div", "", row, "&#10003;");
  ring.style.cssText = "flex:0 0 58px;height:58px;border-radius:50%;display:flex;align-items:center;justify-content:center;" +
    "font-size:34px;color:var(--ink);background:var(--accent);font-family:Georgia,serif";
  h("div", "", row, text);
  return { row, ring };
}

// A line of code coloured by hand: the parts in <span class="s"> and so on
export function code(parent, x, y, w, html, size = 25) {
  const c = h("div", "code", parent, html);
  place(c, x, y, w);
  c.style.fontSize = `${size}px`;
  return c;
}

export { stage };
