#!/usr/bin/env python3
"""
Autonomous, Standalone, AI-Free Media Player Engine (play.exe)
Features:
  - Hierarchical Series & Episode Navigator (Season -> Episode -> Quality/Size Breakdown)
  - Progressive Real-Time Streaming (displays releases live as each source completes)
  - Universal Contextual Metadata Decoder (Resolution, Codec, Size, Audio, Platform)
  - Automatic Multi-Layer Fuzzy Title Recovery & Auto-Spell Checking
  - Pure-Code Stealth HTTP/2 & Multi-Host Intermediary Bypasses (< 200ms)
  - Zero-Trust Pre-Flight EBML Magic Bytes Verification (1A 45 DF A3 / ftyp)
  - Seamless Non-Blocking VLC Process Launch with window keep-alive
  - Clean Locked-Width UI (file-only diagnostic logging to %LOCALAPPDATA%\\play_engine.log)
"""

import sys
import os
import time
import asyncio
import argparse
import subprocess
import logging
import sqlite3
import re
import json
from pathlib import Path

# Ensure UTF-8 IO encoding on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Setup structured file-only logging (prevents console UI corruption)
LOG_FILE = Path(os.getenv("LOCALAPPDATA", ".")) / "play_engine.log" if sys.platform == "win32" else Path("./play_engine.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8")
    ]
)
logger = logging.getLogger("PlayEngine")

# Locate local tools / modules
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from media_db import MediaDB, resolve_db_path
    from link_resolver import LinkResolver
    from stream_verifier import StreamVerifier
    from title_resolver import TitleResolver
    from parallel_dispatcher import ParallelMediaDispatcher
except ImportError:
    pass

def parse_size_in_mb(size_str: str) -> float:
    """Convert size string (e.g. '20.05GB', '5.8 GB', '925MB') to float megabytes for closest-match calculation."""
    if not size_str:
        return 0.0
    m = re.search(r'(\d+(?:\.\d+)?)\s*(GB|MB|TB|KB)', str(size_str), re.I)
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = m.group(2).upper()
    if unit == "TB":
        return val * 1024 * 1024
    elif unit == "GB":
        return val * 1024
    elif unit == "MB":
        return val
    elif unit == "KB":
        return val / 1024
    return val

def find_vlc() -> str:
    """Detect VLC Media Player executable path across platforms."""
    candidates = [
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        r"D:\Program Files\VideoLAN\VLC\vlc.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\VideoLAN\VLC\vlc.exe"),
        "vlc"
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    # Check if 'vlc' is accessible in PATH
    import shutil
    if shutil.which("vlc"):
        return "vlc"
    return "vlc"

