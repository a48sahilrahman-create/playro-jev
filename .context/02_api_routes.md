# Play Engine: API Routes & Specification Reference

## 1. Complete FastAPI Route Catalog

The Play Engine addon exposes 9 primary HTTP routes across user configuration, Stremio addon protocol compliance, keepalive infrastructure, streaming gateways, and interactive authentication.

```
                                  HTTP Request Router
                                           |
    +------------------+-------------------+-------------------+------------------+
    |                  |                   |                   |                  |
    v                  v                   v                   v                  v
Web & PWA          Health Ping         Addon Manifest       Stream Catalog      On-Demand 302
/                  /health             /manifest.json       /stream/{type}/     /playback/resolve
/configure                             /{cfg}/manifest.json   {id}.json
                                                            /{cfg}/stream/
                                                              {type}/{id}.json
                                                               |
                                                               v
                                                      Auth Bridge Endpoints
                                                      POST /api/auth/trigger-login
                                                      GET  /api/auth/status
```

---

### Route 1: `GET /`
- **Purpose**: Universal cross-platform PWA index or automated redirect.
- **Handler**: `serve_ott_app()`
- **Response**: `HTMLResponse` / `FileResponse` (`static/index.html`) if file exists; otherwise `307/302 RedirectResponse(url="/configure")`.
- **Media Type**: `text/html`
- **Companion File**: Supports `GET /manifest.webmanifest` returning `static/manifest.webmanifest` for mobile and Android TV home screen installation.

---

### Route 2: `GET /configure`
- **Purpose**: High-craft dark neutral configuration portal (Vercel/Linear design aesthetic) for generating isolated, credential-encrypted Stremio installation URLs.
- **Handler**: `configure_page(request: Request)`
- **Response**: `HTMLResponse` (HTTP 200)
- **UI Architecture**:
  - Tailwind CSS dark palette (`zinc-950` backdrop, `zinc-900/60` container, `border-white/[0.08]`).
  - Typography: `Geist Sans` and `JetBrains Mono`.
  - Form Fields:
    1. **Public Scrapers Toggle** (`publicScrapers`): Checkbox (default: checked). Enables UHDMovies, VegaMovies, HDHub4u, FastDL.
    2. **OlaMovies Session Cookie** (`olaCookie`): Password input for `PHPSESSID` or authenticated SSO tokens.
    3. **Personal Telegram Bot Token** (`tgBotToken`): Password input for private user-scoped Telegram library bots.
    4. **Telegram Chat / Group ID** (`tgChatId`): Text input (e.g. `-1001234567890`).
- **Client-side URL Encoder**:
  ```javascript
  const config = {
      public: document.getElementById('publicScrapers').checked,
      ola: document.getElementById('olaCookie').value.trim(),
      tg_token: document.getElementById('tgBotToken').value.trim(),
      tg_chat: document.getElementById('tgChatId').value.trim()
  };
  const jsonStr = JSON.stringify(config);
  const b64 = btoa(unescape(encodeURIComponent(jsonStr)))
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '');
  const manifestUrl = `${window.location.origin}/${b64}/manifest.json`;
  const stremioProtocolUrl = manifestUrl.replace(/^https?:\/\//, 'stremio://');
  ```

---

### Route 3: `GET /health`
- **Purpose**: Low-overhead keepalive ping endpoint for cloud hosting providers (Render, Koyeb, Hugging Face, Fly.io).
- **Handler**: `health_check()`
- **Response**: `JSONResponse` (HTTP 200)
- **Payload Schema**:
  ```json
  {
    "status": "healthy",
    "service": "play_engine_stremio_addon",
    "timestamp": 1726862400,
    "uptime": "online"
  }
  ```

---

### Route 4: `GET /manifest.json`
- **Purpose**: Public root Stremio Addon manifest endpoint for default configuration.
- **Handler**: `get_manifest(user_config: str = "default")`
- **Response**: `JSONResponse` (HTTP 200)
- **Manifest Definition**:
  ```json
  {
    "id": "org.playengine.cloudstreamer",
    "version": "1.1.0",
    "name": "Play Engine (Universal 4K)",
    "description": "Multi-Source 4K/1080p Media Engine with on-demand bypasses and private bot isolation.",
    "resources": ["stream"],
    "types": ["movie", "series"],
    "idPrefixes": ["tt", "play:"],
    "catalogs": [],
    "behaviorHints": {
      "configurable": true,
      "configurationRequired": false
    }
  }
  ```

---

