import importlib.util
import io
import json
import sys
import tempfile
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
                {
                    "symbol": "ETHBTC",
                    "status": "TRADING",
                    "baseAsset": "ETH",
                    "quoteAsset": "BTC",
                    "isSpotTradingAllowed": True,
                },
            ]
        }
        self.contracts = [
            {"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT"},
            {"symbol": "1000PEPEUSDT", "base_asset": "1000PEPE", "quote_asset": "USDT"},
            {"symbol": "NEWUSDT", "base_asset": "NEW", "quote_asset": "USDT"},
            {"symbol": "STOCKUSD1", "base_asset": "STOCK", "quote_asset": "USD1"},
            {"symbol": "ONLYUSDT", "base_asset": "ONLY", "quote_asset": "USDT"},
            {"symbol": "ETHBTC", "base_asset": "ETH", "quote_asset": "BTC"},
        ]
        self.volumes = {
            "BTCUSDT": Decimal("900"),
            "1000PEPEUSDT": Decimal("800"),
            "NEWUSDT": Decimal("100"),
            "STOCKUSD1": Decimal("200"),
            "ONLYUSDT": Decimal("300"),
            "ETHBTC": Decimal("50"),
        }
        self.selection = {
            "schema_version": "1.0",
            "data_type": "binance_strategy_selected_pairs",
            "status": "ready",
            "read_only_export": True,
            "generated_at": "2026-07-27T12:00:00Z",
            "source_earliest_at": "2026-07-27T19:00:00+08:00",
            "source_latest_at": "2026-07-27T20:00:00+08:00",
            "summary": {
                "selected_pair_count": 2,
                "source_count": 11,
                "account_count": 29,
            },
            "items": [
                {"symbol": "NEWUSDT"},
                {"symbol": "STOCKUSD1"},
            ],
        }

    def test_calculates_four_required_metrics(self):
        payload = MODULE.build_metrics_payload(
            self.spot,
            self.contracts,
            self.volumes,
            self.selection,
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
        self.assertEqual(payload["schema_version"], "4.0")
        self.assertTrue(payload["public_read_only"])
        self.assertEqual(summary["strategy_selected_pair_count"], 2)
        self.assertEqual(payload["selection_snapshot"]["account_count"], 29)
        self.assertTrue(all("selected" not in item for item in payload["items"]))

    def test_multiplier_contract_matches_spot_asset(self):
        spot_pairs = {("PEPE", "USDT")}
        self.assertEqual(MODULE.spot_equivalent_base("1000PEPE", "USDT", spot_pairs), "PEPE")
        self.assertEqual(MODULE.spot_equivalent_base("1000PEPE", "USDC", spot_pairs), "")

    def test_parses_perpetual_archive_symbols_and_excludes_delivery(self):
        self.assertEqual(
            MODULE.archive_contract("1000PEPEUSDT"),
            {"symbol": "1000PEPEUSDT", "base_asset": "1000PEPE", "quote_asset": "USDT"},
        )
        self.assertIsNone(MODULE.archive_contract("BTCUSDT_260925"))
        self.assertEqual(MODULE.archive_contract("BTCU")["quote_asset"], "U")
        self.assertEqual(MODULE.archive_contract("ETHBTC")["quote_asset"], "BTC")
        with self.assertRaisesRegex(RuntimeError, "无法识别计价资产"):
            MODULE.archive_contract("UNKNOWNPAIR")

    def test_rejects_missing_daily_volume_instead_of_publishing_false_zero(self):
        with self.assertRaisesRegex(RuntimeError, "ONLYUSDT 缺少完整日成交额"):
            MODULE.build_metrics_payload(
                self.spot,
                self.contracts,
                {key: value for key, value in self.volumes.items() if key != "ONLYUSDT"},
                self.selection,
                date(2026, 7, 27),
            )

    def test_rejects_invalid_volume(self):
        invalid = dict(self.volumes)
        invalid["ONLYUSDT"] = Decimal("NaN")
        with self.assertRaisesRegex(RuntimeError, "数值无效 NaN"):
            MODULE.build_metrics_payload(
                self.spot,
                self.contracts,
                invalid,
                self.selection,
                date(2026, 7, 27),
            )

    def test_reads_valid_private_selection_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "selected.json"
            path.write_text(json.dumps(self.selection), encoding="utf-8")
            payload = MODULE.selected_pair_snapshot(path)
        self.assertEqual(
            MODULE.selected_pair_symbols(payload),
            {"NEWUSDT", "STOCKUSD1"},
        )

    def test_rejects_inconsistent_private_selection_snapshot(self):
        invalid = dict(self.selection)
        invalid["summary"] = dict(self.selection["summary"])
        invalid["summary"]["selected_pair_count"] = 99
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "selected.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "summary 与明细不一致"):
                MODULE.selected_pair_snapshot(path)

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

    def test_lists_symbols_from_official_archive_xml(self):
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
          <IsTruncated>false</IsTruncated>
          <CommonPrefixes><Prefix>data/futures/um/daily/klines/BTCUSDT/</Prefix></CommonPrefixes>
          <CommonPrefixes><Prefix>data/futures/um/daily/klines/ONLYUSDT/</Prefix></CommonPrefixes>
        </ListBucketResult>"""

        class Response:
            content = xml

            @staticmethod
            def raise_for_status():
                return None

        class Session:
            @staticmethod
            def get(url, params, timeout):
                return Response()

        self.assertEqual(MODULE.list_archive_symbols(Session()), ["BTCUSDT", "ONLYUSDT"])


if __name__ == "__main__":
    unittest.main()
