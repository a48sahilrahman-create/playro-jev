"""
Cascading Stream Resolver & Real-Time Performance Benchmarking Engine
Features:
  1. Ultra-Low Latency Pre-Flight Benchmark (< 200ms per candidate).
  2. Staggered Parallel Racing:
     - Dispatches top candidates sorted by rank_release with staggered launch delay (150ms).
     - Benchmarks responses in parallel using asyncio.as_completed.
     - The first verified responder becomes primary.
     - Subsequent verified responders populate the waterfall fallback list sorted by TTFB.
  3. Single Candidate Verification:
     - Measures TTFB with high-precision time.perf_counter().
     - Resolves gateway/shorteners via link_resolver.resolve_auto(allow_interactive=False).
     - Validates container magic bytes (MKV, MP4, HLS, TS) via stream_verifier.verify().
     - Flags 403 or JSON error quota exhaustion as QUOTA_EXCEEDED.
     - Returns enriched dict with direct_url, ttfb_ms, valid, format, candidate.
  4. Multi-Provider Embed Fallbacks:
     - Generates and attaches embed fallback sources via EmbedEngine.
  5. Unified Search & Cascade Resolution Helper:
     - Directly integrates with play_engine.search() catalog output.
"""

import os
import sys
import time
import asyncio
import logging
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path

import httpx

# Ensure repo root is on sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import link_resolver as link_resolver_module
from link_resolver import LinkResolver
import stream_verifier as stream_verifier_module
from stream_verifier import StreamVerifier
import parallel_dispatcher
import play_engine
from play_engine import PlayEngine, rank_release

logger = logging.getLogger("cascade_resolver")

# Module-level instances for direct access and patching
link_resolver = LinkResolver()
stream_verifier = StreamVerifier()
_default_play_engine: Optional[PlayEngine] = None


async def resolve_auto(url: str, **kwargs) -> dict:
    """Module-level resolve_auto delegating to link_resolver."""
    return await link_resolver.resolve_auto(url, **kwargs)


def get_play_engine() -> PlayEngine:
    """Lazy-load the global PlayEngine instance."""
    global _default_play_engine
    if _default_play_engine is None:
        _default_play_engine = PlayEngine()
    return _default_play_engine


def _safe_rank(release: dict) -> tuple:
    """Safe wrapper for rank_release to prevent errors on irregular candidate structures."""
    try:
        return rank_release(release)
    except Exception:
        return (0, 0, 0, 0, 0)


def _is_quota_error(val: Any) -> bool:
    """Detect 403 status code or quota exceeded in error messages / payloads."""
    if not val:
        return False
    if isinstance(val, dict):
        if val.get("status_code") == 403 or val.get("code") == 403:
            return True
        for k in ("error", "reason", "message", "detail", "msg"):
            if _is_quota_error(val.get(k)):
                return True
        return False

    val_str = str(val).lower()
    if "403" in val_str and any(tok in val_str for tok in ("forbidden", "quota", "status", "http", "code")):
        return True

    quota_signatures = [
        "quota_exceeded",
        "quota exceeded",
        "rate limit",
        "rate_limit",
        "too many requests",
        "daily quota",
        "download quota",
        "bandwidth limit",
        "user rate limit exceeded",
        "usage limit exceeded",
    ]
    return any(sig in val_str for sig in quota_signatures)


