import importlib.util
import sys
import unittest
from decimal import Decimal
from pathlib import Path


WORK_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(WORK_DIR))
SCRIPT_PATH = WORK_DIR / "binance_futures_only_metrics.py"
SPEC = importlib.util.spec_from_file_location("binance_futures_only_metrics", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class BinanceFuturesOnlyMetricsTest(unittest.TestCase):
    def setUp(self):
        self.spot = {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "isSpotTradingAllowed": True,
                },
                {
                    "symbol": "PEPEUSDT",
                    "status": "TRADING",
                    "baseAsset": "PEPE",
                    "quoteAsset": "USDT",
                    "isSpotTradingAllowed": True,
                },
            ]
        }
        self.futures = {
            "symbols": [
                {"symbol": "BTCUSDT", "status": "TRADING", "contractType": "PERPETUAL", "baseAsset": "BTC", "quoteAsset": "USDT"},
                {"symbol": "1000PEPEUSDT", "status": "TRADING", "contractType": "PERPETUAL", "baseAsset": "1000PEPE", "quoteAsset": "USDT"},
                {"symbol": "NEWUSDT", "status": "TRADING", "contractType": "PERPETUAL", "baseAsset": "NEW", "quoteAsset": "USDT"},
                {"symbol": "STOCKUSD1", "status": "TRADING", "contractType": "PERPETUAL", "baseAsset": "STOCK", "quoteAsset": "USD1"},
                {"symbol": "ONLYUSDT", "status": "TRADING", "contractType": "PERPETUAL", "baseAsset": "ONLY", "quoteAsset": "USDT"},
                {"symbol": "OLDUSDT", "status": "CLOSE", "contractType": "PERPETUAL", "baseAsset": "OLD", "quoteAsset": "USDT"},
                {"symbol": "DELIVERYUSDT", "status": "TRADING", "contractType": "CURRENT_QUARTER", "baseAsset": "DELIVERY", "quoteAsset": "USDT"},
            ]
        }
        self.tickers = [
            {"symbol": "BTCUSDT", "quoteVolume": "900"},
            {"symbol": "1000PEPEUSDT", "quoteVolume": "800"},
            {"symbol": "NEWUSDT", "quoteVolume": "100"},
            {"symbol": "STOCKUSD1", "quoteVolume": "200"},
            {"symbol": "ONLYUSDT", "quoteVolume": "300"},
        ]

    def test_calculates_four_required_metrics(self):
        payload = MODULE.build_metrics_payload(
            self.spot,
            self.futures,
            self.tickers,
            {"NEWUSDT", "STOCKUSD1"},
        )
        summary = payload["summary"]
        self.assertEqual(summary["futures_only_pair_count"], 3)
        self.assertEqual(summary["futures_only_quote_volume_24h"], "600")
        self.assertEqual(summary["selected_futures_only_pair_count"], 2)
        self.assertEqual(summary["selected_futures_only_quote_volume_24h"], "300")
        self.assertEqual(summary["selected_volume_share_percent"], "50")
        self.assertEqual([item["symbol"] for item in payload["items"]], ["ONLYUSDT", "STOCKUSD1", "NEWUSDT"])
        self.assertEqual(payload["status"], "ready")
        self.assertTrue(payload["public_read_only"])

    def test_multiplier_contract_matches_spot_asset(self):
        spot_pairs = {("PEPE", "USDT")}
        self.assertEqual(MODULE.spot_equivalent_base("1000PEPE", "USDT", spot_pairs), "PEPE")
        self.assertEqual(MODULE.spot_equivalent_base("1000PEPE", "USDC", spot_pairs), "")

    def test_excludes_non_trading_and_delivery_contracts(self):
        symbols = MODULE.active_perpetuals(self.futures)
        names = {item["symbol"] for item in symbols}
        self.assertNotIn("OLDUSDT", names)
        self.assertNotIn("DELIVERYUSDT", names)

    def test_rejects_missing_ticker_instead_of_publishing_false_zero(self):
        with self.assertRaisesRegex(RuntimeError, "ONLYUSDT 缺少 24 小时 ticker"):
            MODULE.build_metrics_payload(
                self.spot,
                self.futures,
                self.tickers[:-1],
                {"NEWUSDT"},
            )

    def test_rejects_invalid_volume(self):
        with self.assertRaisesRegex(RuntimeError, "quoteVolume=not-a-number"):
            MODULE.ticker_volumes([{"symbol": "BADUSDT", "quoteVolume": "not-a-number"}])
        self.assertEqual(MODULE.ticker_volumes([{"symbol": "OKUSDT", "quoteVolume": "1.25"}])["OKUSDT"], Decimal("1.25"))


if __name__ == "__main__":
    unittest.main()
