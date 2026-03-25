from __future__ import annotations

import hashlib
import hmac
import unittest

from polymarket_lower_win.chainlink_streams import (
    build_ws_auth_headers,
    build_ws_path,
    parse_feed_id_overrides,
    resolve_feed_ids,
)


class ChainlinkStreamsTests(unittest.TestCase):
    def test_parse_feed_id_overrides(self) -> None:
        payload = "btc=0xabc, eth=0xdef ,bad,sol=0x123"
        actual = parse_feed_id_overrides(payload)
        self.assertEqual(
            actual,
            {
                "btc": "0xabc",
                "eth": "0xdef",
                "sol": "0x123",
            },
        )

    def test_build_ws_auth_headers(self) -> None:
        path = build_ws_path(["0xbtc", "0xeth"])
        headers = build_ws_auth_headers(
            "demo-key",
            "demo-secret",
            path,
            timestamp_ms=1234567890,
        )
        empty_hash = hashlib.sha256(b"").hexdigest()
        expected_string = f"GET {path} {empty_hash} demo-key 1234567890"
        expected_signature = hmac.new(
            b"demo-secret",
            expected_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(headers["Authorization"], "demo-key")
        self.assertEqual(headers["X-Authorization-Timestamp"], "1234567890")
        self.assertEqual(headers["X-Authorization-Signature-SHA256"], expected_signature)

    def test_resolve_feed_ids_with_override(self) -> None:
        mapping = resolve_feed_ids(["btc", "eth"], overrides={"eth": "0xcustom"})
        self.assertEqual(mapping["eth"], "0xcustom")
        self.assertTrue(mapping["btc"].startswith("0x"))


if __name__ == "__main__":
    unittest.main()
