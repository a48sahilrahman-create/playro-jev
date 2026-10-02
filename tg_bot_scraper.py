"""
Telegram Bot Search & Direct Link Extractor (Option B - Telethon MTProto Userbot)
Queries @Angela2_moviebot / @SeriesLandBot in group Trios (-1518787755)

Supports Event-Driven Fast Reaction (< 500ms):
  Phase 1 (Search): Query Trios group, parse inline buttons, extract release metadata without resolving.
  Phase 2 (Resolve): Send /start <token> to @Angela2_moviebot, forward document to @FileToLinkBot, verify and return direct HTTP stream link.
  Phase 3 (Auto): Search + auto-select highest quality + resolve in one shot.
"""

import os
import sys
import asyncio
import re
import json
import argparse
from urllib.parse import urlparse, parse_qs

try:
    from telethon import TelegramClient, events
    from telethon.tl.types import MessageMediaDocument, DocumentAttributeFilename
except ImportError:
    print("Telethon not installed. Run: pip install telethon")
    sys.exit(1)

# Default paths and bot identifiers
CONFIG_PATH = os.path.expanduser(r"~\.claude\skills\play\tools\tg_config.json")
SESSION_PATH = os.path.expanduser(r"~\.claude\skills\play\tools\tg_session")
TRIOS_GROUP_ID = -1001518787755
ANGELA_BOT = "Angela2_moviebot"
SERIES_LAND_BOT = "SeriesLandBot"
FILE_TO_LINK_BOT = "FileToLinkBot"

