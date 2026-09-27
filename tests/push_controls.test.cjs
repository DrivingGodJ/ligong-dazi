const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const appSource = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const controlsSource = appSource.slice(appSource.indexOf("function nativePushStatus()"), appSource.indexOf("async function checkAndroidRelease()"));

test("notification switch belongs to settings immediately above the autosave hint", () => {
  const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
  const form = html.slice(html.indexOf('<form id="profile-form"'), html.indexOf('</form>', html.indexOf('<form id="profile-form"')));
  assert.equal((html.match(/id="push-preference"/g) || []).length, 1);
  assert.match(form, /id="push-preference"[\s\S]*?<\/label>\s*<div class="form-actions">\s*<p id="profile-status"/);
  assert.ok(!form.match(/id="push-notifications-toggle"[^>]*\bname=/));
});

test("notification changes never trigger profile autosave while profile settings still do", () => {
  const handlers = {};
  const saves = [];
  const ranges = [];
  const source = appSource.slice(appSource.indexOf('  elements.profileForm.addEventListener("input"'), appSource.indexOf('  elements.profileRetry.addEventListener'));
  const context = vm.createContext({
    elements: {profileForm: {addEventListener(type, handler) {handlers[type] = handler;}}},
    queueProfileSave(immediate) {saves.push(immediate);},
    updateGroupRangeOutputs(name) {ranges.push(name);},
  });
  vm.runInContext(source, context);
  const notification = {target: {id: "push-notifications-toggle", name: ""}};
  handlers.input(notification);
  handlers.change(notification);
  assert.equal(saves.length, 0);
  handlers.input({target: {id: "group-min-slider", name: "preferred_group_min"}});
  handlers.change({target: {id: "", name: "allow_invitations"}});
  assert.deepEqual(saves, [undefined, true]);
  assert.deepEqual(ranges, ["preferred_group_min"]);
});

function setup(options = {}) {
  const nodes = new Map();
  const storage = options.storage || new Map();
  const scheduled = [];
  const calls = [];
  const toasts = [];
  let subscription = null;
  let bound = Boolean(options.subscribed);
  const state = {
    token: "test-token", user: {id: options.userId || "test-user"},
    pushEnabled: false, pushBusy: false, pushStatusRevision: 0, pushControlsSeen: new Set(),
  };
  const node = (selector) => {
    if (!nodes.has(selector)) {
      const classes = new Set(["is-hidden"]);
      nodes.set(selector, {
        checked: false, disabled: false, textContent: "", attributes: {},
        classList: {toggle(name, hidden) { hidden ? classes.add(name) : classes.delete(name); }, contains(name) {return classes.has(name);}},
        setAttribute(name, value) { this.attributes[name] = value; },
      });
    }
    return nodes.get(selector);
  };
  const makeSubscription = () => ({
    endpoint: "https://fcm.googleapis.com/fcm/send/test",
    toJSON() { return {endpoint: this.endpoint, keys: {p256dh: "key", auth: "auth"}}; },
    async unsubscribe() {
      calls.push({path: "unsubscribe"});
      if (options.unsubscribeError) throw new Error("browser offline");
      subscription = null;
      return true;
    },
  });
  if (options.subscription || options.subscribed) subscription = makeSubscription();
  const registration = {pushManager: {
    async getSubscription() { return subscription; },
    async subscribe() {
      calls.push({path: "subscribe"});
      if (options.subscribeError) throw new Error("push service unavailable");
      subscription = makeSubscription();
      return subscription;
    },
  }};
  const nativeState = {enabled: Boolean(options.enabled), permission: options.permission !== "denied", connected: true, cid: "test-native-device-0123456789"};
  const window = {
    isSecureContext: options.secure !== false, PushManager: {},
    Notification: {permission: options.permission || "default", async requestPermission() {
      calls.push({path: "requestPermission"});
      this.permission = options.requestPermission || "granted";
      return this.permission;
    }},
    setTimeout(fn, delay) {scheduled.push({fn, delay}); return scheduled.length;}, clearTimeout() {},
  };
  if (options.native) window.LigongPush = {
    status() {return options.invalidNative ? "{}" : JSON.stringify(nativeState);},
    enable() { calls.push({path: "nativeEnable"}); if (options.nativeDenied) return; nativeState.enabled = true; nativeState.permission = true; },
    disable() { calls.push({path: "nativeDisable"}); nativeState.enabled = false; },
    disableLocal() { calls.push({path: "nativeDisableLocal"}); nativeState.enabled = false; },
    sync() {},
  };
  if (options.missingBridge) delete window.LigongPush;
  if (options.missingFeature) delete window[options.missingFeature];
  const navigator = {serviceWorker: {ready: options.workerNotReady ? new Promise(() => {}) : Promise.resolve(registration), async getRegistration() {return options.noWorker ? undefined : registration;}}};
  if (options.noServiceWorker) delete navigator.serviceWorker;
  const context = vm.createContext({
    state, IS_NATIVE_ANDROID: Boolean(options.native), window, navigator,
    Notification: window.Notification, Uint8Array, encodeURIComponent, atob,
    document: {querySelector: node},
    localStorage: {getItem(key) {if(options.storageError) throw new Error("blocked"); return storage.get(key);}, setItem(key, value) {if(options.storageError) throw new Error("blocked"); storage.set(key, value);}},
    isIos: () => Boolean(options.ios), isInstalledApp: () => Boolean(options.installed), refreshInstallHint() {},
    showToast(text) {toasts.push(text);}, setButtonLoading(button, loading) {button.disabled = loading;},
    async api(endpoint, config = {}) {
      const method = config.method || "GET";
      calls.push({path: endpoint, method});
      if (options.apiHook) await options.apiHook(endpoint, method);
      if (options.apiError && (options.apiError === method || options.apiError === "all")) throw new Error("server unavailable");
      if (endpoint === "/push/public-key") return {public_key: "YWJj"};
      if (endpoint.startsWith("/push/native/devices/")) return {status: "unsubscribed"};
      if (method === "POST") {bound = true; return {status: "subscribed"};}
      if (method === "DELETE") {bound = false; return {status: "unsubscribed"};}
      return {subscribed: bound};
    },
  });
  vm.runInContext(controlsSource, context);
  return {
    state, context, calls, toasts, storage, scheduled, nativeState,
    refresh: () => context.refreshPushStatus(), set: (value) => context.setPushEnabled(value),
    shown: (selector) => !node(selector).classList.contains("is-hidden"),
    node, isBound: () => bound,
  };
}

