const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const authSource = source.slice(source.indexOf("function switchAuthPanel("), source.indexOf("function showRegisterStep("));
const deviceSource = source.slice(source.indexOf("function isIos()"), source.indexOf("function refreshInstallHint()"));

function setup(userAgent, options = {}) {
  const nodes = new Map();
  const focus = [];
  const node = (selector) => {
    if (!nodes.has(selector)) {
      const classes = new Set(["is-hidden"]);
      nodes.set(selector, {
        classList: {
          add(name) {classes.add(name);},
          remove(name) {classes.delete(name);},
          contains(name) {return classes.has(name);},
          toggle(name, active) {active ? classes.add(name) : classes.delete(name);},
        },
        setAttribute() {},
        focus() {focus.push(selector);},
      });
    }
    return nodes.get(selector);
  };
  const form = node("#register-form");
  form.elements = {display_name: {focus() {focus.push("display_name");}}};
  const context = vm.createContext({
    document: {querySelector: node, referrer: options.referrer || ""},
    elements: {authError: node("#auth-error"), authAppealButton: node("#auth-appeal-button"), registerForm: form},
    navigator: {userAgent, platform: options.platform || "", maxTouchPoints: options.maxTouchPoints || 0, standalone: options.standalone || false},
    window: {matchMedia() {return {matches: options.displayMode || false};}},
    IS_NATIVE_ANDROID: Boolean(options.native),
    hideInlineError() {},
    refreshHumanChallenge() {},
    showRegisterStep() {},
  });
  vm.runInContext(deviceSource + authSource, context);
  return {context, focus, shown: (selector) => !node(selector).classList.contains("is-hidden")};
}

test("mobile registration starts at the install guide, then reveals the web form", () => {
  for (const [userAgent, platform] of [["Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)", "ios"], ["Mozilla/5.0 (Linux; Android 15; Pixel) Mobile", "android"]]) {
    const env = setup(userAgent);
    env.context.switchAuthPanel("register");
    assert.equal(env.context.registrationInstallPlatform(), platform);
    assert.equal(env.shown("#registration-install-gate"), true);
    assert.equal(env.shown("#register-form"), false);
    assert.equal(env.shown(`#${platform}-install-guide`), true);
    assert.equal(env.shown(`#${platform === "ios" ? "android" : "ios"}-install-guide`), false);
    assert.equal(env.focus.at(-1), "#registration-install-title");
    env.context.continueWebRegistration();
    assert.equal(env.shown("#registration-install-gate"), false);
    assert.equal(env.shown("#register-form"), true);
    assert.equal(env.focus.at(-1), "display_name");
    env.context.switchAuthPanel("login");
    assert.equal(env.shown("#login-panel"), true);
    env.context.switchAuthPanel("register");
    assert.equal(env.shown("#registration-install-gate"), true);
    assert.equal(env.shown("#register-form"), false);
  }
});

test("already installed apps and desktop browsers go straight to registration", () => {
  for (const [userAgent, options] of [
    ["Mozilla/5.0 (Macintosh; Intel Mac OS X)", {}],
    ["Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)", {standalone: true}],
    ["Mozilla/5.0 (iPad; CPU OS 17_0)", {displayMode: true}],
    ["Mozilla/5.0 (Linux; Android 15) Mobile LigongDaziNative/7", {native: true}],
    ["Mozilla/5.0 (Linux; Android 15) Mobile", {referrer: "android-app://app.zeabur.ligongdazi"}],
  ]) {
    const env = setup(userAgent, options);
    env.context.switchAuthPanel("register");
    assert.equal(env.context.registrationInstallPlatform(), null);
    assert.equal(env.shown("#registration-install-gate"), false);
    assert.equal(env.shown("#register-form"), true);
  }
});

test("gate and form have separate reading order, with a clear web fallback", () => {
  const panel = html.slice(html.indexOf('<div id="register-panel"'), html.indexOf('<form id="register-form"'));
  assert.match(panel, /id="registration-install-gate"[^>]*is-hidden/);
  assert.match(panel, /id="continue-web-registration"[^>]*>暂不安装，继续网页注册<\/button>/);
  assert.match(panel, /id="android-download-link"[^>]*href="\/downloads\/ligong-dazi-native\.apk"/);
  assert.match(html, /id="register-form" class="form-stack is-hidden"/);
});

test("Android browser receives the native APK, without overwriting the profile install link", async () => {
  const link = {href: "", classList: {add() {}, remove() {}}};
  const status = {textContent: "", classList: {add() {}, remove() {}}};
  const profileLink = {href: "/downloads/ligong-dazi-native.apk"};
  const update = {href: "", textContent: "", classList: {add() {}, remove() {}}};
  const calls = [];
  const context = vm.createContext({
    navigator: {userAgent: "Android Mobile"}, IS_NATIVE_ANDROID: false,
    document: {querySelector(selector) {return {"#android-download-link": link, "#android-download-status": status, "#profile-apk-update": update, "#profile-apk-link": profileLink}[selector];}},
    window: {}, localStorage: {getItem() {return null;}}, sessionStorage: {getItem() {return null;}, setItem() {}},
    isInstalledApp() {return false;}, refreshInstallHint() {}, showToast() {},
    async fetch(endpoint) {calls.push(endpoint); return {ok: true, async json() {return endpoint.includes("native") ? {available: true, download_url: "/downloads/ligong-dazi-native.apk"} : {available: true, version_code: 2, download_url: "/downloads/ligong-dazi.apk"};}};},
  });
  vm.runInContext(source.slice(source.indexOf("async function checkAndroidRelease()"), source.indexOf("async function submitTimeVote(")), context);
  await context.checkAndroidRelease();
  assert.equal(link.href, "/downloads/ligong-dazi-native.apk");
  assert.equal(profileLink.href, "/downloads/ligong-dazi-native.apk");
  assert.deepEqual(calls, ["/api/v1/app/native-version", "/api/v1/app/version"]);
});
