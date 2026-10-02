"""
Universal Gateway Classifier & Fingerprinting Engine
Classifies intermediate gateway pages, ad shorteners, and landing hubs:
  - FORM_MULTI_STEP: Multi-step forms with hidden tokens and dynamic JS cookies (thenaukriadda.in, unblockedgames.world, LinkProtector).
  - JS_DECODE: Client-side encoded payloads (atob(atob()), XOR, Hex, location.replace).
  - DIRECT_CDN: Zero-login streaming links (Workers.dev, R2, Pixeldrain, Google CDN).
  - COUNTDOWN_TIMER: Fake JavaScript countdown timers hiding form buttons.
  - CLOUDFLARE_PROTECTED: Cloudflare anti-bot challenges requiring TLS impersonation.
"""

import re
import json
import os
import base64
import urllib.parse
from typing import Dict, Any, List, Optional, Tuple
from bs4 import BeautifulSoup

PATTERNS_FILE = os.path.join(os.path.dirname(__file__), "cdn_patterns.json")

def load_cdn_patterns() -> List[Dict[str, Any]]:
    """Loads CDN priority rules from cdn_patterns.json."""
    if os.path.exists(PATTERNS_FILE):
        try:
            with open(PATTERNS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("rules", [])
        except Exception:
            pass
    return []

CDN_RULES = load_cdn_patterns()

class GatewayClassifier:
    """Intelligent classification and parameter extraction for mutated gateways."""

    @classmethod
    def classify_page(cls, url: str, html: str, status_code: int = 200) -> Dict[str, Any]:
        """
        Analyze page content and URL to produce a classified profile and recommended extraction steps.
        """
        if not html:
            return {
                "classification": "EMPTY_OR_UNAVAILABLE",
                "confidence": 1.0,
                "url": url,
                "forms_count": 0,
                "cookies_detected": [],
                "recommended_action": "FETCH_FAILED"
            }

        # 1. Cloudflare Anti-Bot Detection
        if status_code in (403, 503) or "Just a moment..." in html or "cf-turnstile" in html or "challenge-platform" in html:
            return {
                "classification": "CLOUDFLARE_PROTECTED",
                "confidence": 0.98,
                "url": url,
                "requires_stealth_tls": True,
                "recommended_action": "USE_STEALTH_CHROME_SESSION"
            }

        soup = BeautifulSoup(html, "html.parser")

        # 2. Check for Direct High-Priority CDNs in HTML
        direct_cdns = cls.extract_direct_cdns(html, base_url=url)
        if direct_cdns and any(c.get("score", 0) >= 80 for c in direct_cdns):
            return {
                "classification": "DIRECT_CDN",
                "confidence": 0.95,
                "url": url,
                "candidates": direct_cdns,
                "top_candidate": direct_cdns[0],
                "recommended_action": "HARVEST_AND_STREAM"
            }

        # 3. Detect Multi-Step Form Gateways (e.g. thenaukriadda.in, unblockedgames.world, LinkProtector)
        forms = soup.find_all("form")
        form_inputs = []
        has_lp_signature = False
        for f in forms:
            inputs = {inp.get("name"): inp.get("value", "") for inp in f.find_all("input") if inp.get("name")}
            form_inputs.append({"action": f.get("action") or url, "id": f.get("id", ""), "inputs": inputs})
            if any(k.startswith("_lp_") for k in inputs.keys()) or f.get("id") == "lp-s1-form":
                has_lp_signature = True

        cookie_setters = cls.extract_cookie_setters(html)
        redirect_script = cls.extract_client_redirect(html)

        if has_lp_signature or (forms and ("sid=" in url or "thenaukriadda" in url or "unblockedgames" in url)):
            return {
                "classification": "FORM_MULTI_STEP",
                "confidence": 0.96,
                "url": url,
                "forms": form_inputs,
                "cookie_setters": cookie_setters,
                "redirect_script": redirect_script,
                "recommended_action": "WALK_FORM_AND_FOLLOW_COOKIE"
            }

        # 4. Detect JS Decode & Obfuscated Redirects (e.g. atob(atob()), XOR, location.replace)
        atob_match = re.search(r"atob\(atob\((['\"][a-zA-Z0-9+/=]{10,}['\"])\)\)", html)
        single_atob = re.search(r"atob\((['\"][a-zA-Z0-9+/=]{20,}['\"])\)", html)
        xor_match = re.search(r'fromCharCode\([^)]+\^', html)

        if atob_match or single_atob or xor_match or (redirect_script and not forms):
            decoded_urls = []
            if atob_match:
                try:
                    tok = atob_match.group(1).strip('\'"')
                    b1 = base64.b64decode(tok).decode('utf-8')
                    b2 = base64.b64decode(b1).decode('utf-8')
                    if b2.startswith("http"):
                        decoded_urls.append(b2)
                except Exception:
                    pass

            return {
                "classification": "JS_DECODE",
                "confidence": 0.90,
                "url": url,
                "decoded_urls": decoded_urls,
                "redirect_script": redirect_script,
                "recommended_action": "EXECUTE_PURE_DECODE"
            }

        # 5. Detect Fake Countdown Timers
        has_timer = bool(re.search(r'(?:countdown|timer|lp-timer|var\s+time\s*=\s*\d+)', html, re.I))
        if has_timer and forms:
            return {
                "classification": "COUNTDOWN_TIMER",
                "confidence": 0.85,
                "url": url,
                "forms": form_inputs,
                "recommended_action": "BYPASS_TIMER_AND_SUBMIT_FORM"
            }

        # 6. Fallback General Landing Page
        return {
            "classification": "GENERAL_LANDING",
            "confidence": 0.70,
            "url": url,
            "candidates": direct_cdns,
            "forms": form_inputs,
            "recommended_action": "HARVEST_ALL_CANDIDATES"
        }

    @classmethod
    def extract_cookie_setters(cls, html: str) -> List[Dict[str, str]]:
        """Extracts dynamic cookie setters from JavaScript blocks."""
        cookies = []
        # Pattern 1: sc("name", "val", minutes) (used by thenaukriadda.in)
        for m in re.finditer(r'sc\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']', html):
            cookies.append({"name": m.group(1), "value": m.group(2), "type": "sc_function"})

        # Pattern 2: s_343("name", "val") (used by unblockedgames.world)
        for m in re.finditer(r's_\d+\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']', html):
            cookies.append({"name": m.group(1), "value": m.group(2), "type": "s_hash_function"})

        # Pattern 3: document.cookie = "name=val;..."
        for m in re.finditer(r'document\.cookie\s*=\s*["\']([^=;]+)=([^;\'"]+)', html):
            cookies.append({"name": m.group(1).strip(), "value": m.group(2).strip(), "type": "document_cookie"})

        return cookies

    @classmethod
    def extract_client_redirect(cls, html: str) -> Optional[str]:
        """Extracts client-side redirects (window.location, location.replace, meta refresh)."""
        # 1. location.replace("...")
        m1 = re.search(r'location\.replace\(\s*["\']([^"\']+)["\']\s*\)', html)
        if m1:
            return m1.group(1).replace(r'\/', '/')

        # 2. window.location = "..." or window.location.href = "..."
        m2 = re.search(r'window\.location(?:\.href)?\s*=\s*["\']([^"\']+)["\']', html)
        if m2:
            return m2.group(1).replace(r'\/', '/')

        # 3. setAttribute("href", "...")
        m3 = re.search(r'setAttribute\(\s*["\']href["\']\s*,\s*["\']([^"\']+)["\']\s*\)', html)
        if m3:
            return m3.group(1).replace(r'\/', '/')

        # 4. Meta refresh: <meta http-equiv="refresh" content="...url=...">
        m4 = re.search(r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\'][^"\']*url=([^"\'\s>]+)', html, re.I)
        if m4:
            return m4.group(1).strip()

        return None

    @classmethod
    def extract_direct_cdns(cls, html: str, base_url: str = "") -> List[Dict[str, Any]]:
        """
        Harvests and scores all streaming candidates according to cdn_patterns.json.
        Returns candidates ranked from highest score (zero-login CDNs) to lowest.
        """
        candidates = []
        seen = set()

        rules = CDN_RULES or load_cdn_patterns()

        for rule in rules:
            pattern = rule["regex"]
            r_type = rule["type"]
            score = rule["score"]
            label = rule["label"]

            for m in re.finditer(pattern, html):
                full_match = m.group(0).replace("&amp;", "&").rstrip('\\"')
                cand_url = full_match
                if "url_template" in rule and m.groups():
                    cand_url = rule["url_template"].format(id=m.group(1))

                if base_url:
                    cand_url = urllib.parse.urljoin(base_url, cand_url)

                if cand_url not in seen:
                    seen.add(cand_url)
                    candidates.append({
                        "url": cand_url,
                        "type": r_type,
                        "score": score,
                        "label": label,
                        "requires_login": rule.get("requires_login", False),
                        "recommended_action": rule.get("recommended_action", "direct_stream")
                    })

        # Sort strictly by score descending
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates
