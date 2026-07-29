#!/usr/bin/env python3
"""统计 Binance 仅合约域币对数量、完整日成交额和选中占比。"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import threading
import xml.etree.ElementTree as ElementTree
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
DEFAULT_TIMEOUT = 45
ARCHIVE_LOOKBACK_DAYS = 7
ARCHIVE_WORKERS = 12
SPOT_EXCHANGE_INFO_URL = "https://data-api.binance.vision/api/v3/exchangeInfo"
ARCHIVE_BUCKET_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
ARCHIVE_SYMBOL_PREFIX = "data/futures/um/daily/klines/"
FUTURES_ARCHIVE_TEMPLATE = (
    "https://data.binance.vision/data/futures/um/daily/klines/"
    "{symbol}/1d/{symbol}-1d-{volume_date}.zip"
)
QUOTE_ASSETS = ("FDUSD", "USDT", "USDC", "BUSD", "TUSD", "USD1")
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
    adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": "BinanceFuturesOnlyMetrics/3.0",
            "Accept": "application/json, application/zip, application/xml, text/xml",
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


def fetch_spot_structure(
    proxy: str | None, no_proxy: bool
) -> tuple[dict[str, Any], requests.Session, str | None]:
    errors: list[str] = []
    for candidate in proxy_candidates(proxy, no_proxy):
        mode = f"代理 {candidate}" if candidate else "直连"
        session = build_session(candidate)
        try:
            spot = fetch_market_json(session, SPOT_EXCHANGE_INFO_URL)
            if not isinstance(spot, dict):
                raise RuntimeError("遇到错误：解析 Binance 现货交易规则，返回内容：根节点不是对象")
            return spot, session, candidate
        except RuntimeError as exc:
            errors.append(f"{mode}失败：{exc}")
    raise RuntimeError(
        "遇到错误：Binance 现货交易规则抓取失败，返回内容：\n"
        + "\n".join(errors)
        + "\n请检查：1. 代理软件是否打开；2. Binance 官方数据接口是否可访问。"
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
    quote_asset = quote_asset.upper()
    if (base, quote_asset) in spot_pairs:
        return base
    for prefix in MULTIPLIER_PREFIXES:
        if base.startswith(prefix) and len(base) > len(prefix):
            candidate = base[len(prefix) :]
            if (candidate, quote_asset) in spot_pairs:
                return candidate
    return ""


def archive_contract(symbol: str) -> dict[str, str] | None:
    """从 USDⓈ-M 归档代码拆出基础资产和计价资产，交割合约返回空。"""
    symbol = symbol.upper()
    if "_" in symbol:
        return None
    for quote_asset in sorted(QUOTE_ASSETS, key=len, reverse=True):
        if symbol.endswith(quote_asset) and len(symbol) > len(quote_asset):
            return {
                "symbol": symbol,
                "base_asset": symbol[: -len(quote_asset)],
                "quote_asset": quote_asset,
            }
    raise RuntimeError(
        f"遇到错误：解析 Binance USDⓈ-M 归档代码 {symbol}，返回内容：无法识别计价资产"
    )


def xml_text(element: ElementTree.Element, local_name: str) -> str:
    for child in element.iter():
        if child.tag.rsplit("}", 1)[-1] == local_name:
            return child.text or ""
    return ""


def list_archive_symbols(session: requests.Session) -> list[str]:
    """从 Binance 官方 S3 目录读取全部 USDⓈ-M 日线币对目录。"""
    marker = ""
    symbols: set[str] = set()
    for page in range(1, 21):
        params = {"delimiter": "/", "prefix": ARCHIVE_SYMBOL_PREFIX}
        if marker:
            params["marker"] = marker
        try:
            response = session.get(ARCHIVE_BUCKET_URL, params=params, timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
        except requests.RequestException as exc:
            raise RuntimeError(
                f"遇到错误：请求 Binance USDⓈ-M 归档目录，返回内容：{exc}"
            ) from exc
        except ElementTree.ParseError as exc:
            raise RuntimeError(
                f"遇到错误：解析 Binance USDⓈ-M 归档目录 XML，返回内容：{exc}"
            ) from exc

        page_prefixes: list[str] = []
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] != "CommonPrefixes":
                continue
            prefix = xml_text(element, "Prefix")
            if not prefix.startswith(ARCHIVE_SYMBOL_PREFIX):
                continue
            symbol = prefix[len(ARCHIVE_SYMBOL_PREFIX) :].strip("/").upper()
            if symbol:
                symbols.add(symbol)
                page_prefixes.append(prefix)

        if xml_text(root, "IsTruncated").lower() != "true":
            if not symbols:
                raise RuntimeError(
                    "遇到错误：解析 Binance USDⓈ-M 归档目录，返回内容：没有币对目录"
                )
            return sorted(symbols)
        next_marker = xml_text(root, "NextMarker")
        marker = next_marker or (page_prefixes[-1] if page_prefixes else "")
        if not marker:
            raise RuntimeError(
                "遇到错误：解析 Binance USDⓈ-M 归档目录，返回内容：分页缺少 marker"
            )

    raise RuntimeError(
        "遇到错误：解析 Binance USDⓈ-M 归档目录，返回内容：分页超过 20 页"
    )


def archive_url(symbol: str, volume_date: date) -> str:
    safe_symbol = quote(symbol.upper(), safe="")
    return FUTURES_ARCHIVE_TEMPLATE.format(
        symbol=safe_symbol, volume_date=volume_date.isoformat()
    )


def fetch_archive_content(
    session: requests.Session, symbol: str, volume_date: date
) -> bytes | None:
    url = archive_url(symbol, volume_date)
    try:
        response = session.get(url, timeout=DEFAULT_TIMEOUT)
        if response.status_code == 404:
            return None
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"遇到错误：请求 Binance 官方日线归档 {url}，返回内容：{exc}") from exc
    return response.content


def parse_archive_quote_volume(content: bytes, symbol: str, volume_date: date) -> Decimal:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            if len(names) != 1:
                raise ValueError(f"ZIP 文件数量不是 1，而是 {len(names)}")
            text = archive.read(names[0]).decode("utf-8-sig")
    except (OSError, UnicodeError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise RuntimeError(
            f"遇到错误：解析 {symbol} {volume_date.isoformat()} 日线归档，返回内容：{exc}"
        ) from exc

    rows = list(csv.reader(io.StringIO(text)))
    if rows and rows[0] and rows[0][0] == "open_time":
        rows = rows[1:]
    if not rows:
        raise RuntimeError(
            f"遇到错误：解析 {symbol} {volume_date.isoformat()} 日线归档，返回内容：CSV 没有数据行"
        )

    total = Decimal("0")
    for row in rows:
        if len(row) < 8:
            raise RuntimeError(
                f"遇到错误：解析 {symbol} {volume_date.isoformat()} 日线归档，返回内容：CSV 列数不足"
            )
        try:
            volume = Decimal(row[7])
        except (InvalidOperation, TypeError) as exc:
            raise RuntimeError(
                f"遇到错误：解析 {symbol} {volume_date.isoformat()} 日线成交额，"
                f"返回内容：quote_volume={row[7]}"
            ) from exc
        if not volume.is_finite() or volume < 0:
            raise RuntimeError(
                f"遇到错误：解析 {symbol} {volume_date.isoformat()} 日线成交额，"
                f"返回内容：数值无效 {row[7]}"
            )
        total += volume
    return total


def latest_archive_date(session: requests.Session, today: date | None = None) -> date:
    reference = today or datetime.now(timezone.utc).date()
    checked: list[str] = []
    for days_back in range(1, ARCHIVE_LOOKBACK_DAYS + 1):
        candidate = reference - timedelta(days=days_back)
        content = fetch_archive_content(session, "BTCUSDT", candidate)
        checked.append(candidate.isoformat())
        if content is None:
            continue
        parse_archive_quote_volume(content, "BTCUSDT", candidate)
        return candidate
    raise RuntimeError(
        "遇到错误：查找 Binance 最近完整日线归档，返回内容："
        f"最近 {ARCHIVE_LOOKBACK_DAYS} 天均不存在，已检查 {', '.join(checked)}"
    )


def archive_day_contracts(
    symbols: list[str], volume_date: date, proxy: str | None
) -> tuple[list[dict[str, str]], dict[str, Decimal]]:
    """并发探测目标日归档，存在文件的非交割合约即该日币对快照。"""
    local_state = threading.local()

    def thread_session() -> requests.Session:
        if not hasattr(local_state, "session"):
            local_state.session = build_session(proxy)
        return local_state.session

    def read_symbol(symbol: str) -> tuple[dict[str, str], Decimal] | None:
        content = fetch_archive_content(thread_session(), symbol, volume_date)
        if content is None:
            return None
        contract = archive_contract(symbol)
        if contract is None:
            return None
        volume = parse_archive_quote_volume(content, symbol, volume_date)
        return contract, volume

    contracts: list[dict[str, str]] = []
    volumes: dict[str, Decimal] = {}
    workers = min(ARCHIVE_WORKERS, max(1, len(symbols)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(read_symbol, symbol): symbol for symbol in symbols}
        for completed, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            if result:
                contract, volume = result
                contracts.append(contract)
                volumes[contract["symbol"]] = volume
            if completed % 200 == 0 or completed == len(symbols):
                print(
                    f"进度：已检查 {completed}/{len(symbols)} 个归档目录，"
                    f"目标日有 {len(contracts)} 个永续币对"
                )
    contracts.sort(key=lambda item: item["symbol"])
    if not contracts:
        raise RuntimeError(
            f"遇到错误：读取 Binance {volume_date.isoformat()} USDⓈ-M 日线归档，"
            "返回内容：没有找到永续币对"
        )
    return contracts, volumes


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def build_metrics_payload(
    spot_payload: dict[str, Any],
    contracts: list[dict[str, str]],
    volumes: dict[str, Decimal],
    selected_symbols: set[str],
    volume_date: date,
) -> dict[str, Any]:
    spot_pairs = active_spot_pairs(spot_payload)
    futures_only = [
        contract
        for contract in contracts
        if not spot_equivalent_base(
            contract["base_asset"], contract["quote_asset"], spot_pairs
        )
    ]
    items: list[dict[str, Any]] = []

    for contract in futures_only:
        symbol = contract["symbol"]
        if symbol not in volumes:
            raise RuntimeError(
                f"遇到错误：计算仅合约域成交额，返回内容：{symbol} 缺少完整日成交额"
            )
        volume = volumes[symbol]
        if not volume.is_finite() or volume < 0:
            raise RuntimeError(
                f"遇到错误：计算 {symbol} 完整日成交额，返回内容：数值无效 {volume}"
            )
        items.append(
            {
                "symbol": symbol,
                "base_asset": contract["base_asset"],
                "quote_asset": contract["quote_asset"],
                "quote_volume_24h": decimal_text(volume),
                "volume_date": volume_date.isoformat(),
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
    period_start = datetime.combine(volume_date, time.min, tzinfo=timezone.utc)
    period_end = period_start + timedelta(days=1)

    return {
        "schema_version": "3.0",
        "data_type": "binance_futures_only_metrics",
        "status": "ready",
        "public_read_only": True,
        "timezone": "Asia/Shanghai",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "window": "latest_complete_utc_day",
        "volume_date": volume_date.isoformat(),
        "period_start": period_start.isoformat().replace("+00:00", "Z"),
        "period_end": period_end.isoformat().replace("+00:00", "Z"),
        "volume_unit": "USD stablecoin quote-asset equivalent",
        "definition": "在目标完整 UTC 日有 Binance USDⓈ-M 永续归档，且当前没有相同基础资产和计价资产的 Binance 现货交易对。",
        "selected_rule": "data/contracts.json 中收录的合约代码与仅合约域币对的交集。",
        "snapshot_rule": "币对数量和成交额均以 volume_date 的 Binance 官方 1d 归档为准，交割合约目录除外。",
        "sources": {
            "spot_exchange_info": SPOT_EXCHANGE_INFO_URL,
            "futures_archive_directory": ARCHIVE_BUCKET_URL,
            "daily_kline_archive": FUTURES_ARCHIVE_TEMPLATE,
        },
        "summary": {
            "futures_only_pair_count": len(items),
            "futures_only_quote_volume_24h": decimal_text(total_volume),
            "selected_futures_only_pair_count": len(selected_items),
            "selected_futures_only_quote_volume_24h": decimal_text(selected_volume),
            "selected_volume_share_percent": decimal_text(share.quantize(Decimal("0.000001"))),
            "active_perpetual_pair_count": len(contracts),
            "active_spot_pair_count": len(spot_pairs),
            "selected_contract_input_count": len(selected_symbols),
        },
        "items": items,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="统计 Binance 仅合约域币对和完整日成交额。")
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
        spot, session, active_proxy = fetch_spot_structure(args.proxy, args.no_proxy)
        volume_date = latest_archive_date(session)
        archive_symbols = list_archive_symbols(session)
        contracts, volumes = archive_day_contracts(
            archive_symbols, volume_date, active_proxy
        )
        payload = build_metrics_payload(
            spot, contracts, volumes, selected, volume_date
        )
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
    print(
        f"{payload['volume_date']} 完整日成交额："
        f"{summary['futures_only_quote_volume_24h']}"
    )
    print(
        "选中币对完整日成交额："
        f"{summary['selected_futures_only_quote_volume_24h']}，"
        f"占比 {summary['selected_volume_share_percent']}%"
    )
    print(f"统计 JSON：{json_path}")
    if public_path:
        print(f"公开 JSON：{public_path}")
    print(f"请求方式：{'代理 ' + active_proxy if active_proxy else '直连'}")
    print("数据来源：Binance 官方 Spot 规则与 USDⓈ-M 完整日归档")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
