# Playro TV - Turnkey Android TV Client

Turnkey Android TV leanback client hosting a hardware-accelerated WebView connected to Playro JEV streaming engine.

---

## 🚀 1-Click Build & Install via ADB

### Prerequisites
- Android SDK / Android Studio Command Line Tools
- ADB installed and added to `PATH`
- Target Android TV device connected via Wi-Fi or USB with Developer Options & USB Debugging enabled

### 1. Build APK
From the `android_tv` root directory:
```bash
# Windows
gradlew.bat assembleDebug

# Linux / macOS
./gradlew assembleDebug
```
The compiled APK will be located at:
`app/build/outputs/apk/debug/app-debug.apk`

### 2. Connect to Android TV via ADB
Find your Android TV's IP address (Settings -> Network & Internet -> Connected Wi-Fi -> IP address):
```bash
adb connect <ANDROID_TV_IP>:5555
```
Verify device connection:
```bash
adb devices
```

### 3. 1-Click Install & Launch
```bash
# Install to device
adb install -r app/build/outputs/apk/debug/app-debug.apk

# Launch Playro TV immediately
adb shell am start -n com.playro.tv/.MainActivity
```

Or run directly with convenience command:
```bash
adb install playro-tv.apk
```

---

## 📁 Directory Structure
```
android_tv/
├── app/
│   ├── build.gradle                              # Android TV build configuration with AndroidX Leanback
│   └── src/
│       └── main/
│           ├── AndroidManifest.xml               # Leanback launcher, permissions & touchscreen=false flags
│           └── java/
│               └── com/
│                   └── playro/
│                       └── tv/
│                           └── MainActivity.java # Fullscreen hardware-accelerated WebView & Native Bridge
└── README.md                                     # Build and deployment documentation
```

---

## ⚡ Features & Native Bridge Architecture

### 1. Native TV WebView Integration
- **Hardware Acceleration**: Enabled in Manifest and View layer (`View.LAYER_TYPE_HARDWARE`).
- **Immersive Fullscreen Sticky Mode**: System bars hidden automatically with TV landscape lock.
- **Auto Audio & Media Permissions**: `setWebChromeClient` automatically grants camera/microphone media stream permissions.
- **WebSettings Optimization**:
  - `JavaScriptEnabled`: true
  - `DomStorageEnabled`: true
  - `MediaPlaybackRequiresUserGesture`: false (instant autoplay)
  - `AllowFileAccess`: true

### 2. JavaScript Interface (`AndroidTVBridge`)
Exposed in JavaScript runtime as `window.AndroidTVBridge`:

| Method | Description |
|---|---|
| `startVoiceSearch()` | Launches native `RecognizerIntent.ACTION_RECOGNIZE_SPEECH` and evaluates `window.onNativeVoiceResult(result)` |
| `getServerIp()` | Returns configured Playro server address (persisted in `SharedPreferences`) |
| `setServerIp(ip)` | Updates server IP address, persists to storage, and immediately navigates to server |

#### Voice Search Callback Handling (Web Client):
```javascript
window.onNativeVoiceResult = function(transcript) {
    console.log("Received voice query from Android TV:", transcript);
    // Trigger Playro JEV search input and submit
    const searchInput = document.querySelector('input[type="search"], #search-box');
    if (searchInput) {
        searchInput.value = transcript;
        searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    }
};
```

### 3. Remote D-Pad & Media Key Forwarding
Native hardware D-Pad navigation keys (`ArrowUp`, `ArrowDown`, `ArrowLeft`, `ArrowRight`, `Enter`, `Back`, `MediaPlayPause`, `MediaFastForward`, `MediaRewind`, `Menu`) are intercepted and dispatched to the web application:

```javascript
window.onNativeDpadKey = function(keyName, keyCode) {
    console.log("TV Remote Key Pressed:", keyName, keyCode);
};
```
Normal spatial navigation and WebView focus events pass through untouched to support standard HTML5 TV focus navigation.