### Route 5: `GET /{user_config}/manifest.json`
- **Purpose**: User-specific parameterized manifest endpoint with custom bot identity reflection.
- **Handler**: `get_manifest(user_config: str)`
- **Path Parameter**: `user_config` (URL-safe base64 string).
- **Response**: `JSONResponse` (HTTP 200)
- **Dynamic Customization**:
  - Decodes `user_config` via `decode_user_config()`.
  - If `cfg.get("tg_token")` is present and valid, modifies the manifest name dynamically:
    ```python
    manifest["name"] = "Play Engine (⚡ Custom Private Bot)"
    ```

---

### Route 6: `GET /{user_config}/stream/{media_type}/{stream_id}.json`
*(Also registered on `/stream/{media_type}/{stream_id}.json`, `/{user_config}/stream/{media_type}/{stream_id}`, and `/stream/{media_type}/{stream_id}`)*
- **Purpose**: Master Stremio stream dispatcher. Resolves catalogs, queries parallel scraper fleet, filters episodes, and delivers playable stream arrays.
- **Handler**: `get_streams(media_type: str, stream_id: str, request: Request, user_config: str = "default")`
- **Parameters**:
  - `media_type`: `"movie"` or `"series"`
  - `stream_id`: IMDb ID (e.g. `tt0468569`, `tt0903747:1:1`) or custom query (`play:Inception`)
  - `user_config`: URL-safe base64 string or `"default"`
- **Execution Flow**:
  1. Decodes `user_config` without exposing raw secrets.
  2. Strips `.json` suffix and removes `play:` prefix if present.
  3. Parses `clean_imdb_id`, `season`, and `episode`.
  4. Resolves IMDb ID to title and year via Cinemeta (`resolve_imdb_to_title`).
  5. Queries `play_engine.search()`.
  6. Filters episodes via `filter_episode_streams()` if request contains season and episode numbers.
  7. Formats stream objects with binge groups and web-readiness hints.
- **Response**: `JSONResponse` (HTTP 200) containing `{"streams": [...]}`.

---

### Route 7: `GET /{user_config}/playback/resolve`
*(Also registered on `/playback/resolve`)*
- **Purpose**: On-demand live stream resolver and 302 HTTP gateway redirection bridge.
- **Handler**: `resolve_and_stream(url: str, title: str = "Media", user_config: str = "default")`
- **Query Parameters**:
  - `url` (required): Encoded target intermediary URL (e.g. DriveSeed, V-Cloud, HubCloud).
  - `title` (optional): Encoded media title for header metadata.
- **Response**: `RedirectResponse(url=direct_stream, status_code=302)`
- **Bypass Behavior**:
  - Direct video URLs (`.mkv`, `.mp4`, `.ts`, `.m3u8`) bypass resolution and immediately redirect.
  - Intermediary URLs trigger `link_resolver.resolve_auto(raw_url)`. If extracted direct URL is valid, redirects to the CDN stream in < 250ms.
  - On resolution failure, falls back to redirecting to `raw_url`.

---

### Route 8: `POST /api/auth/trigger-login`
- **Purpose**: Automated interactive login launcher for Cloudflare Turnstile, PayBase, and Google OAuth sessions.
- **Handler**: `trigger_auth_login(request: Request)`
- **Request Body**:
  ```json
  {
    "provider": "olamovies",
    "timeout": 90.0
  }
  ```
- **Execution Protocol**:
  - Calls `auth_bridge.trigger_interactive_login(provider_key, timeout)`.
  - Spawns a visible Patchright/Playwright Chromium browser window (`--disable-blink-features=AutomationControlled`, `--no-sandbox`).
  - Polls browser context until success cookies (`cf_clearance`, `om_auth_callback`, `__Host-authjs.csrf-token`, or Google SID) are observed.
  - Writes cookies atomically to `session_vault.json` and `olamovies_session_cookies.json`.
  - Automatically dismisses browser window.
- **Response**: `JSONResponse` (HTTP 200)
  ```json
  {
    "status": "SUCCESS",
    "provider": "OlaMovies / PayBase",
    "cookies_captured": 8,
    "elapsed_seconds": 14.2
  }
  ```

---

### Route 9: `GET /api/auth/status`
- **Purpose**: Inspects session vault authentication health for third-party providers.
- **Handler**: `get_auth_status(provider: str = "olamovies")`
- **Query Parameter**: `provider`: Provider key (`"olamovies"`, `"google"`).
- **Response**: `JSONResponse` (HTTP 200)
  ```json
  {
    "provider": "olamovies",
    "authenticated": true,
    "cookies_count": 8
  }
  ```

---

## 2. Stremio Stream Object Schema & behaviorHints

Every stream element returned by Route 6 conforms strictly to the Stremio Addon Protocol specification:

