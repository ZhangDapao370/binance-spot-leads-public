import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
SCRIPT_PATH = SCRIPTS_DIR / "binance_perpetual_contract_monitor.py"
SPEC = importlib.util.spec_from_file_location("binance_perpetual_contract_monitor", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def text_node(value):
    return {"node": "text", "text": value}


def cell(value):
    return {"node": "element", "tag": "td", "child": [text_node(value)]}


def row(*values):
    return {"node": "element", "tag": "tr", "child": [cell(value) for value in values]}


def detail_body(*rows):
    return {"node": "root", "child": [{"node": "element", "tag": "table", "child": list(rows)}]}


class BinancePerpetualMonitorTest(unittest.TestCase):
    def test_parses_single_crypto_contract(self):
        body = detail_body(
            row("USDⓈ-M Perpetual Contract", "DATAIPUSDT"),
            row("Launch Time", "2026-07-03 06:45 (UTC)"),
            row("Underlying Asset", "DATAIP (Data Network)"),
            row("Project Info", "The DATA Network is an open data protocol."),
            row("Settlement Asset", "USDT"),
            row("Maximum Leverage", "20x"),
            row("Capped Funding Rate", "+2.00% / -2.00%"),
            row("Funding Fee Settlement Frequency", "Every Four Hours"),
            row("Tick Size", "0.0001"),
            row("Minimum Trade Amount", "1 DATAIP"),
            row("Minimum Notional Value", "5 USDT"),
            row("Trading Hours", "24/7"),
        )
        article = {
            "code": "crypto-code",
            "title": "Binance Futures Will Launch USDⓈ-Margined DATAIPUSDT Perpetual Contract",
            "releaseDate": 1783056627549,
        }
        detail = {**article, "body": json.dumps(body)}
        contracts = MODULE.parse_detail_article(article, detail)
        self.assertEqual(len(contracts), 1)
        contract = contracts[0]
        self.assertEqual(contract.contract_symbol, "DATAIPUSDT")
        self.assertEqual(contract.base_symbol, "DATAIP")
        self.assertEqual(contract.asset_type, "加密资产")
        self.assertEqual(contract.launch_time, "2026-07-03T06:45:00Z")
        self.assertEqual(contract.maximum_leverage, "20x")
        self.assertEqual(contract.min_trade_amount, "1 DATAIP")

    def test_splits_multiple_tradfi_contracts(self):
        body = detail_body(
            row("Contract Type", "USDT-Priced", "Quanto"),
            row("USDⓈ-M Perpetual Contract", "MINIMAXUSDT", "HK0700USDT"),
            row("Launch Time", "2026-07-17 03:00 (UTC)", "2026-07-23 03:00 (UTC)"),
            row("Underlying Equity/Index", "MiniMax Group Inc", "Tencent Holdings Limited"),
            row("Underlying Equity/Index Info", "MiniMax description", "Tencent description"),
            row("Settlement Asset", "USDT", "USDT"),
            row("Maximum Leverage", "25x", "25x"),
        )
        article = {
            "code": "tradfi-code",
            "title": "Binance Futures Will Launch Multiple USDⓈ-Margined TradFi Perpetual Contracts",
            "releaseDate": 1784201410537,
        }
        detail = {**article, "body": json.dumps(body)}
        contracts = MODULE.parse_detail_article(article, detail)
        self.assertEqual([item.contract_symbol for item in contracts], ["MINIMAXUSDT", "HK0700USDT"])
        self.assertEqual([item.product_variant for item in contracts], ["USDT-Priced", "Quanto"])
        self.assertEqual([item.underlying for item in contracts], ["MiniMax Group Inc", "Tencent Holdings Limited"])
        self.assertEqual([item.project_info for item in contracts], ["MiniMax description", "Tencent description"])
        self.assertTrue(all(item.asset_type == "股票" for item in contracts))

    def test_classifies_pre_ipo_and_rejects_non_launch_notices(self):
        body = detail_body(
            row("USDⓈ-M Perpetual Contract", "ANTHROPICUSDT"),
            row("Launch Time", "2026-06-02 04:30 (UTC)"),
            row("Underlying Equity", "Anthropic PBC"),
            row("Settlement Asset", "USDT"),
        )
        article = {
            "code": "pre-ipo-code",
            "title": "Binance Futures Will Launch ANTHROPICUSDT USDⓈ-Margined Perpetual Contract Pre-IPO Trading",
            "releaseDate": 1780369288377,
        }
        detail = {**article, "body": json.dumps(body)}
        contract = MODULE.parse_detail_article(article, detail)[0]
        self.assertEqual(contract.launch_stage, "pre_ipo")
        self.assertEqual(contract.asset_type, "股票")
        self.assertFalse(
            MODULE.is_perpetual_candidate(
                "Binance Futures Will Delist TESTUSDT Perpetual Contract"
            )
        )
        self.assertFalse(
            MODULE.is_perpetual_candidate(
                "Updates on Tick Size for USDⓈ-M Perpetual Futures Contracts"
            )
        )

    def test_public_payload_is_read_only(self):
        contract = MODULE.PerpetualContract(
            announcement_code="code",
            title="Binance Futures Will Launch TESTUSDT Perpetual Contract",
            contract_symbol="TESTUSDT",
            base_symbol="TEST",
            listing_type="perpetual",
            margin_type="USDⓈ-M",
            product_variant="",
            asset_type="加密资产",
            launch_stage="standard",
            published_at="2026-07-20T08:00:00Z",
            launch_time="2026-07-20T09:00:00Z",
            underlying="Test Token",
            settlement_asset="USDT",
            maximum_leverage="25x",
            capped_funding_rate="+2.00% / -2.00%",
            funding_interval="Every Four Hours",
            tick_size="0.001",
            min_trade_amount="1 TEST",
            min_notional_value="5 USDT",
            trading_hours="24/7",
            project_info="Test project.",
            trade_url="https://www.binance.com/en/futures/TESTUSDT",
            announcement_url="https://www.binance.com/en/support/announcement/detail/code",
        )
        payload = MODULE.build_public_payload([contract])
        self.assertTrue(payload["public_read_only"])
        self.assertEqual(payload["data_type"], "binance_perpetual_contracts")
        self.assertEqual(payload["summary"]["total"], 1)
        self.assertNotIn("workflow_status", payload["items"][0])
        self.assertNotIn("notes", payload["items"][0])

    def test_waits_and_retries_after_rate_limit(self):
        expected = {"code": "000000", "data": {"code": "article-code"}}
        with (
            patch.object(
                MODULE,
                "fetch_json",
                side_effect=[RuntimeError("429 Too Many Requests"), expected],
            ) as fetch_mock,
            patch.object(MODULE.time, "sleep") as sleep_mock,
        ):
            payload = MODULE.fetch_detail_with_rate_limit(Mock(), "article-code")

        self.assertEqual(payload, expected)
        self.assertEqual(fetch_mock.call_count, 2)
        self.assertEqual(
            sleep_mock.call_args_list,
            [
                call(MODULE.DETAIL_REQUEST_DELAY_SECONDS),
                call(MODULE.RATE_LIMIT_RETRY_SECONDS[0]),
                call(MODULE.DETAIL_REQUEST_DELAY_SECONDS),
            ],
        )


if __name__ == "__main__":
    unittest.main()
