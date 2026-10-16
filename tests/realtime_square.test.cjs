const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "../web/reference-ui.css"), "utf8");
const code = source.slice(source.indexOf("function squareCanRefresh("), source.indexOf("function invitationStatusLabel("));

function node() {
  const classes = new Set(), listeners = {};
  return {
    listeners, dataset: {}, innerHTML: "", textContent: "", disabled: false, open: false,
    classList: { add(c) { classes.add(c); }, remove(c) { classes.delete(c); }, contains(c) { return classes.has(c); }, toggle(c, on) { on ? classes.add(c) : classes.delete(c); } },
    addEventListener(name, fn, options) { listeners[name] = fn; if (options) listeners[`${name}Options`] = options; },
    querySelectorAll() { return []; }, querySelector() { return null; },
    setAttribute(name, value) { this[name] = value; }, reset() {}, close() { this.open = false; }, showModal() { this.open = true; },
  };
}

function item(id, count = 1) {
  return { activity: { id, participant_count: count, starts_at: new Date(Date.now() + 86_400_000).toISOString() } };
}

function setup() {
  const nodes = new Map();
  const get = (id) => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  const form = get("#square-filter-form");
  form.elements = { category: { value: "" }, date: { value: "" } };
  const search = get("#square-search-form");
  search.elements = { search: node() };
  search.elements.search.value = "";
  const cancel = node();
  get("#square-filter-dialog").querySelectorAll = () => [cancel];
  const timers = new Map(); let timerId = 0;
  const window = { scrollY: 0, listeners: {}, setTimeout(fn) { timers.set(++timerId, fn); return timerId; }, clearTimeout(id) { timers.delete(id); }, setInterval(fn) { timers.set(++timerId, fn); return timerId; }, clearInterval(id) { timers.delete(id); }, addEventListener(name, fn) { this.listeners[name] = fn; }, dispatchEvent() {} };
  const state = { token: "token", user: { id: "one" }, squareItems: [], squareOffset: 0, squareHasMore: false };
  const requests = [];
  const context = vm.createContext({
    state, elements: { squareFilterForm: form, squareList: get("#square-list"), squareLoadMore: get("#square-load-more") },
    document: { hidden: false, activeElement: null, listeners: {}, querySelector: get, addEventListener(name, fn) { this.listeners[name] = fn; } },
    window, navigator: { onLine: true }, AbortController, URLSearchParams, Date, API_ROOT: "/api/v1", CSS: { escape: String },
    escapeHtml: String, formatApiError: (_, fallback) => fallback, logout() { context.loggedOut = true; },
    async fetch(url, options) { requests.push({ url, options }); return { ok: true, json: async () => ({ items: [], categories: [], has_more: false, next_offset: null }) }; },
  });
  vm.runInContext(source.slice(source.indexOf("const squareLive ="), source.indexOf("const COLLEGES =")), context);
  vm.runInContext(`${code}\nglobalThis.live = squareLive;`, context);
  let renders = 0;
  context.renderSquare = () => { renders += 1; };
  context.live.active = true;
  context.live.ownerKey = "token:one";
  context.live.loadedFilterKey = JSON.stringify(context.live.filters);
  return { context, state, live: context.live, requests, timers, nodes, get, form, cancel, renders: () => renders };
}

function response(items, { more = false, next = null } = {}) {
  return { ok: true, json: async () => ({ items, categories: ["跑步"], has_more: more, next_offset: next }) };
}

