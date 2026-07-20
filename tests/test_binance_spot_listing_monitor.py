import importlib.util
import json
import sys
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "binance_spot_listing_monitor.py"
SPEC = importlib.util.spec_from_file_location("binance_spot_listing_monitor", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def text_node(value):
    return {"node": "text", "text": value}


def element(tag, text="", href=None):
    node = {"node": "element", "tag": tag, "child": [text_node(text)] if text else []}
    if href:
        node["attr"] = {"href": href}
    return node


class BinanceSpotMonitorTest(unittest.TestCase):
    def test_extracts_single_and_multiple_projects(self):
        single = MODULE.extract_title_projects(
            "Binance Will List Aerodrome (AERO) with Seed Tag Applied"
        )
        self.assertEqual([(item.project_name, item.symbol) for item in single], [("Aerodrome", "AERO")])

        multiple = MODULE.extract_title_projects(
            "Binance Will List Genius Terminal (GENIUS) and OpenGradient (OPG) with Seed Tag Applied"
        )
        self.assertEqual(
            [(item.project_name, item.symbol) for item in multiple],
            [("Genius Terminal", "GENIUS"), ("OpenGradient", "OPG")],
        )

    def test_rejects_futures_and_generic_pair_notices(self):
        self.assertEqual(
            MODULE.extract_title_projects(
                "Binance Futures Will List USDⓈ-Margined TESTUSDT Perpetual Contract"
            ),
            [],
        )
        self.assertEqual(
            MODULE.extract_title_projects(
                "Notice on New Trading Pairs & Trading Bots Services on Binance Spot"
            ),
            [],
        )

    def test_parses_official_spot_listing_fields(self):
        body = {
            "node": "root",
            "child": [
                element(
                    "p",
                    "Binance will list Aerodrome (AERO) and open trading for the following "
                    "spot trading pairs at 2026-07-17 11:00 (UTC).",
                ),
                element("p", "New Spot Trading Pairs: AERO /USDT, AERO /USDC and AERO /TRY"),
                element("h3", "What Is Aerodrome:"),
                element("p", "Aerodrome is the trading and liquidity hub of Base."),
                element("h3", "Additional Information:"),
                element("a", "Aerodrome Website", "https://aerodrome.finance/"),
                element("a", "Base", "https://basescan.org/token/0x123"),
                element("a", "Binance Fees", "https://www.binance.com/en/fee/schedule"),
            ],
        }
        article = {
            "code": "abc123",
            "title": "Binance Will List Aerodrome (AERO) with Seed Tag Applied",
            "releaseDate": 1784273401964,
        }
        detail = {
            **article,
            "publishDate": 1784273401964,
            "body": json.dumps(body),
        }
        listings = MODULE.parse_detail_article(article, detail)
        self.assertEqual(len(listings), 1)
        listing = listings[0]
        self.assertEqual(listing.symbol, "AERO")
        self.assertEqual(listing.spot_pairs, ["AERO/USDT", "AERO/USDC", "AERO/TRY"])
        self.assertEqual(listing.trading_starts_at, "2026-07-17T11:00:00Z")
        self.assertEqual(listing.project_links, ["https://aerodrome.finance/"])
        self.assertEqual(listing.contract_links, ["https://basescan.org/token/0x123"])
        self.assertTrue(listing.seed_tag)
        self.assertIn("liquidity hub", listing.summary)

    def test_requires_spot_confirmation_in_body(self):
        body = {"node": "root", "child": [element("p", "A perpetual futures contract will launch.")]}
        article = {
            "code": "not-spot",
            "title": "Binance Will List Example (TEST) with Seed Tag Applied",
            "releaseDate": 1784273401964,
        }
        detail = {**article, "body": json.dumps(body)}
        self.assertEqual(MODULE.parse_detail_article(article, detail), [])

    def test_public_payload_has_stable_read_only_shape(self):
        listing = MODULE.SpotListing(
            announcement_code="abc123",
            title="Binance Will List Aerodrome (AERO) with Seed Tag Applied",
            project_name="Aerodrome",
            symbol="AERO",
            listing_type="spot",
            published_at="2026-07-17T07:30:01Z",
            trading_starts_at="2026-07-17T11:00:00Z",
            spot_pairs=["AERO/USDT"],
            seed_tag=True,
            announcement_url="https://www.binance.com/en/support/announcement/detail/abc123",
            project_links=["https://aerodrome.finance/"],
            contact_links=[],
            contract_links=[],
            summary="Aerodrome is the trading and liquidity hub of Base.",
            contact_status="部分",
            reason="已找到项目官网，社区或联系入口需要继续确认。",
        )
        payload = MODULE.build_public_payload([listing])
        self.assertTrue(payload["public_read_only"])
        self.assertEqual(payload["source"]["name"], "Binance")
        self.assertEqual(payload["summary"]["total"], 1)
        self.assertNotIn("workflow_status", payload["items"][0])
        self.assertNotIn("notes", payload["items"][0])


if __name__ == "__main__":
    unittest.main()
