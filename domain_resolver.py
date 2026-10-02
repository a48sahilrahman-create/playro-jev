"""
JEV Adaptive Domain Resolver
Production-grade dynamic mirror resolution, health tracking, and failover engine.
Supports:
  - UHDMovies (redirect inspection & candidates)
  - HDHub4u (Pingora DNS host dispatcher with base64 decoding)
  - VegaMovies (Edge refresh / meta redirect extraction)
  - OlaMovies (Subdomain permutation & probing)
  - DesireMovies (Concurrent candidate probing & failover)
"""

import sys
import time
import asyncio
import re
import json
import base64
import logging
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Any
import httpx

logger = logging.getLogger("jev.domain_resolver")

# Hardcoded embedded fallback configuration in case mirror_registry.json is missing or corrupt
EMBEDDED_REGISTRY = {
    "uhdmovies": {
        "type": "redirect_or_probe",
        "resolvers": [
            "https://uhdmovies.autos/",
            "https://uhdmovies.my/",
            "https://uhdmovies.mov/"
        ],
        "candidates": [
            "https://uhdmovies.my",
            "https://uhdmovies.autos",
            "https://uhdmovies.mov",
            "https://uhdmovies.one",
            "https://uhdmovies.hair",
            "https://uhdmovies.cloud",
            "https://uhdmovies.store"
        ],
        "fallback": "https://uhdmovies.my",
        "search_dork": "uhdmovies official website"
    },
    "hdhub4u": {
        "type": "pingora_dns",
        "dns_endpoint": "https://dns.pingora.fyi/v2/host",
        "fallback": "https://new1.hdhub4u.free",
        "backup_domain_from_field_h": "hdhub4u.bi",
        "candidates": [
            "https://new1.hdhub4u.free",
            "https://hdhub4u.bi",
            "https://new5.hdhub4u.cl",
            "https://new1.hdhub4u.af"
        ]
    },
    "vegamovies": {
        "type": "edge_refresh",
        "edge_endpoint": "https://vglist.lol/?re=vegamovies",
        "fallback": "https://vegamovies.gallery",
        "candidates": [
            "https://vegamovies.gallery",
            "https://new2.vegamovies.futbol",
            "https://1vegamovies.lat",
            "https://vegamovies.mov"
        ],
        "search_dork": "vegamovies official new domain"
    },
    "olamovies": {
        "type": "subdomain_probe",
        "base_domain": "olamovies.mov",
        "subdomains": [
            "v6",
            "v7",
            "v5",
            "v4",
            "v3",
            "v8"
        ],
        "candidates": [
            "https://v6.olamovies.mov",
            "https://v4.olamovies.mov",
            "https://olamovies.dad",
            "https://olamovies.mov"
        ],
        "fallback": "https://v6.olamovies.mov"
    },
    "desiremovies": {
        "type": "redirect_or_probe",
        "candidates": [
            "https://1desiremovies.rodeo",
            "https://1desiremovies.wales",
            "https://1desiremovies.nexus",
            "https://1desiremovies.cymru"
        ],
        "fallback": "https://1desiremovies.rodeo"
    }
}

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}


