package app.zeabur.ligongdazi.nativeapp;

import android.Manifest;
import android.app.Activity;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
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
import android.webkit.ServiceWorkerClient;
import android.webkit.ServiceWorkerController;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
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
import androidx.webkit.WebViewAssetLoader;

import com.igexin.sdk.PushManager;

import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Collections;

/** A standalone Android window with bundled UI and live data from our HTTPS API. */
public class MainActivity extends Activity {
    public static final String EXTRA_OPEN_URL = "dazi_open_url";
    private static final String HOST = "ligong-dazi.zeabur.app";
    private static final String START_URL = "https://" + HOST + "/?source=android-native&version=" + BuildConfig.VERSION_CODE;
    private static final int PICK_PHOTO = 10;
    private static final int NOTIFICATION_PERMISSION = 11;
    private WebView webView;
    private WebViewAssetLoader bundledAssets;
    private TextView offlineBanner;
    private ConnectivityManager connectivityManager;
    private ConnectivityManager.NetworkCallback connectivityCallback;
    private FrameLayout loadingPanel;
    private TextView loadingDetail;
    private LinearLayout errorPanel;
    private final Handler connectionHandler = new Handler(Looper.getMainLooper());
    private final Runnable slowConnectionNotice = () -> {
        if (loadingPanel != null && loadingPanel.getVisibility() == View.VISIBLE) {
            loadingDetail.setText("界面加载有点慢，正在继续打开…");
        }
    };
    private ValueCallback<Uri[]> fileCallback;
    private final Runnable connectionTimeout = () -> {
        if (loadingPanel.getVisibility() == View.VISIBLE) {
            pageFailed = true;
            webView.stopLoading();
            showConnectionError();
        }
    };
    private Uri cameraUri;
    private boolean pageFailed;
    private String pendingPushToken;
    private NativeUpdateManager updateManager;
    private boolean wasOnline;

    private boolean isOurSite(Uri uri) {
        return "https".equalsIgnoreCase(uri.getScheme()) && HOST.equalsIgnoreCase(uri.getHost())
                && (uri.getPort() == -1 || uri.getPort() == 443);
    }

    private WebResourceResponse missingBundledResource() {
        WebResourceResponse response = new WebResourceResponse("text/plain", "UTF-8",
                new ByteArrayInputStream("安装包缺少页面资源".getBytes(StandardCharsets.UTF_8)));
        response.setStatusCodeAndReasonPhrase(404, "Not Found");
        return response;
    }

    private WebResourceResponse bundledResource(String fileName, String mimeType) {
        try {
            WebResourceResponse response = new WebResourceResponse(mimeType, "UTF-8",
                    getAssets().open(fileName));
            response.setResponseHeaders(Collections.singletonMap("Cache-Control", "no-store"));
            return response;
        } catch (IOException error) {
            return missingBundledResource();
        }
    }

    /** Never fall through to the website for a missing UI file; only data paths use the network. */
    private WebResourceResponse interceptBundledUi(Uri uri, String method) {
        if (!"GET".equalsIgnoreCase(method) || !isOurSite(uri)) return null;
        String path = uri.getPath();
        if ("/".equals(path) || "/index.html".equals(path)) {
            return bundledResource("index.html", "text/html");
        }
        if ("/manifest.webmanifest".equals(path)) {
            return bundledResource("manifest.webmanifest", "application/manifest+json");
        }
        if ("/service-worker.js".equals(path)) {
            return bundledResource("service-worker.js", "application/javascript");
        }
        if (path != null && path.startsWith("/static/")) {
            WebResourceResponse response = bundledAssets.shouldInterceptRequest(uri);
            return response == null ? missingBundledResource() : response;
        }
        return null;
    }

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        PushManager.getInstance().preInit(getApplicationContext());
        getWindow().setStatusBarColor(Color.rgb(20, 17, 27));
        getWindow().setNavigationBarColor(Color.rgb(20, 17, 27));

        bundledAssets = new WebViewAssetLoader.Builder()
                .setDomain(HOST)
                .addPathHandler("/static/", new WebViewAssetLoader.AssetsPathHandler(this))
                .build();
        // Previous app versions registered the site's worker. Route its fetches to the APK too.
        if (Build.VERSION.SDK_INT >= 24) {
            ServiceWorkerController.getInstance().setServiceWorkerClient(new ServiceWorkerClient() {
                @Override public WebResourceResponse shouldInterceptRequest(WebResourceRequest request) {
                    return interceptBundledUi(request.getUrl(), request.getMethod());
                }
            });
        }

