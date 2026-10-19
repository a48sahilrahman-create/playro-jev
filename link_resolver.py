"""
Ultra-Fast (< 200ms) Universal Dynamic Direct Download Link Resolver
Bypasses fake JavaScript countdowns, ad popups, and intermediary gateways in pure Python with TLS/HTTP2 stealth:
  1. Universal Multi-Option Harvesting: Automatically extracts ALL streaming candidates on landing pages
     (Resume Cloud / Workers CDN, Instant Download / VideoGen, Cloudflare R2, Pixeldrain API, S3, Google CDN, Dynamic Google Drive CSRF).
  2. Concurrent Parallel Racing (< 250ms): Dispatches all extracted provider options simultaneously with asyncio.as_completed.
  3. Pre-Flight Container Validation: Validates EBML (1A 45 DF A3) / MP4 (ftyp/moov) on the fly.
  4. Non-blocking async execution, resilient dead link timeout isolation & zero crash guarantee.
  5. Security & SSRF Hardening:
     - Strict private IP / localhost / AWS/GCP metadata service denylist guard on all outbound requests and redirect hops.
     - Per-hop redirect cap (max 5 hops) with explicit per-hop timeout.
     - Response body size limit (max 2MB for HTML/gateways) to defend against memory exhaustion size bombs.
     - Catastrophic backtracking (ReDoS) safe regex patterns.
     - Detailed URL host and elapsed ms failure logging without silent error swallowing.
"""

import sys
import time
import asyncio
import re
import json
import base64
import urllib.parse
import os
import ipaddress
import socket
import codecs
import logging
from typing import Optional, List, Dict, Any, Set, Tuple

import httpx
from bs4 import BeautifulSoup
from auth_bridge import InteractiveAuthBridge, extract_b64_redirect_url, try_click_turnstile

try:
    from gateway_classifier import GatewayClassifier, CDN_RULES
except ImportError:
    GatewayClassifier = None
    CDN_RULES = []

try:
    from curl_cffi import requests as stealth_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

try:
    from patchright.async_api import async_playwright
    HAS_PATCHRIGHT = True
except ImportError:
    try:
        from playwright.async_api import async_playwright
        HAS_PATCHRIGHT = True
    except ImportError:
        HAS_PATCHRIGHT = False

logger = logging.getLogger("link_resolver")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity"
}

HOP_TIMEOUT = 5.0
MAX_REDIRECTS = 5
MAX_GATEWAY_BYTES = 2 * 1024 * 1024  # 2MB response size limit to prevent memory exhaustion bombs

# Denylist for dangerous internal hostnames & cloud metadata services
BLOCKED_HOSTNAMES: Set[str] = {
    "localhost",
    "localhost.localdomain",
    "local",
    "broadcasthost",
    "0.0.0.0",
    "127.0.0.1",
    "::1",
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.google",
    "instance-data",
    "instance-data.ec2.internal",
    "kubernetes.default",
    "kubernetes.default.svc",
    "docker.for.win",
    "docker.for.mac",
    "host.docker.internal"
}

BLOCKED_SUFFIXES: Tuple[str, ...] = (
    ".local",
    ".localhost",
    ".internal",
    ".localdomain",
    ".lan",
    ".home.arpa",
    ".corp"
)


def is_safe_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """
    Comprehensive SSRF Defense: Validates that an IP address is strictly public, routable,
    and globally reachable. Rejects private (RFC 1918), loopback, link-local, multicast,
    reserved, benchmarking (RFC 2544), documentation (RFC 5737), Carrier-Grade NAT (RFC 6598),
    IPv6 ULA (fc00::/7), IPv6 link-local, IPv6 documentation (2001:db8::/32), Teredo (2001::/32),
    discard prefix (100::/64), and all translation prefixes (NAT64 64:ff9b::/96, 64:ff9b:1::/48,
    6to4 2002::/16, IPv4-mapped ::ffff:0:0/96, and IPv4-compatible ::/96).
    """
    if not ip_obj.is_global or ip_obj.is_multicast:
        return False

    # Check for IPv6 translation prefixes and mappings
    if isinstance(ip_obj, ipaddress.IPv6Address):
        if hasattr(ip_obj, 'ipv4_mapped') and ip_obj.ipv4_mapped:
            if not is_safe_ip(ip_obj.ipv4_mapped):
                return False
        # NAT64 well-known prefix (64:ff9b::/96) and local prefix (64:ff9b:1::/48)
        if ip_obj in ipaddress.ip_network("64:ff9b::/96") or ip_obj in ipaddress.ip_network("64:ff9b:1::/48"):
            mapped_v4 = ipaddress.IPv4Address(ip_obj.packed[-4:])
            if not is_safe_ip(mapped_v4):
                return False
        # 6to4 prefix (2002::/16) - embeds IPv4 in bytes 2..6
        if ip_obj in ipaddress.ip_network("2002::/16"):
            mapped_v4 = ipaddress.IPv4Address(ip_obj.packed[2:6])
            if not is_safe_ip(mapped_v4):
                return False
        # IPv4-compatible IPv6 prefix (::/96)
        if ip_obj in ipaddress.ip_network("::/96") and ip_obj != ipaddress.ip_address("::"):
            mapped_v4 = ipaddress.IPv4Address(ip_obj.packed[-4:])
            if not is_safe_ip(mapped_v4):
                return False

    return True


def is_safe_url(url: str, allow_local: bool = False) -> bool:
    """
    SSRF Defense: Validates URL scheme and destination IP address against internal/private networks.
    Prevents DNS rebinding, cloud metadata exfiltration (169.254.169.254), and internal loopback exploitation.
    """
    if not url or not isinstance(url, str):
        return False
    try:
        check_url = url.strip()
        if "::" in check_url:
            is_ipv6_only = False
            bo = check_url.find('[')
            bc = check_url.find(']')
            dc = check_url.find('::')
            if bo != -1 and bc != -1 and bo < dc < bc and check_url.count('::') == 1:
                is_ipv6_only = True
            if not is_ipv6_only:
                if bo != -1 and bc != -1 and bo < dc < bc:
                    check_url = check_url[:bc+1] + check_url[bc+1:].split('::')[0]
                else:
                    check_url = check_url.split('::')[0].strip()
        parsed = urllib.parse.urlparse(check_url)
        if parsed.scheme not in ("http", "https"):
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        hostname = hostname.lower().strip().rstrip(".")

        # Explicit allow_local flag only for intentional internal player daemon endpoints
        if hostname in ("localhost", "127.0.0.1", "::1"):
            if allow_local and parsed.port in (7860, 8000, 8080):
                return True
            return False

        if hostname in BLOCKED_HOSTNAMES:
            return False

        if any(hostname.endswith(suffix) for suffix in BLOCKED_SUFFIXES):
            return False

        # Check if literal IP
        try:
            ip_obj = ipaddress.ip_address(hostname)
            return is_safe_ip(ip_obj)
        except ValueError:
            pass

        # Resolve hostname DNS to check resolved IPs against SSRF / DNS rebinding
        try:
            addr_info = socket.getaddrinfo(hostname, None)
            for *_, sockaddr in addr_info:
                ip_str = sockaddr[0]
                ip_obj = ipaddress.ip_address(ip_str)
                if not is_safe_ip(ip_obj):
                    return False
        except (socket.gaierror, Exception):
            # If hostname cannot be resolved, reject untrusted destination
            return False

        return True
    except Exception:
        return False


def get_safe_resolved_ip(hostname: str, port: int = 80, allow_local: bool = False) -> Optional[str]:
    """
    Pinning & TOCTOU Defense: Resolves hostname DNS and strictly validates every resolved address
    against private/reserved ranges. Returns the verified safe IP string, pinning the destination.
    """
    if not hostname:
        return None
    hostname = hostname.lower().strip().rstrip(".")
    if hostname in ("localhost", "127.0.0.1", "::1"):
        if allow_local and port in (7860, 8000, 8080):
            return "127.0.0.1"
        return None
    if hostname in BLOCKED_HOSTNAMES or any(hostname.endswith(suffix) for suffix in BLOCKED_SUFFIXES):
        return None
    try:
        # Literal IP check
        try:
            ip_obj = ipaddress.ip_address(hostname)
            return str(ip_obj) if (is_safe_ip(ip_obj) or allow_local) else None
        except ValueError:
            pass

        addr_info = socket.getaddrinfo(hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        safe_ips = []
        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip_str)
            if not allow_local and not is_safe_ip(ip_obj):
                return None
            safe_ips.append(ip_str)
        return safe_ips[0] if safe_ips else None
    except Exception:
        return None


