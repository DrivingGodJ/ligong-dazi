#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home}"
export ANDROID_HOME="${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools/cmdline-tools/latest}"

node -e '
const fs = require("node:fs");
const source = JSON.parse(fs.readFileSync("android/twa-manifest.json", "utf8"));
const release = JSON.parse(fs.readFileSync("web/android-release.json", "utf8"));
const gradle = fs.readFileSync("android/app/build.gradle", "utf8");
if (source.appVersionCode !== release.version_code ||
    !gradle.includes(`versionCode ${release.version_code}`) ||
    !gradle.includes(`versionName "${source.appVersionName}"`)) {
  throw new Error("请先同步 Android 工程与 web/android-release.json 的版本号");
}
'

if [[ ! -f android/android.keystore ]]; then
  echo '缺少安卓签名密钥，不能构建可覆盖更新的安装包。' >&2
  exit 1
fi

DAZI_SIGNING_SECRET="$(security find-generic-password -w -s ligong-dazi-android-signing -a ligong-dazi-android)"
export DAZI_SIGNING_SECRET

(cd android && ./gradlew assembleRelease)
"$ANDROID_HOME/build-tools/36.1.0/zipalign" -f -p 4 \
  android/app/build/outputs/apk/release/app-release-unsigned.apk \
  android/app-release-unsigned-aligned.apk
mkdir -p web/downloads
"$ANDROID_HOME/build-tools/36.1.0/apksigner" sign \
  --ks android/android.keystore --ks-key-alias android \
  --ks-pass env:DAZI_SIGNING_SECRET --key-pass env:DAZI_SIGNING_SECRET \
  --out web/downloads/ligong-dazi.apk android/app-release-unsigned-aligned.apk
"$ANDROID_HOME/build-tools/36.1.0/apksigner" verify --verbose --print-certs \
  web/downloads/ligong-dazi.apk
echo '安装包已生成：web/downloads/ligong-dazi.apk'
