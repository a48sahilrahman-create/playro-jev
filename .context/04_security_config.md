# Security Architecture & User Configuration Specification

Technical documentation of the security controls, SSRF protections, URL configuration sanitization, and session vault architecture in `play_stremio_cloud`.

---

## 1. SSRF Security Protection (`link_resolver.py`)

To prevent Server-Side Request Forgery (SSRF) and cloud metadata exfiltration when traversing arbitrary intermediary gateways and redirects, all outbound HTTP connections pass through the SSRF defense layer.

```
                            Outbound URL Request / Redirect Hop
                                            |
                                            v
                             +-----------------------------+
                             |   Scheme Check: http/https? |
                             +--------------+--------------+
                                            |
                             +--------------+--------------+
                             | Hostname & Suffix Denylist  |
                             +--------------+--------------+
                                            |
                             +--------------+--------------+
                             | DNS Resolution (getaddrinfo)|
                             +--------------+--------------+
                                            |
                             +--------------+--------------+
                             |  is_safe_ip() Range Checks  |
                             +--------------+--------------+
                                            |
                             +--------------+--------------+
                             |  Per-Hop Budget Validation  |
                             |  - Max 5 redirect hops      |
                             |  - 5.0s per-hop timeout     |
                             |  - 2MB body size cap        |
                             +--------------+--------------+
                                            |
                                            v
                                  HTTP Request Executed
```

### 1.1 `is_safe_ip()` Algorithm
Evaluates destination IP addresses (`IPv4Address` or `IPv6Address` via standard Python `ipaddress` module) against blocked network categories:
- **Private Networks (RFC-1918)**:
  - `10.0.0.0/8`
  - `172.16.0.0/12`
  - `192.168.0.0/16`
- **Carrier-Grade NAT (CGNAT)**:
  - `100.64.0.0/10`
- **Loopback Addresses**: `127.0.0.0/8` and `::1`
- **Link-Local Addresses (RFC-3927)**: `169.254.0.0/16` and `fe80::/10`
- **Reserved & Special Ranges**:
  - `ip_obj.is_reserved`
  - `ip_obj.is_multicast` (`224.0.0.0/4`)
  - `ip_obj.is_unspecified` (`0.0.0.0` or `::`)

### 1.2 `is_safe_url()` Algorithm
1. **Scheme Validation**: Strictly restricts protocols to `http` and `https`. Unsafe protocols such as `file://`, `gopher://`, `ftp://`, `data:`, and `dict://` are immediately rejected.
2. **Local Port Whitelisting**: Allows internal server loopback requests only if the target is `localhost`, `127.0.0.1`, or `::1` on whitelisted service ports (`7860`, `8000`, `8080`) for internal stream resolution.
3. **Explicit Hostname Denylist (`BLOCKED_HOSTNAMES`)**:
   - Local targets: `localhost`, `localhost.localdomain`, `local`, `broadcasthost`, `0.0.0.0`, `127.0.0.1`, `::1`
   - Cloud metadata endpoints:
     - AWS / OpenStack / Azure: `169.254.169.254`, `instance-data`, `instance-data.ec2.internal`
     - Google Cloud (GCP): `metadata.google.internal`, `metadata.google`
   - Container orchestration internal domains:
     - Kubernetes: `kubernetes.default`, `kubernetes.default.svc`
     - Docker: `docker.for.win`, `docker.for.mac`, `host.docker.internal`
4. **Internal TLD & Suffix Denylist (`BLOCKED_SUFFIXES`)**:
   Blocks any hostname ending with:
   - `.local`
   - `.localhost`
   - `.internal`
   - `.localdomain`
   - `.lan`
   - `.home.arpa`
   - `.corp`
5. **DNS Resolution Verification**:
   If the hostname is not a literal IP, `is_safe_url()` performs live DNS resolution via `socket.getaddrinfo(hostname, None)` and validates **every** resolved IPv4 and IPv6 address through `is_safe_ip()`. If any resolved IP points to a private, loopback, or cloud-metadata network, the URL is blocked.

### 1.3 Per-Hop Request & Redirect Safety (`safe_fetch`)
Every HTTP request performed by the resolver is governed by `safe_fetch`:
- **Hop Cap**: Strictly capped at a maximum of 5 redirects (`MAX_REDIRECTS = 5`). Exceeding 5 hops raises `ValueError("Exceeded maximum redirects cap (5)")`.
- **Per-Hop SSRF Validation**: Every target URL encountered via HTTP 301, 302, 303, 307, or 308 `Location` headers is parsed and re-validated through `is_safe_url()` before the client follows the redirect.
- **Hop Timeout**: Each hop is bound to an independent 5.0-second timeout (`HOP_TIMEOUT = 5.0`) to eliminate slowloris gateway stalls.
- **Memory Exhaustion Guard**: Gateway response bodies are truncated at 2MB (`MAX_GATEWAY_BYTES = 2 * 1024 * 1024`). This defends against decompression bombs or massive HTML payload memory exhaustion.