test("compact toolbar and native filter sheet retain accessible explicit controls", () => {
  const toolbar = html.slice(html.indexOf('<form id="square-search-form"'), html.indexOf("</form>", html.indexOf('<form id="square-search-form"')) + 7);
  assert.match(toolbar, /type="search"/);
  assert.doesNotMatch(toolbar, /name="category"|name="date"/);
  assert.match(html, /<dialog id="square-filter-dialog"[^>]+aria-labelledby="square-filter-heading"/);
  assert.match(html, /data-square-filter-cancel/);
  assert.match(html, /aria-label="刷新活动"/);
  assert.match(css, /\.square-live-publish[^}]+bottom: calc\(var\(--ref-nav-height/);
  assert.match(css, /#panel-square \{ animation-name: square-live-enter/);
  assert.match(code, /new CustomEvent\("dazi:publish-from-square"\)/);
});

test("relative publication time handles elapsed time and bad dates", () => {
  const { context } = setup();
  const now = Date.now();
  assert.equal(context.squareRelativeTime(new Date(now - 120_000).toISOString(), now), "2 分钟前发布");
  assert.equal(context.squareRelativeTime("bad", now), "刚刚发布");
});

test("polling prompts for new IDs without inserting or reordering the current feed", async () => {
  const env = setup();
  env.state.squareItems = [item("old"), item("older")];
  env.state.squareOffset = 2;
  env.context.fetch = async () => response([item("new"), item("older", 2), item("old", 2)]);
  await env.context.loadSquare(false, { probe: true });
  assert.deepEqual(Array.from(env.state.squareItems, (x) => x.activity.id), ["old", "older"]);
  assert.equal(env.state.squareItems[0].activity.participant_count, 2);
  assert.deepEqual(Array.from(env.live.pendingIds), ["new"]);
  assert.match(env.get("#square-new-items").textContent, /1 条新活动/);
});

test("polling silently removes unavailable cards and leaves an untouched feed's DOM alone", async () => {
  const env = setup();
  const keep = item("keep");
  env.state.squareItems = [item("full"), keep];
  env.context.fetch = async () => response([keep]);
  await env.context.loadSquare(false, { probe: true });
  assert.deepEqual(Array.from(env.state.squareItems, (x) => x.activity.id), ["keep"]);
  const renders = env.renders();
  await env.context.loadSquare(false, { probe: true });
  assert.equal(env.renders(), renders);
});

test("filter drafts and cancel do not affect committed polling queries", async () => {
  const env = setup();
  env.context.initializeSquareLive();
  env.live.filters.category = "跑步";
  env.live.loadedFilterKey = JSON.stringify(env.live.filters);
  env.get("#square-filter-open").listeners.click();
  env.form.elements.category.value = "吃饭";
  env.form.elements.date.value = "2030-01-01";
  env.cancel.listeners.click();
  assert.equal(env.live.filters.category, "跑步");
  await env.context.loadSquare(false, { probe: true });
  assert.match(env.requests[0].url, /category=%E8%B7%91%E6%AD%A5/);
  assert.doesNotMatch(env.requests[0].url, /2030/);
  env.form.listeners.submit({ preventDefault() {} });
  await new Promise(setImmediate);
  assert.equal(env.live.filters.category, "吃饭");
  assert.equal(env.live.filters.date, "2030-01-01");
});

test("late old-filter and old-account responses cannot replace current cards or log out a new account", async () => {
  const env = setup();
  let resolveOld;
  env.context.fetch = () => new Promise((resolve) => { resolveOld = resolve; });
  const old = env.context.loadSquare(false);
  env.live.filters.search = "新的条件";
  env.context.fetch = async () => response([item("latest")]);
  await env.context.loadSquare(false);
  resolveOld(response([item("old-filter")]));
  await old;
  assert.equal(env.state.squareItems[0].activity.id, "latest");
  let resolveAccount;
  env.context.fetch = () => new Promise((resolve) => { resolveAccount = resolve; });
  const oldAccount = env.context.loadSquare(false);
  env.context.resetSquareLive();
  env.state.token = "new-token";
  env.state.user = { id: "two" };
  resolveAccount({ ok: false, status: 401, json: async () => ({}) });
  await oldAccount;
  assert.equal(env.state.squareItems.length, 0);
  assert.notEqual(env.context.loggedOut, true);
});

test("append requests are serialized and overlapping pages do not duplicate cards", async () => {
  const env = setup();
  env.state.squareItems = [item("one")]; env.state.squareOffset = 1;
  let resolve;
  env.context.fetch = (url) => { env.requests.push({ url }); return new Promise((r) => { resolve = r; }); };
  const first = env.context.loadSquare(true);
  await env.context.loadSquare(true);
  assert.equal(env.requests.length, 1);
  assert.match(env.requests[0].url, /offset=1/);
  resolve(response([item("one"), item("two")], { more: true, next: 3 }));
  await first;
  assert.deepEqual(Array.from(env.state.squareItems, (x) => x.activity.id), ["one", "two"]);
  assert.equal(env.state.squareOffset, 3);
});

test("offline errors preserve cards and background or inactive tabs do not poll", async () => {
  const env = setup();
  env.state.squareItems = [item("keep")];
  env.context.fetch = async () => { throw new TypeError("offline"); };
  await env.context.loadSquare(false);
  assert.equal(env.state.squareItems[0].activity.id, "keep");
  assert.match(env.get("#square-sync-status").textContent, /保留/);
  env.context.document.hidden = true;
  env.context.scheduleSquareLive();
  assert.equal(env.timers.size, 0);
  await env.context.loadSquare(false);
  env.context.document.hidden = false;
  env.context.navigator.onLine = false;
  env.context.scheduleSquareLive();
  assert.equal(env.timers.size, 0);
  env.context.navigator.onLine = true;
  env.context.setSquareActive(false);
  assert.equal(env.timers.size, 0);
});

test("pull refresh leaves native gestures passive and ignores forms and multi-touch", () => {
  const env = setup();
  env.context.initializeSquareLive();
  const panel = env.get("#panel-square");
  for (const name of ["touchstart", "touchmove", "touchend", "touchcancel"]) assert.equal(panel.listeners[`${name}Options`].passive, true);
  const touch = { touches: [{ clientX: 0, clientY: 0 }], target: { closest: () => ({}) } };
  panel.listeners.touchstart(touch);
  assert.equal(env.live.pull, null);
  touch.target.closest = () => null;
  touch.touches.push({ clientX: 1, clientY: 1 });
  panel.listeners.touchstart(touch);
  assert.equal(env.live.pull, null);
  touch.touches.pop();
  panel.listeners.touchstart(touch);
  assert.equal(env.live.pull.distance, 0);
  panel.listeners.touchmove({ touches: [{ clientX: 0, clientY: 90 }] });
  assert.match(env.get("#square-sync-status").textContent, /松开刷新/);
});
