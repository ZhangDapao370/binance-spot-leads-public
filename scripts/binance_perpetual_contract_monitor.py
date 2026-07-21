#!/usr/bin/env python3
"""抓取 Binance 官方新上线永续合约，并输出网页和 AI 可读取的数据。"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from binance_spot_listing_monitor import (
    ARTICLE_URL_TEMPLATE,
    CATALOG_API_URL,
    CATALOG_ID,
    CATALOG_NAME,
    CATALOG_PAGE_URL,
    DEFAULT_PROXY,
    DETAIL_API_URL,
    build_session,
    catalog_articles,
    fetch_json,
    format_millisecond_time,
    format_utc_text,
    node_text,
    normalize_space,
    parse_body_root,
    proxy_candidates,
    walk_nodes,
)


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name in {"work", "scripts"} else SCRIPT_DIR
CONTRACT_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{2,30}$")
SETTLEMENT_SUFFIXES = ("USDT", "USDC", "USD1", "FDUSD", "USDE", "USD")
COMMODITY_WORDS = (
    "copper",
    "gold",
    "silver",
    "natural gas",
    "crude oil",
    "brent",
    "commodity",
)
DETAIL_REQUEST_DELAY_SECONDS = 1.5
RATE_LIMIT_RETRY_SECONDS = (15, 30, 60)


@dataclass
class PerpetualContract:
    announcement_code: str
    title: str
    contract_symbol: str
    base_symbol: str
    listing_type: str
    margin_type: str
    product_variant: str
    asset_type: str
    launch_stage: str
    published_at: str
    launch_time: str
    underlying: str
    settlement_asset: str
    maximum_leverage: str
    capped_funding_rate: str
    funding_interval: str
    tick_size: str
    min_trade_amount: str
    min_notional_value: str
    trading_hours: str
    project_info: str
    trade_url: str
    announcement_url: str


def is_perpetual_candidate(title: str) -> bool:
    """只把可能新增永续合约的标题送去正文复核。"""
    lowered = title.lower()
    if "futures" not in lowered:
        return False
    if any(word in lowered for word in ("delist", "remove", "update", "tick size", "delivery contract")):
        return False
    return "will launch" in lowered or "will add" in lowered


def extract_tables(root: dict[str, Any]) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    for node in walk_nodes(root):
        if node.get("node") != "element" or node.get("tag") != "table":
            continue
        rows: list[list[str]] = []
        for row_node in walk_nodes(node):
            if row_node.get("node") != "element" or row_node.get("tag") != "tr":
                continue
            cells: list[str] = []
            for cell_node in walk_nodes(row_node):
                if cell_node.get("node") != "element" or cell_node.get("tag") not in ("td", "th"):
                    continue
                cells.append(normalize_space(node_text(cell_node)))
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def contract_table(root: dict[str, Any]) -> list[list[str]]:
    for rows in extract_tables(root):
        for row in rows:
            if not row:
                continue
            label = row[0].lower()
            symbols = [value for value in row[1:] if CONTRACT_SYMBOL_PATTERN.fullmatch(value)]
            if "perpetual contract" in label and symbols:
                return rows
    return []


def find_row(rows: list[list[str]], *labels: str) -> list[str]:
    expected = {label.lower() for label in labels}
    return next((row for row in rows if row and row[0].lower() in expected), [])


def row_value(rows: list[list[str]], index: int, *labels: str) -> str:
    row = find_row(rows, *labels)
    values = row[1:] if row else []
    if not values:
        return ""
    if index < len(values):
        return values[index]
    return values[0] if len(values) == 1 else ""


def split_base_symbol(contract_symbol: str, settlement_asset: str) -> str:
    settlement = settlement_asset.upper()
    if settlement and contract_symbol.endswith(settlement):
        return contract_symbol[: -len(settlement)]
    for suffix in SETTLEMENT_SUFFIXES:
        if contract_symbol.endswith(suffix):
            return contract_symbol[: -len(suffix)]
    return contract_symbol


def classify_asset(title: str, underlying: str, rows: list[list[str]]) -> str:
    combined = f"{title} {underlying}".lower()
    row_labels = {row[0].lower() for row in rows if row}
    if "index perpetual" in combined or "underlying index" in row_labels:
        return "指数"
    if any(word in combined for word in COMMODITY_WORDS):
        return "商品"
    if "underlying equity" in row_labels or "equity perpetual" in combined or "tradfi perpetual" in combined:
        return "股票"
    return "加密资产"


def launch_stage(title: str) -> str:
    lowered = title.lower()
    if "pre-ipo" in lowered:
        return "pre_ipo"
    if "pre-market" in lowered:
        return "pre_market"
    return "standard"


def parse_contract_table(
    article: dict[str, Any], detail: dict[str, Any], root: dict[str, Any], rows: list[list[str]]
) -> list[PerpetualContract]:
    title = normalize_space(str(detail.get("title") or article.get("title") or ""))
    contract_row = next(
        (row for row in rows if row and "perpetual contract" in row[0].lower()),
        [],
    )
    if len(contract_row) < 2:
        return []

    code = str(detail.get("code") or article.get("code") or "")
    published_at = format_millisecond_time(detail.get("publishDate") or article.get("releaseDate"))
    announcement_url = ARTICLE_URL_TEMPLATE.format(code=code)
    margin_type = contract_row[0].replace(" Perpetual Contract", "").strip()
    stage = launch_stage(title)
    contracts: list[PerpetualContract] = []

    for index, raw_symbol in enumerate(contract_row[1:]):
        contract_symbol = normalize_space(raw_symbol).upper()
        if not CONTRACT_SYMBOL_PATTERN.fullmatch(contract_symbol):
            continue
        settlement_asset = row_value(rows, index, "Settlement Asset").upper()
        underlying = row_value(
            rows,
            index,
            "Underlying Asset",
            "Underlying Equity",
            "Underlying Index",
            "Underlying Equity/Index",
        )
        launch_text = row_value(rows, index, "Launch Time")
        launch_text = re.sub(r"\s*\(UTC\)\s*$", "", launch_text)
        contracts.append(
            PerpetualContract(
                announcement_code=code,
                title=title,
                contract_symbol=contract_symbol,
                base_symbol=split_base_symbol(contract_symbol, settlement_asset),
                listing_type="perpetual",
                margin_type=margin_type,
                product_variant=row_value(rows, index, "Contract Type"),
                asset_type=classify_asset(title, underlying, rows),
                launch_stage=stage,
                published_at=published_at,
                launch_time=format_utc_text(launch_text),
                underlying=underlying,
                settlement_asset=settlement_asset,
                maximum_leverage=row_value(rows, index, "Maximum Leverage"),
                capped_funding_rate=row_value(rows, index, "Capped Funding Rate"),
                funding_interval=row_value(rows, index, "Funding Fee Settlement Frequency"),
                tick_size=row_value(rows, index, "Tick Size"),
                min_trade_amount=row_value(rows, index, "Min Trade Amount", "Minimum Trade Amount"),
                min_notional_value=row_value(rows, index, "Min Notional Value", "Minimum Notional Value"),
                trading_hours=row_value(rows, index, "Trading Hours"),
                project_info=row_value(
                    rows,
                    index,
                    "Project Info",
                    "Underlying Equity/Index Info",
                )[:500],
                trade_url=f"https://www.binance.com/en/futures/{contract_symbol}",
                announcement_url=announcement_url,
            )
        )
    return contracts


def parse_detail_article(article: dict[str, Any], detail: dict[str, Any]) -> list[PerpetualContract]:
    title = normalize_space(str(detail.get("title") or article.get("title") or ""))
    if not is_perpetual_candidate(title):
        return []
    root = parse_body_root(detail)
    rows = contract_table(root)
    if not rows:
        return []
    return parse_contract_table(article, detail, root, rows)


def fetch_detail_with_rate_limit(session: Any, code: str) -> dict[str, Any]:
    """放慢正文请求；遇到 Binance 429 时按固定间隔继续重试。"""
    waits = (0, *RATE_LIMIT_RETRY_SECONDS)
    last_error: RuntimeError | None = None
    for attempt, wait_seconds in enumerate(waits, start=1):
        if wait_seconds:
            print(
                "遇到错误：Binance 永续公告请求被限速，返回内容："
                f"公告 {code} 返回 429；等待 {wait_seconds} 秒后进行第 {attempt} 次尝试",
                file=sys.stderr,
            )
            time.sleep(wait_seconds)
        time.sleep(DETAIL_REQUEST_DELAY_SECONDS)
        try:
            return fetch_json(session, DETAIL_API_URL, {"articleCode": code})
        except RuntimeError as exc:
            last_error = exc
            if "429" not in str(exc):
                raise
    assert last_error is not None
    raise last_error


def fetch_perpetual_contracts(
    session: Any, limit: int, scan_pages: int
) -> list[PerpetualContract]:
    contracts: list[PerpetualContract] = []
    seen_codes: set[str] = set()
    for page in range(1, scan_pages + 1):
        payload = fetch_json(
            session,
            CATALOG_API_URL,
            {"type": 1, "catalogId": CATALOG_ID, "pageNo": page, "pageSize": 20},
        )
        articles = catalog_articles(payload)
        if not articles:
            break
        for article in articles:
            title = normalize_space(str(article.get("title") or ""))
            code = str(article.get("code") or "")
            if code in seen_codes or not is_perpetual_candidate(title):
                continue
            seen_codes.add(code)
            detail_payload = fetch_detail_with_rate_limit(session, code)
            detail = detail_payload.get("data")
            if not isinstance(detail, dict):
                raise RuntimeError(f"遇到错误：解析 Binance 永续公告 {code}，返回内容：data 不是对象")
            contracts.extend(parse_detail_article(article, detail))
            if len(contracts) >= limit:
                return contracts[:limit]
    if not contracts:
        raise RuntimeError(
            f"遇到错误：解析 Binance 永续公告，返回内容：扫描 {scan_pages} 页后没有找到新永续合约"
        )
    return contracts[:limit]


def fetch_with_fallback(
    proxy: str | None, no_proxy: bool, limit: int, scan_pages: int
) -> tuple[list[PerpetualContract], str | None]:
    errors: list[str] = []
    for candidate in proxy_candidates(proxy, no_proxy):
        mode = f"代理 {candidate}" if candidate else "直连"
        try:
            return fetch_perpetual_contracts(build_session(candidate), limit, scan_pages), candidate
        except RuntimeError as exc:
            errors.append(f"{mode}失败：{exc}")
    raise RuntimeError(
        "遇到错误：Binance 永续合约抓取失败，返回内容：\n"
        + "\n".join(errors)
        + "\n请检查：1. 代理软件是否打开；2. 127.0.0.1:7897 是否正确；3. Binance 公告页能否访问。"
    )


def contract_key(contract: PerpetualContract) -> str:
    return f"{contract.announcement_code}:{contract.contract_symbol}"


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_seen_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"遇到错误：读取永续状态文件 {path}，返回内容：{exc}") from exc
    values = payload.get("seen_keys") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        raise RuntimeError(f"遇到错误：读取永续状态文件 {path}，返回内容：seen_keys 不是列表")
    return {str(value) for value in values}


def save_seen_keys(path: Path, values: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"updated_at": datetime.now(timezone.utc).isoformat(), "seen_keys": sorted(values)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def write_csv(path: Path, contracts: Iterable[PerpetualContract]) -> None:
    rows = list(contracts)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(PerpetualContract.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for contract in rows:
            writer.writerow(asdict(contract))


def build_public_payload(contracts: Iterable[PerpetualContract]) -> dict[str, Any]:
    items = [asdict(contract) for contract in contracts]
    return {
        "schema_version": "1.0",
        "data_type": "binance_perpetual_contracts",
        "public_read_only": True,
        "timezone": "Asia/Shanghai",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": {"name": "Binance", "category": CATALOG_NAME, "url": CATALOG_PAGE_URL},
        "summary": {
            "total": len(items),
            "standard": sum(1 for item in items if item["launch_stage"] == "standard"),
            "pre_market": sum(1 for item in items if item["launch_stage"] == "pre_market"),
            "pre_ipo": sum(1 for item in items if item["launch_stage"] == "pre_ipo"),
            "crypto": sum(1 for item in items if item["asset_type"] == "加密资产"),
            "tradfi": sum(1 for item in items if item["asset_type"] in {"股票", "指数", "商品"}),
        },
        "items": items,
    }


def write_json(path: Path, contracts: Iterable[PerpetualContract]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_public_payload(contracts), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取 Binance 官方新上线永续合约。")
    parser.add_argument("--proxy", default=DEFAULT_PROXY, help=f"代理地址，默认 {DEFAULT_PROXY}")
    parser.add_argument("--no-proxy", action="store_true", help="不走代理，只用直连")
    parser.add_argument("--limit", type=int, default=40, help="最多输出多少条永续合约记录")
    parser.add_argument("--scan-pages", type=int, default=20, help="最多扫描多少页官方公告")
    parser.add_argument("--csv", default="outputs/binance_perpetual_new.csv", help="新增记录 CSV")
    parser.add_argument("--json", default="outputs/binance_perpetual_new.json", help="新增记录 JSON")
    parser.add_argument("--all-csv", default="outputs/binance_perpetual_all.csv", help="全部记录 CSV")
    parser.add_argument("--all-json", default="outputs/binance_perpetual_all.json", help="全部记录 JSON")
    parser.add_argument("--state", default="work/binance_seen_perpetual_contracts.json", help="去重状态文件")
    parser.add_argument("--include-seen", action="store_true", help="新增输出也包含已经见过的记录")
    parser.add_argument("--bootstrap", action="store_true", help="只标记当前记录，不输出旧数据")
    args = parser.parse_args()

    if args.limit < 1 or args.limit > 300:
        print("遇到错误：参数 limit 不正确，返回内容：必须在 1 到 300 之间", file=sys.stderr)
        return 2
    if args.scan_pages < 1 or args.scan_pages > 111:
        print("遇到错误：参数 scan-pages 不正确，返回内容：必须在 1 到 111 之间", file=sys.stderr)
        return 2

    try:
        all_contracts, active_proxy = fetch_with_fallback(
            args.proxy, args.no_proxy, args.limit, args.scan_pages
        )
        all_contracts.sort(key=lambda item: (item.published_at, item.launch_time), reverse=True)
        state_path = resolve_path(args.state)
        seen_keys = load_seen_keys(state_path)
        current_keys = {contract_key(contract) for contract in all_contracts}
        if args.bootstrap:
            save_seen_keys(state_path, seen_keys | current_keys)
            print(f"完成：已把当前 {len(all_contracts)} 条 Binance 永续合约标记为已见过")
            print(f"状态文件：{state_path}")
            return 0

        new_contracts = [
            contract
            for contract in all_contracts
            if args.include_seen or contract_key(contract) not in seen_keys
        ]
        csv_path = resolve_path(args.csv)
        json_path = resolve_path(args.json)
        all_csv_path = resolve_path(args.all_csv)
        all_json_path = resolve_path(args.all_json)
        write_csv(csv_path, new_contracts)
        write_json(json_path, new_contracts)
        write_csv(all_csv_path, all_contracts)
        write_json(all_json_path, all_contracts)
        save_seen_keys(state_path, seen_keys | current_keys)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"完成：抓到 {len(new_contracts)} 条新增、{len(all_contracts)} 条全部 Binance 永续合约")
    print(f"新增 CSV：{csv_path}")
    print(f"新增 JSON：{json_path}")
    print(f"全部 CSV：{all_csv_path}")
    print(f"全部 JSON：{all_json_path}")
    print(f"状态文件：{state_path}")
    print(f"请求方式：{'代理 ' + active_proxy if active_proxy else '直连'}")
    print("数据来源：Binance 官方 CMS API / New Cryptocurrency Listing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
