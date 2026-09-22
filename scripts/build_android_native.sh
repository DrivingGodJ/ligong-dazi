#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home}"
export ANDROID_HOME="${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools/cmdline-tools/latest}"

node -e '
const fs = require("node:fs");
const release = JSON.parse(fs.readFileSync("web/android-native-release.json", "utf8"));
const gradle = fs.readFileSync("android/nativeapp/build.gradle", "utf8");
if (!gradle.includes(`versionCode ${release.version_code}`) ||
    !gradle.includes(`versionName "${release.version_name}"`)) {
  throw new Error("请同步应用版工程与 web/android-native-release.json 的版本号");
}
'

if [[ ! -f android/android.keystore ]]; then
  echo '缺少安卓签名密钥，不能构建可覆盖更新的安装包。' >&2
  exit 1
fi

DAZI_SIGNING_SECRET="$(security find-generic-password -w -s ligong-dazi-android-signing -a ligong-dazi-android)"
export DAZI_SIGNING_SECRET

(cd android && ./gradlew :nativeapp:assembleRelease)
"$ANDROID_HOME/build-tools/36.1.0/zipalign" -f -p 4 \
  android/nativeapp/build/outputs/apk/release/nativeapp-release-unsigned.apk \
  android/nativeapp-release-unsigned-aligned.apk
mkdir -p web/downloads
"$ANDROID_HOME/build-tools/36.1.0/apksigner" sign \
  --ks android/android.keystore --ks-key-alias android \
  --ks-pass env:DAZI_SIGNING_SECRET --key-pass env:DAZI_SIGNING_SECRET \
  --out web/downloads/ligong-dazi-native.apk android/nativeapp-release-unsigned-aligned.apk
"$ANDROID_HOME/build-tools/36.1.0/apksigner" verify --verbose --print-certs \
  web/downloads/ligong-dazi-native.apk
echo '独立应用版已生成：web/downloads/ligong-dazi-native.apk'
