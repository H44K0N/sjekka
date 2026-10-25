// The films' runtime: one GSAP timeline per film, seeked frame by frame by
// render.py, so every frame is exact and motion is perfectly even.
//
// A film script builds the timeline with explicit times, computed from the
// voice-over's real lengths (voice.py's timing.json): say() places a line
// and returns when it ends and when each word is spoken, fx() places a
// sound effect, beat() snaps a cut to the music. finish() publishes what
// the mixer needs (window.__film) and the seek function (window.__seek).
//
// Pages of the app are stills from capture.py, shown in a Device (a browser
// window or a phone); a Device can scroll, switch state, point, tap and
// lift an element off the screen. Everything moves through the timeline:
// no CSS animations, so nothing depends on the clock.

const Q = new URLSearchParams(location.search);
export const FORMAT = Q.get("format") === "mobile" ? "mobile" : "desktop";
export const PHONE = FORMAT === "mobile";
export const W = PHONE ? 1080 : 1920;
export const H = PHONE ? 1920 : 1080;

export const stage = document.getElementById("stage");
stage.style.width = `${W}px`;
stage.style.height = `${H}px`;

let timing = {}, shots = {}, beats = {};

export async function load(film) {
  // Layout is measured while the film is built, so the fonts must be in first
  await Promise.all(["400 20px Libron", "700 20px Libron", "400 20px Mono", "700 20px Mono"].map(f => document.fonts.load(f)));
  const get = async (url, fallback) => { try { const r = await fetch(url); return r.ok ? r.json() : fallback; } catch { return fallback; } };
  [timing, shots, beats] = await Promise.all([get(`/voice/${film}/timing.json`, {}), get(`/shots/${FORMAT}/index.json`, {}), get("/music/beats.json", {})]);
  return { timing, shots, beats };
}

// ---- elements ----

export function h(tag, cls, parent, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  if (parent) parent.append(e);
  return e;
}

export function layer(name) {
  const e = h("div", `layer ${name || ""}`, stage);
  return e;
}

export function place(e, x, y, w, hgt) {
  Object.assign(e.style, { left: `${x}px`, top: `${y}px` });
  if (w != null) e.style.width = `${w}px`;
  if (hgt != null) e.style.height = `${hgt}px`;
  return e;
}

// Text set as words, each in a mask: "Every week, the *big chains*" (stars mark the accent)
export function words(parent, text, cls = "") {
  const block = h("div", `words ${cls}`, parent);
  const spans = [];
  let accent = false;
  for (const part of text.split(/(\s+|<br>)/)) {
    if (part === "<br>") { h("br", "", block); continue; }
    if (!part.trim()) { block.append(" "); continue; }
    let w = part;
    const open = w.startsWith("*"), close = w.endsWith("*");
    w = w.replace(/^\*|\*$/g, "");
    if (open) accent = true;
    const outer = h("span", "word", block);
    const inner = h("span", accent ? "accent" : "", outer);
    inner.textContent = w;
    spans.push(inner);
    if (close) accent = false;
  }
  block.spans = spans;
  return block;
}

// ---- the film ----

export class Film {
  constructor(name) {
    this.name = name;
    this.tl = gsap.timeline({ paused: true });
    this.cues = [];
    this.sfx = [];
    this.music = null;
    this.cut = [];
  }

