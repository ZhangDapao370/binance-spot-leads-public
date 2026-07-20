#!/usr/bin/env python3
"""抓取 Binance 官方现货新币公告，并输出可供网页和 AI 使用的数据。"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import urlparse

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

try:
    from bs4 import BeautifulSoup
except ModuleNotFoundError as exc:
    print(
        "遇到错误：缺少 Python 包 beautifulsoup4，返回内容：No module named 'bs4'\n"
        "请在 PyCharm 底部 Terminal 运行：python3 -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc


CATALOG_ID = 48
CATALOG_NAME = "New Cryptocurrency Listing"
CATALOG_PAGE_URL = "https://www.binance.com/en/support/announcement/list/48"
CATALOG_API_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
DETAIL_API_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query"
ARTICLE_URL_TEMPLATE = "https://www.binance.com/en/support/announcement/detail/{code}"
DEFAULT_PROXY = "http://127.0.0.1:7897"
DEFAULT_TIMEOUT = 25
PROJECT_TIMEOUT = 12
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name in {"work", "scripts"} else SCRIPT_DIR

PROJECT_PATTERN = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9 .+'’&:/-]*?)\s*"
    r"\((?P<symbol>[A-Za-z0-9][A-Za-z0-9._-]{0,19})\)"
)
PAIR_PATTERN = re.compile(r"\b([A-Z0-9]{1,20})\s*/\s*([A-Z0-9]{1,20})\b")
TRADING_TIME_PATTERN = re.compile(
    r"open trading.{0,220}?at\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*\(UTC\)",
    flags=re.IGNORECASE | re.DOTALL,
)
SOCIAL_HOST_WORDS = (
    "github.com",
    "discord.com",
    "discord.gg",
    "x.com",
    "twitter.com",
    "t.me",
    "telegram.",
    "medium.com",
    "linkedin.com",
    "youtube.com",
)
EXPLORER_HOST_WORDS = (
    "etherscan.io",
    "basescan.org",
    "bscscan.com",
    "arbiscan.io",
    "optimistic.etherscan.io",
    "polygonscan.com",
    "snowtrace.io",
    "solscan.io",
    "tronscan.org",
    "suiscan.xyz",
    "tonviewer.com",
    "katanascan.com",
    "explorer.alchemy.com",
)
CONTACT_PATH_WORDS = (
    "contact",
    "community",
    "discord",
    "telegram",
    "github",
    "twitter",
    "social",
    "team",
)
NOISE_PATH_WORDS = (
    "/releases/",
    "/download/",
    "/tag/",
    "/tags/",
    "/blob/",
    "/tree/",
    "/commit/",
    "/pull/",
    "/issues/",
    "/actions/",
    ".zip",
    ".tar",
    ".gz",
    ".dmg",
    ".exe",
)


@dataclass
class ProjectRef:
    project_name: str
    symbol: str


@dataclass
class LinkRef:
    text: str
    url: str


@dataclass
class SpotListing:
    announcement_code: str
    title: str
    project_name: str
    symbol: str
    listing_type: str
    published_at: str
    trading_starts_at: str
    spot_pairs: list[str]
    seed_tag: bool
    announcement_url: str
    project_links: list[str]
    contact_links: list[str]
    contract_links: list[str]
    summary: str
    contact_status: str
    reason: str


def build_session(proxy: str | None) -> requests.Session:
    """创建可复用请求实例，重试短暂网络错误。"""
    session = requests.Session()
    session.trust_env = False
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
            "Clienttype": "web",
        }
    )
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def fetch_json(session: requests.Session, url: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        response = session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"遇到错误：请求 {url}，返回内容：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"遇到错误：解析 JSON {url}，返回内容：{exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"遇到错误：解析 JSON {url}，返回内容：根节点不是对象")
    if payload.get("code") != "000000":
        raise RuntimeError(
            f"遇到错误：Binance API 返回异常，返回内容："
            f"code={payload.get('code')} message={payload.get('message')}"
        )
    return payload


def format_millisecond_time(value: Any) -> str:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return ""
    if milliseconds <= 0:
        return ""
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def format_utc_text(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    return parsed.isoformat().replace("+00:00", "Z")


def normalize_space(value: str) -> str:
    return " ".join(html.unescape(value).replace("\xa0", " ").split())


def compact_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def normalize_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    url = value.strip()
    url = url.replace("https://www.%suffixOrigin%/%locale%/", "https://www.binance.com/en/")
    url = url.replace("https://www.%suffixOrigin%/", "https://www.binance.com/")
    if not url.startswith(("http://", "https://", "mailto:")):
        return ""
    return url


def node_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    if node.get("node") == "text":
        return str(node.get("text") or "")
    return " ".join(node_text(child) for child in node.get("child", []) if isinstance(child, dict))


def walk_nodes(node: Any) -> Iterator[dict[str, Any]]:
    if not isinstance(node, dict):
        return
    yield node
    for child in node.get("child", []):
        if isinstance(child, dict):
            yield from walk_nodes(child)


def parse_body_root(detail: dict[str, Any]) -> dict[str, Any]:
    raw_body = detail.get("body") or detail.get("contentJson") or {}
    if isinstance(raw_body, dict):
        return raw_body
    if not isinstance(raw_body, str) or not raw_body.strip():
        raise RuntimeError("遇到错误：解析 Binance 公告正文，返回内容：body 为空")
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"遇到错误：解析 Binance 公告正文 JSON，返回内容：{exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("遇到错误：解析 Binance 公告正文，返回内容：body 根节点不是对象")
    return parsed


def extract_links(root: dict[str, Any]) -> list[LinkRef]:
    links: list[LinkRef] = []
    seen: set[str] = set()
    for node in walk_nodes(root):
        if node.get("node") != "element" or node.get("tag") != "a":
            continue
        url = normalize_url((node.get("attr") or {}).get("href"))
        if not url or url in seen:
            continue
        seen.add(url)
        links.append(LinkRef(text=normalize_space(node_text(node)), url=url))
    return links


def extract_title_projects(title: str) -> list[ProjectRef]:
    if not title.startswith("Binance Will List ") or "Futures" in title:
        return []
    title_body = title.removeprefix("Binance Will List ")
    projects: list[ProjectRef] = []
    seen: set[str] = set()
    for match in PROJECT_PATTERN.finditer(title_body):
        name = normalize_space(match.group("name"))
        name = re.sub(r"^(?:and|,)\s+", "", name, flags=re.IGNORECASE).strip(" ,")
        symbol = match.group("symbol").strip().upper()
        if not name or symbol in seen:
            continue
        seen.add(symbol)
        projects.append(ProjectRef(project_name=name, symbol=symbol))
    return projects


def is_spot_listing(root: dict[str, Any]) -> bool:
    text = normalize_space(node_text(root)).lower()
    return "new spot trading pairs" in text and "open trading" in text


def extract_spot_pairs(root: dict[str, Any], symbol: str) -> list[str]:
    text = normalize_space(node_text(root))
    pairs: list[str] = []
    seen: set[str] = set()
    for base, quote in PAIR_PATTERN.findall(text):
        if base != symbol.upper():
            continue
        pair = f"{base}/{quote}"
        if pair not in seen:
            seen.add(pair)
            pairs.append(pair)
    return pairs


def extract_trading_start(root: dict[str, Any]) -> str:
    match = TRADING_TIME_PATTERN.search(normalize_space(node_text(root)))
    return format_utc_text(match.group(1)) if match else ""


def extract_project_summary(root: dict[str, Any], project: ProjectRef) -> str:
    children = root.get("child", [])
    for index, node in enumerate(children):
        if not isinstance(node, dict) or node.get("tag") not in ("h2", "h3", "h4"):
            continue
        heading = normalize_space(node_text(node))
        if not heading.lower().startswith("what is"):
            continue
        heading_compact = compact_text(heading)
        if compact_text(project.project_name) not in heading_compact and project.symbol.lower() not in heading.lower():
            continue
        for following in children[index + 1 :]:
            if not isinstance(following, dict):
                continue
            if following.get("tag") in ("h2", "h3", "h4"):
                break
            text = normalize_space(node_text(following))
            if text:
                return text[:500]
    return ""


def host_matches(value: str, words: tuple[str, ...]) -> bool:
    host = urlparse(value).netloc.lower()
    return any(word in host for word in words)


def is_binance_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "binance.com" in host or "bnbstatic.com" in host or "binance.onelink.me" in host


def link_matches_project(link: LinkRef, project: ProjectRef, single_project: bool) -> bool:
    label_words = set(re.findall(r"[a-z0-9]+", link.text.lower()))
    name_words = [word for word in re.findall(r"[a-z0-9]+", project.project_name.lower()) if len(word) >= 3]
    host_compact = compact_text(urlparse(link.url).netloc)
    label_match = bool(set(name_words).intersection(label_words)) or project.symbol.lower() in label_words
    host_match = any(word in host_compact for word in name_words if len(word) >= 4)
    return label_match or host_match or (single_project and "website" in label_words)


def extract_project_resources(
    links: list[LinkRef], project: ProjectRef, project_count: int
) -> tuple[list[str], list[str]]:
    project_links: list[str] = []
    contract_links: list[str] = []
    for link in links:
        if is_binance_url(link.url):
            continue
        if host_matches(link.url, EXPLORER_HOST_WORDS):
            contract_links.append(link.url)
            continue
        if host_matches(link.url, SOCIAL_HOST_WORDS):
            continue
        if link_matches_project(link, project, project_count == 1):
            project_links.append(link.url)
    return unique(project_links), unique(contract_links)


def unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def contact_details(project_links: list[str], contact_links: list[str]) -> tuple[str, str]:
    if project_links and contact_links:
        return "完整", "已找到项目官网和社区、代码或联系入口。"
    if project_links:
        return "部分", "已找到项目官网，社区或联系入口需要继续确认。"
    return "待补充", "Binance 公告没有识别出项目官网，需要人工二次确认。"


def parse_detail_article(article: dict[str, Any], detail: dict[str, Any]) -> list[SpotListing]:
    title = normalize_space(str(detail.get("title") or article.get("title") or ""))
    projects = extract_title_projects(title)
    if not projects:
        return []
    root = parse_body_root(detail)
    if not is_spot_listing(root):
        return []
    code = str(detail.get("code") or article.get("code") or "")
    published_at = format_millisecond_time(detail.get("publishDate") or article.get("releaseDate"))
    announcement_url = ARTICLE_URL_TEMPLATE.format(code=code)
    links = extract_links(root)
    trading_starts_at = extract_trading_start(root)
    seed_tag = "seed tag" in title.lower()
    listings: list[SpotListing] = []
    for project in projects:
        project_links, contract_links = extract_project_resources(links, project, len(projects))
        contact_status, reason = contact_details(project_links, [])
        listings.append(
            SpotListing(
                announcement_code=code,
                title=title,
                project_name=project.project_name,
                symbol=project.symbol,
                listing_type="spot",
                published_at=published_at,
                trading_starts_at=trading_starts_at,
                spot_pairs=extract_spot_pairs(root, project.symbol),
                seed_tag=seed_tag,
                announcement_url=announcement_url,
                project_links=project_links,
                contact_links=[],
                contract_links=contract_links,
                summary=extract_project_summary(root, project),
                contact_status=contact_status,
                reason=reason,
            )
        )
    return listings


def is_contact_candidate(url: str, text: str = "") -> bool:
    lowered_url = url.lower()
    lowered_text = text.lower()
    if any(word in lowered_url for word in NOISE_PATH_WORDS):
        return False
    if url.startswith("mailto:"):
        return True
    if host_matches(url, SOCIAL_HOST_WORDS):
        return True
    return any(word in lowered_url or word in lowered_text for word in CONTACT_PATH_WORDS)


def extract_contacts_from_homepage(content: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(content, "html.parser")
    contacts: list[str] = []
    for anchor in soup.find_all("a", href=True):
        raw_url = str(anchor.get("href") or "").strip()
        label = normalize_space(anchor.get_text(" ", strip=True))
        if raw_url.startswith("mailto:"):
            url = raw_url
        elif raw_url.startswith(("http://", "https://")):
            url = raw_url
        elif raw_url.startswith("/"):
            parsed = urlparse(base_url)
            url = f"{parsed.scheme}://{parsed.netloc}{raw_url}"
        else:
            continue
        if is_contact_candidate(url, label):
            contacts.append(url)
    return unique(contacts)


def enrich_contacts(session: requests.Session, listings: list[SpotListing]) -> None:
    cache: dict[str, list[str]] = {}
    for listing in listings:
        contacts: list[str] = list(listing.contact_links)
        for homepage in listing.project_links[:1]:
            if homepage not in cache:
                try:
                    response = session.get(homepage, timeout=PROJECT_TIMEOUT)
                    response.raise_for_status()
                    cache[homepage] = extract_contacts_from_homepage(response.text, homepage)
                except requests.RequestException:
                    cache[homepage] = []
            contacts.extend(cache[homepage])
        listing.contact_links = unique(contacts)
        listing.contact_status, listing.reason = contact_details(listing.project_links, listing.contact_links)


def catalog_articles(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    catalogs = data.get("catalogs") if isinstance(data, dict) else None
    if not isinstance(catalogs, list):
        raise RuntimeError("遇到错误：解析 Binance 公告列表，返回内容：catalogs 不是列表")
    catalog = next(
        (item for item in catalogs if isinstance(item, dict) and item.get("catalogId") == CATALOG_ID),
        None,
    )
    articles = catalog.get("articles") if isinstance(catalog, dict) else None
    if not isinstance(articles, list):
        raise RuntimeError("遇到错误：解析 Binance 公告列表，返回内容：articles 不是列表")
    return [article for article in articles if isinstance(article, dict)]


def fetch_spot_listings(session: requests.Session, limit: int, scan_pages: int) -> list[SpotListing]:
    listings: list[SpotListing] = []
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
            if code in seen_codes or not extract_title_projects(title):
                continue
            seen_codes.add(code)
            detail_payload = fetch_json(session, DETAIL_API_URL, {"articleCode": code})
            detail = detail_payload.get("data")
            if not isinstance(detail, dict):
                raise RuntimeError(f"遇到错误：解析 Binance 公告 {code}，返回内容：data 不是对象")
            listings.extend(parse_detail_article(article, detail))
            if len(listings) >= limit:
                return listings[:limit]
    if not listings:
        raise RuntimeError(
            f"遇到错误：解析 Binance 公告，返回内容：扫描 {scan_pages} 页后没有找到现货新币公告"
        )
    return listings[:limit]


def proxy_candidates(proxy: str | None, no_proxy: bool) -> list[str | None]:
    if no_proxy:
        return [None]
    candidates: list[str | None] = []
    if proxy:
        candidates.append(proxy)
    candidates.append(None)
    return candidates


def fetch_with_fallback(
    proxy: str | None, no_proxy: bool, limit: int, scan_pages: int
) -> tuple[list[SpotListing], str | None]:
    errors: list[str] = []
    for candidate in proxy_candidates(proxy, no_proxy):
        mode = f"代理 {candidate}" if candidate else "直连"
        session = build_session(candidate)
        try:
            return fetch_spot_listings(session, limit, scan_pages), candidate
        except RuntimeError as exc:
            errors.append(f"{mode}失败：{exc}")
    raise RuntimeError(
        "遇到错误：Binance 现货新币抓取失败，返回内容：\n"
        + "\n".join(errors)
        + "\n请检查：1. 代理软件是否打开；2. 127.0.0.1:7897 是否正确；3. Binance 公告页能否访问。"
    )


def listing_key(listing: SpotListing) -> str:
    return f"{listing.announcement_code}:{listing.symbol}"


def load_seen_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"遇到错误：读取状态文件 {path}，返回内容：{exc}") from exc
    values = payload.get("seen_keys") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        raise RuntimeError(f"遇到错误：读取状态文件 {path}，返回内容：seen_keys 不是列表")
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


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def write_csv(path: Path, listings: Iterable[SpotListing]) -> None:
    rows = list(listings)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(SpotListing.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for listing in rows:
            row = asdict(listing)
            for field in ("spot_pairs", "project_links", "contact_links", "contract_links"):
                row[field] = " | ".join(row[field])
            writer.writerow(row)


def build_public_payload(listings: Iterable[SpotListing]) -> dict[str, Any]:
    items = [asdict(listing) for listing in listings]
    return {
        "schema_version": "1.0",
        "public_read_only": True,
        "timezone": "Asia/Shanghai",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": {"name": "Binance", "category": CATALOG_NAME, "url": CATALOG_PAGE_URL},
        "summary": {
            "total": len(items),
            "seed_tag": sum(1 for item in items if item["seed_tag"]),
            "with_project_links": sum(1 for item in items if item["project_links"]),
            "with_contacts": sum(1 for item in items if item["contact_links"]),
        },
        "items": items,
    }


def write_json(path: Path, listings: Iterable[SpotListing]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_public_payload(listings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取 Binance 官方现货新币公告。")
    parser.add_argument("--proxy", default=DEFAULT_PROXY, help=f"代理地址，默认 {DEFAULT_PROXY}")
    parser.add_argument("--no-proxy", action="store_true", help="不走代理，只用直连")
    parser.add_argument("--limit", type=int, default=20, help="最多输出多少条现货新币记录")
    parser.add_argument("--scan-pages", type=int, default=20, help="最多扫描多少页官方分类")
    parser.add_argument("--csv", default="outputs/binance_spot_new.csv", help="新增记录 CSV")
    parser.add_argument("--json", default="outputs/binance_spot_new.json", help="新增记录 JSON")
    parser.add_argument("--all-csv", default="outputs/binance_spot_all.csv", help="全部记录 CSV")
    parser.add_argument("--all-json", default="outputs/binance_spot_all.json", help="全部记录 JSON")
    parser.add_argument("--state", default="work/binance_seen_announcements.json", help="去重状态文件")
    parser.add_argument("--include-seen", action="store_true", help="新增输出也包含已经见过的记录")
    parser.add_argument("--bootstrap", action="store_true", help="只标记当前记录，不输出旧数据")
    parser.add_argument("--skip-contact-enrich", action="store_true", help="不进入项目官网补充社区入口")
    args = parser.parse_args()

    if args.limit < 1 or args.limit > 200:
        print("遇到错误：参数 limit 不正确，返回内容：必须在 1 到 200 之间", file=sys.stderr)
        return 2
    if args.scan_pages < 1 or args.scan_pages > 111:
        print("遇到错误：参数 scan-pages 不正确，返回内容：必须在 1 到 111 之间", file=sys.stderr)
        return 2

    try:
        all_listings, active_proxy = fetch_with_fallback(
            args.proxy, args.no_proxy, args.limit, args.scan_pages
        )
        if not args.skip_contact_enrich:
            enrich_contacts(build_session(active_proxy), all_listings)
        all_listings.sort(key=lambda item: item.published_at, reverse=True)

        state_path = resolve_path(args.state)
        seen_keys = load_seen_keys(state_path)
        current_keys = {listing_key(listing) for listing in all_listings}
        if args.bootstrap:
            save_seen_keys(state_path, seen_keys | current_keys)
            print(f"完成：已把当前 {len(all_listings)} 条 Binance 现货记录标记为已见过")
            print(f"状态文件：{state_path}")
            return 0

        new_listings = [
            listing
            for listing in all_listings
            if args.include_seen or listing_key(listing) not in seen_keys
        ]
        csv_path = resolve_path(args.csv)
        json_path = resolve_path(args.json)
        all_csv_path = resolve_path(args.all_csv)
        all_json_path = resolve_path(args.all_json)
        write_csv(csv_path, new_listings)
        write_json(json_path, new_listings)
        write_csv(all_csv_path, all_listings)
        write_json(all_json_path, all_listings)
        save_seen_keys(state_path, seen_keys | current_keys)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"完成：抓到 {len(new_listings)} 条新增、{len(all_listings)} 条全部 Binance 现货新币记录")
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
