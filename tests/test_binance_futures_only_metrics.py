import importlib.util
import io
import sys
import unittest
import zipfile
from datetime import date
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
        self.volumes = {
            "BTCUSDT": Decimal("900"),
            "1000PEPEUSDT": Decimal("800"),
            "NEWUSDT": Decimal("100"),
            "STOCKUSD1": Decimal("200"),
            "ONLYUSDT": Decimal("300"),
        }

    def test_calculates_four_required_metrics(self):
        payload = MODULE.build_metrics_payload(
            self.spot,
            self.futures,
            self.volumes,
            {"NEWUSDT", "STOCKUSD1"},
            date(2026, 7, 27),
        )
        summary = payload["summary"]
        self.assertEqual(summary["futures_only_pair_count"], 3)
        self.assertEqual(summary["futures_only_quote_volume_24h"], "600")
        self.assertEqual(summary["selected_futures_only_pair_count"], 2)
        self.assertEqual(summary["selected_futures_only_quote_volume_24h"], "300")
        self.assertEqual(summary["selected_volume_share_percent"], "50")
        self.assertEqual([item["symbol"] for item in payload["items"]], ["ONLYUSDT", "STOCKUSD1", "NEWUSDT"])
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["window"], "latest_complete_utc_day")
        self.assertEqual(payload["volume_date"], "2026-07-27")
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

    def test_rejects_missing_daily_volume_instead_of_publishing_false_zero(self):
        with self.assertRaisesRegex(RuntimeError, "ONLYUSDT 缺少完整日成交额"):
            MODULE.build_metrics_payload(
                self.spot,
                self.futures,
                {key: value for key, value in self.volumes.items() if key != "ONLYUSDT"},
                {"NEWUSDT"},
                date(2026, 7, 27),
            )

    def test_rejects_invalid_volume(self):
        invalid = dict(self.volumes)
        invalid["ONLYUSDT"] = Decimal("NaN")
        with self.assertRaisesRegex(RuntimeError, "数值无效 NaN"):
            MODULE.build_metrics_payload(
                self.spot,
                self.futures,
                invalid,
                {"NEWUSDT"},
                date(2026, 7, 27),
            )

    def test_parses_official_daily_archive_quote_volume(self):
        csv_text = (
            "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
            "taker_buy_volume,taker_buy_quote_volume,ignore\n"
            "1785110400000,1,2,0.5,1.5,10,1785196799999,1234.56,20,5,600,0\n"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("ONLYUSDT-1d-2026-07-27.csv", csv_text)
        volume = MODULE.parse_archive_quote_volume(
            buffer.getvalue(), "ONLYUSDT", date(2026, 7, 27)
        )
        self.assertEqual(volume, Decimal("1234.56"))

        with self.assertRaisesRegex(RuntimeError, "日线归档"):
            MODULE.parse_archive_quote_volume(b"not-a-zip", "BADUSDT", date(2026, 7, 27))


if __name__ == "__main__":
    unittest.main()