async def verify_single_candidate(
    candidate: dict,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = 2.5
) -> dict:
    """
    Measures TTFB and verifies a single stream candidate.
    Resolves raw candidate target via link_resolver.resolve_auto(target_url, allow_interactive=False).
    Validates resolved URL using stream_verifier.verify(direct_url).
    Measures TTFB in ms.
    If stream returns 403 or JSON error (quota exceeded), marks status: "FAILED", reason: "QUOTA_EXCEEDED".
    Returns: { "status": "SUCCESS" | "FAILED", "candidate": candidate, "direct_url": url, "ttfb_ms": float, "format": str, "valid": bool }
    """
    if not candidate or not isinstance(candidate, dict):
        return {
            "status": "FAILED",
            "candidate": candidate,
            "direct_url": "",
            "ttfb_ms": 0.0,
            "format": "Unknown",
            "valid": False,
            "verified": False,
            "reason": "INVALID_CANDIDATE",
        }

    target_url = candidate.get("target_url") or candidate.get("url") or candidate.get("direct_url")
    if not target_url or not str(target_url).strip():
        return {
            "status": "FAILED",
            "candidate": candidate,
            "direct_url": "",
            "ttfb_ms": 0.0,
            "format": "Unknown",
            "valid": False,
            "verified": False,
            "reason": "NO_URL",
        }

    target_url = str(target_url).strip()
    t0 = time.perf_counter()

    own_client = False
    if client is None:
        own_client = True
        client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            verify=False
        )

    try:
        async def _do_verify() -> dict:
            # Step 1: Gateway / Shortener Resolution
            try:
                if hasattr(link_resolver, "resolve_auto"):
                    try:
                        resolve_res = await link_resolver.resolve_auto(target_url, allow_interactive=False)
                    except TypeError:
                        resolve_res = await link_resolver.resolve_auto(target_url)
                elif hasattr(link_resolver_module, "resolve_auto"):
                    try:
                        resolve_res = await link_resolver_module.resolve_auto(target_url, allow_interactive=False)
                    except TypeError:
                        resolve_res = await link_resolver_module.resolve_auto(target_url)
                else:
                    resolve_res = await resolve_auto(target_url)
            except Exception as e:
                logger.debug(f"[Cascade] Link resolution error for {target_url}: {e}")
                ttfb_ms = round((time.perf_counter() - t0) * 1000, 2)
                is_quota = _is_quota_error(e)
                return {
                    "status": "FAILED",
                    "candidate": candidate,
                    "direct_url": "",
                    "ttfb_ms": ttfb_ms,
                    "format": "Unknown",
                    "valid": False,
                    "verified": False,
                    "reason": "QUOTA_EXCEEDED" if is_quota else "RESOLUTION_ERROR",
                    "error": str(e)
                }

            # Check if resolution failed or returned quota error
            if isinstance(resolve_res, dict):
                is_quota = _is_quota_error(resolve_res)
                if resolve_res.get("status") == "FAILED" or is_quota:
                    ttfb_ms = round((time.perf_counter() - t0) * 1000, 2)
                    return {
                        "status": "FAILED",
                        "candidate": candidate,
                        "direct_url": resolve_res.get("direct_url") or "",
                        "ttfb_ms": ttfb_ms,
                        "format": "Unknown",
                        "valid": False,
                        "verified": False,
                        "reason": "QUOTA_EXCEEDED" if is_quota else (resolve_res.get("reason") or "RESOLUTION_FAILED"),
                        "error": resolve_res.get("error", "")
                    }
                resolved_url = resolve_res.get("direct_url") or resolve_res.get("url")
            elif isinstance(resolve_res, str):
                resolved_url = resolve_res
            else:
                resolved_url = None

            direct_url = (resolved_url or target_url).strip() if (resolved_url or target_url) else ""
            if not direct_url:
                ttfb_ms = round((time.perf_counter() - t0) * 1000, 2)
                return {
                    "status": "FAILED",
                    "candidate": candidate,
                    "direct_url": "",
                    "ttfb_ms": ttfb_ms,
                    "format": "Unknown",
                    "valid": False,
                    "verified": False,
                    "reason": "NO_RESOLVED_URL"
                }

            # Step 2: Container Magic Bytes Verification
            try:
                if hasattr(stream_verifier, "verify"):
                    v_res = await stream_verifier.verify(direct_url, client=client)
                elif hasattr(stream_verifier_module, "verify"):
                    v_res = await stream_verifier_module.verify(direct_url, client=client)
                else:
                    v_res = await StreamVerifier().verify(direct_url, client=client)
            except Exception as e:
                logger.debug(f"[Cascade] Stream verification error for {direct_url}: {e}")
                ttfb_ms = round((time.perf_counter() - t0) * 1000, 2)
                is_quota = _is_quota_error(e)
                return {
                    "status": "FAILED",
                    "candidate": candidate,
                    "direct_url": direct_url,
                    "ttfb_ms": ttfb_ms,
                    "format": "Unknown",
                    "valid": False,
                    "verified": False,
                    "reason": "QUOTA_EXCEEDED" if is_quota else "VERIFICATION_ERROR",
                    "error": str(e)
                }

            ttfb_ms = round((time.perf_counter() - t0) * 1000, 2)

            if not v_res or not isinstance(v_res, dict):
                return {
                    "status": "FAILED",
                    "candidate": candidate,
                    "direct_url": direct_url,
                    "ttfb_ms": ttfb_ms,
                    "format": "Unknown",
                    "valid": False,
                    "verified": False,
                    "reason": "VERIFICATION_EMPTY"
                }

            # Check 403 or JSON error (quota exceeded)
            if _is_quota_error(v_res):
                return {
                    "status": "FAILED",
                    "candidate": candidate,
                    "direct_url": direct_url,
                    "ttfb_ms": ttfb_ms,
                    "format": v_res.get("format", "Unknown"),
                    "valid": False,
                    "verified": False,
                    "reason": "QUOTA_EXCEEDED",
                    "error": v_res.get("error", "403 Quota Exceeded")
                }

            if not v_res.get("valid"):
                return {
                    "status": "FAILED",
                    "candidate": candidate,
                    "direct_url": direct_url,
                    "ttfb_ms": ttfb_ms,
                    "format": v_res.get("format", "Unknown"),
                    "valid": False,
                    "verified": False,
                    "reason": v_res.get("reason") or "INVALID_CONTAINER",
                    "error": v_res.get("error", "")
                }

            v_format = v_res.get("format")
            fmt = v_format or candidate.get("format") or "Unknown"

            v_size = v_res.get("size")
            cand_size = candidate.get("size")
            if v_size and v_size != "Direct Stream":
                size = v_size
            else:
                size = cand_size or v_size or "Unknown"

            enriched = dict(candidate)
            enriched.update({
                "status": "SUCCESS",
                "candidate": candidate,
                "direct_url": direct_url,
                "ttfb_ms": ttfb_ms,
                "format": fmt,
                "valid": True,
                "verified": True,
                "size": size,
            })
            if "total_bytes" in v_res:
                enriched["total_bytes"] = v_res.get("total_bytes")
            if "content_type" in v_res:
                enriched["content_type"] = v_res.get("content_type")

            return enriched

        return await asyncio.wait_for(_do_verify(), timeout=timeout)

    except (asyncio.TimeoutError, Exception) as e:
        logger.debug(f"[Cascade] Candidate verification failed or timed out: {e}")
        ttfb_ms = round((time.perf_counter() - t0) * 1000, 2)
        is_quota = _is_quota_error(e)
        return {
            "status": "FAILED",
            "candidate": candidate,
            "direct_url": target_url,
            "ttfb_ms": ttfb_ms,
            "format": "Unknown",
            "valid": False,
            "verified": False,
            "reason": "QUOTA_EXCEEDED" if is_quota else "TIMEOUT",
            "error": str(e)
        }
    finally:
        if own_client:
            try:
                await client.aclose()
            except Exception:
                pass


