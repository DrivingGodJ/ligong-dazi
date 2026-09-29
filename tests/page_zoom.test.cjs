const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const zoomScript = html.match(/<script>\s*(\(\(\) => \{\s*\/\/ Safari may ignore touch-action[\s\S]*?\}\)\(\);)\s*<\/script>/)?.[1];

test("page disables touch pinch without clamping browser viewport accessibility settings", () => {
  assert.match(html, /html, body \{ touch-action: pan-x pan-y; \}/);
  assert.match(html, /<meta name="viewport" content="width=device-width, initial-scale=1\.0" \/>/);
  assert.doesNotMatch(html, /user-scalable=no|maximum-scale=1/);
  assert.ok(zoomScript);
});

test("gesture fallback blocks pinch but leaves single-finger scrolling alone", () => {
  const listeners = new Map();
  vm.runInNewContext(zoomScript, {
    document: {addEventListener(type, handler, options) {listeners.set(type, {handler, options});}},
  });
  assert.equal(listeners.get("gesturestart").options.passive, false);
  assert.equal(listeners.get("touchmove").options.passive, false);

  const singleTouch = {touches: [{}], cancelable: true, prevented: false, preventDefault() {this.prevented = true;}};
  listeners.get("touchmove").handler(singleTouch);
  assert.equal(singleTouch.prevented, false);

  const twoTouches = {...singleTouch, touches: [{}, {}], prevented: false};
  listeners.get("touchmove").handler(twoTouches);
  assert.equal(twoTouches.prevented, true);

  const gesture = {...singleTouch, prevented: false};
  listeners.get("gesturestart").handler(gesture);
  assert.equal(gesture.prevented, true);

  const nonCancelable = {...twoTouches, cancelable: false, prevented: false};
  listeners.get("touchmove").handler(nonCancelable);
  assert.equal(nonCancelable.prevented, false);
});
