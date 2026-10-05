const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const read = (file) => fs.readFileSync(path.join(__dirname, "..", file), "utf8");
const visual = read("web/visual-language.css");
const reference = read("web/reference-ui.css");
const html = read("web/index.html");
const rule = (source, selector) => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const result = source.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`));
  assert.ok(result, `missing rule: ${selector}`);
  return result[1];
};

test("all functional page titles share the profile's semibold, tracked, single-line role", () => {
  assert.match(visual, /--title-weight: 600/);
  assert.match(visual, /--title-tracking: \.045em/);
  assert.match(visual, /--title-ink: #43276a/);
  assert.match(visual, /--title-ink: #eee2ff/);
  const title = rule(reference, ".page-heading h1");
  for (const property of ["font-weight: var(--title-weight)", "letter-spacing: var(--title-tracking)", "white-space: nowrap", "margin-top: 1rem", "line-height: 1.2"]) {
    assert.ok(title.includes(property));
  }
  // Desktop 4.4 → 4rem and phone 11 → 10.12vw are modest 8–10% reductions.
  assert.match(title, /font-size: clamp\(2\.45rem, 6\.4vw, 4rem\)/);
  assert.match(reference, /\.page-heading h1 \{ font-size: clamp\(2\.162rem, 10\.12vw, 2\.852rem\)/);
  assert.match(reference, /#match-greeting-name \{ min-width: 0; overflow: hidden; text-overflow: ellipsis/);
});

test("authentication, results, loading and dialogs reuse the same heading hierarchy", () => {
  const shared = visual.slice(visual.indexOf("/* Shared polish:"));
  assert.match(shared, /\.auth-intro h1,\s*\.admin-login h1,\s*\.admin-hero h1 \{[^}]*font-weight: var\(--title-weight\)[^}]*letter-spacing: var\(--title-tracking\)/);
  assert.match(shared, /\.tab-panel h2,[\s\S]*?\.modal-shell h2,[\s\S]*?font-weight: var\(--title-weight\)/);
  assert.match(visual, /\.empty-state h2,[\s\S]*?\.result-header h2 \{[^}]*font-weight: var\(--title-weight\)/);
  assert.match(rule(reference, "#result-running h2"), /clamp\(1\.52rem, 5\.52vw, 2\.02rem\)/);
  assert.match(read("web/launch-art.html"), /h1\{[^}]*font-weight:600;letter-spacing:\.045em;white-space:nowrap/);
});

test("warm surfaces and sparse flower-center accents have paired night tokens", () => {
  assert.match(visual, /--surface: #fffefd/);
  assert.match(reference, /--ref-surface: #fffefd/);
  assert.match(visual, /--accent-soft: #fff3ce/);
  assert.match(visual, /--accent-soft: #403622/);
  assert.match(rule(reference, ".page-heading .eyebrow"), /background: var\(--accent-soft\)/);
  assert.match(reference, /--ref-surface: #251e30/);
  assert.match(reference, /body > \.main-nav \.nav-button\.is-active \{ background: #fff2ce; color: #6933b8/);
  assert.match(reference, /body > \.main-nav \.nav-button\.is-active \{ color: #ead7ff; background: #443922/);
  assert.match(reference, /body > \.main-nav \{ background: #fffefa;[^}]*0 -4px 18px/);
  assert.match(reference, /#result-running \.agent-steps li > span \{ color: #c3abd7; background: #302738/);
  assert.match(visual, /\.section-intro,\s*\.modal-copy \{ color: var\(--ink-600\); line-height: 1\.7/);
});

test("mobile copy avoids the flower region without changing the artwork or page order", () => {
  assert.match(reference, /#panel-square \.heading-copy, #panel-activities \.heading-copy \{ max-width: calc\(\(100vw - 2rem\) \* \.44\); text-wrap: balance/);
  assert.match(reference, /#panel-invitations \.page-heading-floral \{ min-height: 260px/);
  const panels = [...html.matchAll(/id="panel-([^"]+)" class="tab-panel/g)].map((match) => match[1]);
  assert.deepEqual(panels, ["match", "square", "invitations", "activities", "profile"]);
  assert.match(html, /<h1 id="profile-heading">我的画像<\/h1>/);
  assert.match(html, /<strong id="profile-credit">100<\/strong>/);
  assert.match(html, /<span class="brand-sparkle" aria-hidden="true">✦<\/span> 让 Agent 帮我找/);
  assert.match(visual, /\.button-primary \{[^}]*background: linear-gradient\(135deg, #8757e7 0%, #6331c0 100%\)/);
});

test("web entry, admin and offline shell all request the same updated visual layer", () => {
  const asset = html.match(/href="(\/static\/visual-language\.css\?v=\d+)"/)[1];
  assert.ok(read("web/admin.html").includes(`href="${asset}"`));
  assert.ok(read("web/service-worker.js").includes(`"${asset}"`));
  const referenceAsset = html.match(/href="(\/static\/reference-ui\.css\?v=\d+)"/)[1];
  assert.ok(read("web/service-worker.js").includes(`"${referenceAsset}"`));
  const launchAsset = html.match(/iframe src="(\/static\/launch-art\.html\?v=\d+)"/)[1];
  assert.ok(read("web/service-worker.js").includes(`"${launchAsset}"`));
});