```json
{
  "name": "⚡ Play [1080P]",
  "title": "Interstellar (2014) — 1080p BluRay\n📦 1080P • 3.2 GB • HEVC • 5.1 Multi [VEGAMOVIES]",
  "url": "https://addon.domain.com/dXNlcl9jZmc/playback/resolve?url=https%3A%2F%2Fnexdrive.net%2Ffile%2Fabc123&title=Interstellar",
  "behaviorHints": {
    "bingeGroup": "play-1080P-VEGAMOVIES",
    "notWebReady": true
  }
}
```

### Schema Field Specification

| Field | Type | Description |
| :--- | :--- | :--- |
| `name` | `string` | Displayed on the left quality badge. Format: `⚡ Play [{RESOLUTION}]` (e.g. `⚡ Play [4K]`, `⚡ Play [1080P]`). |
| `title` | `string` | Multi-line release description. Line 1: Release title and release button info. Line 2: Details row `📦 {res} • {size} • {codec} • {audio} [{SOURCE}]`. |
| `url` | `string` | Absolute URL pointing to Route 7 (`/playback/resolve`), containing URL-encoded `url` and `title` query parameters and preserving user config in the path. |
| `behaviorHints` | `object` | Playback control directives evaluated by the Stremio native/web player. |

### behaviorHints Attributes

#### 1. `bingeGroup` (`string`)
- **Format**: `play-{res_tag}-{source_tag}` (e.g. `play-1080P-VEGAMOVIES`, `play-2160P-UHDMOVIES`).
- **Function**: Stremio player groups sequential episodes under identical binge tags. When an episode finishes, the player automatically queues the next episode matching the exact same resolution and provider tier.

#### 2. `notWebReady` (`boolean`)
- **Evaluation**: `bool("mkv" in (title + label + codec).lower())`
- **Function**: Signals the Stremio client whether the stream container is native HTML5 browser compatible (`mp4`/`hls`) or a non-browser container (`mkv`).
  - When `true`: Stremio Web / PWA prompts the user to route streaming through an external player (VLC, MPV, Nova Video Player) or local streaming server rather than attempting broken direct browser decoding.

---

## 3. User Configuration URL-Safe Base64 Schema

User credentials, session cookies, and private bot tokens are encapsulated strictly in the URL path, preventing database leaks, shared session corruption, and credential persistence on public cloud servers.

```
https://addon.domain.com/{base64_config}/manifest.json
                         └────────────────┘
                                  │
                                  ▼
           Base64 URL-safe Decode + Padding Recovery
                                  │
                                  ▼
           JSON Parsing & Strict Schema Sanitization
```

### 3.1 Security Constraints & Size Caps
Defined in `main.py`:
- `MAX_CONFIG_STR_LEN = 8192`: Rejects configuration strings exceeding 8KB upfront to prevent memory exhaustion and ReDoS attacks.
- `ALLOWED_CONFIG_STR_KEYS = ("ola", "tg_token", "tg_chat")`: Whitelist of authorized string attributes.
- **String Length Cap**: Every string value is strictly truncated to 512 characters (`str(val).strip()[:512]`).

### 3.2 Key Whitelist Definition

| Key | Type | Default | Constraints | Description |
| :--- | :--- | :--- | :--- | :--- |
| `public` | `boolean` | `true` | Coerced to `bool` | Enables or disables querying public fast scrapers (UHDMovies, VegaMovies, HDHub4u). |
| `ola` | `string` | `""` | Max 512 chars | OlaMovies `PHPSESSID` or authenticated SSO session cookie. |
| `tg_token` | `string` | `""` | Max 512 chars | Personal Telegram bot token for private user library searches. |
| `tg_chat` | `string` | `""` | Max 512 chars | Personal Telegram group or channel ID (e.g. `-1001518787755`). |

### 3.3 Decoding & Sanitization Implementation (`decode_user_config`)

```python
def decode_user_config(config_str: Optional[str]) -> Dict[str, Any]:
    if not config_str or not isinstance(config_str, str):
        return {}
    clean_str = config_str.strip()
    if clean_str in ("", "default") or len(clean_str) > MAX_CONFIG_STR_LEN:
        return {}
    try:
        # Automatic Base64 padding correction
        padded = clean_str + '=' * (-len(clean_str) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        
        # Schema whitelist enforcement
        sanitized: Dict[str, Any] = {}
        if "public" in data:
            sanitized["public"] = bool(data["public"])
        for key in ALLOWED_CONFIG_STR_KEYS:
            val = data.get(key)
            if val is not None:
                sanitized[key] = str(val).strip()[:512]
        return sanitized
    except Exception:
        return {}
```
