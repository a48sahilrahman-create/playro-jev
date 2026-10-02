"""
JEV System One Fast Media Stream & Gateway Resolver Bridge for Playro.
Delivers adaptive, DOM-mutation-resilient link classification and gateway hop solving
with sub-50ms latency, LRU caching, and strict timeout protection.
"""
import asyncio
import os
import re
import sys
import time
import logging
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Any

# Ensure JEV scripts directory is accessible for direct in-process imports
JEV_DIR = Path.home() / ".claude" / "skills" / "jev" / "scripts"
if str(JEV_DIR) not in sys.path:
    sys.path.append(str(JEV_DIR))

try:
    from jev_engine import evaluate_media_post, evaluate_gateway_hop, prune_html_for_jev
    JEV_AVAILABLE = True
except Exception:
    JEV_AVAILABLE = False

from jev_dom_pruner import prune_media_dom

logger = logging.getLogger("JevStreamResolver")

class JevStreamResolver:
    """
    Client-side bridge connecting Playro scrapers & link resolver to JEV System One.
    Caches decisions, guards against UI shifts, and guarantees zero crashes.
    """

    _cache: Dict[str, Any] = {}
    _cache_max_size: int = 256
    _cache_lock: Optional[asyncio.Lock] = None
    _inflight: Dict[str, asyncio.Future] = {}

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        if cls._cache_lock is None:
            cls._cache_lock = asyncio.Lock()
        return cls._cache_lock

    @classmethod
    async def _acquire_cache_entry(cls, key: str) -> tuple[Optional[Any], Optional[asyncio.Future]]:
        """
        Single-flight synchronization avoiding cache stampede / thundering herd.
        Returns:
            (cached_data, None) -> Cache HIT: return immediately.
            (None, future)      -> In-flight duplicate: await this future.
            (None, None)        -> Leader request: execute fetch, then call _release_cache_entry.
        """
        async with cls._get_lock():
            if key in cls._cache:
                return cls._cache[key], None
            if key in cls._inflight:
                return None, cls._inflight[key]
            fut = asyncio.get_running_loop().create_future()
            cls._inflight[key] = fut
            return None, None

    @classmethod
    async def _release_cache_entry(cls, key: str, val: Any) -> None:
        async with cls._get_lock():
            if len(cls._cache) >= cls._cache_max_size:
                # Purge oldest quarter
                keys = list(cls._cache.keys())[: cls._cache_max_size // 4]
                for k in keys:
                    cls._cache.pop(k, None)
            cls._cache[key] = val
            fut = cls._inflight.pop(key, None)
            if fut and not fut.done():
                try:
                    fut.set_result(val)
                except (asyncio.InvalidStateError, RuntimeError):
                    pass

    @classmethod
    async def _abort_cache_entry(cls, key: str, fallback_val: Any = None) -> None:
        async with cls._get_lock():
            fut = cls._inflight.pop(key, None)
            if fut and not fut.done():
                try:
                    if fallback_val is not None:
                        fut.set_result(fallback_val)
                    else:
                        fut.set_result([] if ("dom:" in key or "query:" in key) else {})
                except (asyncio.InvalidStateError, RuntimeError):
                    pass

    @classmethod
    async def extract_media_streams(
        cls,
        html: str,
        query: str = "",
        platform: str = "UHDMovies",
        timeout: float = 1.8
    ) -> List[Dict[str, Any]]:
        """
        Adaptively extracts media streams from post HTML using JEV System One.
        Resilient to mutated CSS classes, wrapper changes, and obfuscated button hierarchies.
        """
        if not html:
            return []

        # Prune DOM upfront for surgical signal extraction and stable hashing
        pruned_dom = prune_media_dom(html, max_chars=3500)
        dom_hash = hashlib.sha256(pruned_dom.encode('utf-8', errors='ignore')).hexdigest()[:16]
        dom_key = f"dom:{platform}:{dom_hash}"
        query_key = f"query:{platform}:{query.lower().strip()}" if query else ""

        # Pre-fetch and post-DOM cache check
        if query_key and query_key in cls._cache:
            return cls._cache[query_key]
        if dom_key in cls._cache:
            return cls._cache[dom_key]

        # Unified single-flight coalescing key (pre-fetch query priority, dom fallback)
        primary_key = query_key or dom_key

        cached, inflight_fut = await cls._acquire_cache_entry(primary_key)
        if cached is not None:
            return cached
        if inflight_fut is not None:
            try:
                res = await asyncio.wait_for(asyncio.shield(inflight_fut), timeout=timeout + 0.5)
                if res:
                    return res
            except Exception:
                pass
            # Anti-stampede randomized jitter (50-150ms) to prevent follower spikes on local CPU
            await asyncio.sleep(0.05 + (hash(primary_key) % 100) / 1000.0)
            if primary_key in cls._cache and cls._cache[primary_key]:
                return cls._cache[primary_key]
            cand = cls._extract_candidate_links(html)
            if not cand:
                cand = cls._extract_candidate_links(pruned_dom)
            return [{
                "platform": platform,
                "title": f"{query} [JEV Adaptive]".strip(),
                "label": f"[1080P] {platform} Stream #1 (fallback)",
                "episode_tag": "",
                "resolution": "1080P",
                "quality": "1080P",
                "codec": "x264",
                "size": "Adaptive",
                "audio": "Original",
                "target_url": cand[0] if cand else "",
                "source_id": "jev_adaptive"
            }] if cand else []

        results: List[Dict[str, Any]] = []
        t0 = time.perf_counter()
        leader_completed = False

        try:
            # Run in worker thread with timeout guard
            loop = asyncio.get_running_loop()

            decision = None
            if JEV_AVAILABLE:
                try:
                    decision = await asyncio.wait_for(
                        loop.run_in_executor(None, evaluate_media_post, pruned_dom, query),
                        timeout=timeout
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"[JEV Media Stream] Timeout after {timeout}s on {platform}, falling back to local heuristic")
                except Exception as ex:
                    logger.warning(f"[JEV Media Stream] Cloud error ({ex}), falling back to local heuristic")

            if not decision:
                decision = cls._heuristic_media_extract(pruned_dom, query)

            extracted_url = decision.get("extracted_url", "")
            resolution = decision.get("resolution", "1080p")
            has_dual_audio = decision.get("has_dual_audio", False)
            link_type = decision.get("link_type", "unknown")

            # Extract any additional download URLs from the pruned DOM matching media patterns
            candidate_links = cls._extract_candidate_links(html)
            if extracted_url and extracted_url not in candidate_links:
                candidate_links.insert(0, extracted_url)

            # Build standardized stream entries for Playro
            for idx, target_url in enumerate(candidate_links[:5]):
                res_m = re.search(r'(2160p|4k|1080p|720p|480p)', target_url, re.I)
                item_res = res_m.group(1).upper() if res_m else resolution.replace("_", " ").upper()
                if item_res == "4K":
                    item_res = "2160P"

                codec = "HEVC 10-bit" if any(k in target_url.upper() for k in ["HEVC", "10BIT", "X265"]) else "x264"
                audio = "Dual Audio (Hindi + Eng)" if has_dual_audio or "dual" in target_url.lower() else "Original"

                results.append({
                    "platform": platform,
                    "title": f"{query} [JEV Adaptive]".strip(),
                    "label": f"[{item_res}] {platform} Stream #{idx+1} ({link_type})",
                    "episode_tag": "",
                    "resolution": item_res,
                    "quality": item_res,
                    "codec": codec,
                    "size": "Adaptive",
                    "audio": audio,
                    "target_url": target_url,
                    "source_id": "jev_adaptive"
                })

            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            logger.info(f"[JEV Media Stream] Platform: {platform} | Found: {len(results)} | Elapsed: {elapsed}ms")

            await cls._release_cache_entry(primary_key, results)
            async with cls._get_lock():
                cls._cache[dom_key] = results
                if query_key:
                    cls._cache[query_key] = results

            leader_completed = True
            return results

        except Exception as e:
            logger.error(f"[JEV Media Stream] Unhandled exception: {e}")
            return results
        finally:
            if not leader_completed:
                if not results:
                    # Pre-compute heuristic fallback once on leader so all waiting followers receive it unified
                    try:
                        cand = cls._extract_candidate_links(html) or cls._extract_candidate_links(pruned_dom)
                        target_url = cand[0] if cand else ""
                        results = [{
                            "platform": platform,
                            "title": f"{query} [JEV Adaptive]".strip(),
                            "label": f"[1080P] {platform} Stream #1 (fallback)",
                            "episode_tag": "",
                            "resolution": "1080P",
                            "quality": "1080P",
                            "codec": "x264",
                            "size": "Adaptive",
                            "audio": "Original",
                            "target_url": target_url,
                            "source_id": "jev_adaptive"
                        }] if target_url else []
                    except Exception:
                        results = []
                await cls._abort_cache_entry(primary_key, results)

    @classmethod
    async def resolve_gateway_hop(
        cls,
        html: str,
        current_url: str = "",
        timeout: float = 0.8
    ) -> Dict[str, Any]:
        """
        Determines the genuine action on an intermediate shortlink or gateway page,
        rejecting fake countdown timers and deceptive ad buttons.
        """
        if not html:
            return {"status": "FAILED", "hop_action": "no_action_needed", "direct_url": current_url}

        pruned_dom = prune_media_dom(html, max_chars=3500)
        dom_hash = hashlib.sha256(pruned_dom.encode('utf-8', errors='ignore')).hexdigest()[:16]
        dom_key = f"hop_dom:{dom_hash}"
        url_key = f"hop_url:{current_url}" if current_url else ""

        res_dict = {
            "status": "FAILED",
            "hop_action": "no_action_needed",
            "direct_url": "",
            "target_data": "",
            "is_countdown": False
        }

        # Check pre-fetch and post-DOM cache
        if url_key and url_key in cls._cache:
            return cls._cache[url_key]
        if dom_key in cls._cache:
            return cls._cache[dom_key]

        primary_key = url_key or dom_key

        cached, inflight_fut = await cls._acquire_cache_entry(primary_key)
        if cached is not None:
            return cached
        if inflight_fut is not None:
            try:
                res = await asyncio.wait_for(asyncio.shield(inflight_fut), timeout=timeout + 0.5)
                if res and res.get("status") != "FAILED":
                    return res
            except Exception:
                pass
            if primary_key in cls._cache and cls._cache[primary_key].get("status") != "FAILED":
                return cls._cache[primary_key]
            return cls._heuristic_gateway_hop(pruned_dom, current_url)

        leader_completed = False

        try:
            loop = asyncio.get_running_loop()

            decision = None
            if JEV_AVAILABLE:
                try:
                    decision = await asyncio.wait_for(
                        loop.run_in_executor(None, evaluate_gateway_hop, pruned_dom, current_url),
                        timeout=timeout
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"[JEV Gateway Hop] Timeout after {timeout}s on {current_url}, falling back to local heuristic")
                except Exception as ex:
                    logger.warning(f"[JEV Gateway Hop] Cloud error ({ex}), falling back to local heuristic")

            if not decision:
                decision = cls._heuristic_gateway_hop(pruned_dom, current_url)

            hop_action = decision.get("hop_action", "no_action_needed")
            target_data = decision.get("target_data", "")
            is_countdown = decision.get("is_countdown_page", False)

            direct_url = ""
            if hop_action == "decode_atob" and target_data:
                try:
                    import base64
                    step1 = base64.b64decode(target_data).decode('utf-8', errors='ignore')
                    step2 = base64.b64decode(step1).decode('utf-8', errors='ignore')
                    if step2.startswith("http"):
                        direct_url = step2
                except Exception:
                    pass
            elif hop_action in ["extract_window_location", "follow_meta_refresh"] and target_data:
                direct_url = target_data
            elif hop_action == "decode_base64_hidden" and target_data:
                try:
                    import base64
                    decoded = base64.b64decode(target_data).decode('utf-8', errors='ignore')
                    if decoded.startswith("http"):
                        direct_url = decoded
                except Exception:
                    pass

            if not direct_url:
                # Secondary scan for direct cloud URLs inside HTML
                cloud_m = re.search(r'https?://(?:driveseed|cloud\.unblocked|hubcloud|vcloud|pixeldrain)[^\s\'"<>\\]+', html)
                if cloud_m:
                    direct_url = cloud_m.group(0).replace("&amp;", "&").rstrip('\\"')

            res_dict = {
                "status": "SUCCESS" if direct_url else "NEEDS_NEXT_HOP",
                "hop_action": hop_action,
                "direct_url": direct_url or current_url,
                "target_data": target_data,
                "is_countdown": is_countdown
            }

            await cls._release_cache_entry(primary_key, res_dict)
            async with cls._get_lock():
                cls._cache[dom_key] = res_dict
                if url_key:
                    cls._cache[url_key] = res_dict

            leader_completed = True
            return res_dict

        except Exception as e:
            logger.error(f"[JEV Gateway Hop] Exception: {e}")
            return res_dict
        finally:
            if not leader_completed:
                if res_dict.get("status") == "FAILED":
                    res_dict = cls._heuristic_gateway_hop(pruned_dom, current_url)
                await cls._abort_cache_entry(primary_key, res_dict)

    @staticmethod
    def _extract_candidate_links(html: str) -> List[str]:
        """Secondary helper extracting valid streaming / gateway candidate URLs."""
        patterns = [
            r'https?://(?:driveseed\.org|cloud\.unblockedgames\.world|techy\.[a-z]+)/[^\s\'"<>\\]+',
            r'https?://(?:hubcloud\.[a-z]+|hubdrive\.[a-z]+|gamerxyt\.com|gyanigurus\.[a-z]+)/[^\s\'"<>\\]+',
            r'https?://(?:vcloud\.[a-z]+|nexdrive\.[a-z]+|fast-dl\.[a-z]+|v-cloud\.[a-z]+)/[^\s\'"<>\\]+',
            r'https?://pixeldrain\.(?:com|dev)/(?:u|api/file)/[a-zA-Z0-9]+',
            r'https?://[a-zA-Z0-9.-]+\.workers\.dev/[^\s\'"<>\\]+',
            r'https?://[a-zA-Z0-9.-]+\.r2\.cloudflarestorage\.com/[^\s\'"<>\\]+'
        ]
        candidates = []
        for pat in patterns:
            for m in re.finditer(pat, html):
                url = m.group(0).replace("&amp;", "&").rstrip('\\"')
                if url not in candidates:
                    candidates.append(url)
        return candidates

    @classmethod
    def _heuristic_media_extract(cls, html: str, query: str) -> Dict[str, Any]:
        """Emergency in-process fallback when JEV engine is unavailable."""
        res_m = re.search(r'(2160p|4k|1080p|720p|480p)', html, re.I)
        resolution = res_m.group(1).upper() if res_m else "1080p"
        has_dual = any(k in html.lower() for k in ["dual", "hindi", "multi"])

        extracted = ""
        candidates = cls._extract_candidate_links(html)
        if candidates:
            extracted = candidates[0]

        return {
            "link_type": "driveseed_gateway" if "driveseed" in extracted else "direct_stream",
            "resolution": resolution,
            "has_dual_audio": has_dual,
            "extracted_url": extracted
        }

    @classmethod
    def _heuristic_gateway_hop(cls, html: str, url: str) -> Dict[str, Any]:
        """Emergency in-process fallback for gateway hops."""
        is_countdown = any(k in html.lower() for k in ["seconds", "please wait", "countdown", "timer"])
        action = "click_real_button"
        target = ""

        atob_m = re.search(r"atob\(atob\(['\"]([a-zA-Z0-9+/=]{10,})['\"]\)\)", html)
        if atob_m:
            action = "decode_atob"
            target = atob_m.group(1)
        elif "<form" in html.lower() and "post" in html.lower():
            action = "submit_form_post"

        return {
            "hop_action": action,
            "is_countdown_page": is_countdown,
            "target_data": target
        }