        FrameLayout root = new FrameLayout(this);
        if (Build.VERSION.SDK_INT >= 35) {
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                view.setPadding(0, bars.top, 0, bars.bottom);
                return insets;
            });
        }
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        root.addView(content, new FrameLayout.LayoutParams(-1, -1));

        offlineBanner = new TextView(this);
        offlineBanner.setText("当前已离线 · 页面仍可打开，数据需联网");
        offlineBanner.setTextSize(14);
        offlineBanner.setGravity(Gravity.CENTER);
        offlineBanner.setPadding(dp(16), dp(10), dp(16), dp(10));
        offlineBanner.setMinHeight(dp(44));
        offlineBanner.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        boolean nightMode = (getResources().getConfiguration().uiMode
                & android.content.res.Configuration.UI_MODE_NIGHT_MASK)
                == android.content.res.Configuration.UI_MODE_NIGHT_YES;
        offlineBanner.setBackgroundColor(nightMode ? Color.rgb(60, 39, 77) : Color.rgb(242, 232, 255));
        offlineBanner.setTextColor(nightMode ? Color.WHITE : Color.rgb(67, 31, 110));
        offlineBanner.setVisibility(View.GONE);
        content.addView(offlineBanner, new LinearLayout.LayoutParams(-1, -2));

        FrameLayout pageFrame = new FrameLayout(this);
        content.addView(pageFrame, new LinearLayout.LayoutParams(-1, 0, 1));
        webView = new WebView(this);
        pageFrame.addView(webView, new FrameLayout.LayoutParams(-1, -1));

        loadingPanel = new FrameLayout(this);
        loadingPanel.setBackgroundColor(Color.rgb(249, 245, 255));
        WebView artwork = new WebView(this);
        artwork.setBackgroundColor(Color.TRANSPARENT);
        artwork.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
        artwork.setFocusable(false);
        artwork.setOnTouchListener((v, event) -> true);
        try (java.io.BufferedReader reader = new java.io.BufferedReader(new java.io.InputStreamReader(
                getAssets().open("launch-art.html"), StandardCharsets.UTF_8))) {
            StringBuilder html = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) html.append(line).append('\n');
            boolean night = (getResources().getConfiguration().uiMode & android.content.res.Configuration.UI_MODE_NIGHT_MASK)
                    == android.content.res.Configuration.UI_MODE_NIGHT_YES;
            artwork.loadDataWithBaseURL("file:///android_asset/", html.toString().replace(
                    "@media(prefers-color-scheme:dark)", night ? "@media all" : "@media not all"),
                    "text/html", "UTF-8", null);
        } catch (java.io.IOException ignored) { /* Loading text remains available. */ }
        loadingPanel.addView(artwork, new FrameLayout.LayoutParams(-1, -1));
        loadingDetail = new TextView(this);
        loadingDetail.setText("正在加载…");
        loadingDetail.setTextColor(Color.rgb(84, 42, 138));
        loadingDetail.setBackgroundColor(Color.argb(242, 250, 247, 255));
        loadingDetail.setPadding(dp(18), dp(12), dp(18), dp(12));
        loadingDetail.setTextSize(14);
        loadingDetail.setGravity(Gravity.CENTER);
        loadingDetail.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        FrameLayout.LayoutParams detailLayout = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
        detailLayout.bottomMargin = dp(20);
        loadingPanel.addView(loadingDetail, detailLayout);
        if ((getResources().getConfiguration().uiMode & android.content.res.Configuration.UI_MODE_NIGHT_MASK)
                == android.content.res.Configuration.UI_MODE_NIGHT_YES) {
            loadingPanel.setBackgroundColor(Color.rgb(23, 18, 31));
            loadingDetail.setBackgroundColor(Color.rgb(43, 32, 56));
            loadingDetail.setTextColor(Color.rgb(234, 217, 255));
        }
        pageFrame.addView(loadingPanel, new FrameLayout.LayoutParams(-1, -1));

        errorPanel = new LinearLayout(this);
        errorPanel.setOrientation(LinearLayout.VERTICAL);
        errorPanel.setGravity(Gravity.CENTER);
        errorPanel.setPadding(dp(32), dp(40), dp(32), dp(40));
        errorPanel.setBackgroundColor(Color.rgb(20, 17, 27));
        TextView message = new TextView(this);
        message.setText("暂时无法打开搭子局\n请重新打开或检查网络");
        message.setTextColor(Color.WHITE);
        message.setTextSize(19);
        message.setGravity(Gravity.CENTER);
        errorPanel.addView(message);
        Button retry = new Button(this);
        retry.setText("重新连接");
        retry.setOnClickListener(v -> webView.loadUrl(START_URL));
        errorPanel.addView(retry);
        errorPanel.setVisibility(View.GONE);
        pageFrame.addView(errorPanel, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        updateManager = new NativeUpdateManager(this);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setSupportZoom(true);
        settings.setBuiltInZoomControls(true);
        settings.setDisplayZoomControls(false);
        settings.setAllowFileAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setUserAgentString(settings.getUserAgentString() + " LigongDaziNative/" + BuildConfig.VERSION_CODE);
        webView.addJavascriptInterface(new CalendarBridge(), "LigongCalendar");
        webView.addJavascriptInterface(new PushBridge(), "LigongPush");

        webView.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view,
                    WebResourceRequest request) {
                return interceptBundledUi(request.getUrl(), request.getMethod());
            }

            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (!request.isForMainFrame()) return false;
                Uri uri = request.getUrl();
                if (isNativeApkLink(uri)) {
                    updateManager.check(true);
                    return true;
                }
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

            @Override public void onReceivedHttpError(WebView view, WebResourceRequest request,
                    android.webkit.WebResourceResponse response) {
                if (request.isForMainFrame()) {
                    pageFailed = true;
                    showConnectionError();
                }
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
            if (isNativeApkLink(uri)) { updateManager.check(true); return; }
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
            return "https://" + HOST + relative + separator + "source=android-native&version=" + BuildConfig.VERSION_CODE;
        }
        return START_URL;
    }

    private boolean isNativeApkLink(Uri uri) {
        return isOurSite(uri) && "/downloads/ligong-dazi-native.apk".equals(uri.getPath());
    }

    private boolean notificationPermissionGranted() {
        return (Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(
                this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED)
                && androidx.core.app.NotificationManagerCompat.from(this).areNotificationsEnabled();
    }

    private void initializeNativePush() {
        PushManager.getInstance().initialize(getApplicationContext());
        PushManager.getInstance().turnOnPush(getApplicationContext());
    }

    private void pauseNativePush() {
        pendingPushToken = null;
        PushManager.getInstance().turnOffPush(getApplicationContext());
        notifyPushStatus();
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
            status.put("cid", NativePushRegistrar.getCid(this));
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

    @Override protected void onStart() {
        super.onStart();
        watchConnectivity();
    }

    private void watchConnectivity() {
        connectivityManager = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        refreshConnectivity();
        if (connectivityManager == null) return;
        connectivityCallback = new ConnectivityManager.NetworkCallback() {
            @Override public void onAvailable(Network network) { queueConnectivityRefresh(); }
            @Override public void onLost(Network network) { queueConnectivityRefresh(); }
            @Override public void onCapabilitiesChanged(Network network, NetworkCapabilities capabilities) {
                queueConnectivityRefresh();
            }
        };
        if (Build.VERSION.SDK_INT >= 24) {
            connectivityManager.registerDefaultNetworkCallback(connectivityCallback);
        } else {
            // Android 6 has no default-network callback; observe changes and query the active network.
            connectivityManager.registerNetworkCallback(new NetworkRequest.Builder().build(), connectivityCallback);
        }
    }

    private void queueConnectivityRefresh() {
        runOnUiThread(this::refreshConnectivity);
    }

    @Override protected void onStop() {
        if (connectivityManager != null && connectivityCallback != null) {
            connectivityManager.unregisterNetworkCallback(connectivityCallback);
            connectivityCallback = null;
        }
        super.onStop();
    }

    private void refreshConnectivity() {
        if (offlineBanner == null) return;
        Network network = connectivityManager == null ? null : connectivityManager.getActiveNetwork();
        NetworkCapabilities capabilities = network == null ? null
                : connectivityManager.getNetworkCapabilities(network);
        boolean online = capabilities != null
                && capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                && capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED);
        offlineBanner.setVisibility(online ? View.GONE : View.VISIBLE);
        if (online && !wasOnline && updateManager != null) updateManager.onNetworkRestored();
        wasOnline = online;
    }

    @Override protected void onResume() {
        super.onResume();
        refreshConnectivity();
        if (updateManager != null) updateManager.onResume();
    }

    @Override protected void onPause() {
        if (updateManager != null) updateManager.onPause();
        super.onPause();
    }

    private void showLoading() {
        connectionHandler.removeCallbacks(connectionTimeout);
        connectionHandler.postDelayed(connectionTimeout, 20000);
        connectionHandler.removeCallbacks(slowConnectionNotice);
        loadingDetail.setText("正在打开搭子局…");
        errorPanel.setVisibility(View.GONE);
        loadingPanel.setVisibility(View.VISIBLE);
        connectionHandler.postDelayed(slowConnectionNotice, 8000);
    }

    private void hideConnectionPanels() {
        connectionHandler.removeCallbacks(connectionTimeout);
        connectionHandler.removeCallbacks(slowConnectionNotice);
        loadingPanel.setVisibility(View.GONE);
        errorPanel.setVisibility(View.GONE);
    }

    private void showConnectionError() {
        connectionHandler.removeCallbacks(connectionTimeout);
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
                status.put("cid", NativePushRegistrar.getCid(MainActivity.this));
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
            runOnUiThread(() -> pauseNativePush());
        }

        @JavascriptInterface public void disableLocal() {
            // The webpage has already removed this CID from the server.
            NativePushRegistrar.disableLocally(MainActivity.this);
            runOnUiThread(() -> pauseNativePush());
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
        if (updateManager != null) updateManager.onDestroy();
        connectionHandler.removeCallbacks(slowConnectionNotice);
        connectionHandler.removeCallbacks(connectionTimeout);
        if (fileCallback != null) fileCallback.onReceiveValue(null);
        if (webView != null) {
            ((ViewGroup) webView.getParent()).removeView(webView);
            webView.destroy();
        }
        super.onDestroy();
    }
}