  // A voice line at `at`: returns {start, end, word(i), at(text)}
  say(key, at) {
    const t = timing[key];
    if (!t) throw new Error(`no voice line ${key}`);
    this.cues.push({ key, at: round(at), seconds: t.seconds, text: t.text });
    const found = (text, from = 0) => {
      const want = text.toLowerCase().replace(/[^a-z0-9']/g, "");
      const i = t.words.findIndex((w, j) => j >= from && w.w.toLowerCase().replace(/[^a-z0-9']/g, "").startsWith(want));
      return i < 0 ? null : i;
    };
    return {
      start: at, end: at + t.seconds, words: t.words.map(w => ({ ...w, s: at + w.s, e: at + w.e })),
      word: i => at + t.words[Math.min(i, t.words.length - 1)].s,
      at: (text, from) => { const i = found(text, from); return i == null ? at : at + t.words[i].s; },
      after: (text, from) => { const i = found(text, from); return i == null ? at + t.seconds : at + t.words[i].e; },
    };
  }

  fx(name, at, gain = 1) { this.sfx.push({ name, at: round(at), gain }); }

  // Music: `track` played from `from` seconds into it, starting at film time `at`
  score(track, at, from, opts = {}) {
    this.music = { track, at: round(at), from, gain: opts.gain ?? 1, fadeIn: opts.fadeIn ?? 0.05, ...opts };
    const b = beats[track] || { beats: [], downbeats: [] };
    this.beats = b.beats.filter(x => x >= from).map(x => at + x - from);
    this.downbeats = b.downbeats.filter(x => x >= from).map(x => at + x - from);
  }

  // The first beat (or downbeat, or bar) at or after t, to cut on
  beat(t, kind = "beat", slack = 0.04) {
    const list = kind === "down" ? this.downbeats : this.beats;
    if (!list || !list.length) return t;
    const next = list.find(x => x >= t - slack);
    return next ?? t;
  }

  // Shows a layer from `from` to `to` (hidden either side)
  span(el, from, to) {
    this.tl.set(el, { visibility: "visible" }, from);
    if (to != null) this.tl.set(el, { visibility: "hidden" }, to);
  }

  finish(duration) {
    this.duration = round(duration);
    this.tl.set({}, {}, duration);
    window.__film = { name: this.name, format: FORMAT, width: W, height: H, duration: this.duration,
      cues: this.cues, sfx: this.sfx.sort((a, b) => a.at - b.at), music: this.music };
    window.__seek = t => { this.tl.seek(t, false); grainAt(t); };
    window.__seek(0);
  }
}

const round = t => Math.round(t * 1000) / 1000;

// ---- film grain: noise that moves every frame, decided by the time ----

let grain;
export function addGrain() {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const g = c.getContext("2d"), img = g.createImageData(256, 256);
  let seed = 7;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  for (let i = 0; i < img.data.length; i += 4) {
    const v = 255 * rnd();
    img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
    img.data[i + 3] = 255;
  }
  g.putImageData(img, 0, 0);
  grain = h("div", "grain", stage);
  grain.style.backgroundImage = `url(${c.toDataURL()})`;
}
function grainAt(t) {
  if (!grain) return;
  const f = Math.floor(t * 24);  // grain at film rate, 24 a second
  grain.style.transform = `translate(${(f * 73) % 256 - 128}px, ${(f * 151) % 256 - 128}px)`;
}

// ---- a device showing the app ----

const ARROW = "data:image/svg+xml," + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="26" height="34" viewBox="0 0 26 34"><path d="M2 2 L2 27 L8.5 21 L13 31 L17 29 L12.5 19.5 L21 19.5 Z" fill="#ece6dc" stroke="#0d0c0b" stroke-width="2" stroke-linejoin="round"/></svg>`);

export class Device {
  // kind: "browser" (1440x900 page) or "phone" (390x844 page). The device is
  // laid out at 1:1 and scaled by `scale`, its top-left at (x, y).
  constructor(film, parent, { x = 0, y = 0, scale = 1, kind = PHONE ? "phone" : "browser", url = "sjekka.tischbein.dev" } = {}) {
    this.film = film;
    this.kind = kind;
    const phone = kind === "phone";
    this.view = phone ? { w: 390, h: 844 } : { w: 1440, h: 900 };
    this.top = phone ? 0 : 52;
    this.pad = phone ? 14 : 0;
    this.el = h("div", phone ? "phone" : "browser", parent);
    this.el.style.transformOrigin = "0 0";
    if (phone) {
      h("div", "island", this.el);
    } else {
      const bar = h("div", "bar", this.el);
      for (let i = 0; i < 3; i++) h("span", "dot", bar);
      this.urlEl = h("div", "url", bar, url);
    }
    this.screen = h("div", "screen", this.el);
    place(this.screen, null, null, this.view.w, this.view.h);
    this.screen.style.position = "relative";
    this.page = h("div", "page", this.screen);
    this.states = {};
    this.state = null;
    this.scrollY = 0;
    this.pointer = h(phone ? "div" : "img", phone ? "touch" : "cursor", this.page);
    if (!phone) this.pointer.src = ARROW;
    gsap.set(this.pointer, { x: this.view.w * 0.6, y: this.view.h * 0.7, autoAlpha: 0 });
    this.ripple = h("div", "ripple", this.page);
    this.ring = h("div", "ring", this.page);
    this.x = x; this.y = y; this.scale = scale;
    gsap.set(this.el, { x, y, scale });
  }

  get width() { return (this.view.w + 2 * this.pad) * this.scale; }
  get height() { return (this.view.h + this.top + 2 * this.pad) * this.scale; }

  img(state) {
    if (this.states[state]) return this.states[state];
    const info = shots[state];
    if (!info) throw new Error(`no capture ${state}`);
    const i = h("img", "state", this.page);
    i.src = `/shots/${this.kind === "phone" ? "mobile" : "desktop"}/${state}.png`;
    i.style.width = `${info.width}px`;
    gsap.set(i, { autoAlpha: 0 });
    this.page.prepend(i);
    this.states[state] = i;
    return i;
  }

  // Switches to `state` at `at`, dissolving over `fade` seconds
  show(state, at, fade = 0.18) {
    const next = this.img(state);
    const tl = this.film.tl;
    tl.to(next, { autoAlpha: 1, duration: fade, ease: "none" }, at);
    for (const [name, other] of Object.entries(this.states)) {
      if (name !== state) tl.to(other, { autoAlpha: 0, duration: fade, ease: "none" }, at + (fade ? fade * 0.6 : 0));
    }
    this.state = state;
    return at + fade;
  }

  // Goes to another page: the old one dips out, the new one comes in from
  // the top of the page, and the address bar changes with it
  navigate(state, at, { path = null, dip = 0.14 } = {}) {
    const tl = this.film.tl;
    const old = this.states[this.state];
    if (old) tl.to(old, { autoAlpha: 0, duration: dip, ease: "power1.in" }, at);
    tl.set(this.page, { y: 0 }, at + dip);
    this.scrollY = 0;
    const next = this.img(state);
    tl.fromTo(next, { autoAlpha: 0, y: 14 }, { autoAlpha: 1, y: 0, duration: 0.32, ease: "power2.out" }, at + dip);
    for (const [name, other] of Object.entries(this.states)) if (name !== state && other !== old) tl.set(other, { autoAlpha: 0 }, at + dip);
    if (path != null && this.urlEl) tl.set(this.urlEl, { textContent: `sjekka.tischbein.dev${path}` }, at + dip);
    this.state = state;
    return at + dip + 0.32;
  }

  box(key, state = this.state) {
    const b = shots[state]?.boxes?.[key];
    if (!b) throw new Error(`no box ${key} in ${state}`);
    return b;
  }

  // Scrolls so that page y sits at the top of the screen (or the element `key` at `offset` from the top)
  scroll(target, at, dur = 1.0, { offset = 60, ease = "power3.inOut" } = {}) {
    let y = typeof target === "number" ? target : this.box(target)[1] - offset;
    const info = shots[this.state];
    y = Math.max(0, Math.min(y, info.height - this.view.h));
    this.film.tl.to(this.page, { y: -y, duration: dur, ease }, at);
    this.scrollY = y;
    return at + dur;
  }

  center(key, state) { const [x, y, w, hh] = this.box(key, state); return [x + w / 2, y + hh / 2]; }

  // The pointer glides to an element (desktop: near its left, phones: its middle)
  point(key, at, dur = 0.7, { dx = 0, dy = 0 } = {}) {
    const [x, y, w, hh] = this.box(key);
    const px = this.kind === "phone" ? x + w / 2 : x + Math.min(w / 2, 34);
    const tl = this.film.tl;
    tl.to(this.pointer, { autoAlpha: 1, duration: 0.25 }, at);
    tl.to(this.pointer, { x: px + dx, y: y + hh / 2 + dy, duration: dur, ease: "power3.inOut" }, at);
    return at + dur;
  }

  // A click or tap at `at`: the pointer presses, a ring spreads, a sound
  tap(at) {
    const tl = this.film.tl;
    tl.to(this.pointer, { scale: this.kind === "phone" ? 0.72 : 0.84, duration: 0.08, ease: "power2.out" }, at)
      .to(this.pointer, { scale: 1, duration: 0.22, ease: "back.out(3)" }, at + 0.09);
    tl.set(this.ripple, { x: () => gsap.getProperty(this.pointer, "x"), y: () => gsap.getProperty(this.pointer, "y"), scale: 0.4, autoAlpha: 0.9 }, at)
      .to(this.ripple, { scale: 2.3, autoAlpha: 0, duration: 0.55, ease: "power2.out" }, at);
    this.film.fx(this.kind === "phone" ? "tap" : "click", at);
    return at + 0.1;
  }

  hidePointer(at) { this.film.tl.to(this.pointer, { autoAlpha: 0, duration: 0.3 }, at); }

  // A champagne ring around an element, from `at` for `dur`
  highlight(key, at, dur = 1.6, pad = 8) {
    const [x, y, w, hh] = this.box(key);
    const tl = this.film.tl;
    tl.set(this.ring, { x: x - pad, y: y - pad, width: w + 2 * pad, height: hh + 2 * pad, scale: 1.08 }, at)
      .to(this.ring, { autoAlpha: 1, scale: 1, duration: 0.35, ease: "power3.out" }, at)
      .to(this.ring, { autoAlpha: 0, duration: 0.3 }, at + dur);
    return at + 0.35;
  }

  // A copy of an element that lifts off the screen towards the viewer
  lift(key, parent, at, { scale = 1.25, hold = 2.0, state = this.state, dx = 0, dy = 0 } = {}) {
    const [x, y, w, hh] = this.box(key, state);
    const info = shots[state];
    const card = h("div", "abs", parent);
    const src = `/shots/${this.kind === "phone" ? "mobile" : "desktop"}/${state}.png`;
    Object.assign(card.style, { width: `${w}px`, height: `${hh}px`, borderRadius: "14px", backgroundImage: `url("${src}")`,
      backgroundSize: `${info.width}px ${info.height}px`, backgroundPosition: `${-x}px ${-y}px`, transformOrigin: "50% 50%",
      boxShadow: "0 30px 80px rgba(0,0,0,0.7), 0 0 0 1px rgba(236,230,220,0.18)", visibility: "hidden" });
    // Where the element is on the stage now
    const sx = this.x + this.scale * (this.pad + x), sy = this.y + this.scale * (this.pad + this.top + y - this.scrollY);
    gsap.set(card, { x: sx, y: sy, scale: this.scale, transformOrigin: "0 0" });
    const tl = this.film.tl;
    tl.set(card, { visibility: "visible" }, at)
      .to(card, { x: sx + dx - (w * this.scale * (scale - 1)) / 2, y: sy + dy - (hh * this.scale * (scale - 1)) / 2,
        scale: this.scale * scale, duration: 0.6, ease: "power3.out" }, at)
      .to(card, { x: sx, y: sy, scale: this.scale, duration: 0.5, ease: "power3.inOut" }, at + hold)
      .set(card, { visibility: "hidden" }, at + hold + 0.5);
    return card;
  }

  // Where a page point is on the stage, given the scroll in effect then
  onStage(px, py) {
    return [this.x + this.scale * (this.pad + px), this.y + this.scale * (this.pad + this.top + py - this.scrollY)];
  }
}

// ---- camera: moves and zooms a group as if filmed ----

export class Camera {
  constructor(film, el) {
    this.film = film;
    this.el = el;
    el.style.transformOrigin = `${W / 2}px ${H / 2}px`;
    gsap.set(el, { x: 0, y: 0, scale: 1 });
  }
  // Brings stage point (px, py) to the screen point (qx, qy) at zoom z
  frame(px, py, z, at, dur = 1.2, { qx = W / 2, qy = H / 2, ease = "power3.inOut", rx = 0, ry = 0 } = {}) {
    const x = qx - W / 2 - z * (px - W / 2), y = qy - H / 2 - z * (py - H / 2);
    this.film.tl.to(this.el, { x, y, scale: z, rotationX: rx, rotationY: ry, duration: dur, ease }, at);
    return at + dur;
  }
  reset(at, dur = 1.0, ease = "power3.inOut") {
    this.film.tl.to(this.el, { x: 0, y: 0, scale: 1, rotationX: 0, rotationY: 0, duration: dur, ease }, at);
    return at + dur;
  }
}

// Puts a block's middle at y (it's measured, so call after its text is set)
export function middle(el, y) {
  el.style.top = `${Math.round(y - el.offsetHeight / 2)}px`;
  return el;
}

// Words rising out of their masks, one after another
export function rise(film, block, at, { stagger = 0.06, dur = 0.8, ease = "expo.out" } = {}) {
  film.tl.fromTo(block.spans, { yPercent: 115 }, { yPercent: 0, duration: dur, ease, stagger }, at);
  return at + dur + stagger * (block.spans.length - 1);
}
// Each word as it is spoken
export function onVoice(film, block, line, { dur = 0.55, lead = 0.06, ease = "expo.out" } = {}) {
  block.spans.forEach((s, i) => {
    const t = (line.words[i] ? line.words[i].s : line.end) - lead;
    film.tl.fromTo(s, { yPercent: 115 }, { yPercent: 0, duration: dur, ease }, t);
  });
}
export function sink(film, block, at, { stagger = 0.03, dur = 0.5 } = {}) {
  film.tl.to(block.spans, { yPercent: -115, duration: dur, ease: "power3.in", stagger }, at);
  return at + dur + stagger * (block.spans.length - 1);
}

// A number that counts up from `from` to `to`, Norwegian style ("287,88")
export function count(film, el, from, to, at, dur, { decimals = 0, suffix = "", prefix = "" } = {}) {
  const fmt = new Intl.NumberFormat("nb-NO", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  const o = { v: from };
  el.textContent = prefix + fmt.format(from) + suffix;
  film.tl.to(o, { v: to, duration: dur, ease: "power2.out", onUpdate: () => { el.textContent = prefix + fmt.format(o.v) + suffix; } }, at);
}

// Waits for fonts and images, so the first frame is already complete
export async function ready() {
  await document.fonts.ready;
  await Promise.all([...document.images].map(i => i.decode().catch(() => {})));
  const bgs = [...document.querySelectorAll("[style*='background-image']")].map(e => e.style.backgroundImage.match(/url\("?(.*?)"?\)/)?.[1]).filter(Boolean);
  await Promise.all([...new Set(bgs)].map(src => { const i = new Image(); i.src = src; return i.decode().catch(() => {}); }));
  window.__ready = true;
}