class DomainResolver:
    """
    Production-grade dynamic domain resolver with multi-strategy resolution,
    in-memory TTL cache, single-flight locking, health tracking, and DDG fallback.
    """

    def __init__(
        self,
        registry_path: Optional[str] = None,
        health_path: Optional[str] = None,
        ttl: float = 3600.0
    ):
        self.base_dir = Path(__file__).parent.resolve()
        self.registry_file = Path(registry_path) if registry_path else self.base_dir / "mirror_registry.json"
        self.health_file = Path(health_path) if health_path else self.base_dir / "mirror_health.json"
        self.ttl = float(ttl)

        # In-memory mirror cache: {brand: {"url": str, "resolved_at": float}}
        self._cache: Dict[str, Dict[str, Any]] = {}
        # Concurrency single-flight locks per brand
        self._locks: Dict[str, asyncio.Lock] = {}

        # Load registry configuration
        self.registry: Dict[str, Any] = self._load_registry()

        # Load mirror health state
        self._health: Dict[str, Dict[str, Any]] = self._load_health()

    def _load_registry(self) -> Dict[str, Any]:
        """Loads mirror registry from JSON file with fallback to EMBEDDED_REGISTRY."""
        if self.registry_file.is_file():
            try:
                with open(self.registry_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and data:
                        return data
            except Exception as e:
                logger.warning(f"Failed to read {self.registry_file}: {e}. Using embedded registry.")
        return json.loads(json.dumps(EMBEDDED_REGISTRY))

    def _load_health(self) -> Dict[str, Dict[str, Any]]:
        """Loads health tracking state from mirror_health.json."""
        health: Dict[str, Dict[str, Any]] = {}
        if self.health_file.is_file():
            try:
                with open(self.health_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        health = data
            except Exception as e:
                logger.warning(f"Failed to read {self.health_file}: {e}")

        # Ensure all registered brands have an entry
        for brand, cfg in self.registry.items():
            if brand not in health:
                fallback_url = cfg.get("fallback", "")
                health[brand] = {
                    "active_mirror": fallback_url,
                    "latency_ms": 0.0,
                    "last_success": 0.0,
                    "consecutive_failures": 0,
                    "status": "uninitialized"
                }
        return health

    def _save_health_sync(self):
        """Atomically saves health status to mirror_health.json."""
        try:
            temp_path = self.health_file.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self._health, f, indent=2)
            temp_path.replace(self.health_file)
        except Exception as e:
            logger.error(f"Error saving health to {self.health_file}: {e}")

    def _get_fallback(self, brand: str) -> str:
        """Retrieves hardcoded fallback mirror for brand."""
        b = brand.lower().strip()
        if b in self.registry:
            return self.registry[b].get("fallback", "")
        if b in EMBEDDED_REGISTRY:
            return EMBEDDED_REGISTRY[b].get("fallback", "")
        return ""

    def _get_lock(self, brand: str) -> asyncio.Lock:
        """Lazily creates asyncio.Lock for the given brand within active event loop."""
        if brand not in self._locks:
            self._locks[brand] = asyncio.Lock()
        return self._locks[brand]

    async def _probe_url(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        """
        Performs HEAD (or streaming GET fallback) with follow_redirects=True, timeout=2.0s.
        Returns the resolved URL string without trailing slash if responsive (< 400), else None.
        """
        if not url or not url.startswith("http"):
            return None
        try:
            # 1. Try HEAD first for low latency and zero payload overhead
            r = await client.head(url, follow_redirects=True, timeout=2.0)
            if r.status_code < 400:
                return str(r.url).rstrip('/')
            # 2. If HEAD returns 405 (Method Not Allowed) or 403, retry with stream GET
            if r.status_code in (403, 405):
                async with client.stream("GET", url, follow_redirects=True, timeout=2.0) as stream_resp:
                    if stream_resp.status_code < 400:
                        return str(stream_resp.url).rstrip('/')
        except Exception:
            # 3. Connection or protocol error on HEAD: fallback to short GET stream
            try:
                async with client.stream("GET", url, follow_redirects=True, timeout=2.0) as stream_resp:
                    if stream_resp.status_code < 400:
                        return str(stream_resp.url).rstrip('/')
            except Exception:
                pass
        return None

    async def _probe_candidates_concurrent(
        self,
        client: httpx.AsyncClient,
        candidates: List[str]
    ) -> Optional[str]:
        """
        Concurrently probes candidate URLs using asyncio.as_completed.
        Returns the first responsive URL (< 400) immediately and cancels remaining probes.
        """
        if not candidates:
            return None

        # Filter out empty or duplicate candidates while preserving order
        unique_candidates = []
        for c in candidates:
            c_clean = c.strip().rstrip('/')
            if c_clean and c_clean not in unique_candidates:
                unique_candidates.append(c_clean)

        if not unique_candidates:
            return None

        tasks = [asyncio.create_task(self._probe_url(client, cand)) for cand in unique_candidates]
        winner = None

        try:
            for fut in asyncio.as_completed(tasks):
                try:
                    res = await fut
                    if res:
                        winner = res
                        break
                except Exception:
                    continue
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

        return winner

    async def _resolve_pingora_dns(
        self,
        client: httpx.AsyncClient,
        config: Dict[str, Any]
    ) -> Optional[str]:
        """
        Dynamic resolver for HDHub4u via Pingora DNS host endpoint.
        Decodes base64 field 'c', strips query parameters, verifies HTTP 200,
        falls back to base64 field 'h', then candidate list.
        """
        endpoint = config.get("dns_endpoint", "https://dns.pingora.fyi/v2/host")
        try:
            r = await client.get(endpoint, headers=DEFAULT_HEADERS, timeout=2.0)
            if r.status_code == 200:
                data = r.json()
                # Check field 'c' (full URL)
                if "c" in data:
                    raw_c = base64.b64decode(data["c"]).decode("utf-8")
                    clean_host = raw_c.split("?")[0].rstrip("/")
                    if clean_host.startswith("http"):
                        probe_res = await self._probe_url(client, clean_host)
                        if probe_res:
                            return probe_res

                # Fallback to field 'h' (host domain)
                if "h" in data:
                    raw_h = base64.b64decode(data["h"]).decode("utf-8").strip()
                    clean_h = raw_h if raw_h.startswith("http") else f"https://{raw_h}"
                    probe_res = await self._probe_url(client, clean_h)
                    if probe_res:
                        return probe_res
        except Exception as e:
            logger.debug(f"Pingora DNS resolution error: {e}")

        # If Pingora DNS endpoint fails or hosts are unreachable, probe candidates
        candidates = config.get("candidates", [])
        return await self._probe_candidates_concurrent(client, candidates)

    async def _resolve_edge_refresh(
        self,
        client: httpx.AsyncClient,
        config: Dict[str, Any],
        brand: str
    ) -> Optional[str]:
        """
        Dynamic resolver for VegaMovies via Edge Refresh endpoint.
        Extracts redirect URL from meta refresh tag or JavaScript location replace,
        verifies HTTP 200 on target, then falls back to candidates and DDG dork.
        """
        endpoint = config.get("edge_endpoint", "https://vglist.lol/?re=vegamovies")
        try:
            r = await client.get(endpoint, headers=DEFAULT_HEADERS, timeout=2.5, follow_redirects=True)
            if r.status_code == 200:
                text = r.text
                # Meta refresh pattern 1: <meta http-equiv="refresh" content="...url=...">
                m = re.search(
                    r'''<meta\s+[^>]*http-equiv=["']refresh["'][^>]*content=["'][^"']*url=([^\s"'>]+)''',
                    text,
                    re.IGNORECASE
                )
                if not m:
                    # Meta refresh pattern 2: content first then http-equiv
                    m = re.search(
                        r'''<meta\s+[^>]*content=["'][^"']*url=([^\s"'>]+)["'][^>]*http-equiv=["']refresh["']''',
                        text,
                        re.IGNORECASE
                    )
                if not m:
                    # JS redirect: window.location.replace("...") or window.location = "..."
                    m = re.search(
                        r'''(?:window\.location(?:\.replace)?\s*\(?\s*=\s*|\.replace\s*\(\s*)["'](https?://[^"']+)["']''',
                        text,
                        re.IGNORECASE
                    )

                if m:
                    target_url = m.group(1).strip().rstrip('/')
                    if target_url.startswith("http"):
                        probe_res = await self._probe_url(client, target_url)
                        if probe_res:
                            # Verify ts-search endpoint works if vegamovies
                            if brand == "vegamovies":
                                try:
                                    chk = await client.get(f"{probe_res}/ts-search.php?q=test", headers=DEFAULT_HEADERS, timeout=2.0)
                                    if chk.status_code == 200:
                                        return probe_res
                                except Exception:
                                    pass
                            else:
                                return probe_res
        except Exception as e:
            logger.debug(f"Edge refresh resolution error for {brand}: {e}")

        # Fallback to candidates
        candidates = [c for c in config.get("candidates", []) if "1vegamovies.lat" not in c]
        cand_res = await self._probe_candidates_concurrent(client, candidates)
        if cand_res:
            if brand == "vegamovies":
                try:
                    chk = await client.get(f"{cand_res}/ts-search.php?q=test", headers=DEFAULT_HEADERS, timeout=2.0)
                    if chk.status_code == 200:
                        return cand_res
                except Exception:
                    pass
            else:
                return cand_res

        # Fallback to DuckDuckGo search dork
        search_dork = config.get("search_dork")
        if search_dork:
            dork_res = await self._search_dork_fallback(brand, search_dork)
            if dork_res:
                return dork_res

        return None

    async def _resolve_subdomain_probe(
        self,
        client: httpx.AsyncClient,
        config: Dict[str, Any]
    ) -> Optional[str]:
        """
        Dynamic resolver for OlaMovies via subdomain permutation probing.
        Generates candidate URLs from subdomains and probes concurrently.
        """
        base_domain = config.get("base_domain", "olamovies.mov")
        subdomains = config.get("subdomains", ["v6", "v7", "v5", "v4", "v3", "v8"])
        generated = [f"https://{sub}.{base_domain}" for sub in subdomains]

        all_candidates = list(generated)
        for cand in config.get("candidates", []):
            if cand not in all_candidates:
                all_candidates.append(cand)

        return await self._probe_candidates_concurrent(client, all_candidates)

    async def _resolve_redirect_or_probe(
        self,
        client: httpx.AsyncClient,
        config: Dict[str, Any],
        brand: str
    ) -> Optional[str]:
        """
        Dynamic resolver for UHDMovies / DesireMovies via redirect inspection & candidate probing.
        Probes resolvers (getting final redirected URL), then candidate URLs, then search dork.
        """
        resolvers = config.get("resolvers", [])
        if resolvers:
            res = await self._probe_candidates_concurrent(client, resolvers)
            if res:
                return res

        candidates = config.get("candidates", [])
        if candidates:
            cand_res = await self._probe_candidates_concurrent(client, candidates)
            if cand_res:
                return cand_res

        search_dork = config.get("search_dork")
        if search_dork:
            dork_res = await self._search_dork_fallback(brand, search_dork)
            if dork_res:
                return dork_res

        return None

    async def _search_dork_fallback(self, brand: str, query: str) -> Optional[str]:
        """
        Optional fallback if all probes fail.
        Queries DuckDuckGo HTML / lite endpoints, extracts first domain matching brand name,
        and verifies it with a probe.
        """
        try:
            async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=3.0) as client:
                resp = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
                text = resp.text

                # 1. Parse DDG redirect parameter uddg=
                uddg_matches = re.findall(r'uddg=([^&"\'<>]+)', text)
                for raw_u in uddg_matches:
                    decoded = urllib.parse.unquote(raw_u)
                    if brand.lower() in decoded.lower():
                        parsed = urllib.parse.urlparse(decoded)
                        if parsed.scheme and parsed.netloc:
                            candidate = f"{parsed.scheme}://{parsed.netloc}"
                            probed = await self._probe_url(client, candidate)
                            if probed:
                                return probed

                # 2. Check direct domain mentions in HTML
                domain_matches = re.findall(r'https?://[a-zA-Z0-9.\-]+(?:\.[a-zA-Z]{2,})', text)
                for candidate in domain_matches:
                    if brand.lower() in candidate.lower() and "duckduckgo" not in candidate:
                        clean_c = candidate.rstrip('/')
                        probed = await self._probe_url(client, clean_c)
                        if probed:
                            return probed
        except Exception as e:
            logger.debug(f"DDG search dork fallback error for {brand}: {e}")
        return None

    async def get_mirror(self, brand: str, force_refresh: bool = False) -> str:
        """
        Asynchronously retrieves active mirror for a brand.
        Checks in-memory cache (< 1ms hit), utilizes single-flight locks to prevent stampedes,
        dynamically resolves against live endpoints, tracks latency, and updates mirror_health.json.
        """
        b = brand.lower().strip()
        now = time.time()

        # 1. Fast cache check (lock-free read)
        if not force_refresh and b in self._cache:
            entry = self._cache[b]
            if (now - entry.get("resolved_at", 0)) < self.ttl:
                return entry["url"]

        # 2. Acquire brand-specific lock to guarantee single-flight execution
        lock = self._get_lock(b)
        async with lock:
            # Double check cache inside lock
            now = time.time()
            if not force_refresh and b in self._cache:
                entry = self._cache[b]
                if (now - entry.get("resolved_at", 0)) < self.ttl:
                    return entry["url"]

            # Reload config if needed
            config = self.registry.get(b, EMBEDDED_REGISTRY.get(b, {}))
            res_type = config.get("type", "redirect_or_probe")
            fallback = config.get("fallback", self._get_fallback(b))

            t0 = time.perf_counter()
            resolved_mirror = None

            try:
                limits = httpx.Limits(max_keepalive_connections=20, max_connections=30)
                async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=5.0, limits=limits) as client:
                    if res_type == "pingora_dns":
                        resolved_mirror = await self._resolve_pingora_dns(client, config)
                    elif res_type == "edge_refresh":
                        resolved_mirror = await self._resolve_edge_refresh(client, config, b)
                    elif res_type == "subdomain_probe":
                        resolved_mirror = await self._resolve_subdomain_probe(client, config)
                    elif res_type == "redirect_or_probe":
                        resolved_mirror = await self._resolve_redirect_or_probe(client, config, b)
                    else:
                        # Generic candidate probe
                        candidates = config.get("candidates", [])
                        resolved_mirror = await self._probe_candidates_concurrent(client, candidates)
            except Exception as e:
                logger.error(f"Resolution failed unexpectedly for brand '{b}': {e}")

            latency_ms = (time.perf_counter() - t0) * 1000.0

            if resolved_mirror:
                self._cache[b] = {"url": resolved_mirror, "resolved_at": time.time()}
                await self.report_success(b, latency_ms=latency_ms)
                return resolved_mirror
            else:
                logger.warning(f"All dynamic resolution strategies failed for '{b}'. Falling back to: {fallback}")
                # Cache fallback with brief 60-second TTL to avoid slamming failed endpoints
                self._cache[b] = {"url": fallback, "resolved_at": time.time() - self.ttl + 60.0}
                await self.report_failure(b, failed_url="")
                return fallback

    async def get_all_mirrors(self, force_refresh: bool = False) -> Dict[str, str]:
        """Resolves mirrors for all registered brands concurrently."""
        brands = list(self.registry.keys())
        tasks = [self.get_mirror(brand, force_refresh=force_refresh) for brand in brands]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        mirrors: Dict[str, str] = {}
        for brand, res in zip(brands, results):
            if isinstance(res, Exception):
                mirrors[brand] = self._get_fallback(brand)
            else:
                mirrors[brand] = res
        return mirrors

    def get_mirror_sync(self, brand: str) -> str:
        """
        Synchronous non-blocking helper that immediately returns the cached active mirror
        or fallback without blocking or executing network calls.
        """
        b = brand.lower().strip()
        if b in self._cache and "url" in self._cache[b]:
            return self._cache[b]["url"]
        if b in self._health and self._health[b].get("active_mirror"):
            return self._health[b]["active_mirror"]
        return self._get_fallback(b)

    async def report_failure(self, brand: str, failed_url: str = ""):
        """
        Increments consecutive_failures counter.
        If consecutive_failures >= 3, invalidates cached mirror and schedules background re-probe.
        """
        b = brand.lower().strip()
        if b not in self._health:
            self._health[b] = {
                "active_mirror": self._get_fallback(b),
                "latency_ms": 0.0,
                "last_success": 0.0,
                "consecutive_failures": 0,
                "status": "warning"
            }

        entry = self._health[b]
        entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1

        if entry["consecutive_failures"] >= 3:
            entry["status"] = "degraded"
            logger.warning(f"Brand '{b}' has {entry['consecutive_failures']} consecutive failures. Invalidating cache.")
            self._cache.pop(b, None)
            # Trigger background re-probe if event loop is running
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.get_mirror(b, force_refresh=True))
            except RuntimeError:
                pass
        else:
            entry["status"] = "warning"

        self._save_health_sync()

    async def report_success(self, brand: str, latency_ms: float = 0.0):
        """Resets consecutive_failures, updates latency, marks status as healthy."""
        b = brand.lower().strip()
        current_mirror = self._cache.get(b, {}).get("url")
        if not current_mirror and b in self._health:
            current_mirror = self._health[b].get("active_mirror", self._get_fallback(b))

        self._health[b] = {
            "active_mirror": current_mirror or self._get_fallback(b),
            "latency_ms": round(latency_ms, 2),
            "last_success": time.time(),
            "consecutive_failures": 0,
            "status": "healthy"
        }
        self._save_health_sync()


# Module-level default singleton instance
default_resolver = DomainResolver()


async def get_active_mirror(brand: str) -> str:
    """Module-level async resolver for a single brand."""
    return await default_resolver.get_mirror(brand)


async def get_all_active_mirrors() -> Dict[str, str]:
    """Module-level async resolver for all brands."""
    return await default_resolver.get_all_mirrors()


def get_active_mirror_sync(brand: str) -> str:
    """Module-level sync helper for immediate cached or fallback mirror retrieval."""
    return default_resolver.get_mirror_sync(brand)


async def report_mirror_failure(brand: str, url: str = ""):
    """Module-level async reporter for failed mirror requests."""
    await default_resolver.report_failure(brand, url)


async def report_mirror_success(brand: str, latency: float = 0.0):
    """Module-level async reporter for successful mirror requests."""
    await default_resolver.report_success(brand, latency)


__all__ = [
    "DomainResolver",
    "default_resolver",
    "get_active_mirror",
    "get_all_active_mirrors",
    "get_active_mirror_sync",
    "report_mirror_failure",
    "report_mirror_success",
    "EMBEDDED_REGISTRY",
]
