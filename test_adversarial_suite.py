"""
Comprehensive Adversarial Stress Test Suite for Play Engine Components:
- TitleResolver: Typo stress test, franchise queries, non-existent titles, numeral normalizations, zero crash.
- LinkResolver: Pixeldrain bypass, YouTube passthrough, XOR cipher, dead links, partial timeouts, zero crash.
- StreamVerifier: EBML magic bytes (MKV/MP4/HLS), 403 XML error rejection, streaming chunks (no full file download), dead links, zero crash.
"""

import sys
import time
import asyncio
import json
import unittest

from title_resolver import TitleResolver
from link_resolver import LinkResolver
from stream_verifier import StreamVerifier


class AdversarialStressTestSuite(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.title_resolver = TitleResolver()
        self.link_resolver = LinkResolver()
        self.stream_verifier = StreamVerifier()

    # -------------------------------------------------------------
    # 1. TitleResolver Adversarial & Edge Case Tests
    # -------------------------------------------------------------
    async def test_01_typo_oppenheimer(self):
        res = await self.title_resolver.resolve("Oppenhimer")
        self.assertIn(res["status"], ["EXACT_MATCH", "SUGGESTIONS_FOUND"])
        self.assertEqual(res.get("canonical_title"), "Oppenheimer")
        self.assertLess(res["elapsed_ms"], 200.0)

    async def test_02_typo_avengers_endgame(self):
        res = await self.title_resolver.resolve("avengrs endgme")
        self.assertIn(res["status"], ["EXACT_MATCH", "SUGGESTIONS_FOUND"])
        self.assertIn("Avengers: Endgame", res.get("canonical_title"))
        self.assertLess(res["elapsed_ms"], 200.0)

    async def test_03_numeral_mapping_dune_part_2(self):
        res = await self.title_resolver.resolve("dune part 2")
        self.assertEqual(res["status"], "EXACT_MATCH")
        self.assertEqual(res.get("canonical_title"), "Dune: Part Two")
        self.assertEqual(res.get("year"), 2024)
        self.assertLess(res["elapsed_ms"], 50.0)

    async def test_04_regional_typo_kesu(self):
        res = await self.title_resolver.resolve("kesu ee nadinte")
        self.assertIn(res["status"], ["EXACT_MATCH", "SUGGESTIONS_FOUND"])
        self.assertEqual(res.get("canonical_title"), "Keshu Ee Veedinte Nadhan")
        self.assertLess(res["elapsed_ms"], 400.0)

    async def test_05_non_existent_title(self):
        res = await self.title_resolver.resolve("asdfqwerty12345xyznonexistent")
        self.assertEqual(res["status"], "NO_MATCH")
        self.assertEqual(len(res.get("suggestions", [])), 0)

    async def test_06_empty_and_whitespace_and_symbols(self):
        for bad_input in ["", "   ", "!!!@@#$$%^^&*()", None]:
            res = await self.title_resolver.resolve(bad_input)
            self.assertEqual(res["status"], "NO_MATCH")
            self.assertEqual(res.get("suggestions"), [])

    # -------------------------------------------------------------
    # 2. LinkResolver Adversarial & Edge Case Tests
    # -------------------------------------------------------------
    async def test_07_pixeldrain_instant_transform(self):
        res = await self.link_resolver.resolve_auto("https://pixeldrain.com/u/1noW8snv")
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["host"], "Pixeldrain API")
        self.assertEqual(res["direct_url"], "https://pixeldrain.dev/api/file/1noW8snv")
        self.assertLess(res["elapsed_ms"], 5.0)

    async def test_08_youtube_instant_passthrough(self):
        for yt_url in ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ"]:
            res = await self.link_resolver.resolve_auto(yt_url)
            self.assertEqual(res["status"], "SUCCESS")
            self.assertIn("YouTube", res["host"])
            self.assertTrue(res["direct_url"])

    async def test_09_ddlbase_xor_decoder(self):
        test_plain = "https://pub-r2.dev/stream.mkv"
        key = "mySecret123"
        xored = "".join(chr(ord(c) ^ ord(key[i % len(key)])) for i, c in enumerate(test_plain))
        import base64
        b64_cipher = base64.b64encode(xored.encode("utf-8")).decode("utf-8")

        decoded = self.link_resolver.resolve_ddlbase_xor(b64_cipher, key)
        self.assertEqual(decoded, test_plain)

        # Invalid base64 or empty string must not crash
        self.assertEqual(self.link_resolver.resolve_ddlbase_xor(""), "")
        self.assertEqual(self.link_resolver.resolve_ddlbase_xor("invalid!!b64"), "invalid!!b64")

    async def test_10_dead_domain_and_timeout_isolation(self):
        res = await self.link_resolver.resolve_auto("https://dead-domain-that-does-not-exist-99881122.com/video.mp4")
        self.assertIn(res["status"], ["SUCCESS", "FAILED"])
        self.assertEqual(res["direct_url"], "https://dead-domain-that-does-not-exist-99881122.com/video.mp4")

    async def test_11_link_resolver_empty_and_none(self):
        for bad_url in ["", None]:
            res = await self.link_resolver.resolve_auto(bad_url)
            self.assertEqual(res["status"], "FAILED")

    # -------------------------------------------------------------
    # 3. StreamVerifier Adversarial & Edge Case Tests
    # -------------------------------------------------------------
    async def test_12_stream_verifier_youtube_instant(self):
        res = await self.stream_verifier.verify("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertTrue(res["valid"])
        self.assertIn("Direct Video Stream", res["format"])
        self.assertLess(res["elapsed_ms"], 10.0)

    async def test_13_stream_verifier_genuine_mp4(self):
        res = await self.stream_verifier.verify("https://www.w3schools.com/html/mov_bbb.mp4")
        self.assertTrue(res["valid"])
        self.assertEqual(res["format"], "MP4")
        self.assertEqual(res["status_code"], 206)
        self.assertFalse(res["malware_detected"])

    async def test_14_stream_verifier_403_xml_rejection(self):
        # 403 Access Denied XML response must be rejected as invalid stream
        res = await self.stream_verifier.verify("https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4")
        self.assertFalse(res["valid"])

    async def test_15_stream_verifier_dead_and_unresponsive_links(self):
        res = await self.stream_verifier.verify("https://dead-domain-that-does-not-exist-99881122.com/test.mkv")
        self.assertFalse(res["valid"])
        self.assertIn("error", res)

    async def test_16_stream_verifier_empty_and_none(self):
        for bad_url in ["", None]:
            res = await self.stream_verifier.verify(bad_url)
            self.assertFalse(res["valid"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