for (const options of [{missingFeature: "Notification"}, {missingFeature: "PushManager"}, {noServiceWorker: true}, {secure: false}, {ios: true}, {native: true, missingBridge: true}, {native: true, invalidNative: true}]) {
  test(`unsupported environment hides prompt and switch ${JSON.stringify(options)}`, async () => {
    const env = setup(options);
    await env.refresh();
    assert.equal(env.shown("#push-introduction"), false);
    assert.equal(env.shown("#push-preference"), false);
    await env.set(true);
    assert.equal(env.calls.length, 0);
  });
}

test("permission alone does not expose an enabled switch", async () => {
  const env = setup({permission: "granted"});
  await env.refresh();
  assert.equal(env.shown("#push-introduction"), true);
  assert.equal(env.shown("#push-preference"), false);
});

test("a browser subscription not bound to this account is not enabled", async () => {
  const env = setup({permission: "granted", subscription: true});
  await env.refresh();
  assert.equal(env.shown("#push-introduction"), true);
  assert.equal(env.shown("#push-preference"), false);
});

for (const options of [{}, {ios: true, installed: true}, {storageError: true}]) {
  test(`enable, disable, re-enable and persist the switch ${JSON.stringify(options)}`, async () => {
    const env = setup(options);
    await env.refresh();
    await env.set(true);
    assert.equal(env.node("#push-notifications-toggle").checked, true);
    assert.equal(env.shown("#push-introduction"), false);
    assert.equal(env.shown("#push-preference"), true);
    assert.equal(env.isBound(), true);
    await env.set(false);
    assert.equal(env.isBound(), false);
    assert.equal(env.node("#push-notifications-toggle").checked, false);
    assert.equal(env.shown("#push-preference"), true);
    assert.equal(env.shown("#push-introduction"), false);
    assert.ok(env.node("#push-preference-description").textContent.includes("站内"));
    assert.ok(env.calls.findIndex(call => call.method === "DELETE") < env.calls.findIndex(call => call.path === "unsubscribe"));
    await env.set(true);
    assert.equal(env.node("#push-notifications-toggle").checked, true);
    assert.equal(env.node("#push-notifications-toggle").disabled, false);
    assert.equal(env.calls.filter(call => call.path === "requestPermission").length, 1);
    if (!options.storageError) {
      const resumed = setup({...options, storage: env.storage, permission: "granted"});
      await resumed.refresh();
      assert.equal(resumed.shown("#push-preference"), true);
      assert.equal(resumed.node("#push-notifications-toggle").checked, false);
    }
  });
}

test("an existing active subscription uses the bottom switch", async () => {
  const env = setup({permission: "granted", subscribed: true});
  await env.refresh();
  assert.equal(env.shown("#push-preference"), true);
  assert.equal(env.shown("#push-introduction"), false);
  assert.equal(env.node("#push-notifications-toggle").checked, true);
});

for (const options of [{requestPermission: "denied"}, {apiError: "POST"}, {subscribeError: true}]) {
  test(`failed first enable keeps the prompt and hides the switch ${JSON.stringify(options)}`, async () => {
    const env = setup(options);
    await env.set(true);
    assert.equal(env.shown("#push-preference"), false);
    assert.equal(env.shown("#push-introduction"), true);
    assert.equal(env.node("#push-notifications-toggle").checked, false);
    assert.equal(env.state.pushBusy, false);
    assert.equal(env.storage.size, 0);
  });
}