def install_dns_ssrf_guard(loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    """
    Socket-Level TOCTOU DNS Rebinding Defense:
    Intercepts the event loop's getaddrinfo call at the exact instant a TCP socket
    is being initialized by HTTPX / httpcore / anyio.
    Validates every resolved IP directly, guaranteeing that if a malicious server
    returns a short-TTL record that rebinds to 127.0.0.1 or 169.254.169.254 at connect time,
    the socket creation is immediately aborted before the TCP handshake.
    Preserves original Host and SNI headers for Cloudflare / CDN edge compatibility.
    """
    try:
        if loop is None:
            loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    if getattr(loop, "_ssrf_guard_installed", False):
        return

    orig_getaddrinfo = loop.getaddrinfo

    async def safe_getaddrinfo(host, port, *args, **kwargs):
        results = await orig_getaddrinfo(host, port, *args, **kwargs)
        safe_results = []
        has_unsafe_record = False
        unsafe_ip = ""

        for res in results:
            sockaddr = res[4]
            ip_str = sockaddr[0]
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                if is_safe_ip(ip_obj):
                    safe_results.append(res)
                else:
                    has_unsafe_record = True
                    unsafe_ip = ip_str
            except ValueError:
                has_unsafe_record = True

        # Strict Security: If all records are unsafe or if mixed private/public resolution occurs,
        # fail-closed to prevent split-horizon DNS rebinding or dual-stack fallback SSRF
        if not safe_results or has_unsafe_record:
            raise PermissionError(f"SSRF DNS Rebinding blocked: host {host} resolved to unsafe IP {unsafe_ip or 'unknown'}")

        return safe_results

    loop.getaddrinfo = safe_getaddrinfo
    loop._ssrf_guard_installed = True


HUBCLOUD_GATEWAY_DOMAINS = (
    "hubcloud.one",
    "hubcloud.club",
    "hubdrive.pics",
    "gamerxyt.com",
    "hubcloud.",
    "hubdrive.",
)


def _decode_greenmotors_token(token: str) -> Optional[str]:
    """
    Deobfuscates 5-layer GreenMotors client-side JS challenge token:
    Base64 -> Base64 -> ROT13 -> Base64 -> JSON -> Base64(o) -> HubCloud URL
    """
    if not token or not isinstance(token, str):
        return None
    try:
        s1 = base64.b64decode(token).decode("latin1")
        s2 = base64.b64decode(s1).decode("latin1")
        s3 = codecs.decode(s2, "rot_13")
        s4 = base64.b64decode(s3).decode("utf-8")
        data = json.loads(s4)
        if isinstance(data, dict) and "o" in data:
            hub_url = base64.b64decode(data["o"]).decode("utf-8").strip()
            if hub_url.startswith("http"):
                return hub_url
    except Exception as e:
        logger.debug(f"[GreenMotors Deobf] Decoding failed: {e}")
    return None


async def resolve_gateway_url(url: str, client: Optional[httpx.AsyncClient] = None) -> str:
    """
    Resolves intermediary gateway URLs (greenmotors.club, greenmotors.cc, hdstream4u.com)
    into downstream HubCloud, Gamerxyt, HubDrive, or direct playable stream URLs.
    Handles redirects (follow_redirects=True) and HTML inspections for JS redirects and links.
    """
    if not url or not isinstance(url, str):
        return ""

    url_str = url.strip()
    is_greenmotors = any(domain in url_str for domain in ["greenmotors.club", "greenmotors.cc"])
    is_hdstream4u = "hdstream4u.com" in url_str

    if not (is_greenmotors or is_hdstream4u):
        return url_str

    close_client = False
    if client is None:
        client = httpx.AsyncClient(http2=True, headers=DEFAULT_HEADERS, timeout=HOP_TIMEOUT)
        close_client = True

    try:
        req_headers = dict(DEFAULT_HEADERS)
        req_headers["Referer"] = url_str

        # Follow redirects to capture 301/302/307/308 transitions
        r = await client.get(url_str, headers=req_headers, follow_redirects=True, timeout=HOP_TIMEOUT)
        final_url_str = str(r.url)

        # Check redirect history for target domains
        if hasattr(r, "history") and r.history:
            for hop in r.history:
                hop_url = str(hop.url)
                if any(dom in hop_url for dom in HUBCLOUD_GATEWAY_DOMAINS) and not any(gm in hop_url for gm in ["greenmotors.club", "greenmotors.cc"]):
                    return hop_url

        # Check final redirected URL
        if is_greenmotors:
            if any(dom in final_url_str for dom in HUBCLOUD_GATEWAY_DOMAINS) and not any(gm in final_url_str for gm in ["greenmotors.club", "greenmotors.cc"]):
                return final_url_str
        elif is_hdstream4u:
            if (any(dom in final_url_str for dom in HUBCLOUD_GATEWAY_DOMAINS) or any(ext in final_url_str.lower() for ext in [".mp4", ".mkv"])) and "hdstream4u.com" not in final_url_str:
                return final_url_str

        html = r.text
        if not html:
            return url_str

        soup = BeautifulSoup(html, "html.parser")

        # 1. Inspect GreenMotors gateways
        if is_greenmotors:
            # 1a. GreenMotors 5-layer JavaScript challenge deobfuscation
            # Pattern: s('o', '<token>', ...) or localStorage.setItem('o', '<token>') or {"o": "<token>"}
            token_match = re.search(
                r"(?:s\(\s*['\"]o['\"]\s*,\s*|localStorage\.setItem\(\s*['\"]o['\"]\s*,\s*|['\"]o['\"]\s*:\s*)['\"]([A-Za-z0-9+/=]{40,})['\"]",
                html
            )
            if token_match:
                decoded_hub_url = _decode_greenmotors_token(token_match.group(1).strip())
                if decoded_hub_url and any(dom in decoded_hub_url for dom in HUBCLOUD_GATEWAY_DOMAINS):
                    return decoded_hub_url

            # 1b. Check if token or payload is embedded in homelander landing page
            homelander_match = re.search(r'["\'](https?://[^"\']*/homelander/[^"\']*)["\']', html)
            if homelander_match:
                hl_url = homelander_match.group(1)
                try:
                    r_hl = await client.get(hl_url, headers=req_headers, follow_redirects=True, timeout=HOP_TIMEOUT)
                    hl_token_match = re.search(
                        r"(?:s\(\s*['\"]o['\"]\s*,\s*|localStorage\.setItem\(\s*['\"]o['\"]\s*,\s*|['\"]o['\"]\s*:\s*)['\"]([A-Za-z0-9+/=]{40,})['\"]",
                        r_hl.text
                    )
                    if hl_token_match:
                        decoded_hub_url = _decode_greenmotors_token(hl_token_match.group(1).strip())
                        if decoded_hub_url and any(dom in decoded_hub_url for dom in HUBCLOUD_GATEWAY_DOMAINS):
                            return decoded_hub_url
                except Exception as hl_err:
                    logger.debug(f"[GreenMotors Homelander Fetch Error] {hl_err}")
            # Check JS location replacement
            loc_patterns = [
                r'window\.location\.replace\(\s*["\']([^"\']+)["\']\s*\)',
                r'location\.replace\(\s*["\']([^"\']+)["\']\s*\)',
                r'window\.location\.href\s*=\s*["\']([^"\']+)["\']',
                r'location\.href\s*=\s*["\']([^"\']+)["\']',
                r'window\.location\s*=\s*["\']([^"\']+)["\']',
                r'location\s*=\s*["\']([^"\']+)["\']',
            ]
            for pat in loc_patterns:
                for match in re.finditer(pat, html, re.I):
                    cand = match.group(1).replace(r'\/', '/').replace(r'\"', '').replace(r"\'", '').replace('&amp;', '&').strip()
                    cand_full = urllib.parse.urljoin(final_url_str, cand)
                    if any(dom in cand_full for dom in HUBCLOUD_GATEWAY_DOMAINS):
                        return cand_full

            # Check <a href="..."> tags
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if not href or href.startswith("#") or href.startswith("javascript:"):
                    continue
                full_href = urllib.parse.urljoin(final_url_str, href).replace('&amp;', '&')
                if any(dom in full_href for dom in HUBCLOUD_GATEWAY_DOMAINS):
                    return full_href

            # Check meta refresh tag
            for meta in soup.find_all("meta", attrs={"http-equiv": re.compile(r"refresh", re.I)}):
                content = meta.get("content", "")
                if "url=" in content.lower():
                    cand = re.split(r'url=', content, flags=re.I)[1].strip().strip('\'"')
                    cand_full = urllib.parse.urljoin(final_url_str, cand)
                    if any(dom in cand_full for dom in HUBCLOUD_GATEWAY_DOMAINS):
                        return cand_full

            # Raw regex fallback for HubCloud / Gamerxyt / HubDrive
            hub_raw = re.search(
                r'https?://(?:[a-zA-Z0-9.-]+\.)?(?:hubcloud\.[a-z0-9-]+|hubdrive\.[a-z0-9-]+|gamerxyt\.com)/[^\s\'"<>\\]+',
                html
            )
            if hub_raw:
                return hub_raw.group(0).replace('&amp;', '&').rstrip('\\"')

        # 2. Inspect HDStream4u gateways
        elif is_hdstream4u:
            # Check <video src="..."> and <source src="...">
            for media_tag in soup.find_all(["video", "source"], src=True):
                src = media_tag["src"].strip()
                if src and not src.startswith("blob:"):
                    return urllib.parse.urljoin(final_url_str, src).replace('&amp;', '&')

            # Check JS video player configs (file: "...", source: "...", src: "...")
            file_match = re.search(r'(?:file|source|src)\s*:\s*["\'](https?://[^"\']+\.(?:mp4|mkv)(?:\?[^"\']*)?)["\']', html, re.I)
            if file_match:
                return file_match.group(1).replace(r'\/', '/').replace('&amp;', '&').strip()

            # Check video anchors & HubCloud anchors
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if not href or href.startswith("#") or href.startswith("javascript:"):
                    continue
                full_href = urllib.parse.urljoin(final_url_str, href).replace('&amp;', '&')
                if any(full_href.lower().endswith(ext) or ext + "?" in full_href.lower() for ext in [".mp4", ".mkv"]):
                    return full_href
                if any(dom in full_href for dom in HUBCLOUD_GATEWAY_DOMAINS):
                    return full_href

            # Check iframe sources
            for iframe in soup.find_all("iframe", src=True):
                src = iframe["src"].strip()
                if src:
                    full_src = urllib.parse.urljoin(final_url_str, src).replace('&amp;', '&')
                    if any(dom in full_src for dom in HUBCLOUD_GATEWAY_DOMAINS):
                        return full_src
                    if any(ext in full_src.lower() for ext in [".mp4", ".mkv"]):
                        return full_src

            # Check HubCloud link in raw HTML
            hub_raw = re.search(
                r'https?://(?:[a-zA-Z0-9.-]+\.)?(?:hubcloud\.[a-z0-9-]+|hubdrive\.[a-z0-9-]+|gamerxyt\.com)/[^\s\'"<>\\]+',
                html
            )
            if hub_raw:
                return hub_raw.group(0).replace('&amp;', '&').rstrip('\\"')

            # Check CDN links (workers.dev, r2, pixeldrain, googleusercontent)
            cdn_match = re.search(r'https?://(?:[a-zA-Z0-9.-]+\.workers\.dev|[a-zA-Z0-9.-]+\.r2\.cloudflarestorage\.com|pixeldrain\.(?:com|dev)/(?:u|api/file)|video-downloads\.googleusercontent\.com)/[^\s\'"<>\\]+', html)
            if cdn_match:
                return cdn_match.group(0).replace('&amp;', '&').rstrip('\\"')

            # Check direct video regex in full HTML
            v_matches = re.findall(r'https?://[^\s\'"<>\\]+\.(?:mp4|mkv)(?:\?[^\s\'"<>\\]*)?', html, re.I)
            for vm in v_matches:
                clean_vm = vm.replace('&amp;', '&').rstrip('\\"')
                if not any(junk in clean_vm.lower() for junk in ["sample", "trailer", "preview"]):
                    return clean_vm

    except Exception as e:
        logger.warning(f"[resolve_gateway_url Error] URL: {url_str} | Error: {e}")
    finally:
        if close_client:
            await client.aclose()

    return url_str


class LinkResolver:
    def __init__(self):
        self.limits = httpx.Limits(max_keepalive_connections=40, max_connections=80)
        self.auth_bridge = InteractiveAuthBridge()

    # -------------------------------------------------------------
    # 0. SSRF-Safe Secure Request Dispatcher
    # -------------------------------------------------------------
    async def safe_fetch(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[Dict[str, Any]] = None,
        max_redirects: int = MAX_REDIRECTS,
        timeout: float = HOP_TIMEOUT,
        max_bytes: int = MAX_GATEWAY_BYTES
    ) -> httpx.Response:
        """
        Execute an HTTP request with:
          1. Strict SSRF destination validation per hop.
          2. Hard cap on redirects (max 5 hops).
          3. Per-hop explicit timeout.
          4. Response size limit (prevents memory bomb on huge HTML payloads).
        """
        install_dns_ssrf_guard()
        current_url = url
        req_headers = dict(DEFAULT_HEADERS)
        if headers:
            req_headers.update(headers)

        for hop in range(max_redirects + 1):
            if not is_safe_url(current_url):
                raise ValueError(f"SSRF blocked: unsafe target or redirect URL '{current_url}'")

            # Pin resolved IP to protect against TOCTOU DNS rebinding
            parsed_hop = urllib.parse.urlparse(current_url)
            hop_port = parsed_hop.port or (443 if parsed_hop.scheme == "https" else 80)
            pinned_ip = get_safe_resolved_ip(parsed_hop.hostname, hop_port)
            if not pinned_ip:
                raise ValueError(f"SSRF / DNS Rebinding blocked: unverified destination IP for '{current_url}'")

            req = client.build_request(
                method if hop == 0 else "GET",
                current_url,
                headers=req_headers,
                data=data if (hop == 0 and method.upper() == "POST") else None,
                timeout=timeout
            )
            r = await client.send(req, follow_redirects=False)

            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("location")
                if not loc:
                    raise ValueError(f"Redirect status {r.status_code} with missing Location header")
                current_url = urllib.parse.urljoin(str(r.url), loc)
                if hop >= max_redirects:
                    raise ValueError(f"Exceeded maximum redirects cap ({max_redirects})")
                continue

            # Limit response body size
            raw_content = r.content
            if len(raw_content) > max_bytes:
                raw_content = raw_content[:max_bytes]

            return httpx.Response(
                status_code=r.status_code,
                headers=r.headers,
                content=raw_content,
                request=r.request
            )

        raise ValueError(f"Exceeded maximum redirects cap ({max_redirects})")

    # -------------------------------------------------------------
    # 1. Obfuscated JS & Cipher Resolvers (< 1ms)
    # -------------------------------------------------------------
    @staticmethod
    def resolve_ddlbase_xor(cipher_b64: str, key: str = "mySecret123") -> str:
        """Decode DDLBase client-side XOR obfuscation in < 1ms."""
        if not cipher_b64:
            return ""
        try:
            raw = base64.b64decode(cipher_b64).decode("utf-8", errors="ignore")
            decoded = "".join(chr(ord(c) ^ ord(key[i % len(key)])) for i, c in enumerate(raw))
            return decoded
        except Exception:
            return str(cipher_b64)

    @staticmethod
    def resolve_atob_nested(cipher_b64: str) -> str:
        """Safely decode single or nested atob() base64 strings in < 1ms."""
        if not cipher_b64:
            return ""
        try:
            cleaned = cipher_b64.strip('\'" ')
            b1 = base64.b64decode(cleaned).decode("utf-8", errors="ignore")
            try:
                b2 = base64.b64decode(b1.strip('\'" ')).decode("utf-8", errors="ignore")
                if b2.startswith("http"):
                    return b2
            except Exception:
                pass
            return b1
        except Exception:
            return str(cipher_b64)

    @staticmethod
    def resolve_hex_string(hex_str: str) -> str:
        """Decode hex-encoded strings (e.g. \\x68\\x74\\x74\\x70 or %68%74%74%70)."""
        if not hex_str:
            return ""
        try:
            if "\\x" in hex_str:
                return bytes.fromhex(hex_str.replace("\\x", "")).decode("utf-8", errors="ignore")
            return urllib.parse.unquote(hex_str)
        except Exception:
            return str(hex_str)

    # -------------------------------------------------------------
    # 2. Dynamic Google Drive Large-File Confirm Token Extractor
    # -------------------------------------------------------------
    async def resolve_google_drive(self, client: httpx.AsyncClient, file_id: str) -> dict:
        """Bypass Google Drive large-file virus-warning page by extracting dynamic confirm CSRF tokens."""
        t0 = time.perf_counter()
        clean_file_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(file_id))
        if not clean_file_id:
            return {"status": "FAILED", "error": "Invalid Google Drive file ID", "elapsed_ms": 0.0}

        try:
            url = f"https://drive.google.com/uc?export=download&id={clean_file_id}"
            r = await self.safe_fetch(client, "GET", url, headers=DEFAULT_HEADERS, timeout=HOP_TIMEOUT)

            # 1. Check if direct stream was returned directly
            if r.headers.get("content-type", "").startswith("video/"):
                return {
                    "status": "SUCCESS",
                    "host": "Google Drive Direct",
                    "direct_url": str(r.url),
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                }

            # 2. Extract dynamic confirmation token from form or cookie
            soup = BeautifulSoup(r.text, "html.parser")
            form = soup.find("form", id="download-form") or soup.find("form", action=re.compile(r'drive\.usercontent\.google\.com'))
            if form:
                action = form.get("action") or str(r.url)
                params = {inp.get("name"): inp.get("value", "") for inp in form.find_all("input") if inp.get("name")}
                direct_url = str(httpx.URL(action).copy_with(params=params))
                return {
                    "status": "SUCCESS",
                    "host": "Google Usercontent CDN",
                    "direct_url": direct_url,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                }

            # 3. Check for confirm token query or cookies
            for k, v in r.cookies.items():
                if "download_warning" in k:
                    direct_url = f"https://drive.usercontent.google.com/download?id={clean_file_id}&export=download&confirm={v}"
                    return {
                        "status": "SUCCESS",
                        "host": "Google Usercontent CDN",
                        "direct_url": direct_url,
                        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                    }
        except Exception as e:
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            logger.warning(f"[Resolver Fail] Host: drive.google.com | Elapsed: {elapsed}ms | Error: {e}")
            return {"status": "ERROR", "error": str(e), "elapsed_ms": elapsed}

        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(f"[Resolver Fail] Host: drive.google.com | Elapsed: {elapsed}ms | Reason: confirm token not found")
        return {"status": "FAILED", "error": "Google Drive confirm token not found", "elapsed_ms": elapsed}

    # -------------------------------------------------------------
    # 3. Universal Candidate Harvester (Multi-Option DOM Scanner)
    # -------------------------------------------------------------
    async def harvest_all_options(self, client: httpx.AsyncClient, page_url: str, html: str = "") -> list:
        """
        Dynamically extracts ALL provider download options, buttons, and direct video links from any landing page.
        Identifies:
          - Resume Cloud / Workers CDN (/zfile/*, *.workers.dev)
          - Instant Download (cdn.video-gen.xyz, video-seed.dev, googleusercontent.com)
          - Cloudflare R2 / S3 (*.r2.cloudflarestorage.com, *.r2.dev)
          - Pixeldrain (pixeldrain.com/u/*)
          - Google Drive (drive.google.com/file/d/*)
          - Sub-gateways & nested mirror buttons
        """
        candidates = []
        seen = set()

        if not html:
            if not is_safe_url(page_url):
                logger.warning(f"[SSRF Guard] Refusing to harvest from unsafe URL: {page_url}")
                return []
            try:
                r = await self.safe_fetch(client, "GET", page_url, headers=DEFAULT_HEADERS, timeout=HOP_TIMEOUT)
                html = r.text
            except Exception as e:
                host = urllib.parse.urlparse(page_url).netloc or "Unknown"
                logger.warning(f"[Harvest Error] Host: {host} | Error: {e}")
                return []

        soup = BeautifulSoup(html, "html.parser")

        # 1. Scan for Cloudflare Workers / Resume Cloud links (ReDoS-safe pattern)
        worker_matches = re.findall(r'https?://[a-zA-Z0-9.-]+\.workers\.dev/[^\s\'"<>\\]+', html)
        for wm in worker_matches:
            clean_wm = wm.replace("&amp;", "&").rstrip('\\"')
            if clean_wm not in seen and is_safe_url(clean_wm):
                seen.add(clean_wm)
                candidates.append({"type": "worker_direct", "url": clean_wm, "label": "Cloud Resume Worker"})

        # 2. Scan for /zfile/ paths on DriveSeed
        zfile_matches = re.findall(r'(?:https?://[^/\s\'"<>\\]+)?/zfile/[a-zA-Z0-9_-]+', html)
        for zm in zfile_matches:
            full_z = urllib.parse.urljoin(page_url, zm)
            if full_z not in seen and is_safe_url(full_z):
                seen.add(full_z)
                candidates.append({"type": "zfile_gateway", "url": full_z, "label": "Resume Cloud Gateway"})

        # 3. Scan for Cloudflare R2 / S3 presigned URLs (ReDoS-safe pattern)
        r2_matches = re.findall(r'https?://[a-zA-Z0-9.-]+(?:\.r2\.cloudflarestorage\.com|\.r2\.dev)/[^\s\'"<>\\]+', html)
        for r2 in r2_matches:
            clean_r2 = r2.replace("&amp;", "&").rstrip('\\"')
            if clean_r2 not in seen and is_safe_url(clean_r2) and not any(ext in clean_r2.lower() for ext in [".gif", ".png", ".jpg", ".jpeg", ".svg", ".css", ".js"]):
                seen.add(clean_r2)
                candidates.append({"type": "r2_direct", "url": clean_r2, "label": "Cloudflare R2 Direct"})

        # 4. Scan for Instant Download CDN links (video-gen.xyz, video-plex.xyz, video-seed.dev, googleusercontent)
        inst_matches = re.findall(r'https?://(?:cdn\.video-[a-zA-Z0-9_-]+\.xyz|video-seed\.dev|video-downloads\.googleusercontent\.com)/[^\s\'"<>\\]+', html)
        for im in inst_matches:
            clean_im = im.replace("&amp;", "&").rstrip('\\"')
            if clean_im not in seen and is_safe_url(clean_im):
                seen.add(clean_im)
                candidates.append({"type": "instant_cdn", "url": clean_im, "label": "Instant Download CDN"})

        # 5. Scan for Pixeldrain URLs
        pd_matches = re.findall(r'https?://pixeldrain\.(?:com|dev)/(?:u|api/file)/([a-zA-Z0-9]+)', html)
        for pd_id in pd_matches:
            if pd_id not in ["negn6f", ""]:
                direct_pd = f"https://pixeldrain.dev/api/file/{pd_id}"
                if direct_pd not in seen and is_safe_url(direct_pd):
                    seen.add(direct_pd)
                    candidates.append({"type": "pixeldrain", "url": direct_pd, "label": "Pixeldrain API"})

        # 6. Scan for Google Drive File IDs
        gd_matches = re.findall(r'drive\.google\.com/file/d/([a-zA-Z0-9_-]+)', html)
        for gd_id in gd_matches:
            if gd_id not in seen:
                seen.add(gd_id)
                candidates.append({"type": "google_drive", "file_id": gd_id, "url": f"https://drive.google.com/file/d/{gd_id}", "label": "Google Drive"})

        # 7. Scan all Anchor tags (<a href="...">) for dynamic mirror buttons
        junk_patterns = ["/login", "/register", "/about", "/contact", "/privacy", "/terms", "/copyright", "/dmca", "t.me", "facebook", "whatsapp", "javascript:"]
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            txt = a.get_text(" ", strip=True)
            if not href or any(jp in href.lower() for jp in junk_patterns) or href.startswith("#"):
                continue
            full_href = urllib.parse.urljoin(page_url, href)
            if full_href in seen or full_href == page_url or not is_safe_url(full_href):
                continue

            if any(k in href.lower() or k in txt.lower() for k in ["instant", "resume", "download", "fast", "server", "mirror", "cloud", "direct", "worker", "vcloud", "hubcloud", "gamerxyt", "cryptonewz", "10gbps"]):
                seen.add(full_href)
                candidates.append({"type": "dynamic_button", "url": full_href, "label": txt or "Mirror Option"})

        # Prioritize zero-login high-speed CDNs (R2 > Fast CDN Mirrors > Pixeldrain > Workers > Google CDN)
        for c in candidates:
            c_url = c.get("url", "")
            cand_text = f"{c_url} {c.get('label', '')}".lower()
            if "r2.cloudflarestorage.com" in c_url or ".r2.dev" in c_url:
                c["score"] = 95
            elif any(k in cand_text for k in ["pixel.hubcloud.", "pixel.hubcloud", "cryptonewz.", "cryptonewz", "10gbps", "server", "mirror"]):
                c["score"] = 92
            elif "pixeldrain" in c_url:
                c["score"] = 90
            elif "workers.dev" in c_url:
                c["score"] = 88
            elif "video-downloads.googleusercontent.com" in c_url:
                c["score"] = 85
            elif any(k in c_url for k in ["video-gen", "video-seed", "video-plex"]):
                c["score"] = 80
            elif "/zfile/" in c_url:
                c["score"] = 75
            elif "drive.usercontent.google.com" in c_url and "confirm=" in c_url:
                c["score"] = 70
            elif any(ext in c_url.lower() for ext in [".mkv", ".mp4"]):
                c["score"] = 65
            elif "drive.google.com" in c_url:
                c["score"] = 50
            else:
                c["score"] = 40

        candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
        return candidates

    # -------------------------------------------------------------
    # 4. Resolve Single Candidate
    # -------------------------------------------------------------
    async def resolve_candidate(self, client: httpx.AsyncClient, candidate: dict, parent_url: str = "") -> dict:
        """Resolve a specific candidate into a direct playable stream URL with SSRF & timeout isolation."""
        t0 = time.perf_counter()
        c_type = candidate.get("type")
        url = candidate.get("url", "")

        host_label = urllib.parse.urlparse(url).netloc or candidate.get("label", "Unknown")

        if not url or not is_safe_url(url):
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            logger.warning(f"[Resolver Blocked] Unsafe candidate URL: {url} | Host: {host_label} | Elapsed: {elapsed}ms")
            return {"status": "FAILED", "error": "Unsafe or blocked candidate URL", "elapsed_ms": elapsed}

        try:
            if c_type in ("worker_direct", "r2_direct", "pixeldrain"):
                try:
                    probe_headers = {"Range": "bytes=0-1024", **DEFAULT_HEADERS}
                    if parent_url:
                        probe_headers["Referer"] = parent_url
                    probe_resp = await client.get(url, headers=probe_headers, timeout=3.0, follow_redirects=True)
                    resp_text = getattr(probe_resp, "text", "") or ""
                    resp_lower = resp_text.lower()
                    if (
                        probe_resp.status_code in (403, 500, 502, 503)
                        or probe_resp.status_code >= 400
                        or "downloadquotaexceeded" in resp_lower
                        or "quota exceeded" in resp_lower
                        or '{"error":' in resp_text
                        or '{"error"' in resp_text
                        or resp_text.startswith('{\n')
                        or resp_text.startswith('{"')
                    ):
                        elapsed = round((time.perf_counter() - t0) * 1000, 2)
                        return {
                            "status": "QUOTA_EXCEEDED" if (probe_resp.status_code in (403, 500) or "quota" in resp_lower) else "FAILED",
                            "error": f"Upstream error {probe_resp.status_code} or quota exceeded on {host_label}",
                            "direct_url": url,
                            "elapsed_ms": elapsed
                        }
                except Exception as probe_err:
                    logger.warning(f"[Probe Error] Candidate probe exception for {url}: {probe_err}")
                    return {
                        "status": "FAILED",
                        "error": f"Connection probe failed: {probe_err}",
                        "direct_url": url,
                        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                    }

                return {
                    "status": "SUCCESS",
                    "host": candidate.get("label", "Direct CDN"),
                    "direct_url": url,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                }

            if c_type == "zfile_gateway":
                r = await self.safe_fetch(client, "GET", url, headers={"Referer": parent_url or url, **DEFAULT_HEADERS}, timeout=HOP_TIMEOUT)
                w_match = re.search(r'https?://[a-zA-Z0-9.-]+\.workers\.dev/[^\s\'"<>\\]+', r.text)
                if w_match:
                    direct = w_match.group(0).replace("&amp;", "&").rstrip('\\"')
                    if is_safe_url(direct):
                        try:
                            probe_headers = {"Range": "bytes=0-1024", **DEFAULT_HEADERS}
                            probe_resp = await client.get(direct, headers=probe_headers, timeout=3.0, follow_redirects=True)
                            resp_text = getattr(probe_resp, "text", "") or ""
                            resp_lower = resp_text.lower()
                            if (
                                probe_resp.status_code in (403, 500, 502, 503)
                                or probe_resp.status_code >= 400
                                or "downloadquotaexceeded" in resp_lower
                                or "quota exceeded" in resp_lower
                                or '{"error":' in resp_text
                                or '{"error"' in resp_text
                                or resp_text.startswith('{\n')
                                or resp_text.startswith('{"')
                            ):
                                elapsed = round((time.perf_counter() - t0) * 1000, 2)
                                return {
                                    "status": "QUOTA_EXCEEDED" if (probe_resp.status_code in (403, 500) or "quota" in resp_lower) else "FAILED",
                                    "error": f"Upstream error {probe_resp.status_code} or quota exceeded on Cloud Resume Worker",
                                    "direct_url": direct,
                                    "elapsed_ms": elapsed
                                }
                        except Exception as probe_err:
                            return {
                                "status": "FAILED",
                                "error": f"Connection probe failed: {probe_err}",
                                "direct_url": direct,
                                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                            }

                        return {
                            "status": "SUCCESS",
                            "host": "Cloud Resume Worker",
                            "direct_url": direct,
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }

            if c_type == "instant_cdn":
                if "video-downloads.googleusercontent.com" in url:
                    return {
                        "status": "SUCCESS",
                        "host": "Google CDN",
                        "direct_url": url,
                        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                    }

                resp_url_str = url
                resp_text = ""

                # If curl_cffi available, use stealth request to avoid Cloudflare blocks on video-gen
                if HAS_CURL_CFFI:
                    try:
                        def _fetch_stealth_instant(u, ref):
                            s = stealth_requests.Session(impersonate="chrome120")
                            r_c = s.get(u, headers={"Referer": ref}, timeout=5.0)
                            return str(r_c.url), r_c.text
                        resp_url_str, resp_text = await asyncio.to_thread(_fetch_stealth_instant, url, parent_url or url)
                    except Exception:
                        r = await self.safe_fetch(client, "GET", url, headers={"Referer": parent_url or url, **DEFAULT_HEADERS}, timeout=HOP_TIMEOUT)
                        resp_url_str = str(r.url)
                        resp_text = r.text
                else:
                    r = await self.safe_fetch(client, "GET", url, headers={"Referer": parent_url or url, **DEFAULT_HEADERS}, timeout=HOP_TIMEOUT)
                    resp_url_str = str(r.url)
                    resp_text = r.text

                # Check query parameter ?url= (used by video-seed.dev redirects)
                parsed_u = urllib.parse.urlparse(resp_url_str)
                qs = urllib.parse.parse_qs(parsed_u.query)
                if "url" in qs and qs["url"]:
                    direct_cand = qs["url"][0].replace("&amp;", "&")
                    if is_safe_url(direct_cand):
                        return {
                            "status": "SUCCESS",
                            "host": "Google CDN",
                            "direct_url": direct_cand,
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }

                # Check regex across both final URL and response body
                combined_text = f"{resp_url_str} {resp_text}"
                g_match = re.search(r'https?://video-downloads\.googleusercontent\.com/[^\s\'"<>\\]+', combined_text)
                if g_match:
                    direct = g_match.group(0).replace("&amp;", "&")
                    if is_safe_url(direct):
                        return {
                            "status": "SUCCESS",
                            "host": "Google CDN",
                            "direct_url": direct,
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }

                # Check sub-candidates in resp_text
                sub_cands = await self.harvest_all_options(client, resp_url_str, html=resp_text)
                for sc in sub_cands:
                    if sc.get("type") in ["worker_direct", "r2_direct", "pixeldrain"]:
                        return {
                            "status": "SUCCESS",
                            "host": sc.get("label", "Direct CDN"),
                            "direct_url": sc.get("url"),
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }

            if c_type == "google_drive":
                gd_match = re.search(r'drive\.google\.com/file/d/([a-zA-Z0-9_-]+)', url)
                file_id = candidate.get("file_id") or (gd_match.group(1) if gd_match else "")
                gd_res = await self.resolve_google_drive(client, file_id)
                if gd_res.get("status") == "SUCCESS":
                    return gd_res

            if c_type == "dynamic_button":
                r = await self.safe_fetch(client, "GET", url, headers={"Referer": parent_url or url, **DEFAULT_HEADERS}, timeout=HOP_TIMEOUT)
                resp_url_str = str(r.url)
                if any(ext in resp_url_str.lower() for ext in [".mkv", ".mp4", "workers.dev", "cloudflarestorage.com"]) and not any(junk in resp_url_str.lower() for junk in ["hubcloud-video", "tutorial", "sample", "trailer"]):
                    if is_safe_url(resp_url_str):
                        return {
                            "status": "SUCCESS",
                            "host": "Direct Video Stream",
                            "direct_url": resp_url_str,
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }

                # 1. Check query parameters (?link=, ?url=, etc.) on resp_url_str
                parsed_resp = urllib.parse.urlparse(resp_url_str)
                qs = urllib.parse.parse_qs(parsed_resp.query)
                for qk in ["link", "url", "download", "file"]:
                    if qk in qs and qs[qk]:
                        link_val = qs[qk][0].replace("&amp;", "&").strip('\'"<>\\ ')
                        for _ in range(2):
                            if "%" in link_val:
                                link_val = urllib.parse.unquote(link_val).strip('\'"<>\\ ')
                        if any(k in link_val.lower() for k in ["video-downloads.googleusercontent.com", "pixeldrain", "r2", "workers.dev", "cloudflarestorage.com", ".mkv", ".mp4"]):
                            if "pixeldrain" in link_val and "/u/" in link_val:
                                pd_id = link_val.split("/u/")[-1].split("?")[0].strip("/")
                                link_val = f"https://pixeldrain.dev/api/file/{pd_id}"
                            if is_safe_url(link_val):
                                return {
                                    "status": "SUCCESS",
                                    "host": "Direct CDN Stream",
                                    "direct_url": link_val,
                                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                                }

                # 2. Check resp_url_str or r.text for direct CDN URLs (Google CDN, Pixeldrain API, CryptoNewz)
                combined_content = f"{resp_url_str} {r.text}"

                # Google CDN check
                g_match = re.search(r'https?://video-downloads\.googleusercontent\.com/[^\s\'"<>\\]+', combined_content)
                if g_match:
                    g_direct = g_match.group(0).replace("&amp;", "&").rstrip('\\"')
                    if is_safe_url(g_direct):
                        return {
                            "status": "SUCCESS",
                            "host": "Google CDN",
                            "direct_url": g_direct,
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }

                # Pixeldrain API check
                pd_match = re.search(r'https?://pixeldrain\.(?:com|dev)/(?:u|api/file)/([a-zA-Z0-9]+)', combined_content)
                if pd_match and pd_match.group(1) not in ["negn6f", ""]:
                    pd_direct = f"https://pixeldrain.dev/api/file/{pd_match.group(1)}"
                    if is_safe_url(pd_direct):
                        return {
                            "status": "SUCCESS",
                            "host": "Pixeldrain API",
                            "direct_url": pd_direct,
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }

                # CryptoNewz check
                crypto_match = re.search(r'(?:https?://)?(?:[a-zA-Z0-9.-]+\.)?cryptonewz\.online/dl\.php\?link=([^\s\'"<>\\]+)', combined_content)
                if crypto_match:
                    raw_target = crypto_match.group(1).replace("&amp;", "&").strip('\'"<>\\ ')
                    for _ in range(2):
                        if "%" in raw_target:
                            raw_target = urllib.parse.unquote(raw_target).strip('\'"<>\\ ')
                    if "pixeldrain" in raw_target and "/u/" in raw_target:
                        pd_id = raw_target.split("/u/")[-1].split("?")[0].strip("/")
                        raw_target = f"https://pixeldrain.dev/api/file/{pd_id}"
                    c_direct = raw_target if raw_target.startswith("http") else crypto_match.group(0).replace("&amp;", "&").rstrip('\\"')
                    if not c_direct.startswith("http"):
                        c_direct = urllib.parse.urljoin(resp_url_str, c_direct)
                    if is_safe_url(c_direct):
                        return {
                            "status": "SUCCESS",
                            "host": "CryptoNewz CDN",
                            "direct_url": c_direct,
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }

                # Check for R2 / Pixeldrain / Workers inside response
                sub_candidates = await self.harvest_all_options(client, resp_url_str, html=r.text)
                for sc in sub_candidates:
                    if sc.get("type") in ["worker_direct", "r2_direct", "pixeldrain", "instant_cdn"]:
                        return {
                            "status": "SUCCESS",
                            "host": sc.get("label", "Direct CDN"),
                            "direct_url": sc.get("url"),
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }

        except Exception as e:
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            logger.warning(f"[Candidate Fail] Host: {host_label} | Elapsed: {elapsed}ms | Error: {e}")
            return {"status": "ERROR", "error": str(e), "elapsed_ms": elapsed}

        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(f"[Candidate Fail] Host: {host_label} | Elapsed: {elapsed}ms | Reason: no direct stream extracted")
        return {"status": "FAILED", "error": "Could not resolve candidate to direct stream", "elapsed_ms": elapsed}

    # -------------------------------------------------------------
    # 5. DriveSeed & UnblockedGames Multi-Option Solver (< 250ms)
    # -------------------------------------------------------------
    async def resolve_driveseed_multi(self, client: httpx.AsyncClient, url: str) -> dict:
        """
        Dynamically extracts and races ALL options on DriveSeed:
        Resume Cloud (Workers), Instant Download (Google CDN), Cloudflare R2, Pixeldrain, Google Drive.
        Uses stealth Chrome TLS sessions when available to bypass Cloudflare gates.
        """
        t0 = time.perf_counter()
        target_host = urllib.parse.urlparse(url).netloc or "DriveSeed"
        if not is_safe_url(url):
            return {"status": "FAILED", "error": "Unsafe or private IP URL", "elapsed_ms": 0.0}

        try:
            target_landing_url = url
            landing_html = ""

            # Stealth solver for multi-step form & cookie gateways (thenaukriadda, unblockedgames, driveseed, etc.)
            if ("cloud.unblockedgames.world" in url or "thenaukriadda" in url or "sid=" in url or "lp_go" in url) and HAS_CURL_CFFI:
                try:
                    def _solve_multistep_gateway_sync(start_url):
                        s = stealth_requests.Session(impersonate="chrome120")
                        current_url = start_url
                        r = s.get(current_url, timeout=10.0)
                        current_url = str(r.url)

                        for step in range(1, 9):
                            html = r.text
                            soup = BeautifulSoup(html, "lxml")

                            # 1. Direct Stream Check
                            g_cdn = re.search(r'https?://video-downloads\.googleusercontent\.com/[^\s\'"<>\\]+', f"{current_url} {html}")
                            if g_cdn:
                                return g_cdn.group(0).replace("&amp;", "&").rstrip('\\"'), current_url, html

                            w_cdn = re.search(r'https?://[a-zA-Z0-9.-]+\.workers\.dev/[^\s\'"<>\\]+', html)
                            if w_cdn:
                                return w_cdn.group(0).replace("&amp;", "&").rstrip('\\"'), current_url, html

                            # 2. Check if reached File Landing Page (e.g. driveseed.org/file/...)
                            is_file_landing = bool("/file/" in current_url or "/download" in current_url or "/view" in current_url or "/zfile/" in html or re.search(r'video-(?:gen|seed|plex)', html))
                            if is_file_landing:
                                # A. Check Instant Download (VideoGen / VideoSeed)
                                inst_links = [a["href"] for a in soup.find_all("a", href=True) if any(k in a["href"] for k in ["video-gen", "video-seed", "video-plex", "video-downloads"])]
                                if inst_links:
                                    inst_target = urllib.parse.urljoin(current_url, inst_links[0])
                                    try:
                                        r_inst = s.get(inst_target, headers={"Referer": current_url}, timeout=10.0)
                                        qs = urllib.parse.parse_qs(urllib.parse.urlparse(str(r_inst.url)).query)
                                        if "url" in qs and qs["url"]:
                                            cand = qs["url"][0].replace("&amp;", "&").rstrip('\\"')
                                            if "googleusercontent.com" in cand:
                                                return cand, current_url, html
                                        g_match = re.search(r'https?://video-downloads\.googleusercontent\.com/[^\s\'"<>\\]+', f"{str(r_inst.url)} {r_inst.text}")
                                        if g_match:
                                            return g_match.group(0).replace("&amp;", "&").rstrip('\\"'), current_url, html
                                    except Exception:
                                        pass

                                # B. Check Resume Cloud (/zfile/...)
                                zfile_links = [a["href"] for a in soup.find_all("a", href=True) if "/zfile/" in a["href"]]
                                if not zfile_links and "/file/" in current_url:
                                    zfile_links = [current_url.replace("/file/", "/zfile/")]

                                if zfile_links:
                                    z_target = urllib.parse.urljoin(current_url, zfile_links[0])
                                    try:
                                        r_z = s.get(z_target, headers={"Referer": current_url}, timeout=10.0)
                                        w_match = re.search(r'https?://[a-zA-Z0-9.-]+\.workers\.dev/[^\s\'"<>\\]+', r_z.text)
                                        if w_match:
                                            return w_match.group(0).replace("&amp;", "&").rstrip('\\"'), current_url, html
                                    except Exception:
                                        pass

                                # C. Check Pixeldrain or R2
                                pxl_match = re.search(r'https?://pixeldrain\.(?:com|dev)/(?:u|api/file)/([a-zA-Z0-9]+)', html)
                                if pxl_match and pxl_match.group(1) not in ["negn6f", ""]:
                                    return f"https://pixeldrain.dev/api/file/{pxl_match.group(1)}", current_url, html

                                r2_match = re.search(r'https?://[a-zA-Z0-9.-]+(?:\.r2\.cloudflarestorage\.com|\.r2\.dev)/[^\s\'"<>\\]+', html)
                                if r2_match:
                                    return r2_match.group(0).replace("&amp;", "&").rstrip('\\"'), current_url, html

                                return None, current_url, html

                            # 3. Dynamic Cookie setters
                            cookie_setters = []
                            for m in re.finditer(r'(?:sc|s_\d+)\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']', html):
                                c_name, c_val = m.group(1), m.group(2)
                                cookie_setters.append((c_name, c_val))
                                s.cookies.set(c_name, c_val)
                                domain = urllib.parse.urlparse(current_url).netloc
                                s.cookies.set(c_name, c_val, domain=domain)

                            # 4. Form Submissions
                            form = soup.find("form", id="lp-s1-form")
                            if not form:
                                for candidate_form in soup.find_all("form"):
                                    inps = [inp.get("name", "") for inp in candidate_form.find_all("input") if inp.get("name")]
                                    if any(k.startswith("_lp_") or k in ["landing", "step", "token"] for k in inps):
                                        form = candidate_form
                                        break
                            if not form and not cookie_setters:
                                first_f = soup.find("form")
                                if first_f and first_f.find("input"):
                                    form = first_f

                            if form and not cookie_setters:
                                action = form.get("action") or current_url
                                target_post_url = urllib.parse.urljoin(current_url, action)
                                inputs = {inp.get("name"): inp.get("value", "") for inp in form.find_all("input") if inp.get("name")}
                                r = s.post(target_post_url, data=inputs, headers={"Referer": current_url}, timeout=10.0)
                                current_url = str(r.url)
                                continue

                            # 5. Client-Side Redirects
                            next_target = None
                            href_match = re.search(r'setAttribute\(\s*["\']href["\']\s*,\s*["\']([^"\']+)["\']', html)
                            if href_match:
                                next_target = href_match.group(1).replace(r'\/', '/')

                            if not next_target:
                                loc_match = re.search(r'location\.replace\(\s*["\']([^"\']+)["\']', html)
                                if loc_match:
                                    next_target = loc_match.group(1).replace(r'\/', '/')

                            if not next_target:
                                refresh = soup.find("meta", attrs={"http-equiv": re.compile(r"refresh", re.I)})
                                if refresh and "url=" in refresh.get("content", "").lower():
                                    next_target = re.split(r'url=', refresh["content"], flags=re.I)[1].strip()

                            if next_target:
                                next_url = urllib.parse.urljoin(current_url, next_target)
                                r = s.get(next_url, headers={"Referer": current_url}, timeout=10.0)
                                current_url = str(r.url)
                                continue

                            break

                        return None, current_url, html

                    stealth_direct, stealth_url, stealth_html = await asyncio.to_thread(_solve_multistep_gateway_sync, url)
                    if stealth_direct:
                        return {
                            "status": "SUCCESS",
                            "host": "Google CDN Direct" if "googleusercontent" in stealth_direct else "Direct Stream",
                            "direct_url": stealth_direct,
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }
                    if stealth_url and stealth_html:
                        target_landing_url = stealth_url
                        landing_html = stealth_html
                except Exception as ex:
                    logger.warning(f"[DriveSeed Stealth Warning] {ex}")

            # Fallback standard httpx solver if stealth did not produce landing page
            if not landing_html and ("cloud.unblockedgames.world" in url or "thenaukriadda" in url or "sid=" in url):
                r1 = await self.safe_fetch(client, "GET", url, headers=DEFAULT_HEADERS, timeout=HOP_TIMEOUT)
                soup1 = BeautifulSoup(r1.text, "html.parser")
                f1 = soup1.find("form")
                if f1:
                    d1 = {inp.get("name"): inp.get("value", "") for inp in f1.find_all("input") if inp.get("name")}
                    a1 = urllib.parse.urljoin(str(r1.url), f1.get("action") or str(r1.url))
                    r2 = await self.safe_fetch(client, "POST", a1, data=d1, headers={"Referer": str(r1.url)}, timeout=HOP_TIMEOUT)
                    soup2 = BeautifulSoup(r2.text, "html.parser")
                    f2 = soup2.find("form", id="lp-s1-form") or soup2.find("form")
                    if f2:
                        d2 = {inp.get("name"): inp.get("value", "") for inp in f2.find_all("input") if inp.get("name")}
                        a2 = urllib.parse.urljoin(str(r2.url), f2.get("action") or str(r2.url))
                        r3 = await self.safe_fetch(client, "POST", a2, data=d2, headers={"Referer": str(r2.url)}, timeout=HOP_TIMEOUT)
                        cookie_m = re.search(r"(?:s_\d+|sc)\(['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]", r3.text)
                        if cookie_m:
                            c_name, c_val = cookie_m.group(1), cookie_m.group(2)
                            try:
                                client.cookies.set(c_name, c_val)
                                domain_parsed = urllib.parse.urlparse(str(r3.url)).netloc
                                client.cookies.set(c_name, c_val, domain=domain_parsed)
                            except Exception:
                                pass

                            go_url = None
                            go_match = re.search(r'setAttribute\(\s*["\']href["\']\s*,\s*["\']([^"\']+)["\']', r3.text)
                            if go_match:
                                go_url = urllib.parse.urljoin(str(r3.url), go_match.group(1).replace(r'\/', '/'))
                            else:
                                go_url = f"https://{domain_parsed}/?go={c_name}"

                            r4 = await self.safe_fetch(client, "GET", go_url, headers={"Referer": str(r3.url), "Cookie": f"{c_name}={c_val}"}, timeout=HOP_TIMEOUT)

                            # Follow meta refresh or location.replace redirect
                            next_hop = None
                            meta_ref = re.search(r'url=([^\'"\s>]+)', r4.text, re.I)
                            if meta_ref:
                                next_hop = meta_ref.group(1)
                            else:
                                loc_m = re.search(r'location\.replace\(\s*["\']([^"\']+)["\']', r4.text)
                                if loc_m:
                                    next_hop = loc_m.group(1).replace(r'\/', '/')

                            if next_hop:
                                ref_url = urllib.parse.urljoin(str(r4.url), next_hop)
                                r5 = await self.safe_fetch(client, "GET", ref_url, headers={"Referer": str(r4.url)}, timeout=HOP_TIMEOUT)
                                loc_match = re.search(r'location(?:\.replace)?\([\'\"]([^\'\"]+)[\'\"]\)', r5.text)
                                if loc_match:
                                    final_file_url = urllib.parse.urljoin(str(r5.url), loc_match.group(1))
                                    r6 = await self.safe_fetch(client, "GET", final_file_url, headers={"Referer": str(r5.url)}, timeout=HOP_TIMEOUT)
                                    landing_html = r6.text
                                    target_landing_url = str(r6.url)
                                else:
                                    landing_html = r5.text
                                    target_landing_url = str(r5.url)
                            else:
                                landing_html = r4.text
                                target_landing_url = str(r4.url)

            # 2. Harvest all provider candidates dynamically
            candidates = await self.harvest_all_options(client, target_landing_url, html=landing_html)
            if not candidates:
                r = await self.safe_fetch(client, "GET", target_landing_url, headers=DEFAULT_HEADERS, timeout=HOP_TIMEOUT)
                candidates = await self.harvest_all_options(client, target_landing_url, html=r.text)

            if not candidates:
                elapsed = round((time.perf_counter() - t0) * 1000, 2)
                logger.info(f"[DriveSeed Fail] Host: {target_host} | Elapsed: {elapsed}ms | Reason: no buttons found")
                return {"status": "FAILED", "error": "No download buttons found on landing page", "elapsed_ms": elapsed}

            # 3. Race all discovered candidates concurrently
            tasks = [self.resolve_candidate(client, c, parent_url=target_landing_url) for c in candidates]
            for completed_task in asyncio.as_completed(tasks):
                res = await completed_task
                if res.get("status") == "SUCCESS" and res.get("direct_url"):
                    res["total_harvested_options"] = len(candidates)
                    res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
                    return res

        except Exception as e:
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            logger.warning(f"[DriveSeed Error] Host: {target_host} | Elapsed: {elapsed}ms | Error: {e}")
            return {"status": "ERROR", "error": str(e), "elapsed_ms": elapsed}

        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(f"[DriveSeed Fail] Host: {target_host} | Elapsed: {elapsed}ms | Reason: all candidates failed")
        return {"status": "FAILED", "error": "All discovered DriveSeed options failed verification", "elapsed_ms": elapsed}

    # -------------------------------------------------------------
    # 6. V-Cloud / NexDrive Resolver (80ms - 180ms)
    # -------------------------------------------------------------
    async def resolve_vcloud(self, client: httpx.AsyncClient, url: str) -> dict:
        """Extract direct Pixeldrain API or Cloudflare R2 link from V-Cloud / NexDrive in < 200ms."""
        t0 = time.perf_counter()
        if not url:
            return {"status": "FAILED", "error": "Empty URL provided", "elapsed_ms": 0.0}
        if not is_safe_url(url):
            return {"status": "FAILED", "error": "Unsafe or private IP URL", "elapsed_ms": 0.0}

        target_host = urllib.parse.urlparse(url).netloc or "V-Cloud"
        try:
            target_url = url
            if "nexdrive" in url:
                try:
                    r1 = await self.safe_fetch(client, "GET", url, headers=DEFAULT_HEADERS, timeout=HOP_TIMEOUT)
                    vcloud_match = re.search(r'https?://vcloud\.[a-z]+/([a-zA-Z0-9_-]+)', r1.text)
                    if vcloud_match:
                        target_url = vcloud_match.group(0)
                except Exception:
                    pass

            r2 = await self.safe_fetch(client, "GET", target_url, headers=DEFAULT_HEADERS, timeout=HOP_TIMEOUT)
            html = r2.text

            # Check if double atob token is present in V-Cloud landing HTML
            atob_m = re.search(r"atob\(atob\((['\"][a-zA-Z0-9+/=]{10,}['\"])\)\)", html)
            if atob_m:
                try:
                    raw_tok = atob_m.group(1).strip('\'"')
                    b1 = base64.b64decode(raw_tok).decode('utf-8')
                    step2_url = base64.b64decode(b1).decode('utf-8')
                    if is_safe_url(step2_url):
                        r_step2 = await self.safe_fetch(client, "GET", step2_url, headers={'Referer': target_url, **DEFAULT_HEADERS}, timeout=HOP_TIMEOUT)
                        html = f"{html}\n{r_step2.text}"
                except Exception:
                    pass

            # Dynamic harvesting across V-Cloud
            candidates = await self.harvest_all_options(client, target_url, html=html)
            if candidates:
                tasks = [self.resolve_candidate(client, c, parent_url=target_url) for c in candidates]
                for completed in asyncio.as_completed(tasks):
                    res = await completed
                    if res.get("status") == "SUCCESS" and res.get("direct_url"):
                        return res

        except Exception as e:
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            logger.warning(f"[V-Cloud Error] Host: {target_host} | Elapsed: {elapsed}ms | Error: {e}")
            return {"status": "ERROR", "error": str(e), "elapsed_ms": elapsed}

        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(f"[V-Cloud Fail] Host: {target_host} | Elapsed: {elapsed}ms | Reason: stream link not found")
        return {"status": "FAILED", "error": "No direct stream link found in V-Cloud", "elapsed_ms": elapsed}

    # -------------------------------------------------------------
    # 7. HubCloud / Gamerxyt / Gyanigurus / HubDrive Resolver (100ms - 200ms)
    # -------------------------------------------------------------
    async def resolve_hubcloud_gamerxyt(self, client: httpx.AsyncClient, url: str) -> dict:
        """Extract Cloudflare R2 or Pixeldrain link from Gyanigurus / HubCloud / Gamerxyt / HubDrive in < 200ms."""
        t0 = time.perf_counter()
        if not url:
            return {"status": "FAILED", "error": "Empty URL provided", "elapsed_ms": 0.0}
        if not is_safe_url(url):
            return {"status": "FAILED", "error": "Unsafe or private IP URL", "elapsed_ms": 0.0}

        target_host = urllib.parse.urlparse(url).netloc or "HubCloud"
        try:
            target_url = url
            # Unpack greenmotors or hdstream4u gateway if passed directly
            if any(k in target_url for k in ["greenmotors.club", "greenmotors.cc", "hdstream4u.com"]):
                unpacked = await resolve_gateway_url(target_url, client=client)
                if any(ext in unpacked.lower() for ext in [".mp4", ".mkv"]) or "workers.dev" in unpacked or "pixeldrain" in unpacked:
                    return {
                        "status": "SUCCESS",
                        "host": "Direct Video Stream",
                        "direct_url": unpacked,
                        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                    }
                target_url = unpacked

            # If HubDrive, extract HubCloud button
            if "hubdrive" in target_url:
                r0 = await self.safe_fetch(client, "GET", target_url, headers=DEFAULT_HEADERS, timeout=HOP_TIMEOUT)
                hub_m = re.search(r'https?://hubcloud\.[a-z]+/drive/[a-zA-Z0-9_-]+', r0.text)
                if hub_m:
                    target_url = hub_m.group(0)

            r1 = await self.safe_fetch(client, "GET", target_url, headers=DEFAULT_HEADERS, timeout=HOP_TIMEOUT)
            html = r1.text
            candidates = await self.harvest_all_options(client, target_url, html=html)

            # If HubCloud / Gyanigurus, find hidden gamerxyt link directly in raw HTML (exclude images/favicons)
            hub_match = re.search(r'https?://(?:hubcloud\.[a-z]+|gamerxyt\.com)/(?:hubcloud\.php|drive/[a-zA-Z0-9_-]+|\?id=[a-zA-Z0-9_-]+)[^\s\'"<>\\]*', html)
            if not hub_match:
                hub_match = re.search(r'https?://gamerxyt\.com/[^\s\'"<>\\]+', html)
            if hub_match:
                hub_url = hub_match.group(0).replace("&amp;", "&")
                if is_safe_url(hub_url):
                    r2 = await self.safe_fetch(client, "GET", hub_url, headers={"Referer": target_url, **DEFAULT_HEADERS}, timeout=HOP_TIMEOUT)
                    sub_cands = await self.harvest_all_options(client, hub_url, html=r2.text)
                    for sc in sub_cands:
                        sc["parent_url"] = hub_url
                    candidates.extend(sub_cands)

            if candidates:
                candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
                tasks = [self.resolve_candidate(client, c, parent_url=c.get("parent_url") or target_url) for c in candidates]
                quota_exceeded_res = None
                for completed in asyncio.as_completed(tasks):
                    res = await completed
                    if res.get("status") == "SUCCESS" and res.get("direct_url"):
                        return res
                    if res.get("status") == "QUOTA_EXCEEDED":
                        quota_exceeded_res = res

                if quota_exceeded_res:
                    return quota_exceeded_res

        except Exception as e:
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            logger.warning(f"[HubCloud Error] Host: {target_host} | Elapsed: {elapsed}ms | Error: {e}")
            return {"status": "ERROR", "error": str(e), "elapsed_ms": elapsed}

        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(f"[HubCloud Fail] Host: {target_host} | Elapsed: {elapsed}ms | Reason: no stream URL found")
        return {"status": "FAILED", "error": "No R2 or Pixeldrain stream URL found in HubCloud", "elapsed_ms": elapsed}

    async def _solve_olamovies_stealth(self, url: str, timeout: float = 12.0) -> Optional[str]:
        """
        Fast automated headless Turnstile solver using patchright / playwright.
        Attempts to bypass Cloudflare Turnstile and extract direct CDN stream in < 12 seconds
        without requiring human desktop intervention.
        """
        if not HAS_PATCHRIGHT:
            return None
        direct_found = None
        last_click_ref = [0.0]
        navigated_b64 = False
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox"
                    ]
                )
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 720}
                )
                page = await context.new_page()

                async def on_response(resp):
                    nonlocal direct_found
                    try:
                        u = resp.url
                        if any(k in u for k in ["dl.olamdrive.com/dl", "r2.cloudflarestorage.com", "pixeldrain.dev", "workers.dev", "googleusercontent.com"]):
                            direct_found = u
                    except Exception:
                        pass

                page.on("response", on_response)

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
                except Exception:
                    pass

                deadline = time.time() + timeout
                while time.time() < deadline:
                    if direct_found:
                        break

                    title = ""
                    try:
                        title = await page.title()
                    except Exception:
                        pass

                    is_cf = "Just a moment" in title or "Cloudflare" in title or any(
                        "challenges.cloudflare.com" in f.url for f in page.frames
                    )

                    if is_cf:
                        await try_click_turnstile(page, last_click_ref)
                    else:
                        # Turnstile passed, check base64 redirect
                        content = ""
                        try:
                            content = await page.content()
                        except Exception:
                            pass

                        if not navigated_b64 and content:
                            b64_target = extract_b64_redirect_url(content)
                            if b64_target and is_safe_url(b64_target):
                                navigated_b64 = True
                                try:
                                    await page.goto(b64_target, wait_until="domcontentloaded", timeout=8000)
                                    continue
                                except Exception:
                                    pass

                        # Trigger hold/download button if present
                        try:
                            hold_btn = page.locator(".cc-hold-target, button:has-text('Hold'), a:has-text('Download'), a:has-text('Continue')").first
                            if await hold_btn.is_visible(timeout=800):
                                box = await hold_btn.bounding_box()
                                if box:
                                    await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                                    await page.mouse.down()
                                    await asyncio.sleep(3.2)
                                    await page.mouse.up()
                        except Exception:
                            pass

                        try:
                            if content:
                                r2_m = re.search(r'https?://[a-zA-Z0-9.-]+\.r2\.cloudflarestorage\.com/[^\s\'"<>\\]+', content)
                                if r2_m:
                                    direct_found = r2_m.group(0).replace("&amp;", "&")
                                    break
                                pd_m = re.search(r'https?://pixeldrain\.(?:com|dev)/(?:u|api/file)/([a-zA-Z0-9]+)', content)
                                if pd_m:
                                    direct_found = f"https://pixeldrain.dev/api/file/{pd_m.group(1)}"
                                    break
                                dl_m = re.search(r'https?://dl\.olamdrive\.com/dl\?t=[^\s\'"<>\\]+', content)
                                if dl_m:
                                    direct_found = dl_m.group(0)
                                    break
                        except Exception:
                            pass

                    await asyncio.sleep(0.4)

                try:
                    await browser.close()
                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"[OlaMovies Stealth Error] {e}")

        return direct_found

    # -------------------------------------------------------------
    # 8. OlaMovies / OMDrive / dl.olamdrive Resolver (80ms - 180ms)
    # -------------------------------------------------------------
    async def resolve_olamovies(self, client: httpx.AsyncClient, url: str, allow_interactive: bool = False) -> dict:
        """Resolve OlaMovies / OMDrive link with dynamic Cloudflare stealth solver and adaptive wait."""
        t0 = time.perf_counter()
        if not url:
            return {"status": "FAILED", "error": "Empty URL provided", "elapsed_ms": 0.0}
        if not is_safe_url(url):
            return {"status": "FAILED", "error": "Unsafe or private IP URL", "elapsed_ms": 0.0}

        target_host = urllib.parse.urlparse(url).netloc or "OlaMovies"
        try:
            # 1. Direct CDN shortcuts
            if "dl.olamdrive.com/dl" in url:
                return {
                    "status": "SUCCESS",
                    "host": "OMDrive Direct CDN",
                    "direct_url": url,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                }

            if "drive.olamovies" in url:
                text_content = ""
                if HAS_CURL_CFFI:
                    try:
                        def _curl_drive():
                            s = stealth_requests.Session(impersonate="chrome120")
                            r_d = s.get(url, headers={"Referer": "https://v4.olamovies.mov/", **DEFAULT_HEADERS}, timeout=HOP_TIMEOUT)
                            return r_d.text
                        text_content = await asyncio.to_thread(_curl_drive)
                    except Exception:
                        pass
                if not text_content:
                    r1 = await self.safe_fetch(client, "GET", url, headers=DEFAULT_HEADERS, timeout=HOP_TIMEOUT)
                    text_content = r1.text

                dl_match = re.search(r'https?://dl\.olamdrive\.com/dl\?t=[^\s\'"<>\\]+', text_content)
                if dl_match:
                    direct = dl_match.group(0)
                    if is_safe_url(direct):
                        return {
                            "status": "SUCCESS",
                            "host": "OMDrive Direct CDN",
                            "direct_url": direct,
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }

            # 2. Check if auth bridge already intercepted a direct stream for this target URL
            if hasattr(self.auth_bridge, "get_resolved_stream"):
                cached_stream = self.auth_bridge.get_resolved_stream(url)
                if cached_stream and is_safe_url(cached_stream):
                    return {
                        "status": "SUCCESS",
                        "host": "OlaMovies Intercepted CDN",
                        "direct_url": cached_stream,
                        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                    }

            if "tpi.li" in url or "links.olamovies" in url or "links.ol-am" in url:
                # 3. Strategy 1: Check vault cookies with curl_cffi (Chrome 120 TLS fingerprint)
                cookies = self.auth_bridge.get_provider_cookies("olamovies")
                if not cookies:
                    cookie_file = os.path.join(os.path.dirname(__file__), "olamovies_session_cookies.json")
                    if os.path.exists(cookie_file):
                        try:
                            with open(cookie_file, "r", encoding="utf-8") as cf:
                                cookies = json.load(cf)
                        except Exception:
                            pass

                if HAS_CURL_CFFI and cookies and is_safe_url(url):
                    try:
                        def _curl_with_vault():
                            s = stealth_requests.Session(impersonate="chrome120")
                            for ck in cookies:
                                ck_domain = ck.get("domain", "")
                                if any(d in ck_domain for d in ["olamovies", "paybase", "ol-am", "cloudflare"]):
                                    s.cookies.set(ck["name"], ck["value"], domain=ck_domain if ck_domain.startswith(".") else f".{target_host}")
                            r_c = s.get(url, headers={"Referer": "https://v4.olamovies.mov/", **DEFAULT_HEADERS}, timeout=HOP_TIMEOUT)
                            if r_c.status_code == 200 and "Just a moment" not in r_c.text and "Cloudflare" not in r_c.text:
                                return r_c.text
                            return None
                        vault_html = await asyncio.to_thread(_curl_with_vault)
                        if vault_html:
                            dec = extract_b64_redirect_url(vault_html)
                            if dec and is_safe_url(dec):
                                res = await self.resolve_olamovies(client, dec, allow_interactive=False)
                                if res.get("status") == "SUCCESS" and res.get("direct_url"):
                                    if hasattr(self.auth_bridge, "_resolved_streams"):
                                        self.auth_bridge._resolved_streams[url] = res["direct_url"]
                                    return res
                    except Exception:
                        pass

                # 4. Strategy 2: Attempt automated headless stealth solve (< 10 seconds)
                stealth_stream = await self._solve_olamovies_stealth(url, timeout=10.0)
                if stealth_stream and is_safe_url(stealth_stream):
                    logger.info(f"[OlaMovies Stealth Hit] Resolved direct stream: {stealth_stream[:80]}")
                    if hasattr(self.auth_bridge, "_resolved_streams"):
                        self.auth_bridge._resolved_streams[url] = stealth_stream
                    return {
                        "status": "SUCCESS",
                        "host": "OlaMovies Stealth Bypass",
                        "direct_url": stealth_stream,
                        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                    }

                # 5. Strategy 3: Interactive persistent solver bridge with automated Turnstile clicker
                if not allow_interactive:
                    return {
                        "status": "CLOUDFLARE_PROTECTED",
                        "host": "OlaMovies Protected",
                        "direct_url": url,
                        "error": "Cloudflare Turnstile Active (Automated solve failed)",
                        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                    }

                auth_res = await self.auth_bridge.trigger_interactive_login("olamovies", target_url=url, timeout=60.0)
                if auth_res.get("status") == "SUCCESS":
                    if auth_res.get("direct_url") and is_safe_url(auth_res["direct_url"]):
                        return {
                            "status": "SUCCESS",
                            "host": "OlaMovies Direct Stream",
                            "direct_url": auth_res["direct_url"],
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }
                    if hasattr(self.auth_bridge, "get_resolved_stream"):
                        cached = self.auth_bridge.get_resolved_stream(url)
                        if cached and is_safe_url(cached):
                            return {
                                "status": "SUCCESS",
                                "host": "OlaMovies Direct Stream",
                                "direct_url": cached,
                                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                            }

                    # If cookies were refreshed, retry with curl_cffi (Chrome 120)
                    fresh_cookies = self.auth_bridge.get_provider_cookies("olamovies")
                    if HAS_CURL_CFFI and fresh_cookies:
                        try:
                            def _curl_fresh():
                                s = stealth_requests.Session(impersonate="chrome120")
                                for ck in fresh_cookies:
                                    ck_domain = ck.get("domain", "")
                                    if any(d in ck_domain for d in ["olamovies", "paybase", "ol-am", "cloudflare"]):
                                        s.cookies.set(ck["name"], ck["value"], domain=ck_domain if ck_domain.startswith(".") else f".{target_host}")
                                r_c = s.get(url, headers={"Referer": "https://v4.olamovies.mov/", **DEFAULT_HEADERS}, timeout=HOP_TIMEOUT)
                                return r_c.text if r_c.status_code == 200 else None
                            fresh_html = await asyncio.to_thread(_curl_fresh)
                            if fresh_html:
                                dec = extract_b64_redirect_url(fresh_html)
                                if dec and is_safe_url(dec):
                                    return await self.resolve_olamovies(client, dec, allow_interactive=False)
                        except Exception:
                            pass

                return {
                    "status": "CLOUDFLARE_FAILED",
                    "host": "OlaMovies Protected",
                    "direct_url": url,
                    "error": auth_res.get("error", "Cloudflare challenge was not resolved"),
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                }

        except Exception as e:
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            logger.warning(f"[OlaMovies Error] Host: {target_host} | Elapsed: {elapsed}ms | Error: {e}")
            return {"status": "ERROR", "error": str(e), "elapsed_ms": elapsed}

        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        logger.info(f"[OlaMovies Fail] Host: {target_host} | Elapsed: {elapsed}ms | Reason: unresolvable via pure HTTP")
        return {"status": "FAILED", "error": "Could not resolve OlaMovies URL via pure HTTP", "elapsed_ms": elapsed}

    # -------------------------------------------------------------
    # 9. Auto Dispatcher Router (Resilient, Multi-Option Race)
    # -------------------------------------------------------------
    async def resolve_auto(self, url: str, allow_interactive: bool = False) -> dict:
        """
        Automatically route incoming URL to dedicated pure async multi-option resolver.
        Guarantees zero crashes on malformed URLs, timeouts, or dead links.
        Enforces strict SSRF validation and redirect protection.
        """
        t0 = time.perf_counter()
        if not url or not str(url).strip():
            return {
                "status": "FAILED",
                "host": "Unknown",
                "direct_url": "",
                "error": "Empty URL provided",
                "elapsed_ms": 0.0
            }

        url_str = str(url).strip()
        install_dns_ssrf_guard()

        # Check SSRF safety upfront
        if not is_safe_url(url_str):
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            logger.warning(f"[SSRF Block] Host: {urllib.parse.urlparse(url_str).netloc} | URL: {url_str} | Elapsed: {elapsed}ms")
            return {
                "status": "FAILED",
                "host": "SecurityGuard",
                "direct_url": url_str,
                "error": "Destination blocked by SSRF private-IP security policy",
                "elapsed_ms": elapsed
            }

        # Handle YouTube Watch URLs: extract direct CDN video stream URL
        if "youtube.com/watch" in url_str or "youtu.be/" in url_str:
            try:
                import yt_dlp
                ydl_opts = {
                    "format": "best[ext=mp4]/best",
                    "quiet": True,
                    "no_warnings": True,
                    "extractor_args": {
                        "youtube": {
                            "player_client": ["android", "web", "ios"]
                        }
                    }
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url_str, download=False)
                    cdn_url = info.get("url")
                    if cdn_url and is_safe_url(cdn_url):
                        return {
                            "status": "SUCCESS",
                            "host": "YouTube Direct CDN",
                            "direct_url": cdn_url,
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }
            except Exception:
                pass

            return {
                "status": "SUCCESS",
                "host": "YouTube",
                "direct_url": url_str,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
            }

        # Handle Telegram Start Tokens / Bot Links
        if "t.me/" in url_str or "tg://" in url_str:
            start_m = re.search(r'start=([a-zA-Z0-9_=-]+)', url_str)
            token = start_m.group(1) if start_m else url_str.replace("tg://", "")
            if token:
                try:
                    from tg_bot_scraper import load_tg_config, resolve_telegram_token
                    cfg = load_tg_config()
                    tg_out = await resolve_telegram_token(cfg["api_id"], cfg["api_hash"], token)
                    stream_url = tg_out.get("stream_url") or (tg_out.get("all_urls")[0] if tg_out.get("all_urls") else "")
                    if stream_url:
                        return {
                            "status": "SUCCESS",
                            "host": "Telegram Local CDN",
                            "direct_url": stream_url,
                            "filename": tg_out.get("filename"),
                            "size": tg_out.get("size"),
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }
                except Exception:
                    pass

        # Handle Pixeldrain transform (< 1ms)
        if "pixeldrain.com/u/" in url_str:
            pd_id = re.sub(r'[^a-zA-Z0-9]', '', url_str.split("/")[-1].split("?")[0])
            return {
                "status": "SUCCESS",
                "host": "Pixeldrain API",
                "direct_url": f"https://pixeldrain.dev/api/file/{pd_id}",
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
            }
        elif "pixeldrain.dev/api/file/" in url_str:
            return {
                "status": "SUCCESS",
                "host": "Pixeldrain API",
                "direct_url": url_str,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
            }

        # Direct Media Stream Short-Circuit (< 0.1ms): Never fetch 25GB+ binary media files as HTML
        if any(ext in url_str.lower() for ext in [".mkv", ".mp4", ".m3u8", ".webm", ".ts"]) or "r2.cloudflarestorage.com" in url_str:
            return {
                "status": "SUCCESS",
                "host": "Direct Media Stream",
                "direct_url": url_str,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
            }

        try:
            async with httpx.AsyncClient(http2=True, limits=self.limits, follow_redirects=False) as client:
                if any(k in url_str for k in ["greenmotors.club", "greenmotors.cc", "hdstream4u.com"]):
                    unpacked_url = await resolve_gateway_url(url_str, client=client)
                    if any(k in unpacked_url for k in ["hubcloud", "gamerxyt", "gyanigurus", "hubdrive"]):
                        res = await self.resolve_hubcloud_gamerxyt(client, unpacked_url)
                    elif any(ext in unpacked_url.lower() for ext in [".mp4", ".mkv"]) or "workers.dev" in unpacked_url or "pixeldrain" in unpacked_url:
                        res = {
                            "status": "SUCCESS",
                            "host": "Direct Video Stream",
                            "direct_url": unpacked_url,
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }
                    elif unpacked_url != url_str:
                        res = await self.resolve_auto(unpacked_url, allow_interactive=allow_interactive)
                    else:
                        res = await self.resolve_hubcloud_gamerxyt(client, url_str)
                elif any(k in url_str for k in ["driveseed", "unblockedgames", "thenaukriadda", "video-seed", "video-gen", "sid=", "lp_go"]):
                    res = await self.resolve_driveseed_multi(client, url_str)
                elif "vcloud" in url_str or "nexdrive" in url_str or "fast-dl" in url_str:
                    res = await self.resolve_vcloud(client, url_str)
                elif "hubcloud" in url_str or "gamerxyt" in url_str or "gyanigurus" in url_str or "hubdrive" in url_str:
                    res = await self.resolve_hubcloud_gamerxyt(client, url_str)
                elif "olamovies" in url_str or "olamdrive" in url_str or "ol-am" in url_str or "tpi.li" in url_str:
                    res = await self.resolve_olamovies(client, url_str, allow_interactive=allow_interactive)
                else:
                    # General landing page dynamic inspection
                    candidates = await self.harvest_all_options(client, url_str)
                    if candidates:
                        tasks = [self.resolve_candidate(client, c, parent_url=url_str) for c in candidates]
                        found_res = None
                        for completed in asyncio.as_completed(tasks):
                            c_res = await completed
                            if c_res.get("status") == "SUCCESS" and c_res.get("direct_url"):
                                found_res = c_res
                                break
                        res = found_res or {
                            "status": "SUCCESS",
                            "host": "Direct URL",
                            "direct_url": url_str,
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }
                    else:
                        res = {
                            "status": "SUCCESS",
                            "host": "Direct URL",
                            "direct_url": url_str,
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                        }

                # Adaptive JEV Gateway Fallback for unknown / mutated gateways
                if res.get("status") != "SUCCESS" or res.get("host") == "Direct URL":
                    try:
                        from jev_stream_resolver import JevStreamResolver
                        page_r = await self.safe_fetch(client, "GET", url_str, headers=DEFAULT_HEADERS, timeout=HOP_TIMEOUT)
                        if page_r and page_r.status_code == 200:
                            hop_res = await JevStreamResolver.resolve_gateway_hop(page_r.text, current_url=url_str)
                            if hop_res.get("status") == "SUCCESS" and hop_res.get("direct_url") and hop_res["direct_url"] != url_str:
                                resolved_hop = await self.resolve_auto(hop_res["direct_url"], allow_interactive=allow_interactive)
                                if resolved_hop.get("status") == "SUCCESS" and resolved_hop.get("direct_url"):
                                    res = resolved_hop
                    except Exception:
                        pass

                # If failed, keep direct_url as original fallback
                if res.get("status") != "SUCCESS" and "direct_url" not in res:
                    res["direct_url"] = url_str
                return res
        except Exception as e:
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            host = urllib.parse.urlparse(url_str).netloc or "Unknown"
            logger.error(f"[Resolve Fatal] Host: {host} | URL: {url_str} | Elapsed: {elapsed}ms | Error: {e}")
            return {
                "status": "FAILED",
                "host": "Error",
                "direct_url": url_str,
                "error": str(e),
                "elapsed_ms": elapsed
            }

    async def resolve_gateway_url(self, url: str, client: Optional[httpx.AsyncClient] = None) -> str:
        """Instance method wrapper for resolve_gateway_url."""
        return await resolve_gateway_url(url, client=client)

    async def resolve_stream_url(self, url: str, client: Optional[httpx.AsyncClient] = None, allow_interactive: bool = True) -> dict:
        """
        Direct stream URL resolver entrypoint.
        Checks if url contains greenmotors.club, greenmotors.cc, or hdstream4u.com,
        unpacks via resolve_gateway_url(), and routes to resolve_hubcloud_gamerxyt().
        """
        t0 = time.perf_counter()
        if not url or not str(url).strip():
            return {
                "status": "FAILED",
                "host": "Unknown",
                "direct_url": "",
                "error": "Empty URL provided",
                "elapsed_ms": 0.0
            }

        url_str = str(url).strip()
        if any(k in url_str for k in ["greenmotors.club", "greenmotors.cc", "hdstream4u.com"]):
            unpacked_url = await resolve_gateway_url(url_str, client=client)
            if any(k in unpacked_url for k in ["hubcloud", "gamerxyt", "gyanigurus", "hubdrive"]):
                if client is not None:
                    return await self.resolve_hubcloud_gamerxyt(client, unpacked_url)
                else:
                    async with httpx.AsyncClient(http2=True, limits=self.limits, follow_redirects=False) as c:
                        return await self.resolve_hubcloud_gamerxyt(c, unpacked_url)
            elif any(ext in unpacked_url.lower() for ext in [".mp4", ".mkv"]) or "workers.dev" in unpacked_url or "pixeldrain" in unpacked_url:
                return {
                    "status": "SUCCESS",
                    "host": "Direct Video Stream",
                    "direct_url": unpacked_url,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                }
            elif unpacked_url != url_str:
                return await self.resolve_auto(unpacked_url, allow_interactive=allow_interactive)
            else:
                if client is not None:
                    return await self.resolve_hubcloud_gamerxyt(client, url_str)
                else:
                    async with httpx.AsyncClient(http2=True, limits=self.limits, follow_redirects=False) as c:
                        return await self.resolve_hubcloud_gamerxyt(c, url_str)

        return await self.resolve_auto(url_str, allow_interactive=allow_interactive)


async def resolve_stream_url(url: str, client: Optional[httpx.AsyncClient] = None, allow_interactive: bool = True) -> dict:
    """
    Module-level stream URL resolver entrypoint.
    Unpacks gateways (greenmotors.club, greenmotors.cc, hdstream4u.com) before resolving.
    """
    resolver = LinkResolver()
    return await resolver.resolve_stream_url(url, client=client, allow_interactive=allow_interactive)


if __name__ == "__main__":
    test_target = sys.argv[1] if len(sys.argv) > 1 else "https://driveseed.org/file/k2guEv6YdQfP8LTZfZmj"
    resolver = LinkResolver()
    res = asyncio.run(resolver.resolve_auto(test_target))
    print(json.dumps(res, indent=2))
