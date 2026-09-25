package app.zeabur.ligongdazi.nativeapp;

import android.Manifest;
import android.app.Activity;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.content.pm.PackageManager;
import android.provider.MediaStore;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.webkit.DownloadListener;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.URLUtil;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import androidx.core.content.FileProvider;
import androidx.core.content.ContextCompat;

import com.igexin.sdk.PushManager;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

/** A standalone Android window for the live site; never launches the site in a browser. */
public class MainActivity extends Activity {
    public static final String EXTRA_OPEN_URL = "dazi_open_url";
    private static final String HOST = "ligong-dazi.zeabur.app";
    private static final String START_URL = "https://" + HOST + "/?source=android-native&version=4";
    private static final int PICK_PHOTO = 10;
    private static final int NOTIFICATION_PERMISSION = 11;
    private WebView webView;
    private LinearLayout loadingPanel;
    private TextView loadingDetail;
    private LinearLayout errorPanel;
    private final Handler connectionHandler = new Handler(Looper.getMainLooper());
    private final Runnable slowConnectionNotice = () -> {
        if (loadingPanel != null && loadingPanel.getVisibility() == View.VISIBLE) {
            loadingDetail.setText("服务器响应有点慢，正在继续连接…");
        }
    };
    private ValueCallback<Uri[]> fileCallback;
    private Uri cameraUri;
    private boolean pageFailed;
    private String pendingPushToken;

    private boolean isOurSite(Uri uri) {
        return "https".equalsIgnoreCase(uri.getScheme()) && HOST.equalsIgnoreCase(uri.getHost())
                && (uri.getPort() == -1 || uri.getPort() == 443);
    }

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        PushManager.getInstance().preInit(getApplicationContext());
        getWindow().setStatusBarColor(Color.rgb(20, 17, 27));
        getWindow().setNavigationBarColor(Color.rgb(20, 17, 27));

