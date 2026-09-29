const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const read = (file) => fs.readFileSync(path.join(__dirname, "..", file), "utf8");
const main = read("web/index.html");
const admin = read("web/admin.html");
const launch = read("web/launch-art.html");
const styles = read("web/styles.css");
const reference = read("web/reference-ui.css");
const native = read("android/nativeapp/src/main/java/app/zeabur/ligongdazi/nativeapp/MainActivity.java");

test("web views allow native pinch zoom without gesture interception", () => {
  for (const html of [main, admin, launch]) {
    assert.match(html, /name="viewport"[^>]*width=device-width/);
    assert.doesNotMatch(html, /user-scalable\s*=\s*no|maximum-scale\s*=\s*1|minimum-scale\s*=\s*1/);
  }
  assert.doesNotMatch(main, /touch-action:\s*pan-x\s+pan-y/);
  assert.doesNotMatch(main, /addEventListener\(["'](?:gesturestart|gesturechange|touchmove)["']/);
  assert.match(native, /settings\.setBuiltInZoomControls\(true\)/);
  assert.match(native, /settings\.setDisplayZoomControls\(false\)/);
});

test("editable fields avoid small type that triggers iOS focus zoom", () => {
  assert.match(styles, /input:not\(\[type="checkbox"\]\):not\(\[type="radio"\]\):not\(\[type="range"\]\),\s*select,\s*textarea\s*\{\s*font-size: max\(1rem, 16px\);/);
  assert.doesNotMatch(reference, /#match-form \.two-columns input\s*\{[^}]*font-size:/);
  assert.match(reference, /@media \(max-width: 660px\)\s*\{\s*#match-form \.form-section\.two-columns:has\(input\[type="datetime-local"\]\)/);
});
