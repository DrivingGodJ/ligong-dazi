const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.join(__dirname, "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");
const gradle = read("android/nativeapp/build.gradle");
const native = read("android/nativeapp/src/main/java/app/zeabur/ligongdazi/nativeapp/MainActivity.java");
const app = read("web/app.js");
const html = read("web/index.html");

test("APK bundles every referenced static UI resource without bundling admin or downloads", () => {
  const bundle = gradle.slice(gradle.indexOf("def bundleWebUi ="), gradle.indexOf("android.sourceSets.main.assets.srcDir(bundleWebUi)"));
  const included = new Set([...bundle.matchAll(/'([\w.-]+\.(?:html|js|css|svg|png|webmanifest))'/g)].map((match) => match[1]));
  const uiSources = [html, app, read("web/styles.css"), read("web/reference-ui.css"), read("web/visual-language.css"), read("web/service-worker.js"), read("web/manifest.webmanifest")].join("\n");
  const referenced = new Set([...uiSources.matchAll(/\/static\/([\w.-]+)/g)].map((match) => match[1]));
  for (const file of referenced) assert.ok(included.has(file), `${file} is missing from the APK bundle`);
  for (const file of ["index.html", "app.js", "styles.css", "reference-ui.css", "visual-language.css", "manifest.webmanifest", "service-worker.js"]) {
    assert.ok(included.has(file), `${file} is missing from the APK bundle`);
  }
  assert.doesNotMatch(bundle, /'admin\.|'ligong-dazi.*\.apk|downloads\/|launch-screen\.png/);
});

test("native WebView serves the shell locally while leaving data requests on HTTPS", () => {
  assert.match(native, /\.setDomain\(HOST\)/);
  assert.match(native, /\.addPathHandler\("\/static\/", new WebViewAssetLoader\.AssetsPathHandler\(this\)\)/);
  assert.match(native, /if \(!"GET"\.equalsIgnoreCase\(method\) \|\| !isOurSite\(uri\)\) return null;/);
  assert.match(native, /"\/"\.equals\(path\)[\s\S]*?bundledResource\("index\.html", "text\/html"\)/);
  assert.match(native, /path\.startsWith\("\/static\/"\)/);
  assert.match(native, /return response == null \? missingBundledResource\(\) : response;/);
  assert.match(native, /ServiceWorkerController\.getInstance\(\)\.setServiceWorkerClient/);
  assert.match(native, /return null;\s*\}\s*@Override public void onCreate/);
  assert.match(app, /if \(IS_NATIVE_ANDROID\) \{[\s\S]*?getRegistrations\(\)[\s\S]*?unregister\(\)/);
});

test("native network failure reveals the bundled offline state and keeps login intact", () => {
  assert.match(html, /id="native-offline-panel"[^>]*is-hidden/);
  assert.match(html, /id="native-offline-retry"[^>]*>重新连接<\/button>/);
  const source = app.slice(app.indexOf("function showNativeOfflineState()"), app.indexOf("async function logout("));
  const classes = new Set(["is-hidden"]);
  let authShown = false;
  const context = vm.createContext({
    showAuthShell() {authShown = true;},
    elements: {authView: {classList: {add(value) {classes.add(value);}}}},
    document: {querySelector() {return {classList: {remove(value) {classes.delete(value);}}};}},
  });
  vm.runInContext(source, context);
  context.showNativeOfflineState();
  assert.equal(authShown, true);
  assert.equal(classes.has("is-native-offline"), true);
  assert.equal(classes.has("is-hidden"), false);
});
