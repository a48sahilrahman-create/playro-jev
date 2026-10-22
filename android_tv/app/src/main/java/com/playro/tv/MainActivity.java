package com.playro.tv;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.ConnectivityManager;
import android.net.NetworkInfo;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognizerIntent;
import android.text.InputType;
import android.text.Spannable;
import android.text.SpannableString;
import android.text.format.Formatter;
import android.text.style.ForegroundColorSpan;
import android.text.style.StyleSpan;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Universal Android TV & Mobile Activity hosting a hardware-accelerated WebView
 * with Autonomous Multi-Tier Connection Resolution (Local LAN Auto-Discovery + Cloudflare Fallback),
 * native dark reconnect overlay, remote D-Pad forwarding, voice search, and external player bridge.
 */
@SuppressWarnings("deprecation")
public class MainActivity extends Activity {

    private static final String TAG = "PlayroTV";
    private static final String PREFS_NAME = "playro_tv_prefs";
    private static final String KEY_SERVER_URL = "server_url";
    private static final String KEY_SERVER_IP = "server_ip";

    // Primary Default Endpoints
    public static final String DEFAULT_LAN_URL = "http://192.168.220.37:7860";
    public static final String DEFAULT_CLOUD_URL = "https://walk-procedures-weblog-bath.trycloudflare.com";
    public static final int SERVER_DEFAULT_PORT = 7860;

    private static final long CONNECTION_TIMEOUT_MS = 10000;
    private static final int REQUEST_CODE_VOICE_SEARCH = 2001;
    private static final int REQUEST_CODE_AUDIO_PERM = 2002;