async def resolve_stream_cascade(
    candidates: List[dict],
    max_workers: int = 5,
    timeout: float = 4.0,
    stagger_delay: float = 0.150,
    client: Optional[httpx.AsyncClient] = None,
    candidate_timeout: float = 2.5,
    **kwargs
) -> dict:
    """
    Takes up to max_workers top candidates (from rank_release) and benchmarks them in parallel
    using staggered launch delay (150ms).
    The fastest verified responder (valid: True, status: "SUCCESS") becomes primary.
    Remaining healthy responders populate the waterfall list sorted ascending by TTFB.
    Attaches multi-provider embed fallbacks from EmbedEngine.
    Returns:
    {
        "status": "SUCCESS" | "FALLBACK",
        "primary": primary_dict,
        "waterfall": [list of healthy mirrors sorted by TTFB],
        "embed_fallback": embed_dict,
        "candidates_tested": int
    }
    """
    imdb_id = kwargs.get("imdb_id", "")
    title = kwargs.get("title", "")
    season = kwargs.get("season", None)
    episode = kwargs.get("episode", None)
    media_type = kwargs.get("media_type") or kwargs.get("type") or "movie"

    # Infer metadata from candidates if available
    if candidates and isinstance(candidates, list):
        for c in candidates:
            if isinstance(c, dict):
                if not imdb_id and c.get("imdb_id"):
                    imdb_id = c["imdb_id"]
                if not title and c.get("title"):
                    title = c["title"]
                if season is None and c.get("season") is not None:
                    season = c["season"]
                if episode is None and c.get("episode") is not None:
                    episode = c["episode"]
                if media_type == "movie" and (c.get("media_type") or c.get("type")):
                    media_type = c.get("media_type") or c.get("type")

    def _build_embed_fallback() -> dict:
        try:
            from embed_engine import EmbedEngine
            return EmbedEngine.get_preferred_embed(
                title=title,
                season=season,
                episode=episode,
                imdb_id=imdb_id,
                media_type=media_type,
                item_id=imdb_id,
            )
        except Exception as e:
            logger.debug(f"[Cascade] Error generating embed fallback: {e}")
            return {}

    if not candidates or not isinstance(candidates, list):
        return {
            "status": "FALLBACK",
            "primary": None,
            "waterfall": [],
            "embed_fallback": _build_embed_fallback(),
            "candidates_tested": 0
        }

    # Rank and slice top candidates
    sorted_candidates = sorted(candidates, key=_safe_rank, reverse=True)
    top_candidates = sorted_candidates[:max_workers]

    if not top_candidates:
        return {
            "status": "FALLBACK",
            "primary": None,
            "waterfall": [],
            "embed_fallback": _build_embed_fallback(),
            "candidates_tested": 0
        }

    own_client = False
    if client is None:
        own_client = True
        client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            verify=False
        )

    primary: Optional[dict] = None
    waterfall: List[dict] = []
    t_start = time.perf_counter()
    cand_timeout = min(candidate_timeout, timeout)

    try:
        async def _staggered_worker(cand: dict, delay: float) -> Optional[dict]:
            if delay > 0:
                await asyncio.sleep(delay)
            return await verify_single_candidate(cand, client=client, timeout=cand_timeout)

        tasks = [
            asyncio.create_task(_staggered_worker(cand, i * stagger_delay))
            for i, cand in enumerate(top_candidates)
        ]

        try:
            for fut in asyncio.as_completed(tasks):
                elapsed = time.perf_counter() - t_start
                rem = timeout - elapsed
                if rem <= 0:
                    break
                try:
                    res = await asyncio.wait_for(fut, timeout=rem)
                except (asyncio.TimeoutError, Exception):
                    continue

                if res and res.get("status") == "SUCCESS" and res.get("valid") is True:
                    if primary is None:
                        primary = res
                    else:
                        waterfall.append(res)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    finally:
        if own_client:
            try:
                await client.aclose()
            except Exception:
                pass

    # Sort remaining healthy waterfall responders strictly by TTFB
    waterfall.sort(key=lambda x: x.get("ttfb_ms", float("inf")))

    status = "SUCCESS" if primary is not None else "FALLBACK"
    return {
        "status": status,
        "primary": primary,
        "waterfall": waterfall,
        "embed_fallback": _build_embed_fallback(),
        "candidates_tested": len(top_candidates)
    }


