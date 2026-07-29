#!/usr/bin/env python3
"""统计 Binance 仅合约域币对数量、24 小时成交额和选中占比。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ModuleNotFoundError as exc:
    print(
        "遇到错误：缺少 Python 包 requests，返回内容：No module named 'requests'\n"
        "请在 PyCharm 底部 Terminal 运行：python3 -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name in {"work", "scripts"} else SCRIPT_DIR
DEFAULT_PROXY = "http://127.0.0.1:7897"
DEFAULT_TIMEOUT = 25
SPOT_EXCHANGE_INFO_URL = "https://data-api.binance.vision/api/v3/exchangeInfo"
FUTURES_EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
FUTURES_24HR_TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"
MULTIPLIER_PREFIXES = ("10000000", "1000000", "100000", "10000", "1000")


def build_session(proxy: str | None) -> requests.Session:
    """创建并复用一个公共市场数据请求实例。"""
    session = requests.Session()
    session.trust_env = False
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": "BinanceFuturesOnlyMetrics/1.0",
            "Accept": "application/json",
        }
    )
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def proxy_candidates(proxy: str | None, no_proxy: bool) -> list[str | None]:
    if no_proxy:
        return [None]
    return [proxy, None] if proxy else [None]


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def fetch_market_json(session: requests.Session, url: str) -> dict[str, Any] | list[Any]:
    try:
        response = session.get(url, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"遇到错误：请求 {url}，返回内容：{exc}") from exc
    except ValueError as exc:
        raise RuntimeError(f"遇到错误：解析 JSON {url}，返回内容：{exc}") from exc
    if not isinstance(payload, (dict, list)):
        raise RuntimeError(f"遇到错误：解析 JSON {url}，返回内容：根节点不是对象或列表")
    return payload


def fetch_market_data(
    proxy: str | None, no_proxy: bool
) -> tuple[dict[str, Any], dict[str, Any], list[Any], str | None]:
    errors: list[str] = []
    for candidate in proxy_candidates(proxy, no_proxy):
        mode = f"代理 {candidate}" if candidate else "直连"
        session = build_session(candidate)
        try:
            spot = fetch_market_json(session, SPOT_EXCHANGE_INFO_URL)
            futures = fetch_market_json(session, FUTURES_EXCHANGE_INFO_URL)
            tickers = fetch_market_json(session, FUTURES_24HR_TICKER_URL)
            if not isinstance(spot, dict) or not isinstance(futures, dict) or not isinstance(tickers, list):
                raise RuntimeError("遇到错误：解析 Binance 市场数据，返回内容：接口根节点类型不正确")
            return spot, futures, tickers, candidate
        except RuntimeError as exc:
            errors.append(f"{mode}失败：{exc}")
    raise RuntimeError(
        "遇到错误：Binance 仅合约域统计抓取失败，返回内容：\n"
        + "\n".join(errors)
        + "\n请检查：1. 代理软件是否打开；2. 官方现货和合约行情接口是否可访问。"
    )


def selected_contract_symbols(path: Path) -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"遇到错误：读取选中合约 {path}，返回内容：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"遇到错误：解析选中合约 {path}，返回内容：{exc}") from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise RuntimeError(f"遇到错误：解析选中合约 {path}，返回内容：items 不是列表")
    return {
        str(item.get("contract_symbol") or "").upper()
        for item in items
        if isinstance(item, dict) and item.get("contract_symbol")
    }


def active_spot_pairs(payload: dict[str, Any]) -> set[tuple[str, str]]:
    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        raise RuntimeError("遇到错误：解析现货交易规则，返回内容：symbols 不是列表")
    return {
        (str(item.get("baseAsset") or "").upper(), str(item.get("quoteAsset") or "").upper())
        for item in symbols
        if isinstance(item, dict)
        and item.get("status") == "TRADING"
        and item.get("isSpotTradingAllowed", True) is not False
        and item.get("baseAsset")
        and item.get("quoteAsset")
    }


def spot_equivalent_base(base_asset: str, quote_asset: str, spot_pairs: set[tuple[str, str]]) -> str:
    """识别 1000PEPE 这类仅在合约代码中带倍率前缀的现货资产。"""
    base = base_asset.upper()
    quote = quote_asset.upper()
    if (base, quote) in spot_pairs:
        return base
    for prefix in MULTIPLIER_PREFIXES:
        if base.startswith(prefix) and len(base) > len(prefix):
            candidate = base[len(prefix) :]
            if (candidate, quote) in spot_pairs:
                return candidate
    return ""


def active_perpetuals(payload: dict[str, Any]) -> list[dict[str, str]]:
    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        raise RuntimeError("遇到错误：解析合约交易规则，返回内容：symbols 不是列表")
    result: list[dict[str, str]] = []
    for item in symbols:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "TRADING" or item.get("contractType") != "PERPETUAL":
            continue
        symbol = str(item.get("symbol") or "").upper()
        base_asset = str(item.get("baseAsset") or "").upper()
        quote_asset = str(item.get("quoteAsset") or "").upper()
        if symbol and base_asset and quote_asset:
            result.append(
                {"symbol": symbol, "base_asset": base_asset, "quote_asset": quote_asset}
            )
    return result


def ticker_volumes(payload: list[Any]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for item in payload:
        if not isinstance(item, dict) or not item.get("symbol"):
            continue
        symbol = str(item["symbol"]).upper()
        raw_volume = item.get("quoteVolume")
        try:
            volume = Decimal(str(raw_volume))
        except (InvalidOperation, TypeError) as exc:
            raise RuntimeError(
                f"遇到错误：解析 {symbol} 24 小时成交额，返回内容：quoteVolume={raw_volume}"
            ) from exc
        if not volume.is_finite() or volume < 0:
            raise RuntimeError(
                f"遇到错误：解析 {symbol} 24 小时成交额，返回内容：数值无效 {raw_volume}"
            )
        result[symbol] = volume
    return result


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def build_metrics_payload(
    spot_payload: dict[str, Any],
    futures_payload: dict[str, Any],
    ticker_payload: list[Any],
    selected_symbols: set[str],
) -> dict[str, Any]:
    spot_pairs = active_spot_pairs(spot_payload)
    perpetuals = active_perpetuals(futures_payload)
    volumes = ticker_volumes(ticker_payload)
    items: list[dict[str, Any]] = []

    for contract in perpetuals:
        symbol = contract["symbol"]
        base_asset = contract["base_asset"]
        quote_asset = contract["quote_asset"]
        spot_base = spot_equivalent_base(base_asset, quote_asset, spot_pairs)
        if spot_base:
            continue
        if symbol not in volumes:
            raise RuntimeError(
                f"遇到错误：计算仅合约域成交额，返回内容：{symbol} 缺少 24 小时 ticker"
            )
        volume = volumes[symbol]
        items.append(
            {
                "symbol": symbol,
                "base_asset": base_asset,
                "quote_asset": quote_asset,
                "quote_volume_24h": decimal_text(volume),
                "selected": symbol in selected_symbols,
                "trade_url": f"https://www.binance.com/en/futures/{symbol}",
            }
        )

    items.sort(key=lambda item: Decimal(item["quote_volume_24h"]), reverse=True)
    total_volume = sum((Decimal(item["quote_volume_24h"]) for item in items), Decimal("0"))
    selected_items = [item for item in items if item["selected"]]
    selected_volume = sum(
        (Decimal(item["quote_volume_24h"]) for item in selected_items), Decimal("0")
    )
    share = Decimal("0") if total_volume == 0 else selected_volume / total_volume * Decimal("100")

    return {
        "schema_version": "1.0",
        "data_type": "binance_futures_only_metrics",
        "status": "ready",
        "public_read_only": True,
        "timezone": "Asia/Shanghai",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "window": "rolling_24h",
        "volume_unit": "USD stablecoin quote-asset equivalent",
        "definition": "当前交易中的 USDⓈ-M 永续合约，且相同基础资产和计价资产没有 Binance 现货交易对。",
        "selected_rule": "data/contracts.json 中收录的合约代码与仅合约域币对的交集。",
        "sources": {
            "spot_exchange_info": SPOT_EXCHANGE_INFO_URL,
            "futures_exchange_info": FUTURES_EXCHANGE_INFO_URL,
            "futures_24h_ticker": FUTURES_24HR_TICKER_URL,
        },
        "summary": {
            "futures_only_pair_count": len(items),
            "futures_only_quote_volume_24h": decimal_text(total_volume),
            "selected_futures_only_pair_count": len(selected_items),
            "selected_futures_only_quote_volume_24h": decimal_text(selected_volume),
            "selected_volume_share_percent": decimal_text(share.quantize(Decimal("0.000001"))),
            "active_perpetual_pair_count": len(perpetuals),
            "active_spot_pair_count": len(spot_pairs),
            "selected_contract_input_count": len(selected_symbols),
        },
        "items": items,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="统计 Binance 仅合约域币对和 24 小时成交额。")
    parser.add_argument("--proxy", default=DEFAULT_PROXY, help=f"代理地址，默认 {DEFAULT_PROXY}")
    parser.add_argument("--no-proxy", action="store_true", help="不走代理，只用直连")
    parser.add_argument(
        "--contracts-json",
        default="binance_public_site/data/contracts.json",
        help="作为我们选中范围的永续合约 JSON",
    )
    parser.add_argument(
        "--json",
        default="outputs/binance_futures_only_metrics.json",
        help="本机统计 JSON",
    )
    parser.add_argument("--public-json", default="", help="可选的公开站统计 JSON")
    args = parser.parse_args()

    try:
        selected = selected_contract_symbols(resolve_path(args.contracts_json))
        spot, futures, tickers, active_proxy = fetch_market_data(args.proxy, args.no_proxy)
        payload = build_metrics_payload(spot, futures, tickers, selected)
        json_path = resolve_path(args.json)
        write_json(json_path, payload)
        public_path = resolve_path(args.public_json) if args.public_json else None
        if public_path:
            write_json(public_path, payload)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    summary = payload["summary"]
    print(f"完成：监控 {summary['futures_only_pair_count']} 个 Binance 仅合约域币对")
    print(f"仅合约域 24 小时成交额：{summary['futures_only_quote_volume_24h']}")
    print(
        "选中币对 24 小时成交额："
        f"{summary['selected_futures_only_quote_volume_24h']}，"
        f"占比 {summary['selected_volume_share_percent']}%"
    )
    print(f"统计 JSON：{json_path}")
    if public_path:
        print(f"公开 JSON：{public_path}")
    print(f"请求方式：{'代理 ' + active_proxy if active_proxy else '直连'}")
    print("数据来源：Binance 官方 Spot / USDⓈ-M Futures 公共市场数据 API")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