test("failed server disable rolls the switch back on", async () => {
  const env = setup({permission: "granted", subscribed: true, apiError: "DELETE"});
  await env.refresh();
  await env.set(false);
  assert.equal(env.node("#push-notifications-toggle").checked, true);
  assert.equal(env.isBound(), true);
  assert.ok(!env.calls.some(call => call.path === "unsubscribe"));
});

test("browser unsubscribe failure still stops delivery on the server", async () => {
  const env = setup({permission: "granted", subscribed: true, unsubscribeError: true});
  await env.refresh();
  await env.set(false);
  assert.equal(env.node("#push-notifications-toggle").checked, false);
  assert.equal(env.isBound(), false);
  assert.equal(env.shown("#push-preference"), true);
});

test("OS permission revoked shows an off switch with recovery help", async () => {
  const env = setup({permission: "granted", subscribed: true});
  await env.refresh();
  env.context.Notification.permission = "denied";
  await env.refresh();
  assert.equal(env.shown("#push-preference"), true);
  assert.equal(env.node("#push-notifications-toggle").checked, false);
  assert.ok(env.node("#push-preference-description").textContent.includes("设置"));
});

test("native bridge enables, disconnects this device and re-enables", async () => {
  const env = setup({native: true});
  await env.set(true);
  assert.equal(env.node("#push-notifications-toggle").checked, true);
  await env.set(false);
  assert.equal(env.node("#push-notifications-toggle").checked, false);
  assert.equal(env.shown("#push-preference"), true);
  assert.ok(env.calls.findIndex(call => call.method === "DELETE") < env.calls.findIndex(call => call.path === "nativeDisableLocal"));
  assert.equal(env.calls.filter(call => call.method === "DELETE").length, 1);
  assert.ok(!env.calls.some(call => call.path === "nativeDisable"));
  await env.set(true);
  assert.equal(env.node("#push-notifications-toggle").checked, true);
});

test("native permission denial does not expose the switch prematurely", async () => {
  const env = setup({native: true, nativeDenied: true});
  await env.set(true);
  assert.equal(env.shown("#push-preference"), false);
  assert.equal(env.shown("#push-introduction"), true);
});

test("native server delete failure cannot falsely turn notifications off", async () => {
  const env = setup({native: true, enabled: true, apiError: "DELETE"});
  await env.refresh();
  await env.set(false);
  assert.equal(env.node("#push-notifications-toggle").checked, true);
  assert.ok(!env.calls.some(call => call.path === "nativeDisable"));
});

test("a slow worker times out and restores usable controls", async () => {
  const env = setup({workerNotReady: true});
  const pending = env.set(true);
  await new Promise(resolve => setImmediate(resolve));
  const timeout = env.scheduled.find(item => item.delay === 10000);
  assert.ok(timeout);
  timeout.fn();
  await pending;
  assert.equal(env.state.pushBusy, false);
  assert.equal(env.shown("#push-preference"), false);
  assert.ok(env.toasts.some(text => text.includes("超时")));
});

test("repeat clicks while saving do not create duplicate subscriptions", async () => {
  let release;
  const waiting = new Promise(resolve => {release = resolve;});
  const env = setup({apiHook: async (_endpoint, method) => {if (method === "POST") await waiting;}});
  const first = env.set(true);
  await env.set(true);
  release();
  await first;
  assert.equal(env.calls.filter(call => call.method === "POST").length, 1);
});

test("remembered controls belong only to the current account", async () => {
  const storage = new Map([["dazi_push_controls_other-user", "seen"]]);
  const env = setup({storage});
  await env.refresh();
  assert.equal(env.shown("#push-preference"), false);
});

test("a stale status response cannot overwrite a newer state", async () => {
  const env = setup({permission: "granted", subscribed: true});
  let release;
  const delayed = new Promise(resolve => { release = resolve; });
  let count = 0;
  env.context.api = async () => ++count === 1 ? delayed : {subscribed: false};
  const oldRefresh = env.refresh();
  await new Promise(resolve => setImmediate(resolve));
  await env.refresh();
  release({subscribed: true});
  await oldRefresh;
  assert.equal(env.node("#push-notifications-toggle").checked, false);
  assert.equal(env.shown("#push-preference"), false);
});

test("older native shells without a CID still support their disable bridge", async () => {
  const env = setup({native: true, enabled: true});
  delete env.nativeState.cid;
  await env.refresh();
  await env.set(false);
  assert.equal(env.node("#push-notifications-toggle").checked, false);
  assert.equal(env.calls.filter(call => call.path === "nativeDisable").length, 1);
});