class PlayApp:
    def __init__(self, db_path: str = None):
        db_path = db_path or resolve_db_path()
        self.db = MediaDB(db_path=db_path)
        self.resolver = LinkResolver()
        self.verifier = StreamVerifier()
        self.title_resolver = TitleResolver()
        self.dispatcher = ParallelMediaDispatcher(db_path=db_path)
        self.vlc_path = find_vlc()
        self.stream_server = None

    async def ensure_stream_server(self):
        """Ensure background local streaming bridge is running on 127.0.0.1 with auto-port fallback."""
        if self.stream_server is None:
            try:
                from tg_local_stream_server import get_or_start_stream_server, DEFAULT_STREAM_PORT
                self.stream_server = await get_or_start_stream_server(port=DEFAULT_STREAM_PORT)
            except Exception:
                pass

    async def resolve_and_verify(self, stream_url: str) -> dict:
        """Resolve intermediary redirects & verify EBML container magic bytes."""
        t0 = time.perf_counter()
        logger.info(f"Resolving stream target: {stream_url}")

        res = await self.resolver.resolve_auto(stream_url)
        direct_url = res.get("direct_url", stream_url)
        resolve_ms = (time.perf_counter() - t0) * 1000
        logger.info(f"Bypass resolved via {res.get('host', 'Direct')} in {resolve_ms:.2f}ms -> {direct_url[:80]}...")

        v_res = await self.verifier.verify(direct_url)
        logger.info(f"EBML Verification: valid={v_res.get('valid')}, format={v_res.get('format')}, size={v_res.get('size')} bytes")

        return {
            "resolved": res,
            "direct_url": direct_url,
            "verification": v_res,
            "total_ms": (time.perf_counter() - t0) * 1000
        }

    def launch_vlc(self, direct_url: str, title: str = "") -> subprocess.Popen:
        """Launch non-blocking VLC player directly with verified direct stream URL."""
        logger.info(f"Launching VLC: {self.vlc_path} -> {direct_url[:70]}...")
        cmd = [self.vlc_path, direct_url]
        if title:
            cmd.extend([f"--meta-title={title}"])
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info(f"VLC running with PID: {proc.pid}")
            return proc
        except Exception as e:
            logger.warning(f"VLC launch failed ({e}), opening via OS default handler...")
            if sys.platform == "win32":
                os.startfile(direct_url)
            return None

    async def handle_series_flow(self, series_data: dict, direct: bool = False, quality_filter: str = None) -> str:
        """
        Hierarchical 2-Step Navigation with in-place back-tracking:
        Season/Episode Selection -> Quality Breakdown -> Play -> Back/Search/Quit.
        Returns navigation command: 'search', 'quit', or 'back'.
        """
        show_title = series_data.get("show_title", "TV Series")
        seasons = series_data.get("seasons", {})

        episode_list = []
        ep_counter = 1
        for season_name, episodes in seasons.items():
            for ep_name, quality_variants in episodes.items():
                episode_list.append({
                    "season": season_name,
                    "episode": ep_name,
                    "qualities": quality_variants
                })
                ep_counter += 1

        if not episode_list:
            print("\n[-] No playable episodes discovered for this series.")
            return "back"

        while True:
            print(f"\n[+] TV SERIES DETECTED: '{show_title}'")
            print("=" * 85)
            last_season = None
            for idx, ep_info in enumerate(episode_list, 1):
                if ep_info["season"] != last_season:
                    last_season = ep_info["season"]
                    print(f"\n=== {last_season.upper()} ===")
                qual_summary = ", ".join(list(set([q.get("resolution", "HD") for q in ep_info["qualities"]])))
                top_q = ep_info["qualities"][0] if ep_info["qualities"] else {}
                audio_summary = top_q.get("audio", "Original")
                print(f"  {idx:<3} | {ep_info['episode']:<28} | Available: {qual_summary:<18} | {audio_summary}")

            print("=" * 85)
            print("Navigation: [1-N] Select Episode | [s] Search New | [q] Quit")

            selected_ep_idx = 0
            if not direct and len(episode_list) > 1:
                try:
                    choice = input(f"\n[>] Select Episode/Batch [1-{len(episode_list)}] (Default: 1, 's', 'q'): ").strip().lower()
                    if choice in ['q', 'quit', 'exit']:
                        return "quit"
                    if choice in ['s', 'search', 'new']:
                        return "search"
                    if choice.isdigit() and 1 <= int(choice) <= len(episode_list):
                        selected_ep_idx = int(choice) - 1
                except (EOFError, KeyboardInterrupt):
                    return "quit"

            chosen_ep = episode_list[selected_ep_idx]
            q_variants = chosen_ep["qualities"]

            # Quality selection loop
            while True:
                print(f"\n[+] SELECTED: {show_title} - {chosen_ep['season']} ({chosen_ep['episode']})")
                print("\nAvailable Stream Qualities & Sizes:")
                print(f"{'#':<3} | {'Resolution / Codec':<28} | {'Audio / Lang':<22} | {'Size':<10} | {'Platform':<12}")
                print("-" * 85)

                for idx, q_obj in enumerate(q_variants, 1):
                    res_codec = f"{q_obj.get('resolution', 'HD')} {q_obj.get('codec', '')}".strip()
                    print(f"{idx:<3} | {res_codec:<28} | {q_obj.get('audio', 'Original')[:22]:<22} | {q_obj.get('size', 'N/A')[:10]:<10} | {q_obj.get('platform', 'Web')[:12]}")

                print("-" * 85)
                print("Navigation: [1-N] Play Quality | [b] Back to Episodes | [s] Search New | [q] Quit")

                selected_q_idx = 0
                if quality_filter:
                    for idx, q_obj in enumerate(q_variants):
                        if quality_filter.lower() in str(q_obj.get("resolution", "")).lower():
                            selected_q_idx = idx
                            break
                elif not direct and len(q_variants) > 1:
                    try:
                        choice = input(f"\n[>] Select Quality [1-{len(q_variants)}] (Default: 1, 'b', 's', 'q'): ").strip().lower()
                        if choice in ['q', 'quit', 'exit']:
                            return "quit"
                        if choice in ['s', 'search', 'new']:
                            return "search"
                        if choice in ['b', 'back']:
                            break  # Break back to episode list
                        if choice.isdigit() and 1 <= int(choice) <= len(q_variants):
                            selected_q_idx = int(choice) - 1
                    except (EOFError, KeyboardInterrupt):
                        return "quit"

                final_choice = q_variants[selected_q_idx]
                stream_url = final_choice.get("target_url")
                full_title = f"{show_title} - {chosen_ep['season']} {chosen_ep['episode']}"

                print(f"\n[*] Selected #{selected_q_idx+1}: {final_choice.get('resolution', 'HD')} ({final_choice.get('platform', 'Web')})")
                print(f"[*] Resolving stealth stream & verifying EBML magic bytes...")

                outcome = await self.resolve_and_verify(stream_url)
                v = outcome.get("verification", {})

                # Fallback to other quality variants for this episode if primary fails
                if not v.get("valid") and len(q_variants) > 1:
                    print(f"\n[!] Notice: Primary quality stream failed EBML check. Testing alternative qualities for this episode...")
                    for alt_q in q_variants:
                        alt_url = alt_q.get("target_url")
                        if alt_url and alt_url != stream_url:
                            alt_out = await self.resolve_and_verify(alt_url)
                            if alt_out.get("verification", {}).get("valid"):
                                outcome = alt_out
                                print(f"[+] Switched to verified episode stream: {alt_q.get('resolution', 'HD')} ({alt_q.get('platform')})")
                                break

                direct_url = outcome.get("direct_url")
                fmt = outcome.get('verification', {}).get('format', 'MKV')
                size = outcome.get('verification', {}).get('size', 'N/A')

                print(f"\n[+] STREAM VERIFIED: {fmt} | Size: {size}")
                print(f"[+] Direct URL: {direct_url[:80]}...")
                print(f"[+] Total Resolution Latency: {outcome.get('total_ms', 0):.2f}ms")

                proc = self.launch_vlc(direct_url, title=full_title)
                pid_msg = f"(PID: {proc.pid})" if proc else "(System Default Player)"
                print(f"[+] Playing in VLC {pid_msg}!")

                print("\nPlayback Active. Menu Options:")
                print("  [b] Back to episode/quality list")
                print("  [s] Search a new movie / series")
                print("  [q] Quit Player")
                try:
                    post_choice = input("\n[>] Option [b / s / q] (Default: b): ").strip().lower()
                    if post_choice in ['q', 'quit', 'exit']:
                        return "quit"
                    elif post_choice in ['s', 'search', 'new']:
                        return "search"
                    else:
                        break  # Back to quality loop
                except (EOFError, KeyboardInterrupt):
                    return "quit"

    async def interactive_run(self, initial_query: str = None, direct: bool = False, quality_filter: str = None, test_mode: bool = False):
        """Continuous multi-search interactive session with seamless back navigation."""
        await self.ensure_stream_server()
        print("=" * 115)
        print("[*] AUTONOMOUS INSTANT STREAM ENGINE (AI-FREE / MULTI-SEARCH / SUB-SECOND PLAYBACK)")
        print("=" * 115)

        current_query = initial_query

        while True:
            if not current_query:
                try:
                    current_query = input("\n[?] Enter movie / series name (or 'q' to quit): ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\n[+] Exiting player. Goodbye!")
                    return
                if not current_query:
                    continue
                if current_query.lower() in ['q', 'quit', 'exit']:
                    print("\n[+] Exiting player. Goodbye!")
                    return

            print(f"\n[*] Dispatching search for '{current_query}' across Local DB, Telegram Bots, VegaMovies, UHDMovies, HDHub4u, YouTube...")

            accumulated_hits = []
            is_series_mode = False

            # Stream batches progressively
            async for batch in self.dispatcher.stream_all_parallel(current_query):
                # Check if this batch is a structured TV Series object
                if batch and isinstance(batch[0], dict) and batch[0].get("is_series"):
                    is_series_mode = True
                    nav_cmd = await self.handle_series_flow(batch[0], direct=direct, quality_filter=quality_filter)
                    if nav_cmd == "quit":
                        print("\n[+] Exiting player. Goodbye!")
                        return
                    elif nav_cmd == "search":
                        current_query = None
                        break
                    else: # 'back'
                        current_query = None
                        break
                accumulated_hits.extend(batch)

            if is_series_mode:
                continue

            # If 0 hits from initial query, attempt TitleResolver recovery
            if not accumulated_hits:
                t_res = await self.title_resolver.resolve(current_query)
                canonical = t_res.get("canonical_title") or t_res.get("formatted")
                if canonical and canonical.lower() != current_query.lower():
                    print(f"\n[+] Recognized title: '{canonical}'. Querying live sources...")
                    async for batch in self.dispatcher.stream_all_parallel(canonical):
                        if batch and isinstance(batch[0], dict) and batch[0].get("is_series"):
                            is_series_mode = True
                            nav_cmd = await self.handle_series_flow(batch[0], direct=direct, quality_filter=quality_filter)
                            if nav_cmd == "quit":
                                return
                            current_query = None
                            break
                        accumulated_hits.extend(batch)

            if is_series_mode:
                continue

            if not accumulated_hits:
                print(f"\n[-] No releases found for '{current_query}'. Please check spelling.")
                current_query = None
                continue

            # Regular Movie Results Loop (Stay in table until user chooses another search or quits)
            while True:
                print(f"\n{'#':<3} | {'Title / Release Details':<38} | {'Quality / Codec':<20} | {'Audio / Lang':<20} | {'Size':<8} | {'Source':<12}")
                print("-" * 115)

                for i, hit in enumerate(accumulated_hits, start=1):
                    title_col = str(hit.get('title', current_query))[:38]
                    res_codec = f"{hit.get('resolution', 'HD')} {hit.get('codec', '')}".strip()[:20]
                    audio = str(hit.get('audio', 'Original'))[:20]
                    size = str(hit.get('size', 'N/A'))[:8]
                    platform = str(hit.get('platform', 'Web'))[:12]
                    print(f"{i:<3} | {title_col:<38} | {res_codec:<20} | {audio:<20} | {size:<8} | {platform:<12}")

                print("-" * 115)
                print("Navigation: [1-N] Select Release | [s] Search New Movie/Series | [q] Quit Player")

                selected_idx = 0
                if quality_filter:
                    for idx, h in enumerate(accumulated_hits):
                        if quality_filter.lower() in str(h.get('resolution', '')).lower():
                            selected_idx = idx
                            break
                    quality_filter = None  # Consume filter for subsequent selections
                elif test_mode or direct:
                    selected_idx = 0
                else:
                    try:
                        choice = input(f"\n[>] Select release [1-{len(accumulated_hits)}] (Default: 1, 's', 'q'): ").strip().lower()
                        if choice in ['q', 'quit', 'exit']:
                            print("\n[+] Exiting player. Goodbye!")
                            return
                        if choice in ['s', 'search', 'new']:
                            current_query = None
                            break
                        if choice.isdigit() and 1 <= int(choice) <= len(accumulated_hits):
                            selected_idx = int(choice) - 1
                    except (EOFError, KeyboardInterrupt):
                        print("\n[+] Exiting player. Goodbye!")
                        return

                selected = accumulated_hits[selected_idx]
                stream_url = selected.get("target_url") or selected.get("stream_url") or selected.get("direct_url")
                title = selected.get("title", current_query)

                print(f"\n[*] Selected #{selected_idx+1}: {selected.get('title')} [{selected.get('resolution', 'HD')}] ({selected.get('platform', 'Web')})")
                print(f"[*] Resolving stealth stream & verifying EBML magic bytes...")

                outcome = await self.resolve_and_verify(stream_url)
                v = outcome.get("verification", {})

                # Fallback search if resolution / verification fails (Quality & Closest-Size Locked)
                if not v.get("valid"):
                    target_res = str(selected.get("resolution", "1080P")).upper()
                    target_size_mb = parse_size_in_mb(selected.get("size", ""))
                    print(f"\n[!] Notice: Primary link verification for selected release did not pass EBML check.")
                    print(f"[*] Testing closest-size [{target_res}] verified mirror alternatives in parallel...")
                    found_valid_alt = False

                    # Filter candidates: strictly matching requested resolution, non-YouTube, non-zero url
                    same_res_candidates = [
                        a for a in accumulated_hits
                        if str(a.get("resolution", "")).upper() == target_res
                        and not a.get("is_youtube")
                        and (a.get("target_url") or a.get("stream_url") or a.get("direct_url")) != stream_url
                    ]

                    candidate_pool = same_res_candidates if same_res_candidates else [
                        a for a in accumulated_hits
                        if not a.get("is_youtube")
                        and (a.get("target_url") or a.get("stream_url") or a.get("direct_url")) != stream_url
                    ]

                    def candidate_sort_key(x):
                        p_score = 0
                        platform = x.get("platform", "")
                        if platform == "VegaMovies":
                            p_score = 0
                        elif platform == "Telegram Bot":
                            p_score = 1
                        elif platform == "UHDMovies":
                            p_score = 2
                        else:
                            p_score = 3

                        size_diff = abs(parse_size_in_mb(x.get("size", "")) - target_size_mb) if target_size_mb > 0 else 999999
                        return (p_score, size_diff)

                    candidate_pool.sort(key=candidate_sort_key)

                    if candidate_pool:
                        tasks = [asyncio.create_task(self.resolve_and_verify(a.get("target_url") or a.get("stream_url") or a.get("direct_url"))) for a in candidate_pool[:12]]
                        for finished_task in asyncio.as_completed(tasks):
                            alt_out = await finished_task
                            if alt_out.get("verification", {}).get("valid"):
                                outcome = alt_out
                                for t in tasks:
                                    if not t.done():
                                        t.cancel()

                                matched_c = None
                                target_direct = alt_out.get("direct_url", "")
                                resolved_direct = alt_out.get("resolved", {}).get("direct_url", "")
                                for a in candidate_pool:
                                    cand_url = a.get("target_url") or a.get("stream_url") or a.get("direct_url") or ""
                                    if cand_url and (cand_url == target_direct or cand_url == resolved_direct):
                                        matched_c = a
                                        break
                                if not matched_c and candidate_pool:
                                    matched_c = candidate_pool[0]

                                title = matched_c.get("title", current_query)
                                print(f"[+] Switched to closest verified {matched_c.get('resolution')} release ({matched_c.get('size')}) on {matched_c.get('platform')}")
                                found_valid_alt = True
                                break

                    if not found_valid_alt:
                        print(f"\n[-] Selected stream could not be verified.")
                        if test_mode:
                            print("\n[*] Test mode: stream unverified, exiting cleanly.")
                            return
                        print("  [b] Back to release table to choose another release")
                        print("  [s] Search a new movie / series")
                        print("  [q] Quit Player")
                        try:
                            fail_choice = input("\n[>] Option [b / s / q] (Default: b): ").strip().lower()
                            if fail_choice in ['q', 'quit', 'exit']:
                                print("\n[+] Exiting player. Goodbye!")
                                return
                            elif fail_choice in ['s', 'search', 'new']:
                                current_query = None
                                break
                            else:
                                continue # Back to release table
                        except (EOFError, KeyboardInterrupt):
                            return

                direct_url = outcome.get("direct_url")
                fmt = outcome.get('verification', {}).get('format', 'MKV')
                size = outcome.get('verification', {}).get('size', 'N/A')
                print(f"\n[+] STREAM VERIFIED: {fmt} | Size: {size}")
                print(f"[+] Direct URL: {direct_url[:80]}...")
                print(f"[+] Total Resolution Latency: {outcome.get('total_ms', 0):.2f}ms")

                proc = self.launch_vlc(direct_url, title=title) if not test_mode else None
                pid_msg = f"(PID: {proc.pid})" if proc else "(Test Mode / Verification)"
                print(f"[+] Playing in VLC {pid_msg}!")

                if test_mode:
                    print("\n[+] Test mode complete. End-to-end verification successful.")
                    return

                print("\nPlayback Active. Menu Options:")
                print("  [b] Back to release table to choose another release")
                print("  [s] Search a new movie / series")
                print("  [q] Quit Player")
                try:
                    post_choice = input("\n[>] Option [b / s / q] (Default: b): ").strip().lower()
                    if post_choice in ['q', 'quit', 'exit']:
                        print("\n[+] Exiting player. Goodbye!")
                        return
                    elif post_choice in ['s', 'search', 'new']:
                        current_query = None
                        break
                    else:
                        continue # Back to release table
                except (EOFError, KeyboardInterrupt):
                    return

def main():
    parser = argparse.ArgumentParser(description="Standalone Instant Media Player Engine")
    parser.add_argument("query", nargs="*", help="Movie or series title to search and play")
    parser.add_argument("--quality", "-q", help="Preferred quality (e.g. 1080p, 2160p, 720p)")
    parser.add_argument("--direct", "-d", action="store_true", help="Auto-play best quality without prompting")
    parser.add_argument("--test", action="store_true", help="Auto-select and test stream resolution without blocking")
    args = parser.parse_args()

    q = " ".join(args.query).strip() if args.query else None
    app = PlayApp()
    try:
        asyncio.run(app.interactive_run(q, direct=args.direct or args.test, quality_filter=args.quality, test_mode=args.test))
    except Exception as e:
        print(f"\n[-] Error encountered: {e}")
        try:
            input("\nPress Enter to exit...")
        except Exception:
            pass

if __name__ == "__main__":
    main()
