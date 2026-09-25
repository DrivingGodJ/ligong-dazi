package app.zeabur.ligongdazi.nativeapp;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/** Keeps the provider CID bound to the account currently signed in inside the WebView. */
final class NativePushRegistrar {
    private static final String API_ROOT = "https://ligong-dazi.zeabur.app/api/v1";
    private static final String PREFERENCES = "dazi_native_push";
    private static final String KEY_TOKEN = "access_token";
    private static final String KEY_CID = "getui_cid";
    private static final String KEY_ENABLED = "enabled";

    private NativePushRegistrar() {
    }

    private static SharedPreferences preferences(Context context) {
        return context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
    }

    static boolean isEnabled(Context context) {
        return preferences(context).getBoolean(KEY_ENABLED, false);
    }

    static String getCid(Context context) {
        return preferences(context).getString(KEY_CID, "");
    }

    static void enable(Context context, String accessToken) {
        if (!validToken(accessToken)) return;
        preferences(context).edit()
                .putBoolean(KEY_ENABLED, true)
                .putString(KEY_TOKEN, accessToken)
                .apply();
        String cid = getCid(context);
        if (!cid.isEmpty()) register(context, cid);
    }

    static void syncAccount(Context context, String accessToken) {
        if (!isEnabled(context) || !validToken(accessToken)) return;
        preferences(context).edit().putString(KEY_TOKEN, accessToken).apply();
        String cid = getCid(context);
        if (!cid.isEmpty()) register(context, cid);
    }

    static void register(Context context, String cid) {
        if (cid == null || !cid.matches("[A-Za-z0-9_-]{16,160}")) return;
        Context appContext = context.getApplicationContext();
        SharedPreferences prefs = preferences(appContext);
        prefs.edit().putString(KEY_CID, cid).apply();
        String token = prefs.getString(KEY_TOKEN, "");
        if (!prefs.getBoolean(KEY_ENABLED, false) || !validToken(token)) return;
        runRequest(appContext, "POST", "/push/native/devices", token, cid, false);
    }

    static void disable(Context context, String accessToken) {
        Context appContext = context.getApplicationContext();
        SharedPreferences prefs = preferences(appContext);
        String token = validToken(accessToken) ? accessToken : prefs.getString(KEY_TOKEN, "");
        String cid = prefs.getString(KEY_CID, "");
        if (validToken(token) && cid.matches("[A-Za-z0-9_-]{16,160}")) {
            runRequest(appContext, "DELETE", "/push/native/devices/" + cid, token, cid, true);
        } else {
            clearAccount(prefs);
        }
    }

    private static boolean validToken(String token) {
        return token != null && token.length() >= 24 && token.length() <= 4096;
    }

    private static void clearAccount(SharedPreferences preferences) {
        preferences.edit().remove(KEY_TOKEN).putBoolean(KEY_ENABLED, false).apply();
    }

    private static void runRequest(
            Context context, String method, String path, String token, String cid,
            boolean clearAfter) {
        new Thread(() -> {
            HttpURLConnection connection = null;
            try {
                connection = (HttpURLConnection) new URL(API_ROOT + path).openConnection();
                connection.setRequestMethod(method);
                connection.setConnectTimeout(8000);
                connection.setReadTimeout(8000);
                connection.setRequestProperty("Authorization", "Bearer " + token);
                connection.setRequestProperty("Accept", "application/json");
                if ("POST".equals(method)) {
                    connection.setDoOutput(true);
                    connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                    byte[] body = new JSONObject().put("cid", cid).toString()
                            .getBytes(StandardCharsets.UTF_8);
                    try (OutputStream output = connection.getOutputStream()) {
                        output.write(body);
                    }
                }
                int status = connection.getResponseCode();
                if (status >= 200 && status < 300 && !clearAfter) {
                    preferences(context).edit().putString(KEY_CID, cid).apply();
                }
            } catch (Exception ignored) {
                // The next login, app resume or CID callback retries registration.
            } finally {
                if (connection != null) connection.disconnect();
                if (clearAfter) clearAccount(preferences(context));
            }
        }, "dazi-push-registration").start();
    }

    static void openNotification(Context context, String payload) {
        String relativeUrl = "/?tab=activities";
        try {
            String candidate = new JSONObject(payload).optString("url", relativeUrl);
            if (candidate.startsWith("/") && !candidate.startsWith("//")) {
                relativeUrl = candidate;
            }
        } catch (Exception ignored) {
            // Keep the safe activities fallback.
        }
        Intent intent = new Intent(context, MainActivity.class)
                .putExtra(MainActivity.EXTRA_OPEN_URL, relativeUrl)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                        | Intent.FLAG_ACTIVITY_CLEAR_TOP
                        | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        context.startActivity(intent);
    }
}
