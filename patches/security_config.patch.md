# Security & Config Isolation Patch Proposal for `main.py`
**Author:** Worker 3 (Security & Config Isolation Specialist)  
**Target:** `main.py::decode_user_config` & `main.py::encode_user_config`  
**Status:** PROPOSED (Ready for Worker 1 integration)  

---

## 1. Vulnerability & Threat Analysis

### A. Non-Dict JSON Return (`AttributeError` DoS)
- **Vulnerability:** `json.loads(raw)` can evaluate to primitives (`int`, `str`, `bool`, `null`, `list`).
- **Impact:** Calling `cfg.get("tg_token")` in `get_manifest` or `get_streams` raises an unhandled `AttributeError` (e.g. `'int' object has no attribute 'get'`), causing an instant HTTP 500 crash for any user requesting `/{user_config}/manifest.json` with payload `123` or `null`.
- **Severity:** HIGH (Availability / Remote DoS).

### B. Unbounded Payload Size (Memory / CPU Bomb)
- **Vulnerability:** No upper-bound length check on `config_str`. An attacker could send a 10MB URL parameter to consume worker memory and CPU during base64 decoding and JSON tokenization.
- **Severity:** MEDIUM (Resource Exhaustion).

### C. Schema Injection & Unvalidated Field Types
- **Vulnerability:** Arbitrary dictionary keys and malicious non-string data types could be passed through `user_config`, polluting scraper configs or triggering unexpected exceptions in downstream consumers.
- **Severity:** MEDIUM (Config Pollution).

### D. Base64 Character Set & Padding Resilience
- **Vulnerability:** Standard base64 characters (`+`, `/`) or percent-encoded padding (`%3D`) from web clients could fail `urlsafe_b64decode` if not normalized.

---

## 2. Proposed Hardened Implementation

```python
import base64
import json
import urllib.parse
from typing import Dict, Any

MAX_CONFIG_STR_LEN = 8192  # 8 KB upper bound to prevent memory exhaustion bombs
ALLOWED_CONFIG_KEYS = {"public", "ola", "tg_token", "tg_chat"}

def decode_user_config(config_str: str) -> Dict[str, Any]:
    """
    Safely decode and sanitize base64 JSON user config from URL path.
    Guarantees:
      1. Always returns a Dict[str, Any] (never raises AttributeError on .get()).
      2. Enforces maximum length limit to prevent memory exhaustion attacks.
      3. Normalizes URL-encoding, base64 URL-safe/standard alphabets, and padding.
      4. Whitelists and sanitizes allowed fields with strict type coercion.
    """
    if not config_str or not isinstance(config_str, str):
        return {}
    
    clean_str = config_str.strip()
    if clean_str in ("", "default", "{}"):
        return {}

    # 1. Enforce length cap
    if len(clean_str) > MAX_CONFIG_STR_LEN:
        return {}

    try:
        # 2. Unquote URL encoding if present
        unquoted = urllib.parse.unquote(clean_str)
        # Normalize standard base64 to urlsafe base64
        normalized = unquoted.replace("-", "+").replace("_", "/")
        # Fix padding
        padded = normalized + "=" * (-len(normalized) % 4)
        
        raw_bytes = base64.b64decode(padded.encode("ascii", errors="ignore"), validate=False)
        raw_text = raw_bytes.decode("utf-8", errors="replace")
        
        parsed = json.loads(raw_text)
        if not isinstance(parsed, dict):
            return {}

        # 3. Sanitize and whitelist schema
        sanitized: Dict[str, Any] = {}
        if "public" in parsed:
            sanitized["public"] = bool(parsed["public"])
        
        for str_key in ("ola", "tg_token", "tg_chat"):
            if str_key in parsed and parsed[str_key] is not None:
                val = str(parsed[str_key]).strip()
                # Cap individual field lengths (e.g. 512 chars) to avoid header/parameter injection
                sanitized[str_key] = val[:512]

        return sanitized
    except Exception:
        return {}


def encode_user_config(config_dict: Dict[str, Any]) -> str:
    """
    Safely encode user config dict into URL-safe base64 string.
    Sanitizes schema before encoding and strips trailing padding.
    """
    if not isinstance(config_dict, dict) or not config_dict:
        return ""

    sanitized: Dict[str, Any] = {}
    if "public" in config_dict:
        sanitized["public"] = bool(config_dict["public"])

    for str_key in ("ola", "tg_token", "tg_chat"):
        if str_key in config_dict and config_dict[str_key]:
            sanitized[str_key] = str(config_dict[str_key]).strip()[:512]

    try:
        raw = json.dumps(sanitized, separators=(",", ":"))
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    except Exception:
        return ""
```

---

## 3. Git Diff Style Patch

```diff
--- a/main.py
+++ b/main.py
@@ -88,17 +88,43 @@
+MAX_CONFIG_STR_LEN = 8192
+
 def decode_user_config(config_str: str) -> Dict[str, Any]:
-    """Safely decode base64 JSON config from URL path."""
-    if not config_str or config_str == "default":
+    """Safely decode base64 JSON config from URL path with schema validation."""
+    if not config_str or not isinstance(config_str, str):
         return {}
+    clean_str = config_str.strip()
+    if clean_str in ("", "default", "{}") or len(clean_str) > MAX_CONFIG_STR_LEN:
+        return {}
     try:
-        # Fix padding if missing
-        padded = config_str + '=' * (-len(config_str) % 4)
-        raw = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
-        return json.loads(raw)
+        unquoted = urllib.parse.unquote(clean_str)
+        normalized = unquoted.replace("-", "+").replace("_", "/")
+        padded = normalized + '=' * (-len(normalized) % 4)
+        raw_bytes = base64.b64decode(padded.encode("ascii", errors="ignore"), validate=False)
+        parsed = json.loads(raw_bytes.decode("utf-8", errors="replace"))
+        if not isinstance(parsed, dict):
+            return {}
+        sanitized: Dict[str, Any] = {}
+        if "public" in parsed:
+            sanitized["public"] = bool(parsed["public"])
+        for k in ("ola", "tg_token", "tg_chat"):
+            if k in parsed and parsed[k] is not None:
+                sanitized[k] = str(parsed[k]).strip()[:512]
+        return sanitized
     except Exception:
         return {}
 
 def encode_user_config(config_dict: Dict[str, Any]) -> str:
-    """Encode user config dict into URL-safe base64 string."""
-    raw = json.dumps(config_dict)
-    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8").rstrip("=")
+    """Encode sanitized user config dict into URL-safe base64 string."""
+    if not isinstance(config_dict, dict) or not config_dict:
+        return ""
+    sanitized: Dict[str, Any] = {}
+    if "public" in config_dict:
+        sanitized["public"] = bool(config_dict["public"])
+    for k in ("ola", "tg_token", "tg_chat"):
+        if k in config_dict and config_dict[k]:
+            sanitized[k] = str(config_dict[k]).strip()[:512]
+    try:
+        raw = json.dumps(sanitized, separators=(",", ":"))
+        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
+    except Exception:
+        return ""
```