        FrameLayout root = new FrameLayout(this);
        if (Build.VERSION.SDK_INT >= 35) {
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                view.setPadding(0, bars.top, 0, bars.bottom);
                return insets;
            });
        }
        webView = new WebView(this);
        root.addView(webView, new FrameLayout.LayoutParams(-1, -1));

        loadingPanel = new LinearLayout(this);
        loadingPanel.setOrientation(LinearLayout.VERTICAL);
        loadingPanel.setGravity(Gravity.CENTER);
        loadingPanel.setPadding(dp(32), dp(40), dp(32), dp(40));
        loadingPanel.setBackgroundColor(Color.rgb(20, 17, 27));
        loadingPanel.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        ProgressBar progress = new ProgressBar(this);
        LinearLayout.LayoutParams progressLayout = new LinearLayout.LayoutParams(dp(48), dp(48));
        progressLayout.bottomMargin = dp(24);
        loadingPanel.addView(progress, progressLayout);
        TextView loadingTitle = new TextView(this);
        loadingTitle.setText("正在连接理工搭子局");
        loadingTitle.setTextColor(Color.WHITE);
        loadingTitle.setTextSize(20);
        loadingTitle.setGravity(Gravity.CENTER);
        loadingTitle.setTypeface(loadingTitle.getTypeface(), android.graphics.Typeface.BOLD);
        loadingPanel.addView(loadingTitle);
        loadingDetail = new TextView(this);
        loadingDetail.setText("正在加载最新内容，请稍候…");
        loadingDetail.setTextColor(Color.rgb(192, 181, 211));
        loadingDetail.setTextSize(15);
        loadingDetail.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams detailLayout = new LinearLayout.LayoutParams(-2, -2);
        detailLayout.topMargin = dp(10);
        loadingPanel.addView(loadingDetail, detailLayout);
        root.addView(loadingPanel, new FrameLayout.LayoutParams(-1, -1));

        errorPanel = new LinearLayout(this);
        errorPanel.setOrientation(LinearLayout.VERTICAL);
        errorPanel.setGravity(Gravity.CENTER);
        errorPanel.setPadding(dp(32), dp(40), dp(32), dp(40));
        errorPanel.setBackgroundColor(Color.rgb(20, 17, 27));
        TextView message = new TextView(this);
        message.setText("暂时连不上搭子局\n检查网络后再试一次");
        message.setTextColor(Color.WHITE);
        message.setTextSize(19);
        message.setGravity(Gravity.CENTER);
        errorPanel.addView(message);
        Button retry = new Button(this);
        retry.setText("重新连接");
        retry.setOnClickListener(v -> webView.loadUrl(START_URL));
        errorPanel.addView(retry);
        errorPanel.setVisibility(View.GONE);
        root.addView(errorPanel, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setAllowFileAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setUserAgentString(settings.getUserAgentString() + " LigongDaziNative/4");
        webView.addJavascriptInterface(new CalendarBridge(), "LigongCalendar");
        webView.addJavascriptInterface(new PushBridge(), "LigongPush");

        webView.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (!request.isForMainFrame()) return false;
                Uri uri = request.getUrl();
                if (isOurSite(uri)) return false;
                openOutside(uri);
                return true;
            }

            @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) {
                pageFailed = false;
                showLoading();
            }

            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    pageFailed = true;
                    showConnectionError();
                }
            }

            @Override public void onPageFinished(WebView view, String url) {
                if (!pageFailed) hideConnectionPanels();
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                                        FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                cameraUri = null;
                try {
                    Intent picker = params.createIntent();
                    Intent chooser = Intent.createChooser(picker, "选择周围照片");
                    boolean wantsImage = false;
                    for (String type : params.getAcceptTypes()) {
                        if (type.startsWith("image/") || type.equals("image/*")) wantsImage = true;
                    }
                    if (wantsImage) {
                        File shared = new File(getCacheDir(), "shared");
                        if (!shared.exists() && !shared.mkdirs()) throw new IllegalStateException("无法准备拍照目录");
                        File photo = File.createTempFile("dazi-photo-", ".jpg", shared);
                        cameraUri = FileProvider.getUriForFile(MainActivity.this,
                                getPackageName() + ".files", photo);
                        Intent camera = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
                        camera.putExtra(MediaStore.EXTRA_OUTPUT, cameraUri);
                        camera.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                        if (camera.resolveActivity(getPackageManager()) != null) {
                            chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, new Intent[]{camera});
                        } else {
                            cameraUri = null;
                            photo.delete();
                        }
                    }
                    startActivityForResult(chooser, PICK_PHOTO);
                } catch (Exception e) {
                    fileCallback.onReceiveValue(null);
                    fileCallback = null;
                    toast("暂时无法打开相册，请检查系统相册应用");
                }
                return true;
            }
        });

        webView.setDownloadListener((url, userAgent, disposition, mime, size) -> {
            Uri uri = Uri.parse(url);
            if (!isOurSite(uri)) { openOutside(uri); return; }
            if (url.endsWith(".apk")) { openOutside(uri); return; }
            try {
                DownloadManager.Request request = new DownloadManager.Request(uri);
                request.setMimeType(mime);
                request.setTitle(URLUtil.guessFileName(url, disposition, mime));
                request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                request.setDestinationInExternalFilesDir(this, Environment.DIRECTORY_DOWNLOADS,
                        URLUtil.guessFileName(url, disposition, mime));
                ((DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE)).enqueue(request);
                toast("文件已开始下载");
            } catch (Exception e) { toast("下载未能开始，请稍后重试"); }
        });

        if (NativePushRegistrar.isEnabled(this) && notificationPermissionGranted()) {
            initializeNativePush();
        }
        if (savedInstanceState == null) webView.loadUrl(resolveStartUrl(getIntent()));
        else webView.restoreState(savedInstanceState);
    }

    private String resolveStartUrl(Intent intent) {
        String relative = intent == null ? null : intent.getStringExtra(EXTRA_OPEN_URL);
        if (relative != null && relative.startsWith("/") && !relative.startsWith("//")) {
            String separator = relative.contains("?") ? "&" : "?";
            return "https://" + HOST + relative + separator + "source=android-native&version=4";
        }
        return START_URL;
    }

    private boolean notificationPermissionGranted() {
        return Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(
                this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED;
    }

    private void initializeNativePush() {
        PushManager.getInstance().initialize(getApplicationContext());
    }

    private void requestNativePush(String accessToken) {
        if (accessToken == null || accessToken.length() < 24) return;
        pendingPushToken = accessToken;
        if (!notificationPermissionGranted() && Build.VERSION.SDK_INT >= 33) {
            requestPermissions(
                    new String[]{Manifest.permission.POST_NOTIFICATIONS},
                    NOTIFICATION_PERMISSION);
            return;
        }
        NativePushRegistrar.enable(this, accessToken);
        initializeNativePush();
        notifyPushStatus();
    }

    private void notifyPushStatus() {
        if (webView == null) return;
        JSONObject status = new JSONObject();
        try {
            status.put("enabled", NativePushRegistrar.isEnabled(this));
            status.put("permission", notificationPermissionGranted());
            status.put("connected", !NativePushRegistrar.getCid(this).isEmpty());
        } catch (Exception ignored) {
        }
        webView.evaluateJavascript(
                "window.dispatchEvent(new CustomEvent('dazi-native-push-status',{detail:"
                        + status.toString() + "}));",
                null);
    }

    @Override public void onRequestPermissionsResult(
            int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != NOTIFICATION_PERMISSION) return;
        if (notificationPermissionGranted() && pendingPushToken != null) {
            NativePushRegistrar.enable(this, pendingPushToken);
            initializeNativePush();
        }
        pendingPushToken = null;
        notifyPushStatus();
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (webView != null) webView.loadUrl(resolveStartUrl(intent));
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void showLoading() {
        connectionHandler.removeCallbacks(slowConnectionNotice);
        loadingDetail.setText("正在加载最新内容，请稍候…");
        errorPanel.setVisibility(View.GONE);
        loadingPanel.setVisibility(View.VISIBLE);
        connectionHandler.postDelayed(slowConnectionNotice, 8000);
    }

    private void hideConnectionPanels() {
        connectionHandler.removeCallbacks(slowConnectionNotice);
        loadingPanel.setVisibility(View.GONE);
        errorPanel.setVisibility(View.GONE);
    }

    private void showConnectionError() {
        connectionHandler.removeCallbacks(slowConnectionNotice);
        loadingPanel.setVisibility(View.GONE);
        errorPanel.setVisibility(View.VISIBLE);
    }

    private void openOutside(Uri uri) {
        String scheme = uri.getScheme();
        if (!("https".equalsIgnoreCase(scheme) || "mailto".equalsIgnoreCase(scheme)
                || "tel".equalsIgnoreCase(scheme) || "geo".equalsIgnoreCase(scheme))) return;
        try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); }
        catch (ActivityNotFoundException e) { toast("手机上没有可以打开此链接的应用"); }
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show();
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != PICK_PHOTO || fileCallback == null) return;
        Uri[] selected = resultCode == RESULT_OK ? WebChromeClient.FileChooserParams.parseResult(resultCode, data) : null;
        if (resultCode == RESULT_OK && (selected == null || selected.length == 0) && cameraUri != null) {
            selected = new Uri[]{cameraUri};
        }
        if (selected != null) {
            for (Uri uri : selected) {
                if (!"content".equalsIgnoreCase(uri.getScheme())) { selected = null; break; }
            }
        }
        fileCallback.onReceiveValue(selected);
        fileCallback = null;
        cameraUri = null;
    }

    private class CalendarBridge {
        @JavascriptInterface public void save(String content) {
            runOnUiThread(() -> {
                // The bridge is only used with our own HTTPS page; never accept arbitrary URLs or paths.
                if (webView == null || !isOurSite(Uri.parse(webView.getUrl())) || content == null
                        || content.length() > 262144 || !content.startsWith("BEGIN:VCALENDAR")) return;
                try {
                    File directory = new File(getCacheDir(), "shared");
                    if (!directory.exists() && !directory.mkdirs()) throw new IllegalStateException("无法保存日历");
                    File file = new File(directory, "搭子活动.ics");
                    try (FileOutputStream output = new FileOutputStream(file)) {
                        output.write(content.getBytes(StandardCharsets.UTF_8));
                    }
                    Uri uri = FileProvider.getUriForFile(MainActivity.this, getPackageName() + ".files", file);
                    Intent view = new Intent(Intent.ACTION_VIEW).setDataAndType(uri, "text/calendar")
                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    try { startActivity(Intent.createChooser(view, "导入手机日历")); }
                    catch (ActivityNotFoundException e) {
                        Intent share = new Intent(Intent.ACTION_SEND).setType("text/calendar")
                                .putExtra(Intent.EXTRA_STREAM, uri)
                                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                        startActivity(Intent.createChooser(share, "分享日历文件"));
                    }
                } catch (Exception e) { toast("日历文件无法打开，请稍后重试"); }
            });
        }
    }

    private class PushBridge {
        @JavascriptInterface public String status() {
            JSONObject status = new JSONObject();
            try {
                status.put("enabled", NativePushRegistrar.isEnabled(MainActivity.this));
                status.put("permission", notificationPermissionGranted());
                status.put("connected", !NativePushRegistrar.getCid(MainActivity.this).isEmpty());
            } catch (Exception ignored) {
            }
            return status.toString();
        }

        @JavascriptInterface public void enable(String accessToken) {
            runOnUiThread(() -> requestNativePush(accessToken));
        }

        @JavascriptInterface public void sync(String accessToken) {
            NativePushRegistrar.syncAccount(MainActivity.this, accessToken);
            if (NativePushRegistrar.isEnabled(MainActivity.this)
                    && notificationPermissionGranted()) {
                runOnUiThread(() -> {
                    initializeNativePush();
                    notifyPushStatus();
                });
            }
        }

        @JavascriptInterface public void disable(String accessToken) {
            NativePushRegistrar.disable(MainActivity.this, accessToken);
        }
    }

    @Override public void onBackPressed() {
        if (errorPanel.getVisibility() == View.VISIBLE) { webView.loadUrl(START_URL); return; }
        if (webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override protected void onSaveInstanceState(Bundle outState) {
        webView.saveState(outState);
        super.onSaveInstanceState(outState);
    }

    @Override protected void onDestroy() {
        connectionHandler.removeCallbacks(slowConnectionNotice);
        if (fileCallback != null) fileCallback.onReceiveValue(null);
        if (webView != null) {
            ((ViewGroup) webView.getParent()).removeView(webView);
            webView.destroy();
        }
        super.onDestroy();
    }
}