async def cascade_search_and_resolve(
    query: str,
    media_type: str = "movie",
    season: Optional[int] = None,
    episode: Optional[int] = None,
    max_workers: int = 5,
    timeout: float = 4.0,
    engine: Optional[Any] = None,
    imdb_id: str = "",
    **kwargs
) -> dict:
    """
    Helper that calls play_engine.search(), takes the top candidates, and calls resolve_stream_cascade().
    """
    if engine is not None:
        search_res = await engine.search(query, season=season, episode=episode)
    elif hasattr(play_engine, "search") and callable(getattr(play_engine, "search")):
        search_res = await play_engine.search(query, season=season, episode=episode)
    else:
        eng = get_play_engine()
        search_res = await eng.search(query, season=season, episode=episode)

    if isinstance(search_res, dict):
        candidates = search_res.get("releases", [])
    elif isinstance(search_res, list):
        candidates = search_res
    else:
        candidates = []

    return await resolve_stream_cascade(
        candidates,
        max_workers=max_workers,
        timeout=timeout,
        title=query,
        media_type=media_type,
        season=season,
        episode=episode,
        imdb_id=imdb_id,
        **kwargs
    )


if __name__ == "__main__":
    async def _main():
        q = sys.argv[1] if len(sys.argv) > 1 else "Inception"
        print(f"[*] Cascading search & resolve for: {q}")
        res = await cascade_search_and_resolve(q)
        print(f"[*] Status: {res['status']}")
        if res.get("primary"):
            p = res["primary"]
            print(f"[+] Primary: {p.get('direct_url')} | TTFB: {p.get('ttfb_ms')}ms | Format: {p.get('format')} | Size: {p.get('size')}")
        print(f"[*] Waterfall count: {len(res.get('waterfall', []))}")
        for i, w in enumerate(res.get("waterfall", [])):
            print(f"    [{i+1}] {w.get('direct_url')} | TTFB: {w.get('ttfb_ms')}ms")
        if res.get("embed_fallback"):
            print(f"[*] Embed fallback: {res['embed_fallback'].get('embed_url')}")

    asyncio.run(_main())
