"""
Embedded Local Async HTTP Telegram Stream Server (< 10ms)
Maps HTTP/1.1 206 Partial Content range requests directly to Telethon chunk iterators.
Serves full-bitrate 1080p/4K video streams directly from Telegram MTProto on 127.0.0.1:8989.
"""

import os
import sys
import asyncio
import re
from pathlib import Path
from aiohttp import web
from telethon import TelegramClient
from telethon.tl.types import MessageMediaDocument

BASE_DIR = Path(__file__).resolve().parent
SESSION_PATH = str(BASE_DIR / "tg_session")
DEFAULT_STREAM_PORT = 8989
ACTIVE_STREAM_PORT = 8989

class TelegramLocalStreamServer:
    def __init__(self, client: TelegramClient = None, api_id: int = 2040, api_hash: str = "b18441a1ff607e10a989891a5462e627"):
        self.api_id = api_id
        self.api_hash = api_hash
        self.client = client
        self.port = DEFAULT_STREAM_PORT
        self.app = web.Application()
        self.app.router.add_get("/stream/{chat_id}/{msg_id}", self.handle_stream)
        self.app.router.add_get("/health", self.handle_health)
        self.runner = None
        self.site = None

    async def init_client(self):
        """Initialize and connect Telethon MTProto client if not already provided."""
        if self.client is None or not self.client.is_connected():
            try:
                from tg_bot_scraper import get_shared_tg_client
                self.client = await get_shared_tg_client(self.api_id, self.api_hash)
            except Exception:
                self.client = TelegramClient(SESSION_PATH, self.api_id, self.api_hash)
                await self.client.connect()

    async def handle_health(self, request):
        return web.Response(text=f"OK:{self.port}", status=200)

    async def handle_stream(self, request):
        """Streams Telegram document chunks dynamically with HTTP Range support."""
        await self.init_client()
        if not await self.client.is_user_authorized():
            return web.Response(text="Telegram not authorized", status=401)

        try:
            chat_id = int(request.match_info["chat_id"])
            msg_id = int(request.match_info["msg_id"])
        except ValueError:
            return web.Response(text="Invalid chat or message ID", status=400)

        try:
            msg = await self.client.get_messages(chat_id, ids=msg_id)
            if not msg or not msg.media or not isinstance(msg.media, MessageMediaDocument):
                return web.Response(text="Document not found", status=404)

            doc = msg.media.document
            file_size = doc.size
            mime_type = doc.mime_type or "video/mp4"

            # Parse HTTP Range header
            range_header = request.headers.get("Range", "")
            start_byte = 0
            end_byte = file_size - 1

            if range_header and range_header.startswith("bytes="):
                range_match = re.search(r"bytes=(\d+)-(\d*)", range_header)
                if range_match:
                    start_byte = int(range_match.group(1))
                    if range_match.group(2):
                        end_byte = int(range_match.group(2))

            length = end_byte - start_byte + 1

            headers = {
                "Content-Type": mime_type,
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
                "Content-Length": str(length),
                "Access-Control-Allow-Origin": "*"
            }

            response = web.StreamResponse(status=206, reason="Partial Content", headers=headers)
            await response.prepare(request)

            # Stream chunks directly from Telegram MTProto (aligning chunk size to 128KB)
            chunk_size = 128 * 1024
            bytes_sent = 0
            async for chunk in self.client.iter_download(doc, offset=start_byte, request_size=chunk_size):
                if len(chunk) == 0:
                    break
                remaining_needed = length - bytes_sent
                to_send = chunk[:remaining_needed]
                await response.write(to_send)
                bytes_sent += len(to_send)
                if bytes_sent >= length:
                    break

            await response.write_eof()
            return response

        except Exception as e:
            return web.Response(text=f"Stream error: {e}", status=500)

    async def start(self, port: int = DEFAULT_STREAM_PORT) -> int:
        """Start local aiohttp server in the background with port fallback."""
        global ACTIVE_STREAM_PORT
        await self.init_client()
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        # Try designated port, then fallback to next 10 ports
        for candidate_port in range(port, port + 10):
            try:
                self.site = web.TCPSite(self.runner, "127.0.0.1", candidate_port)
                await self.site.start()
                self.port = candidate_port
                ACTIVE_STREAM_PORT = candidate_port
                return candidate_port
            except OSError:
                continue

        # If range fails, let OS assign free port
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        assigned_port = self.site._server.sockets[0].getsockname()[1]
        self.port = assigned_port
        ACTIVE_STREAM_PORT = assigned_port
        return assigned_port

    async def stop(self):
        """Cleanup server and telethon connections."""
        if self.runner:
            await self.runner.cleanup()
        if self.client:
            await self.client.disconnect()

STREAM_PORT = DEFAULT_STREAM_PORT

# Singleton instance
_stream_server = None

async def get_or_start_stream_server(client: TelegramClient = None, port: int = DEFAULT_STREAM_PORT) -> TelegramLocalStreamServer:
    global _stream_server
    if _stream_server is None:
        _stream_server = TelegramLocalStreamServer(client=client)
        await _stream_server.start(port=port)
    elif client and (_stream_server.client is None or not _stream_server.client.is_connected()):
        _stream_server.client = client
    return _stream_server

if __name__ == "__main__":
    server = TelegramLocalStreamServer()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(server.start())
    print(f"Telegram local stream server running on http://127.0.0.1:{STREAM_PORT}")
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        loop.run_until_complete(server.stop())