    private WebView webView;
    private FrameLayout rootContainer;
    private LinearLayout splashOverlay;
    private LinearLayout reconnectOverlay;
    private TextView statusTextView;
    private TextView reconnectStatusView;
    private TextView reconnectDetailsView;
    private Button btnAutoDiscover;
    private Button btnRetry;
    private Button btnConfigCloud;
    private AlertDialog serverConfigDialog;
    private AlertDialog exitDialog;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final Handler timeoutHandler = new Handler(Looper.getMainLooper());
    private Runnable timeoutRunnable;
    private boolean pageLoaded = false;
    private String currentLoadingUrl = "";
    private final ExecutorService probeExecutor = Executors.newFixedThreadPool(16);
    private final AtomicBoolean isResolving = new AtomicBoolean(false);

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Configure fullscreen layout
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        getWindow().setFlags(
                WindowManager.LayoutParams.FLAG_FULLSCREEN,
                WindowManager.LayoutParams.FLAG_FULLSCREEN
        );
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true);
            setTurnScreenOn(true);
        } else {
            getWindow().addFlags(
                    WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED |
                    WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON |
                    WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD
            );
        }

        hideSystemUI();

        // Audio recording permission for voice search
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQUEST_CODE_AUDIO_PERM);
        }

        // Dynamic Orientation: Landscape for Leanback/TV, Unspecified for Mobile
        if (isTvDevice()) {
            setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE);
        } else {
            setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED);
        }

        // Initialize root container
        rootContainer = new FrameLayout(this);
        rootContainer.setLayoutParams(new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));

        // Initialize hardware-accelerated WebView
        webView = new WebView(this);
        webView.setLayerType(View.LAYER_TYPE_HARDWARE, null);
        webView.setFocusable(true);
        webView.setFocusableInTouchMode(true);
        rootContainer.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));

        // Build native sleek Splash/Loading Overlay
        createSplashOverlay();
        rootContainer.addView(splashOverlay, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));

        // Build native sleek Reconnect/Fallback Overlay
        createReconnectOverlay();
        rootContainer.addView(reconnectOverlay, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));

        setContentView(rootContainer);

        configureWebSettings();

        // Register native JavaScript bridge
        webView.addJavascriptInterface(new AndroidTVBridge(this), "AndroidTVBridge");

        // Kick off Autonomous Multi-Tier Connection Discovery
        resolveAndConnect(false);
    }

    private void createSplashOverlay() {
        float density = getResources().getDisplayMetrics().density;

        splashOverlay = new LinearLayout(this);
        splashOverlay.setOrientation(LinearLayout.VERTICAL);
        splashOverlay.setGravity(Gravity.CENTER);
        splashOverlay.setBackgroundColor(Color.parseColor("#09090b"));
        splashOverlay.setClickable(true);
        splashOverlay.setFocusable(true);

        // App title: "PLAYRO" with "PLAY" in bold white and "RO" in bold vibrant orange
        TextView titleTextView = new TextView(this);
        SpannableString titleSpannable = new SpannableString("PLAYRO");
        titleSpannable.setSpan(new ForegroundColorSpan(Color.parseColor("#FFFFFF")), 0, 4, Spannable.SPAN_EXCLUSIVE_EXCLUSIVE);
        titleSpannable.setSpan(new ForegroundColorSpan(Color.parseColor("#FF6B00")), 4, 6, Spannable.SPAN_EXCLUSIVE_EXCLUSIVE);
        titleSpannable.setSpan(new StyleSpan(Typeface.BOLD), 0, 6, Spannable.SPAN_EXCLUSIVE_EXCLUSIVE);
        titleTextView.setText(titleSpannable);
        titleTextView.setTextSize(TypedValue.COMPLEX_UNIT_SP, 44);
        titleTextView.setLetterSpacing(0.08f);
        titleTextView.setGravity(Gravity.CENTER);
        splashOverlay.addView(titleTextView, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        // Indeterminate clean ProgressBar
        ProgressBar progressBar = new ProgressBar(this);
        progressBar.setIndeterminate(true);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            progressBar.setIndeterminateTintList(ColorStateList.valueOf(Color.parseColor("#FF6B00")));
        }
        int pbSize = (int) (48 * density);
        LinearLayout.LayoutParams pbParams = new LinearLayout.LayoutParams(pbSize, pbSize);
        pbParams.topMargin = (int) (26 * density);
        pbParams.bottomMargin = (int) (16 * density);
        splashOverlay.addView(progressBar, pbParams);

        // Status text
        statusTextView = new TextView(this);
        statusTextView.setText("Connecting to Playro Engine...");
        statusTextView.setTextColor(Color.parseColor("#A1A1AA"));
        statusTextView.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        statusTextView.setGravity(Gravity.CENTER);
        splashOverlay.addView(statusTextView, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));
    }

    private void createReconnectOverlay() {
        float density = getResources().getDisplayMetrics().density;

        reconnectOverlay = new LinearLayout(this);
        reconnectOverlay.setOrientation(LinearLayout.VERTICAL);
        reconnectOverlay.setGravity(Gravity.CENTER);
        reconnectOverlay.setBackgroundColor(Color.parseColor("#09090b"));
        reconnectOverlay.setPadding((int) (28 * density), (int) (28 * density), (int) (28 * density), (int) (28 * density));
        reconnectOverlay.setVisibility(View.GONE);
        reconnectOverlay.setClickable(true);
        reconnectOverlay.setFocusable(true);

        // Title: PLAYRO
        TextView titleTextView = new TextView(this);
        SpannableString titleSpannable = new SpannableString("PLAYRO");
        titleSpannable.setSpan(new ForegroundColorSpan(Color.parseColor("#FFFFFF")), 0, 4, Spannable.SPAN_EXCLUSIVE_EXCLUSIVE);
        titleSpannable.setSpan(new ForegroundColorSpan(Color.parseColor("#FF6B00")), 4, 6, Spannable.SPAN_EXCLUSIVE_EXCLUSIVE);
        titleSpannable.setSpan(new StyleSpan(Typeface.BOLD), 0, 6, Spannable.SPAN_EXCLUSIVE_EXCLUSIVE);
        titleTextView.setText(titleSpannable);
        titleTextView.setTextSize(TypedValue.COMPLEX_UNIT_SP, 36);
        titleTextView.setLetterSpacing(0.08f);
        titleTextView.setGravity(Gravity.CENTER);
        reconnectOverlay.addView(titleTextView);

        // Subtitle Status
        reconnectStatusView = new TextView(this);
        reconnectStatusView.setText("Playro Engine Offline or Unreachable");
        reconnectStatusView.setTextColor(Color.parseColor("#EF4444"));
        reconnectStatusView.setTextSize(TypedValue.COMPLEX_UNIT_SP, 17);
        reconnectStatusView.setTypeface(Typeface.DEFAULT_BOLD);
        reconnectStatusView.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams statusParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        statusParams.topMargin = (int) (14 * density);
        statusParams.bottomMargin = (int) (8 * density);
        reconnectOverlay.addView(reconnectStatusView, statusParams);

        // Technical details label
        reconnectDetailsView = new TextView(this);
        reconnectDetailsView.setText("Probed Local LAN and Cloud endpoints. Ensure Playro server is running on PC or connect via Cloud.");
        reconnectDetailsView.setTextColor(Color.parseColor("#71717A"));
        reconnectDetailsView.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        reconnectDetailsView.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams detailsParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        detailsParams.bottomMargin = (int) (26 * density);
        reconnectOverlay.addView(reconnectDetailsView, detailsParams);

        // Button 1: Auto-Detect on Local Wi-Fi
        btnAutoDiscover = createStyledButton("⚡ Auto-Detect Server on Wi-Fi", "#FF6B00", "#FFFFFF");
        btnAutoDiscover.setOnClickListener(v -> resolveAndConnect(true));
        reconnectOverlay.addView(btnAutoDiscover);

        // Button 2: Retry Connection
        btnRetry = createStyledButton("🔄 Retry Connection", "#27272A", "#F4F4F5");
        btnRetry.setOnClickListener(v -> resolveAndConnect(false));
        LinearLayout.LayoutParams retryParams = (LinearLayout.LayoutParams) btnRetry.getLayoutParams();
        retryParams.topMargin = (int) (12 * density);
        reconnectOverlay.addView(btnRetry);

        // Button 3: Configure Cloud URL
        btnConfigCloud = createStyledButton("🌐 Configure Cloud URL", "#18181B", "#A1A1AA");
        btnConfigCloud.setOnClickListener(v -> showServerConfigDialog("Manual Cloud Configuration"));
        LinearLayout.LayoutParams configParams = (LinearLayout.LayoutParams) btnConfigCloud.getLayoutParams();
        configParams.topMargin = (int) (12 * density);
        reconnectOverlay.addView(btnConfigCloud);
    }

    private Button createStyledButton(String text, String bgColor, String textColor) {
        float density = getResources().getDisplayMetrics().density;
        Button button = new Button(this);
        button.setText(text);
        button.setTextColor(Color.parseColor(textColor));
        button.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setBackgroundColor(Color.parseColor(bgColor));
        button.setPadding((int) (20 * density), (int) (12 * density), (int) (20 * density), (int) (12 * density));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                (int) (280 * density),
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        button.setLayoutParams(params);
        return button;
    }

    private void showSplashOverlay(String message) {
        if (splashOverlay != null) {
            splashOverlay.animate().cancel();
            splashOverlay.setAlpha(1.0f);
            splashOverlay.setVisibility(View.VISIBLE);
            splashOverlay.bringToFront();
            if (statusTextView != null && message != null) {
                statusTextView.setText(message);
            }
        }
        if (reconnectOverlay != null) {
            reconnectOverlay.setVisibility(View.GONE);
        }
    }

    private void hideSplashOverlayWithAnimation() {
        if (splashOverlay != null && splashOverlay.getVisibility() == View.VISIBLE) {
            splashOverlay.animate()
                    .alpha(0f)
                    .setDuration(300)
                    .withEndAction(() -> {
                        splashOverlay.setVisibility(View.GONE);
                        if (webView != null) {
                            webView.requestFocus();
                        }
                    })
                    .start();
        }
    }

    private void showReconnectOverlay(String summary, String details) {
        if (splashOverlay != null) {
            splashOverlay.setVisibility(View.GONE);
        }
        if (reconnectOverlay != null) {
            reconnectOverlay.setVisibility(View.VISIBLE);
            reconnectOverlay.bringToFront();
            if (reconnectStatusView != null && summary != null) {
                reconnectStatusView.setText(summary);
            }
            if (reconnectDetailsView != null && details != null) {
                reconnectDetailsView.setText(details);
            }
        }
    }

    public boolean isTvDevice() {
        return getPackageManager().hasSystemFeature(PackageManager.FEATURE_LEANBACK);
    }

    public String formatUrlForDevice(String url) {
        if (url == null || url.trim().isEmpty()) {
            url = DEFAULT_LAN_URL;
        }
        url = url.trim();
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "http://" + url;
        }

        boolean isTv = isTvDevice();
        if (isTv) {
            // TV: append "?tv=1" if not already present
            if (!url.contains("tv=1")) {
                if (url.contains("?")) {
                    url = url + "&tv=1";
                } else {
                    if (url.endsWith("/")) {
                        url = url + "?tv=1";
                    } else {
                        url = url + "/?tv=1";
                    }
                }
            }
        } else {
            // Phone/touch: load standard clean URL (remove tv=1 parameter)
            if (url.contains("?tv=1&")) {
                url = url.replace("?tv=1&", "?");
            } else if (url.contains("?tv=1")) {
                url = url.replace("?tv=1", "");
            } else if (url.contains("&tv=1")) {
                url = url.replace("&tv=1", "");
            }
            if (url.endsWith("?")) {
                url = url.substring(0, url.length() - 1);
            }
        }
        return url;
    }

    /**
     * Purges obsolete / ephemeral tunnels (e.g. dead localhost.run subdomains)
     */
    private boolean isStaleUrl(String url) {
        if (url == null || url.trim().isEmpty()) return true;
        String lower = url.toLowerCase();
        return lower.contains("lhr.life") ||
               lower.contains("localhost.run") ||
               lower.contains("10.144.84.197") ||
               lower.contains("0.0.0.0");
    }

    public String getSavedServerUrl() {
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        String url = prefs.getString(KEY_SERVER_URL, null);
        if (url == null || url.trim().isEmpty()) {
            url = prefs.getString(KEY_SERVER_IP, null);
        }
        if (isStaleUrl(url)) {
            return null;
        }
        return url;
    }

    public void saveServerUrl(String url) {
        if (url == null || url.trim().isEmpty()) {
            url = DEFAULT_LAN_URL;
        }
        url = formatUrlForDevice(url);
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        prefs.edit()
                .putString(KEY_SERVER_URL, url)
                .putString(KEY_SERVER_IP, url)
                .apply();
    }

    /**
     * Probes an HTTP endpoint for Playro /health contract
     */
    private boolean probeHealth(String baseUrl, int timeoutMs) {
        if (baseUrl == null || baseUrl.trim().isEmpty()) return false;
        HttpURLConnection conn = null;
        try {
            String clean = baseUrl.trim();
            if (clean.contains("?")) {
                clean = clean.substring(0, clean.indexOf("?"));
            }
            if (clean.endsWith("/")) {
                clean = clean.substring(0, clean.length() - 1);
            }
            URL url = new URL(clean + "/health");
            conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(timeoutMs);
            conn.setReadTimeout(timeoutMs);
            conn.setRequestMethod("GET");
            conn.setRequestProperty("User-Agent", "PlayroTV-NativeProbe");
            conn.setInstanceFollowRedirects(false);

            int code = conn.getResponseCode();
            if (code == 200) {
                BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream()));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) {
                    sb.append(line);
                }
                reader.close();
                String body = sb.toString();
                return body.contains("healthy") || body.contains("play_engine");
            }
        } catch (Exception ignored) {
        } finally {
            if (conn != null) {
                try { conn.disconnect(); } catch (Exception ignored) {}
            }
        }
        return false;
    }

    /**
     * Autonomous Multi-Tier Connection Handshake:
     * Tier 1: Saved User URL (if active and not dead)
     * Tier 2: Local LAN Development Host (192.168.220.37:7860)
     * Tier 3: Active Public Cloudflare Tunnel
     * Tier 4: Local Subnet Scan on Port 7860
     * Tier 5: Fallback to Graceful Reconnect Overlay
     */
    public void resolveAndConnect(final boolean forceDeepScan) {
        if (isResolving.getAndSet(true)) {
            return;
        }

        showSplashOverlay("Discovering Playro Engine...");

        probeExecutor.execute(() -> {
            try {
                AtomicReference<String> discoveredUrl = new AtomicReference<>(null);

                // Check Wi-Fi state
                ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
                NetworkInfo activeNetwork = cm != null ? cm.getActiveNetworkInfo() : null;
                boolean isWifi = activeNetwork != null && activeNetwork.getType() == ConnectivityManager.TYPE_WIFI;

                String saved = getSavedServerUrl();

                // 1. Check Saved URL first if present
                if (saved != null && !forceDeepScan) {
                    mainHandler.post(() -> showSplashOverlay("Verifying configured endpoint..."));
                    if (probeHealth(saved, 1200)) {
                        discoveredUrl.set(saved);
                    }
                }

                // 2. Check Local LAN URL (High-speed 100+ Mbps local Wi-Fi path)
                if (discoveredUrl.get() == null && isWifi) {
                    mainHandler.post(() -> showSplashOverlay("Probing Local Wi-Fi Host (192.168.220.37)..."));
                    if (probeHealth(DEFAULT_LAN_URL, 1000)) {
                        discoveredUrl.set(DEFAULT_LAN_URL);
                    }
                }

                // 3. Check Public Cloudflare Tunnel
                if (discoveredUrl.get() == null) {
                    mainHandler.post(() -> showSplashOverlay("Probing Playro Cloud Engine..."));
                    if (probeHealth(DEFAULT_CLOUD_URL, 2000)) {
                        discoveredUrl.set(DEFAULT_CLOUD_URL);
                    }
                }

                // 4. Subnet Parallel Discovery if still null and on Wi-Fi
                if (discoveredUrl.get() == null && isWifi) {
                    mainHandler.post(() -> showSplashOverlay("Scanning Local Wi-Fi subnet for Playro..."));
                    String subnetPrefix = getSubnetPrefix();
                    if (subnetPrefix != null) {
                        discoveredUrl.set(scanSubnetForPlayro(subnetPrefix));
                    }
                }

                final String finalTarget = discoveredUrl.get();

                mainHandler.post(() -> {
                    isResolving.set(false);
                    if (finalTarget != null) {
                        saveServerUrl(finalTarget);
                        showSplashOverlay("Connected! Loading Playro Engine...");
                        loadServer(finalTarget);
                    } else {
                        showReconnectOverlay(
                                "Playro Engine Offline or Unreachable",
                                "Local Wi-Fi (" + DEFAULT_LAN_URL + ") and Cloud were unreachable.\nStart Playro on PC or verify Wi-Fi connection."
                        );
                    }
                });

            } catch (Exception e) {
                Log.e(TAG, "Resolution error", e);
                mainHandler.post(() -> {
                    isResolving.set(false);
                    showReconnectOverlay("Connection Discovery Error", e.getMessage());
                });
            }
        });
    }

    private String getSubnetPrefix() {
        try {
            WifiManager wifiManager = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wifiManager != null) {
                int ip = wifiManager.getConnectionInfo().getIpAddress();
                String ipString = Formatter.formatIpAddress(ip);
                if (ipString != null && ipString.contains(".")) {
                    return ipString.substring(0, ipString.lastIndexOf('.') + 1);
                }
            }
        } catch (Exception ignored) {}
        return "192.168.220.";
    }

    private String scanSubnetForPlayro(String subnetPrefix) {
        final List<String> candidateIps = new ArrayList<>();
        // Priority targets: dev machine, gateway, common local IPs
        candidateIps.add(subnetPrefix + "37");
        candidateIps.add(subnetPrefix + "1");
        candidateIps.add(subnetPrefix + "100");
        candidateIps.add(subnetPrefix + "101");
        for (int i = 2; i <= 60; i++) {
            String ip = subnetPrefix + i;
            if (!candidateIps.contains(ip)) candidateIps.add(ip);
        }

        final AtomicReference<String> found = new AtomicReference<>(null);
        final CountDownLatch latch = new CountDownLatch(candidateIps.size());

        for (final String ip : candidateIps) {
            probeExecutor.execute(() -> {
                try {
                    if (found.get() != null) return;
                    Socket socket = new Socket();
                    socket.connect(new InetSocketAddress(ip, SERVER_DEFAULT_PORT), 350);
                    socket.close();

                    String candUrl = "http://" + ip + ":" + SERVER_DEFAULT_PORT;
                    if (probeHealth(candUrl, 600)) {
                        found.compareAndSet(null, candUrl);
                    }
                } catch (Exception ignored) {
                } finally {
                    latch.countDown();
                }
            });
        }

        try {
            latch.await(3, TimeUnit.SECONDS);
        } catch (InterruptedException ignored) {}

        return found.get();
    }

    private void armConnectionTimeout(final String targetUrl) {
        timeoutHandler.removeCallbacksAndMessages(null);
        timeoutRunnable = () -> {
            if (!pageLoaded && !isFinishing() && !isDestroyed()) {
                Log.w(TAG, "Connection timeout (10s) for URL: " + targetUrl);
                handleConnectionFailure(targetUrl, "Connection timed out (10s):\n" + targetUrl);
            }
        };
        timeoutHandler.postDelayed(timeoutRunnable, CONNECTION_TIMEOUT_MS);
    }

    private void handleConnectionFailure(String failingUrl, String reason) {
        if (pageLoaded) {
            return;
        }
        timeoutHandler.removeCallbacksAndMessages(null);

        runOnUiThread(() -> {
            showReconnectOverlay("Server Connection Failed", reason);
        });
    }

    @Override
    protected void onResume() {
        super.onResume();
        hideSystemUI();
        if (webView != null) {
            webView.onResume();
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (webView != null) {
            webView.onPause();
        }
    }

    @Override
    protected void onDestroy() {
        timeoutHandler.removeCallbacksAndMessages(null);
        if (splashOverlay != null) {
            splashOverlay.animate().cancel();
        }
        if (serverConfigDialog != null && serverConfigDialog.isShowing()) {
            serverConfigDialog.dismiss();
        }
        if (exitDialog != null && exitDialog.isShowing()) {
            exitDialog.dismiss();
        }
        if (webView != null) {
            webView.destroy();
        }
        probeExecutor.shutdownNow();
        super.onDestroy();
    }

    private void hideSystemUI() {
        View decorView = getWindow().getDecorView();
        decorView.setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_FULLSCREEN
        );
    }

    private boolean handleCustomUrl(WebView view, String url) {
        if (url == null || url.trim().isEmpty()) {
            return false;
        }
        if (url.startsWith("about:") || url.startsWith("javascript:")) {
            return false;
        }
        boolean isCustom = url.startsWith("intent:") ||
                           url.startsWith("vlc:") ||
                           url.startsWith("market:") ||
                           (!url.startsWith("http://") && !url.startsWith("https://"));
        if (isCustom) {
            try {
                Intent intent;
                if (url.startsWith("intent:")) {
                    intent = Intent.parseUri(url, Intent.URI_INTENT_SCHEME);
                } else {
                    intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                }
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                view.getContext().startActivity(intent);
                return true;
            } catch (ActivityNotFoundException e) {
                Log.w(TAG, "No application found to handle custom URL: " + url, e);
                Toast.makeText(view.getContext(), "No app found to open link", Toast.LENGTH_SHORT).show();
                return true;
            } catch (Exception e) {
                Log.w(TAG, "Error handling custom URL: " + url, e);
                return true;
            }
        }
        return false;
    }

    private void configureWebSettings() {
        webView.setLayerType(View.LAYER_TYPE_HARDWARE, null);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setSupportZoom(false);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                if (handleCustomUrl(view, url)) {
                    return true;
                }
                return false;
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (request != null && request.getUrl() != null) {
                    if (handleCustomUrl(view, request.getUrl().toString())) {
                        return true;
                    }
                }
                return false;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                if (url == null || url.equals("about:blank")) {
                    return;
                }
                pageLoaded = true;
                timeoutHandler.removeCallbacksAndMessages(null);
                hideSplashOverlayWithAnimation();
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                super.onReceivedError(view, request, error);
                if (request != null && request.isForMainFrame()) {
                    String failingUrl = request.getUrl() != null ? request.getUrl().toString() : "";
                    Log.e(TAG, "Connection failed to server: " + failingUrl);
                    handleConnectionFailure(failingUrl, "Connection failed to server:\n" + failingUrl);
                }
            }

            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                super.onReceivedError(view, errorCode, description, failingUrl);
                Log.e(TAG, "Connection failed to: " + failingUrl + " (" + description + ")");
                handleConnectionFailure(failingUrl, "Connection failed to:\n" + failingUrl);
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse errorResponse) {
                super.onReceivedHttpError(view, request, errorResponse);
                if (request != null && request.isForMainFrame() && errorResponse != null) {
                    int statusCode = errorResponse.getStatusCode();
                    if (statusCode >= 500) {
                        String failingUrl = request.getUrl() != null ? request.getUrl().toString() : "";
                        Log.e(TAG, "HTTP " + statusCode + " error on server: " + failingUrl);
                        // Trigger seamless auto-discovery before throwing error
                        mainHandler.post(() -> resolveAndConnect(false));
                    }
                }
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                runOnUiThread(() -> {
                    try {
                        request.grant(request.getResources());
                    } catch (Exception e) {
                        Log.e(TAG, "Error auto-granting web permission request", e);
                    }
                });
            }
        });
    }

    public void loadServer(String serverAddress) {
        if (serverAddress == null || serverAddress.trim().isEmpty()) {
            serverAddress = DEFAULT_LAN_URL;
        }
        final String targetUrl = formatUrlForDevice(serverAddress);
        currentLoadingUrl = targetUrl;
        pageLoaded = false;

        runOnUiThread(() -> {
            showSplashOverlay("Connecting to Playro Engine...");
            armConnectionTimeout(targetUrl);
            if (webView != null) {
                Log.i(TAG, "Loading Playro server: " + targetUrl);
                webView.loadUrl(targetUrl);
            }
        });
    }

    public void showServerConfigDialog(String reasonMessage) {
        if (isFinishing() || isDestroyed()) {
            return;
        }
        runOnUiThread(() -> {
            if (serverConfigDialog != null && serverConfigDialog.isShowing()) {
                return;
            }

            AlertDialog.Builder builder = new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert);
            builder.setTitle("Playro Server Configuration");

            StringBuilder message = new StringBuilder();
            if (reasonMessage != null && !reasonMessage.trim().isEmpty()) {
                message.append(reasonMessage).append("\n\n");
            }
            message.append("Enter Playro JEV Server URL (Local LAN or Cloudflare):\n(")
                   .append(DEFAULT_LAN_URL)
                   .append(")");
            builder.setMessage(message.toString());

            final EditText input = new EditText(this);
            input.setSingleLine(true);
            input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
            String current = getSavedServerUrl();
            input.setText(current != null ? current : DEFAULT_LAN_URL);
            input.setSelectAllOnFocus(true);

            FrameLayout container = new FrameLayout(this);
            FrameLayout.LayoutParams params = new FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
            );
            int margin = (int) (18 * getResources().getDisplayMetrics().density);
            params.leftMargin = margin;
            params.rightMargin = margin;
            input.setLayoutParams(params);
            container.addView(input);
            builder.setView(container);

            builder.setPositiveButton("Connect", (dialog, which) -> {
                String newUrl = input.getText().toString().trim();
                if (!newUrl.isEmpty()) {
                    saveServerUrl(newUrl);
                    loadServer(newUrl);
                }
            });

            builder.setNeutralButton("Reset Default", (dialog, which) -> {
                saveServerUrl(DEFAULT_LAN_URL);
                loadServer(DEFAULT_LAN_URL);
            });

            builder.setNegativeButton("Cancel", (dialog, which) -> dialog.dismiss());

            serverConfigDialog = builder.create();
            serverConfigDialog.show();
        });
    }

    public void promptExit() {
        if (isFinishing() || isDestroyed()) {
            return;
        }
        runOnUiThread(() -> {
            if (exitDialog != null && exitDialog.isShowing()) {
                return;
            }
            exitDialog = new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                    .setTitle("Exit Playro TV")
                    .setMessage("Are you sure you want to exit?")
                    .setPositiveButton("Exit", (dialog, which) -> finish())
                    .setNegativeButton("Cancel", (dialog, which) -> dialog.dismiss())
                    .create();
            exitDialog.show();
        });
    }

    public void startVoiceSearch() {
        runOnUiThread(() -> {
            try {
                Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
                intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
                intent.putExtra(RecognizerIntent.EXTRA_PROMPT, "Search Playro TV...");
                startActivityForResult(intent, REQUEST_CODE_VOICE_SEARCH);
            } catch (ActivityNotFoundException e) {
                Log.e(TAG, "Speech recognition not supported on this device", e);
                Toast.makeText(this, "Voice search is not supported on this device", Toast.LENGTH_SHORT).show();
            } catch (Exception e) {
                Log.e(TAG, "Failed to launch voice search", e);
                Toast.makeText(this, "Voice search error: " + e.getMessage(), Toast.LENGTH_SHORT).show();
            }
        });
    }

    public void openVlc(String streamUrl) {
        if (streamUrl == null || streamUrl.trim().isEmpty()) {
            Log.e(TAG, "openVlc called with null or empty URL");
            return;
        }
        final String targetUrl = streamUrl.trim();
        runOnUiThread(() -> {
            try {
                Intent intent = new Intent(Intent.ACTION_VIEW);
                intent.setPackage("org.videolan.vlc");
                intent.setDataAndType(Uri.parse(targetUrl), "video/*");
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(intent);
            } catch (ActivityNotFoundException e) {
                Log.w(TAG, "VLC for Android not installed, falling back to external player chooser", e);
                Toast.makeText(this, "VLC not found, opening media chooser...", Toast.LENGTH_SHORT).show();
                openExternalPlayer(targetUrl, "video/*");
            } catch (Exception e) {
                Log.e(TAG, "Failed to launch VLC", e);
                Toast.makeText(this, "Unable to launch VLC: " + e.getMessage(), Toast.LENGTH_SHORT).show();
            }
        });
    }

    public void openExternalPlayer(String streamUrl, String mimeType) {
        if (streamUrl == null || streamUrl.trim().isEmpty()) {
            Log.e(TAG, "openExternalPlayer called with null or empty URL");
            return;
        }
        final String targetUrl = streamUrl.trim();
        final String resolvedMime = (mimeType != null && !mimeType.trim().isEmpty()) ? mimeType.trim() : "video/*";
        runOnUiThread(() -> {
            try {
                Intent intent = new Intent(Intent.ACTION_VIEW);
                intent.setDataAndType(Uri.parse(targetUrl), resolvedMime);
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                Intent chooser = Intent.createChooser(intent, "Play stream with...");
                chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(chooser);
            } catch (Exception e) {
                Log.e(TAG, "Failed to launch external player chooser", e);
                Toast.makeText(this, "No media player available: " + e.getMessage(), Toast.LENGTH_SHORT).show();
            }
        });
    }

    public void openPackagePlayer(String streamUrl, String packageName, String mimeType) {
        if (streamUrl == null || streamUrl.trim().isEmpty()) {
            Log.e(TAG, "openPackagePlayer called with null or empty URL");
            return;
        }
        final String targetUrl = streamUrl.trim();
        final String resolvedMime = (mimeType != null && !mimeType.trim().isEmpty()) ? mimeType.trim() : "video/*";
        runOnUiThread(() -> {
            try {
                Intent intent = new Intent(Intent.ACTION_VIEW);
                intent.setDataAndType(Uri.parse(targetUrl), resolvedMime);
                if (packageName != null && !packageName.trim().isEmpty()) {
                    intent.setPackage(packageName.trim());
                }
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(intent);
            } catch (ActivityNotFoundException e) {
                Log.w(TAG, "Package " + packageName + " not found, falling back to external player chooser", e);
                openExternalPlayer(targetUrl, resolvedMime);
            } catch (Exception e) {
                Log.e(TAG, "Failed to launch package player: " + packageName, e);
                Toast.makeText(this, "Unable to launch player: " + e.getMessage(), Toast.LENGTH_SHORT).show();
            }
        });
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQUEST_CODE_VOICE_SEARCH && resultCode == RESULT_OK && data != null) {
            ArrayList<String> results = data.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS);
            if (results != null && !results.isEmpty()) {
                String voiceResult = results.get(0);
                evaluateVoiceResult(voiceResult);
            }
        }
    }

    private void evaluateVoiceResult(String result) {
        if (result == null) return;
        final String escaped = result.replace("\\", "\\\\")
                                     .replace("'", "\\'")
                                     .replace("\"", "\\\"")
                                     .replace("\n", "\\n")
                                     .replace("\r", "");
        runOnUiThread(() -> {
            if (webView != null) {
                String js = "window.onVoiceSearchResult && window.onVoiceSearchResult('" + escaped + "');" +
                            "if (typeof window.onNativeVoiceResult === 'function') { window.onNativeVoiceResult('" + escaped + "'); }";
                webView.evaluateJavascript(js, null);
            }
        });
    }

    private void forwardKeyToWeb(String keyName, int keyCode) {
        if (webView == null) return;
        webView.post(() -> {
            String js = "if (typeof window.onNativeDpadKey === 'function') { " +
                        "  window.onNativeDpadKey('" + keyName + "', " + keyCode + "); " +
                        "}";
            webView.evaluateJavascript(js, null);
        });
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        if ((serverConfigDialog != null && serverConfigDialog.isShowing()) ||
            (exitDialog != null && exitDialog.isShowing())) {
            return super.dispatchKeyEvent(event);
        }

        int action = event.getAction();
        int keyCode = event.getKeyCode();

        // Handle long press on Menu or Settings keys
        if (keyCode == KeyEvent.KEYCODE_MENU || keyCode == KeyEvent.KEYCODE_SETTINGS) {
            if (action == KeyEvent.ACTION_DOWN) {
                if (event.getRepeatCount() == 0) {
                    event.startTracking();
                } else if (event.isLongPress() ||
                        (event.getEventTime() - event.getDownTime() >= ViewConfiguration.getLongPressTimeout())) {
                    showServerConfigDialog("Menu / Settings key long-pressed");
                    return true;
                }
            }
        }

        // Back navigation
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            if (action == KeyEvent.ACTION_DOWN) {
                if (reconnectOverlay != null && reconnectOverlay.getVisibility() == View.VISIBLE) {
                    promptExit();
                    return true;
                }
                if (splashOverlay != null && splashOverlay.getVisibility() == View.VISIBLE) {
                    promptExit();
                    return true;
                }
                forwardKeyToWeb("Back", keyCode);
                return true;
            } else if (action == KeyEvent.ACTION_UP) {
                if ((reconnectOverlay != null && reconnectOverlay.getVisibility() == View.VISIBLE) ||
                    (splashOverlay != null && splashOverlay.getVisibility() == View.VISIBLE)) {
                    return true;
                }
                if (webView != null && webView.canGoBack()) {
                    webView.goBack();
                } else {
                    promptExit();
                }
                return true;
            }
        }

        String keyName = null;
        switch (keyCode) {
            case KeyEvent.KEYCODE_DPAD_UP:
                keyName = "ArrowUp";
                break;
            case KeyEvent.KEYCODE_DPAD_DOWN:
                keyName = "ArrowDown";
                break;
            case KeyEvent.KEYCODE_DPAD_LEFT:
                keyName = "ArrowLeft";
                break;
            case KeyEvent.KEYCODE_DPAD_RIGHT:
                keyName = "ArrowRight";
                break;
            case KeyEvent.KEYCODE_DPAD_CENTER:
            case KeyEvent.KEYCODE_ENTER:
                keyName = "Enter";
                break;
            case KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE:
                keyName = "MediaPlayPause";
                break;
            case KeyEvent.KEYCODE_MEDIA_PLAY:
                keyName = "MediaPlay";
                break;
            case KeyEvent.KEYCODE_MEDIA_PAUSE:
                keyName = "MediaPause";
                break;
            case KeyEvent.KEYCODE_MEDIA_FAST_FORWARD:
                keyName = "MediaFastForward";
                break;
            case KeyEvent.KEYCODE_MEDIA_REWIND:
                keyName = "MediaRewind";
                break;
            case KeyEvent.KEYCODE_MENU:
                keyName = "Menu";
                break;
            case KeyEvent.KEYCODE_SETTINGS:
                keyName = "Settings";
                break;
        }

        if (keyName != null && action == KeyEvent.ACTION_DOWN) {
            forwardKeyToWeb(keyName, keyCode);
        }

        return super.dispatchKeyEvent(event);
    }

    @Override
    public boolean onKeyLongPress(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_MENU || keyCode == KeyEvent.KEYCODE_SETTINGS) {
            showServerConfigDialog("Menu / Settings key long-pressed");
            return true;
        }
        return super.onKeyLongPress(keyCode, event);
    }

    @Override
    public void onBackPressed() {
        if ((reconnectOverlay != null && reconnectOverlay.getVisibility() == View.VISIBLE) ||
            (splashOverlay != null && splashOverlay.getVisibility() == View.VISIBLE)) {
            promptExit();
            return;
        }
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            promptExit();
        }
    }

    @Override
    public boolean dispatchTouchEvent(MotionEvent event) {
        // 3-finger tap on mobile to open server configuration
        if (event.getPointerCount() >= 3) {
            int action = event.getActionMasked();
            if (action == MotionEvent.ACTION_POINTER_DOWN || action == MotionEvent.ACTION_DOWN) {
                showServerConfigDialog("3-Finger Tap Detected");
                return true;
            }
        }
        return super.dispatchTouchEvent(event);
    }

    /**
     * Native JavaScript Interface exposed as window.AndroidTVBridge
     */
    public class AndroidTVBridge {
        private final Context context;

        public AndroidTVBridge(Context context) {
            this.context = context;
        }

        @JavascriptInterface
        public void startVoiceSearch() {
            MainActivity.this.startVoiceSearch();
        }

        @JavascriptInterface
        public void openVlc(String streamUrl) {
            MainActivity.this.openVlc(streamUrl);
        }

        @JavascriptInterface
        public void openExternalPlayer(String streamUrl, String mimeType) {
            MainActivity.this.openExternalPlayer(streamUrl, mimeType);
        }

        @JavascriptInterface
        public void openPackagePlayer(String streamUrl, String packageName, String mimeType) {
            MainActivity.this.openPackagePlayer(streamUrl, packageName, mimeType);
        }

        @JavascriptInterface
        public String getServerIp() {
            String saved = MainActivity.this.getSavedServerUrl();
            return saved != null ? saved : DEFAULT_LAN_URL;
        }

        @JavascriptInterface
        public void setServerIp(String ip) {
            MainActivity.this.saveServerUrl(ip);
            runOnUiThread(() -> MainActivity.this.loadServer(ip));
        }

        @JavascriptInterface
        public String getServerUrl() {
            String saved = MainActivity.this.getSavedServerUrl();
            return saved != null ? saved : DEFAULT_LAN_URL;
        }

        @JavascriptInterface
        public void setServerUrl(String url) {
            MainActivity.this.saveServerUrl(url);
            runOnUiThread(() -> MainActivity.this.loadServer(url));
        }

        @JavascriptInterface
        public void showServerConfig() {
            runOnUiThread(() -> MainActivity.this.showServerConfigDialog("Requested from web interface"));
        }

        @JavascriptInterface
        public void promptExit() {
            MainActivity.this.promptExit();
        }

        @JavascriptInterface
        public void scanServers() {
            runOnUiThread(() -> MainActivity.this.resolveAndConnect(true));
        }
    }
}
