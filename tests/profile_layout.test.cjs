const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const read = (file) => fs.readFileSync(path.join(__dirname, "..", file), "utf8");
const html = read("web/index.html");
const css = read("web/reference-ui.css");
const app = read("web/app.js");
const profileForm = html.slice(html.indexOf('<form id="profile-form"'), html.indexOf('<div class="profile-logout"'));

test("bio spans the form directly after the basic/preferences grid, before settings and hobbies", () => {
  const basic = profileForm.slice(profileForm.indexOf('class="profile-section-card profile-basic-card"'), profileForm.indexOf('class="profile-section-card profile-preferences-card"'));
  assert.equal((profileForm.match(/name="bio"/g) || []).length, 1);
  assert.doesNotMatch(basic, /name="bio"/);
  assert.match(profileForm, /<\/section>\s*<\/div>\s*<label class="profile-bio-field profile-section-card">\s*自我介绍\s*<textarea name="bio" rows="4" maxlength="500" placeholder="简单说说你的活动习惯"><\/textarea>/);
  assert.ok(profileForm.indexOf('name="bio"') < profileForm.indexOf('id="hobby-skill-heading"'));
  assert.match(css, /#panel-profile \.profile-bio-field\s*\{\s*grid-template-columns: minmax\(0, 1fr\)/);
});

test("moving bio keeps the existing profile serializer and delegated autosave", () => {
  const form = {};
  const values = new Map([
    ["display_name", "小周"], ["campus", "南京"], ["gender", "female"],
    ["bio", " 常去图书馆自习，也打羽毛球 "], ["interests", "自习，羽毛球"],
    ["preferred_group_min", "2"], ["preferred_group_max", "4"], ["allow_invitations", "on"],
  ]);
  const context = vm.createContext({
    elements: {profileForm: form},
    FormData: class {
      constructor(actual) { assert.equal(actual, form); }
      get(name) { return values.get(name) ?? null; }
      has(name) { return values.has(name); }
    },
    collectHobbySkills() { return []; },
  });
  vm.runInContext(app.slice(app.indexOf("function splitList("), app.indexOf("function queueProfileSave(")), context);
  assert.equal(context.profilePayload().bio, "常去图书馆自习，也打羽毛球");
  values.set("bio", "   ");
  assert.equal(context.profilePayload().bio, null);
  assert.match(app, /elements\.profileForm\.addEventListener\("input",/);
  assert.match(app, /elements\.profileForm\.addEventListener\("change",/);
});

test("phone settings stack before the full-width bio without changing the desktop grid", () => {
  assert.match(css, /\.profile-grid \{ display: grid; grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(css, /@media \(max-width: 640px\) \{[^}]*\}[^}]*\}\s*#panel-profile \.profile-grid \{ grid-template-columns: minmax\(0, 1fr\); \}/);
});

test("profile title has a scoped single-line semibold role and modest Chinese tracking", () => {
  const title = css.match(/#panel-profile #profile-heading\s*\{([^}]+)\}/)[1];
  assert.match(title, /white-space: nowrap/);
  assert.match(title, /font-weight: 600/);
  assert.match(title, /letter-spacing: \.045em/);
  assert.match(title, /color: #43276a/);
  assert.match(css, /#panel-profile #profile-heading\s*\{ color: #eee2ff/);
});

test("warm independent navigation and flower-center accents do not recolor the main purple CTA", () => {
  assert.match(css, /body > \.main-nav\s*\{ background: #fffefa; border-color:/);
  assert.match(css, /body > \.main-nav \.nav-button\.is-active\s*\{ background: #fff2ce; color: #6933b8/);
  assert.match(css, /\.nav-button:not\(\.is-active\)\s*\{ color: #7a7185/);
  assert.match(css, /\.brand-sparkle\s*\{ color: #ffe47d/);
  assert.match(css, /\.ai-summary-card \.button-accent\s*\{ color: #fff; background: linear-gradient\(135deg, #8d57e9, #6b35cf\)/);
  assert.match(css, /--profile-surface: #251e30; --profile-border: #4d3d60/);
  assert.match(html, /<div class="credit-card" aria-label="当前信用分">\s*<span>搭子信用<\/span>\s*<strong id="profile-credit">100<\/strong>/);
  assert.match(html, /class="page-flower" src="\/static\/mascot-bloom\.svg"/);
});

test("offline shell caches the same profile stylesheet version as the entry page", () => {
  const asset = html.match(/href="(\/static\/reference-ui\.css\?v=\d+)"/)[1];
  assert.ok(read("web/service-worker.js").includes(`"${asset}"`));
});