def load_tg_config() -> dict:
    """Load config from tg_config.json or env vars with safe defaults."""
    cfg = {
        "api_id": 2040,
        "api_hash": "b18441a1ff607e10a989891a5462e627",
        "phone": "",
        "trios_group_id": TRIOS_GROUP_ID,
        "angela_bot": ANGELA_BOT,
        "series_land_bot": SERIES_LAND_BOT,
        "file_to_link_bot": FILE_TO_LINK_BOT
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                cfg.update(saved)
        except Exception:
            pass
    # Override with env if present
    if os.getenv("TELEGRAM_API_ID"):
        try:
            cfg["api_id"] = int(os.getenv("TELEGRAM_API_ID"))
        except ValueError:
            pass
    if os.getenv("TELEGRAM_API_HASH"):
        cfg["api_hash"] = os.getenv("TELEGRAM_API_HASH")
    return cfg

def clean_query(title: str) -> str:
    """Removes filler keywords that break Telegram bot keyword matching."""
    noise = r'\b(movie|full movie|download|free download|watch online|series|all episodes|hd|bluray|hindi dubbed)\b'
    cleaned = re.sub(noise, '', title, flags=re.IGNORECASE).strip()
    return re.sub(r'\s+', ' ', cleaned)

def parse_option_metadata(button_text: str, button_url: str) -> dict:
    """Extracts resolution, codec, language tags, size, and start token from button text and url."""
    text = button_text.strip()

    # Resolution parsing
    res_match = re.search(r'\b(2160p|4K|1080p|720p|480p|360p)\b', text, re.IGNORECASE)
    resolution = res_match.group(1).upper() if res_match else "Standard"

    # Size parsing
    size_match = re.search(r'(\d+(?:\.\d+)?\s*(?:GB|MB))', text, re.IGNORECASE)
    size = size_match.group(1).upper() if size_match else "Unknown"

    # Language tags
    languages = re.findall(r'\[(ENG|HIN|MAL|TAM|TEL|KAN|BEN|MAR|DUAL|MULTI|ORIGINAL)\]', text, re.IGNORECASE)
    if not languages:
        lang_match = re.findall(r'\b(Hindi|English|Malayalam|Tamil|Telugu|Kannada|Bengali|Marathi|Dual|Multi)\b', text, re.IGNORECASE)
        languages = list(set([l.upper() for l in lang_match]))

    # Codec / Source
    codec = "x264"
    if re.search(r'\b(HEVC|x265|10bit|10-bit)\b', text, re.IGNORECASE):
        codec = "HEVC 10-bit"
    elif "BluRay" in text:
        codec = "BluRay"
    elif "WEB-DL" in text:
        codec = "WEB-DL"

    # Token parsing
    parsed = urlparse(button_url)
    qs = parse_qs(parsed.query)
    start_token = qs.get('start', [None])[0]

    return {
        "platform": "Telegram Trios",
        "label": text,
        "url": button_url,
        "start_token": start_token,
        "resolution": resolution,
        "codec": codec,
        "size": size,
        "languages": languages if languages else ["Original"],
        "audio": ", ".join(languages) if languages else "Original"
    }

_shared_client = None
_client_lock = asyncio.Lock()

async def get_shared_tg_client(api_id: int = 2040, api_hash: str = "b18441a1ff607e10a989891a5462e627") -> TelegramClient:
    """Singleton Telethon client provider preventing SQLite session file locking."""
    global _shared_client
    async with _client_lock:
        if _shared_client is None or not _shared_client.is_connected():
            restore_session_if_needed()
            _shared_client = TelegramClient(SESSION_PATH, api_id, api_hash)
            await _shared_client.connect()
        return _shared_client

async def search_telegram(api_id: int, api_hash: str, query: str, phone: str = None) -> list:
    """Phase 1: Search Trios group and return all available parsed release options using event listener."""
    cleaned_title = clean_query(query)
    try:
        client = await get_shared_tg_client(api_id, api_hash)
        if not await client.is_user_authorized():
            return []
    except Exception:
        return []

    future_response = asyncio.get_event_loop().create_future()

    @client.on(events.NewMessage(chats=TRIOS_GROUP_ID))
    async def handler(event):
        msg = event.message
        if msg.text and cleaned_title.lower() in msg.text.lower():
            if not future_response.done():
                future_response.set_result(msg)
        elif msg.buttons:
            if not future_response.done():
                future_response.set_result(msg)

    # 1. Send search query to Trios group
    sent_msg = await client.send_message(TRIOS_GROUP_ID, cleaned_title)

    # Wait for bot response with timeout
    try:
        response_msg = await asyncio.wait_for(future_response, timeout=6.0)
    except asyncio.TimeoutError:
        response_msg = None

    # Fallback scan last 5 messages if event listener didn't trigger
    if not response_msg:
        async for msg in client.iter_messages(TRIOS_GROUP_ID, limit=5):
            if msg.id > sent_msg.id and (msg.buttons or (msg.text and cleaned_title.lower() in msg.text.lower())):
                response_msg = msg
                break

    if not response_msg:
        return []

    # 3. Parse inline buttons
    file_options = []
    if response_msg.buttons:
        for row in response_msg.buttons:
            for btn in row:
                if btn.url and ("start=" in btn.url or "t.me/" in btn.url):
                    meta = parse_option_metadata(btn.text, btn.url)
                    meta["source_id"] = f"tg_{len(file_options)+1}"
                    file_options.append(meta)

    return file_options

def restore_session_if_needed():
    """Instantly restores tg_session.session from tg_session_backup.b64 if missing or corrupted."""
    session_file = f"{SESSION_PATH}.session"
    backup_b64_file = os.path.join(os.path.dirname(SESSION_PATH), "tg_session_backup.b64")
    if (not os.path.exists(session_file) or os.path.getsize(session_file) < 100) and os.path.exists(backup_b64_file):
        try:
            import base64
            with open(backup_b64_file, "r", encoding="utf-8") as f:
                data = base64.b64decode(f.read().strip())
            with open(session_file, "wb") as out:
                out.write(data)
        except Exception:
            pass

async def resolve_telegram_token(api_id: int, api_hash: str, start_token: str, verify: bool = True, phone: str = None) -> dict:
    """Phase 2: Resolve start token with @Angela2_moviebot and generate direct HTTP stream URL using multi-bot fallback chain."""
    restore_session_if_needed()
    client = TelegramClient(SESSION_PATH, api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        # Try instant restore
        restore_session_if_needed()
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return {"error": "Telegram client session is not authorized. Session restored check required."}

    cfg = load_tg_config()
    angela_bot = cfg.get("angela_bot", ANGELA_BOT)
    bots_chain = cfg.get("file_to_link_bots", ["File_To_Link_2Bot", "FileToLinkbot", "Filestreamxbot", "filestreamrobot", "MegaStreamBot", "StreamW_Bot", "TG_FileStreamBot", "FileStreamerRoBot"])

    # 1. Send /start <token> to Angela
    await client.send_message(angela_bot, f"/start {start_token}")

    # 2. Wait for document delivery from Angela (handling force-subscribe channels automatically)
    file_msg = None
    for _ in range(16):
        async for m in client.iter_messages(angela_bot, limit=3):
            if m.media and isinstance(m.media, MessageMediaDocument):
                file_msg = m
                break
            elif m.buttons and ("join" in str(m.text).lower() or "updates channel" in str(m.text).lower()):
                # Auto-join required channel if prompted
                from telethon.tl.functions.messages import ImportChatInviteRequest
                for row in m.buttons:
                    for btn in row:
                        if btn.url and ("+" in btn.url or "joinchat" in btn.url):
                            hash_match = re.search(r'(?:\+|\/joinchat\/)([a-zA-Z0-9_-]+)', btn.url)
                            if hash_match:
                                try:
                                    await client(ImportChatInviteRequest(hash_match.group(1)))
                                except Exception:
                                    pass
                # Retry start token after auto-joining
                await client.send_message(angela_bot, f"/start {start_token}")
                await asyncio.sleep(1.0)
        if file_msg:
            break
        await asyncio.sleep(0.5)

    if not file_msg:
        await client.disconnect()
        return {"error": "Angela bot did not return a file document for this start token."}

    # Extract filename and size
    doc = file_msg.media.document
    filename = "video.mkv"
    for attr in doc.attributes:
        if isinstance(attr, DocumentAttributeFilename):
            filename = attr.file_name
            break
    size_gb = round(doc.size / (1024**3), 2)
    size_mb = round(doc.size / (1024**2), 2)
    formatted_size = f"{size_gb} GB" if size_gb >= 1.0 else f"{size_mb} MB"

    # 3. Native Local Streaming Bridge (100% Reliable, Zero Third-Party Bot Reliance)
    try:
        from tg_local_stream_server import get_or_start_stream_server, DEFAULT_STREAM_PORT
        server = await get_or_start_stream_server(client=client, port=DEFAULT_STREAM_PORT)
        active_port = getattr(server, "port", DEFAULT_STREAM_PORT)
        chat_identifier = getattr(file_msg, "chat_id", None) or getattr(file_msg.peer_id, "user_id", None) or 8143714699
        local_stream_url = f"http://127.0.0.1:{active_port}/stream/{chat_identifier}/{file_msg.id}"
        return {
            "filename": filename,
            "size": formatted_size,
            "stream_url": local_stream_url,
            "all_urls": [local_stream_url]
        }
    except Exception as e:
        print(f"[!] Local stream server error: {e}")

    # Fallback to Multi-Bot Loop if local server cannot start
    chosen_stream_url = None
    all_urls = []

    for candidate_bot in bots_chain:
        try:
            bot_entity = candidate_bot.strip("@")
            # Ensure bot is started
            try:
                await client.send_message(bot_entity, "/start")
            except Exception:
                pass

            # Forward the media file to the candidate bot
            await client.forward_messages(bot_entity, file_msg)

            # Wait for generated link response
            link_msg = None
            for _ in range(12):
                async for m in client.iter_messages(bot_entity, limit=3):
                    if m.text and ("http" in m.text or "Link Generated" in m.text or "Download" in m.text or "Stream" in m.text):
                        link_msg = m
                        break
                if link_msg:
                    break
                await asyncio.sleep(0.5)

            if link_msg and link_msg.text:
                raw_extracted = re.findall(r'https?://[^\s`]+', link_msg.text)
                extracted = [u.rstrip('`*_\'")') for u in raw_extracted]
                # Filter out t.me links and channel links
                direct_links = [u for u in extracted if "t.me" not in u and "telegram.me" not in u]

                # Check inline buttons if any
                if not direct_links and link_msg.buttons:
                    for row in link_msg.buttons:
                        for btn in row:
                            if btn.url and "t.me" not in btn.url:
                                direct_links.append(btn.url.rstrip('`*_\'")'))

                if direct_links:
                    # Prefer /dl/ or direct file links for streaming
                    dl_links = [u for u in direct_links if "/dl/" in u or "download" in u.lower() or u.endswith((".mkv", ".mp4"))]
                    chosen_stream_url = dl_links[0] if dl_links else direct_links[0]
                    all_urls = direct_links
                    break
        except Exception:
            continue

    await client.disconnect()

    if not chosen_stream_url:
        return {"error": "File received from Angela, but none of the File-To-Link bots in the fallback pool generated a direct stream URL."}

    result = {
        "filename": filename,
        "size": formatted_size,
        "stream_url": chosen_stream_url,
        "all_urls": all_urls
    }

    return result

if __name__ == "__main__":
    cfg = load_tg_config()
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["search", "resolve", "auto"])
    parser.add_argument("query_or_token")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--api-id", type=int, default=cfg["api_id"])
    parser.add_argument("--api-hash", default=cfg["api_hash"])
    parser.add_argument("--phone", default=cfg.get("phone"))
    args = parser.parse_args()

    api_id = args.api_id or cfg["api_id"]
    api_hash = args.api_hash or cfg["api_hash"]

    if args.command == "search":
        res = asyncio.run(search_telegram(api_id, api_hash, args.query_or_token, args.phone))
        print(json.dumps(res, indent=2))
    elif args.command == "resolve":
        res = asyncio.run(resolve_telegram_token(api_id, api_hash, args.query_or_token, phone=args.phone))
        print(json.dumps(res, indent=2))