---

## 2. User Configuration Security & Normalization

The system features a zero-storage user configuration architecture. User settings and credentials travel solely inside URL path segments, eliminating multi-tenant cross-contamination and centralized database breaches.

```
Incoming URL: /{user_config}/stream/movie/tt0468569.json
                             |
                             v
               +-----------------------------+
               |  Length Cap: <= 8192 bytes  |
               +--------------+--------------+
                              |
               +--------------+--------------+
               |  Base64 Padding Recovery &  |
               |  URL-Safe Character Fix     |
               +--------------+--------------+
                              |
               +--------------+--------------+
                              |
               |  JSON Type Assertion:       |
               |  Must be Dict (Reject list, |
               |  primitives, bools, ints)   |
               +--------------+--------------+
                              |
               +--------------+--------------+
               |  Strict Schema Whitelist:   |
               |  - public: bool             |
               |  - ola: str (<=512 chars)   |
               |  - tg_token: str (<=512)    |
               |  - tg_chat: str (<=512)     |
               +--------------+--------------+
                              |
                              v
                Sanitized In-Memory User Config
```

### 2.1 Base64 Decoding & Normalization (`decode_user_config`)
1. **Length Guard**: The configuration path parameter is restricted to a maximum length of 8192 bytes (`MAX_CONFIG_STR_LEN = 8192`). Oversized payloads are discarded, falling back to `{}`.
2. **Special Tokens**: Reserved string `"default"` and empty strings resolve immediately to empty config `{}`.
3. **URL-Safe Base64 Padding Recovery**:
   Appends dynamic `=` padding (`padded = clean_str + '=' * (-len(clean_str) % 4)`) and decodes using `base64.urlsafe_b64decode()`, automatically handling standard `-` and `_` URL-safe transformations.
4. **Data Structure Validation**:
   Verifies that parsed JSON is strictly a Python `dict` (`isinstance(data, dict)`). Non-dict payloads (e.g., raw integers, arrays, boolean flags, or strings) are rejected, preventing downstream `AttributeError` or unhandled method crashes.

### 2.2 Schema Whitelist & Field Truncation
Only explicitly whitelisted keys are preserved; arbitrary keys injected by clients are discarded:

| Field | Type | Constraint | Purpose |
| :--- | :--- | :--- | :--- |
| `public` | `bool` | `bool(data["public"])` | Enables or disables fallback to public aggregators (UHDMovies, VegaMovies, HDHub4u). |
| `ola` | `str` | Stripped string, max 512 chars | User-provided session cookies or tokens for OlaMovies bypass. |
| `tg_token` | `str` | Stripped string, max 512 chars | User Telegram MTProto access token / custom bot authorization. |
| `tg_chat` | `str` | Stripped string, max 512 chars | User custom Telegram chat ID destination. |

### 2.3 Zero Server-Side Persistence Invariant
- Streaming tokens, Telegram session IDs, and private provider cookies are **never** written to the local SQLite catalog (`media_index.db`), application log files, or persistent server storage.
- Credentials exist in volatile memory only during the execution of the active request and are freed upon response delivery.

---

## 3. Session Vault & Auth Bridge (`auth_bridge.py`)

For providers requiring interactive Cloudflare Turnstile challenge completion or human Google OAuth sign-in, the system incorporates `InteractiveAuthBridge`.

### 3.1 Session Vault (`session_vault.json`)
- **Location**: `D:\play_stremio_cloud\session_vault.json` (with secondary synchronization to `olamovies_session_cookies.json`).
- **Function**: Acts as an isolated cookie repository for provider authentication.
- **Provider Scopes**:
  - `olamovies`: Tracks `cf_clearance`, `om_auth_callback`, `__Host-authjs.csrf-token`, `__Secure-authjs.session-token`, and `om_session`.
  - `google`: Tracks `SID`, `SSID`, `HSID`, `__Secure-1PSID`, and `__Secure-3PSID`.

### 3.2 Interactive Authentication Flow
When scrapers encounter HTTP 403 Cloudflare Turnstile blocks or authentication gates:
1. `InteractiveAuthBridge.trigger_interactive_login()` is invoked (with concurrency lock protection).
2. Spawns an interactive, non-headless Chromium window via **Patchright** (stealth CDP driver) or **Playwright**:
   - Launches with disabled Blink features (`--disable-blink-features=AutomationControlled`).
   - Sets Chrome 130 viewport and desktop window geometry.
3. A background polling loop monitors cookies across success domains (`olamovies.mov`, `paybase.olamovies.mov`, `google.com`).
4. Once required security tokens (e.g., `cf_clearance`, `__Host-authjs.csrf-token`) are captured:
   - Cookies are saved atomically to `session_vault.json`.
   - The browser window is automatically closed.
   - The pending media resolution resumes immediately without manual user intervention.
