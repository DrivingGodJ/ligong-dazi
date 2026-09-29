const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const read = (file) => fs.readFileSync(path.join(__dirname, "..", file), "utf8");
const native = read("android/nativeapp/src/main/java/app/zeabur/ligongdazi/nativeapp/MainActivity.java");
const updater = read("android/nativeapp/src/main/java/app/zeabur/ligongdazi/nativeapp/NativeUpdateManager.java");
const manifest = read("android/nativeapp/src/main/AndroidManifest.xml");
const app = read("web/app.js");

test("native update checks on resume and handles the profile APK link", () => {
  assert.match(native, /updateManager = new NativeUpdateManager\(this\)/);
  assert.match(native, /onResume\(\)[\s\S]*?updateManager\.onResume\(\)/);
  assert.match(native, /onPause\(\)[\s\S]*?updateManager\.onPause\(\)/);
  assert.match(native, /online && !wasOnline && updateManager != null\) updateManager\.onNetworkRestored\(\)/);
  assert.match(native, /isNativeApkLink\(uri\)[\s\S]*?updateManager\.check\(true\)/);
  assert.match(native, /setDownloadListener\([\s\S]*?isNativeApkLink\(uri\)\) \{ updateManager\.check\(true\);/);
  assert.match(native, /updateManager\.onDestroy\(\)/);
});

test("native update validates metadata, downloaded bytes and APK identity before installation", () => {
  assert.match(updater, /CHECK_INTERVAL_MS = 6L \* 60 \* 60 \* 1000/);
  assert.match(updater, /!APK_PATH\.equals\(path\)/);
  assert.match(updater, /hash\.matches\("\[0-9a-f\]\{64\}"\)/);
  assert.match(updater, /MAX_APK_BYTES = 50 \* 1024 \* 1024/);
  assert.match(updater, /MessageDigest\.getInstance\("SHA-256"\)/);
  assert.match(updater, /release\.sha256\.equals\(actualHash\.toString\(\)\)/);
  assert.match(updater, /archiveCode != expectedCode \|\| archiveCode <= BuildConfig\.VERSION_CODE/);
  assert.match(updater, /Arrays\.equals\(archiveSigners, installedSigners\)/);
  assert.match(updater, /new Intent\(Intent\.ACTION_VIEW\)[\s\S]*?application\/vnd\.android\.package-archive/);
  assert.match(updater, /FLAG_GRANT_READ_URI_PERMISSION/);
  assert.match(updater, /canRequestPackageInstalls\(\)/);
  assert.match(updater, /ACTION_MANAGE_UNKNOWN_APP_SOURCES/);
  assert.match(updater, /if \(!foreground\) return;[\s\S]*?if \(canRequestInstall\(\)\) openInstaller\(\)/);
  assert.match(manifest, /android\.permission\.REQUEST_INSTALL_PACKAGES/);
});

test("bundled web UI keeps the manual update link without a duplicate native toast", async () => {
  let shown = false;
  let toasts = 0;
  const update = {href: "", textContent: "", classList: {add() {}, remove() {shown = true;}}};
  const context = vm.createContext({
    navigator: {userAgent: "Android LigongDaziNative/10"},
    IS_NATIVE_ANDROID: true,
    document: {querySelector() {return update;}},
    localStorage: {getItem() {return null;}},
    sessionStorage: {getItem() {return null;}, setItem() {}},
    isInstalledApp() {return true;},
    refreshInstallHint() {},
    showToast() {toasts++;},
    async fetch() {return {ok: true, async json() {return {available: true, version_code: 11, version_name: "2.0-beta", download_url: "/downloads/ligong-dazi-native.apk"};}};},
  });
  vm.runInContext(app.slice(app.indexOf("async function checkAndroidRelease()"), app.indexOf("async function submitTimeVote(")), context);
  await context.checkAndroidRelease();
  assert.equal(shown, true);
  assert.equal(update.href, "/downloads/ligong-dazi-native.apk");
  assert.equal(toasts, 0);
});
