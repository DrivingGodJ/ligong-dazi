package app.zeabur.ligongdazi.nativeapp;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.Gravity;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import androidx.core.content.FileProvider;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Checks and installs the signed standalone APK without blocking the bundled UI. */
final class NativeUpdateManager {
    private static final String ORIGIN = "https://ligong-dazi.zeabur.app";
    private static final String VERSION_PATH = "/api/v1/app/native-version";
    private static final String APK_PATH = "/downloads/ligong-dazi-native.apk";
    private static final long CHECK_INTERVAL_MS = 6L * 60 * 60 * 1000;
    private static final int MAX_APK_BYTES = 50 * 1024 * 1024;
    private final Activity activity;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());
    private long lastCheckAt;
    private int promptedVersion;
    private boolean busy;
    private volatile boolean cancelled;
    private boolean destroyed;
    private boolean foreground;
    private boolean awaitingInstallPermission;
    private Release pendingRelease;
    private File pendingInstall;
    private AlertDialog downloadDialog;
    private TextView progressText;
    private ProgressBar progressBar;

    private static final class Release {
        final int versionCode;
        final String versionName;
        final String sha256;

        Release(int versionCode, String versionName, String sha256) {
            this.versionCode = versionCode;
            this.versionName = versionName;
            this.sha256 = sha256;
        }
    }

    NativeUpdateManager(Activity activity) {
        this.activity = activity;
    }

    void onResume() {
        foreground = true;
        if (pendingInstall != null) {
            if (canRequestInstall()) openInstaller();
            else if (awaitingInstallPermission) {
                pendingInstall = null;
                awaitingInstallPermission = false;
                toast("未允许安装更新，可稍后在我的画像中重试");
            } else requestInstallPermission();
            return;
        }
        if (pendingRelease != null) {
            Release release = pendingRelease;
            pendingRelease = null;
            offerUpdate(release, false);
            return;
        }
        if (System.currentTimeMillis() - lastCheckAt >= CHECK_INTERVAL_MS) check(false);
    }

    void onPause() {
        foreground = false;
    }

    void onNetworkRestored() {
        if (foreground && lastCheckAt == 0) check(false);
    }

    void check(boolean manual) {
        if (destroyed || busy) return;
        busy = true;
        lastCheckAt = System.currentTimeMillis();
        worker.execute(() -> {
            Release release = null;
            Exception failure = null;
            try { release = fetchRelease(); }
            catch (Exception error) { failure = error; }
            Release found = release;
            Exception problem = failure;
            main.post(() -> {
                busy = false;
                if (destroyed) return;
                if (problem != null) {
                    lastCheckAt = 0;
                    if (manual && foreground) showError("暂时无法检查更新，请检查网络后重试。", true);
                } else if (found == null) {
                    if (manual && foreground) toast("已是最新版本");
                } else if (foreground) {
                    offerUpdate(found, manual);
                } else {
                    pendingRelease = found;
                }
            });
        });
    }

    private void offerUpdate(Release release, boolean manual) {
        if (!manual && promptedVersion == release.versionCode) return;
        promptedVersion = release.versionCode;
        new AlertDialog.Builder(activity)
                .setTitle("发现新版本 " + release.versionName)
                .setMessage("下载完成并校验安装包后，将由安卓系统确认安装。更新期间仍可使用当前版本。")
                .setPositiveButton("现在更新", (dialog, which) -> download(release))
                .setNegativeButton("稍后", null)
                .show();
    }

    private Release fetchRelease() throws Exception {
        HttpURLConnection connection = connect(VERSION_PATH);
        try {
            if (connection.getResponseCode() != 200) throw new IllegalStateException("version status");
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            try (InputStream input = connection.getInputStream()) {
                byte[] buffer = new byte[4096];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    if (bytes.size() + count > 16384) throw new IllegalStateException("version size");
                    bytes.write(buffer, 0, count);
                }
            }
            JSONObject data = new JSONObject(bytes.toString("UTF-8"));
            if (!data.optBoolean("available", false)) throw new IllegalStateException("release unavailable");
            int code = data.getInt("version_code");
            if (code <= BuildConfig.VERSION_CODE) return null;
            String path = data.getString("download_url");
            String hash = data.getString("sha256").toLowerCase(java.util.Locale.ROOT);
            String name = data.optString("version_name", "新版");
            if (!APK_PATH.equals(path) || !hash.matches("[0-9a-f]{64}") || name.length() > 32) {
                throw new IllegalStateException("invalid release metadata");
            }
            return new Release(code, name, hash);
        } finally {
            connection.disconnect();
        }
    }

    private static HttpURLConnection connect(String path) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(ORIGIN + path).openConnection();
        connection.setConnectTimeout(8000);
        connection.setReadTimeout(15000);
        connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("Accept", "application/json");
        return connection;
    }

    private void download(Release release) {
        if (destroyed || busy) return;
        busy = true;
        cancelled = false;
        LinearLayout content = new LinearLayout(activity);
        content.setOrientation(LinearLayout.VERTICAL);
        int padding = Math.round(24 * activity.getResources().getDisplayMetrics().density);
        content.setPadding(padding, padding / 2, padding, 0);
        progressText = new TextView(activity);
        progressText.setText("正在下载更新…");
        progressText.setGravity(Gravity.CENTER_VERTICAL);
        content.addView(progressText);
        progressBar = new ProgressBar(activity, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setIndeterminate(true);
        LinearLayout.LayoutParams barParams = new LinearLayout.LayoutParams(-1, -2);
        barParams.topMargin = padding / 2;
        content.addView(progressBar, barParams);
        downloadDialog = new AlertDialog.Builder(activity)
                .setTitle("更新至 " + release.versionName)
                .setView(content)
                .setNegativeButton("取消", (dialog, which) -> cancelled = true)
                .setOnCancelListener(dialog -> cancelled = true)
                .show();
        worker.execute(() -> {
            File file = null;
            Exception failure = null;
            try { file = downloadAndVerify(release); }
            catch (Exception error) { failure = error; }
            File verified = file;
            Exception problem = failure;
            main.post(() -> {
                busy = false;
                if (downloadDialog != null) downloadDialog.dismiss();
                downloadDialog = null;
                progressBar = null;
                progressText = null;
                if (destroyed || cancelled) return;
                if (problem != null) {
                    showError("更新包下载或校验失败，请重试。", true);
                } else {
                    pendingInstall = verified;
                    if (!foreground) return;
                    if (canRequestInstall()) openInstaller();
                    else requestInstallPermission();
                }
            });
        });
    }

    private File downloadAndVerify(Release release) throws Exception {
        File directory = new File(activity.getCacheDir(), "shared");
        if (!directory.exists() && !directory.mkdirs()) throw new IllegalStateException("cache directory");
        File temporary = new File(directory, "dazi-update.apk.part");
        File verified = new File(directory, "dazi-update.apk");
        HttpURLConnection connection = connect(APK_PATH);
        try {
            if (connection.getResponseCode() != 200) throw new IllegalStateException("download status");
            long expectedLength = connection.getContentLengthLong();
            if (expectedLength > MAX_APK_BYTES) throw new IllegalStateException("APK too large");
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            long total = 0;
            try (InputStream input = connection.getInputStream();
                 FileOutputStream output = new FileOutputStream(temporary)) {
                byte[] buffer = new byte[16384];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    if (cancelled || Thread.currentThread().isInterrupted()) throw new InterruptedException();
                    total += count;
                    if (total > MAX_APK_BYTES) throw new IllegalStateException("APK too large");
                    output.write(buffer, 0, count);
                    digest.update(buffer, 0, count);
                    if (expectedLength > 0) {
                        int percent = (int) Math.min(100, total * 100 / expectedLength);
                        main.post(() -> {
                            if (progressBar != null && progressText != null) {
                                progressBar.setIndeterminate(false);
                                progressBar.setProgress(percent);
                                progressText.setText("正在下载更新… " + percent + "%");
                            }
                        });
                    }
                }
            }
            if (cancelled || total == 0 || (expectedLength >= 0 && total != expectedLength)) {
                throw new IllegalStateException("incomplete APK");
            }
            StringBuilder actualHash = new StringBuilder();
            for (byte value : digest.digest()) actualHash.append(String.format("%02x", value & 0xff));
            if (!release.sha256.equals(actualHash.toString())) throw new IllegalStateException("SHA-256 mismatch");
            if (verified.exists() && !verified.delete()) throw new IllegalStateException("old APK cache");
            if (!temporary.renameTo(verified)) throw new IllegalStateException("APK cache move");
            verifyArchive(verified, release.versionCode);
            return verified;
        } catch (Exception error) {
            temporary.delete();
            verified.delete();
            throw error;
        } finally {
            connection.disconnect();
        }
    }

    private void verifyArchive(File file, int expectedCode) throws Exception {
        PackageManager manager = activity.getPackageManager();
        int flags = Build.VERSION.SDK_INT >= 28
                ? PackageManager.GET_SIGNING_CERTIFICATES : PackageManager.GET_SIGNATURES;
        PackageInfo archive = manager.getPackageArchiveInfo(file.getAbsolutePath(), flags);
        PackageInfo installed = manager.getPackageInfo(activity.getPackageName(), flags);
        if (archive == null || !activity.getPackageName().equals(archive.packageName)) {
            throw new IllegalStateException("APK package mismatch");
        }
        long archiveCode = Build.VERSION.SDK_INT >= 28 ? archive.getLongVersionCode() : archive.versionCode;
        if (archiveCode != expectedCode || archiveCode <= BuildConfig.VERSION_CODE) {
            throw new IllegalStateException("APK version mismatch");
        }
        Signature[] archiveSigners = Build.VERSION.SDK_INT >= 28
                ? archive.signingInfo.getApkContentsSigners() : archive.signatures;
        Signature[] installedSigners = Build.VERSION.SDK_INT >= 28
                ? installed.signingInfo.getApkContentsSigners() : installed.signatures;
        if (archiveSigners == null || installedSigners == null || archiveSigners.length == 0
                || !Arrays.equals(archiveSigners, installedSigners)) {
            throw new IllegalStateException("APK signing mismatch");
        }
    }

    private boolean canRequestInstall() {
        return Build.VERSION.SDK_INT < 26 || activity.getPackageManager().canRequestPackageInstalls();
    }

    private void requestInstallPermission() {
        new AlertDialog.Builder(activity)
                .setTitle("允许安装这次更新")
                .setMessage("安卓需要你先允许“搭子局·应用版”安装更新包。授权后返回应用，会继续打开系统安装确认页。")
                .setPositiveButton("打开设置", (dialog, which) -> {
                    try {
                        awaitingInstallPermission = true;
                        Intent settings = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                                Uri.parse("package:" + activity.getPackageName()));
                        activity.startActivity(settings);
                    } catch (ActivityNotFoundException error) {
                        awaitingInstallPermission = false;
                        pendingInstall = null;
                        showError("无法打开安装权限设置，请在系统设置中允许此应用安装更新。", false);
                    }
                })
                .setNegativeButton("稍后", (dialog, which) -> pendingInstall = null)
                .show();
    }

    private void openInstaller() {
        File file = pendingInstall;
        pendingInstall = null;
        awaitingInstallPermission = false;
        if (file == null || !file.isFile()) return;
        try {
            Uri uri = FileProvider.getUriForFile(activity, activity.getPackageName() + ".files", file);
            Intent install = new Intent(Intent.ACTION_VIEW)
                    .setDataAndType(uri, "application/vnd.android.package-archive")
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            install.setClipData(ClipData.newRawUri("搭子局更新包", uri));
            activity.startActivity(install);
        } catch (ActivityNotFoundException | IllegalArgumentException error) {
            showError("未找到系统安装器，请稍后从下载页面手动安装。", false);
        }
    }

    private void showError(String message, boolean retry) {
        AlertDialog.Builder dialog = new AlertDialog.Builder(activity)
                .setTitle("应用更新")
                .setMessage(message)
                .setNegativeButton("稍后", null);
        if (retry) dialog.setPositiveButton("重试", (ignored, which) -> check(true));
        dialog.show();
    }

    private void toast(String message) {
        Toast.makeText(activity, message, Toast.LENGTH_SHORT).show();
    }

    void onDestroy() {
        destroyed = true;
        cancelled = true;
        worker.shutdownNow();
        if (downloadDialog != null) downloadDialog.dismiss();
    }
}
