const DATA_PATH = "data/listings.json";

const state = {
  listings: [],
  query: "",
  tag: "全部",
  tradingStatus: "全部",
  expandedId: null,
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function listingId(listing) {
  return `${listing.announcement_code || "announcement"}:${listing.symbol || "symbol"}`;
}

function safeUrl(value) {
  try {
    const url = new URL(String(value));
    if (["http:", "https:", "mailto:"].includes(url.protocol)) return url.href;
  } catch {
    return null;
  }
  return null;
}

function linkLabel(url, fallback) {
  const lowered = url.toLowerCase();
  if (lowered.startsWith("mailto:")) return "邮箱";
  if (lowered.includes("github.com")) return "GitHub";
  if (lowered.includes("discord")) return "Discord";
  if (lowered.includes("t.me") || lowered.includes("telegram")) return "Telegram";
  if (lowered.includes("x.com") || lowered.includes("twitter.com")) return "X";
  if (lowered.includes("linkedin.com")) return "LinkedIn";
  if (lowered.includes("youtube.com")) return "YouTube";
  if (lowered.includes("scan") || lowered.includes("explorer")) return "合约";
  return fallback;
}

function validLinks(listing, includeContracts) {
  const candidates = [
    ...(Array.isArray(listing.project_links) ? listing.project_links.map((url) => ({ url, fallback: "官网" })) : []),
    ...(Array.isArray(listing.contact_links) ? listing.contact_links.map((url) => ({ url, fallback: "项目入口" })) : []),
    ...(includeContracts && Array.isArray(listing.contract_links)
      ? listing.contract_links.map((url) => ({ url, fallback: "合约" }))
      : []),
  ];
  const seen = new Set();
  return candidates.filter(({ url }) => {
    const safe = safeUrl(url);
    if (!safe || seen.has(safe)) return false;
    seen.add(safe);
    return true;
  });
}

function linksNode(listing, labeled = false, includeContracts = false) {
  const container = element("div", labeled ? "link-list link-list-labeled" : "link-list");
  const links = validLinks(listing, includeContracts);
  if (!links.length) {
    container.append(element("span", "muted-text", "待补充"));
    return container;
  }
  links.slice(0, labeled ? 14 : 4).forEach(({ url, fallback }) => {
    const safe = safeUrl(url);
    const label = linkLabel(safe, fallback);
    const anchor = element("a", "link-button", labeled ? label : "↗");
    anchor.href = safe;
    anchor.target = "_blank";
    anchor.rel = "noreferrer";
    anchor.title = `${label}：${safe}`;
    anchor.setAttribute("aria-label", `${listing.project_name} ${label}`);
    container.append(anchor);
  });
  return container;
}

function pairNode(listing, limit = 4) {
  const container = element("div", "pair-list");
  const pairs = Array.isArray(listing.spot_pairs) ? listing.spot_pairs : [];
  if (!pairs.length) {
    container.append(element("span", "muted-text", "待确认"));
    return container;
  }
  pairs.slice(0, limit).forEach((pair) => container.append(element("span", "pair-chip", pair)));
  if (pairs.length > limit) container.append(element("span", "muted-text", `+${pairs.length - limit}`));
  return container;
}

function tagNode(listing) {
  return listing.seed_tag
    ? element("span", "seed-badge", "Seed Tag")
    : element("span", "plain-badge", "普通");
}

function formatBeijingTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date
    .toLocaleString("zh-CN", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    })
    .replaceAll("/", "-");
}

function formatSyncTime(value) {
  if (!value) return "尚未同步";
  const full = formatBeijingTime(value);
  return full === "-" ? full : full.slice(5, 16);
}

function tradingStatus(listing) {
  if (!listing.trading_starts_at) return "时间待确认";
  const timestamp = new Date(listing.trading_starts_at).getTime();
  if (Number.isNaN(timestamp)) return "时间待确认";
  return timestamp > Date.now() ? "待开盘" : "已开盘";
}

function statusNode(listing) {
  const status = tradingStatus(listing);
  const className = status === "待开盘" ? "status-upcoming" : status === "已开盘" ? "status-open" : "status-unknown";
  return element("span", `trade-status ${className}`, status);
}

function timeNode(listing, field, withStatus) {
  const container = element("div", "time-block");
  container.append(element("span", "", formatBeijingTime(listing[field])));
  if (withStatus) {
    const status = element("div", "status-line");
    status.append(statusNode(listing));
    container.append(status);
  }
  return container;
}

function projectNode(listing, includeDate = false) {
  const project = element("div", "project-cell");
  project.append(element("strong", "", listing.project_name || "未知项目"));
  const suffix = includeDate ? ` · ${formatBeijingTime(listing.published_at).slice(0, 10)}` : "";
  project.append(element("span", "", `$${listing.symbol || "-"}${suffix}`));
  return project;
}

function detailNode(listing) {
  const detail = element("div", "listing-detail");

  const summary = element("div", "detail-section");
  summary.append(element("span", "detail-label", "项目简介"));
  summary.append(element("p", "", listing.summary || "Binance 公告暂未提供可识别的项目简介。"));
  summary.append(element("p", "", listing.reason || "项目方入口需要人工二次确认。"));

  const links = element("div", "detail-section");
  links.append(element("span", "detail-label", "项目与合约入口"));
  links.append(linksNode(listing, true, true));

  const source = element("div", "detail-section source-note");
  source.append(element("span", "detail-label", "官方来源与说明"));
  source.append(element("p", "", "这条记录已同时通过标题和公告正文的现货上币复核。公开页面只能读取，不能修改源数据。"));
  const announcementUrl = safeUrl(listing.announcement_url);
  if (announcementUrl) {
    const announcement = element("a", "announcement-link", "查看 Binance 原始公告 ↗");
    announcement.href = announcementUrl;
    announcement.target = "_blank";
    announcement.rel = "noreferrer";
    source.append(announcement);
  }

  detail.append(summary, links, source);
  return detail;
}

function cell(child, className = "") {
  const td = element("td", className);
  if (typeof child === "string") td.textContent = child;
  else td.append(child);
  return td;
}

function toggleListing(id) {
  state.expandedId = state.expandedId === id ? null : id;
  renderListings();
}

function filteredListings() {
  const query = state.query.trim().toLowerCase();
  return state.listings.filter((listing) => {
    const pairs = Array.isArray(listing.spot_pairs) ? listing.spot_pairs.join(" ") : "";
    const haystack = [listing.project_name, listing.symbol, listing.title, pairs]
      .map((value) => String(value || "").toLowerCase());
    const tag = listing.seed_tag ? "Seed" : "普通";
    return (
      (!query || haystack.some((value) => value.includes(query))) &&
      (state.tag === "全部" || state.tag === tag) &&
      (state.tradingStatus === "全部" || state.tradingStatus === tradingStatus(listing))
    );
  });
}

function renderDesktop(listings) {
  const tbody = document.querySelector("#listing-table-body");
  const fragment = document.createDocumentFragment();
  listings.forEach((listing) => {
    const id = listingId(listing);
    const expanded = state.expandedId === id;
    const row = element("tr", expanded ? "listing-row expanded" : "listing-row");
    const toggle = element("button", "icon-button", expanded ? "▴" : "▾");
    toggle.type = "button";
    toggle.title = expanded ? "收起详情" : "展开详情";
    toggle.setAttribute("aria-label", `${expanded ? "收起" : "展开"} ${listing.project_name}`);
    toggle.addEventListener("click", () => toggleListing(id));
    row.append(
      cell(projectNode(listing)),
      cell(pairNode(listing)),
      cell(timeNode(listing, "published_at", false), "time-cell"),
      cell(timeNode(listing, "trading_starts_at", true), "time-cell"),
      cell(linksNode(listing)),
      cell(tagNode(listing)),
      cell(toggle),
    );
    fragment.append(row);
    if (expanded) {
      const detailRow = element("tr", "detail-row");
      const detailCell = cell(detailNode(listing));
      detailCell.colSpan = 7;
      detailRow.append(detailCell);
      fragment.append(detailRow);
    }
  });
  tbody.replaceChildren(fragment);
}

function renderMobile(listings) {
  const list = document.querySelector("#mobile-list");
  const fragment = document.createDocumentFragment();
  listings.forEach((listing) => {
    const id = listingId(listing);
    const expanded = state.expandedId === id;
    const article = element("article", "mobile-listing");
    const summary = element("button", "mobile-summary");
    summary.type = "button";
    summary.append(projectNode(listing, true), tagNode(listing), element("span", "expand-symbol", expanded ? "▴" : "▾"));
    summary.addEventListener("click", () => toggleListing(id));
    const meta = element("div", "mobile-meta");
    meta.append(pairNode(listing, 3), statusNode(listing));
    article.append(summary, meta);
    if (expanded) article.append(detailNode(listing));
    fragment.append(article);
  });
  list.replaceChildren(fragment);
}

function renderListings() {
  const listings = filteredListings();
  document.querySelector("#result-count").textContent = `当前显示 ${listings.length} 条，共 ${state.listings.length} 条`;
  renderDesktop(listings);
  renderMobile(listings);
  document.querySelector("#empty-state").classList.toggle("hidden", listings.length !== 0);
  document.querySelector(".table-wrap").classList.toggle("hidden", listings.length === 0);
  document.querySelector("#mobile-list").classList.toggle("hidden", listings.length === 0);
}

function showNotice(message, isError = false) {
  const notice = document.querySelector("#notice");
  notice.textContent = message;
  notice.className = isError ? "notice error-notice" : "notice";
}

async function copyText(value) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("浏览器拒绝复制命令");
}

async function loadListings() {
  try {
    const response = await fetch(DATA_PATH, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (!payload || !Array.isArray(payload.items)) throw new Error("items 不是列表");
    if (payload.items.some((item) => item.listing_type !== "spot")) throw new Error("数据中出现非现货记录");
    state.listings = payload.items;
    state.expandedId = state.listings[0] ? listingId(state.listings[0]) : null;
    document.querySelector("#stat-total").textContent = String(state.listings.length);
    document.querySelector("#stat-seed").textContent = String(state.listings.filter((item) => item.seed_tag).length);
    document.querySelector("#stat-websites").textContent = String(
      state.listings.filter((item) => Array.isArray(item.project_links) && item.project_links.length).length,
    );
    document.querySelector("#stat-upcoming").textContent = String(
      state.listings.filter((item) => tradingStatus(item) === "待开盘").length,
    );
    document.querySelector("#sync-state").textContent = `最近同步：${formatSyncTime(payload.generated_at)}`;
    renderListings();
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    showNotice(`遇到错误：读取公开数据失败，返回内容：${detail}`, true);
    document.querySelector("#result-count").textContent = "公开数据暂时无法读取";
  }
}

document.querySelector("#search-input").addEventListener("input", (event) => {
  state.query = event.target.value;
  renderListings();
});

document.querySelector("#status-filter").addEventListener("change", (event) => {
  state.tradingStatus = event.target.value;
  renderListings();
});

document.querySelectorAll("[data-tag]").forEach((button) => {
  button.addEventListener("click", () => {
    state.tag = button.dataset.tag;
    document.querySelectorAll("[data-tag]").forEach((item) => item.classList.toggle("active", item === button));
    renderListings();
  });
});

document.querySelector("#copy-endpoint").addEventListener("click", async () => {
  const endpoint = new URL(DATA_PATH, window.location.href).href;
  try {
    await copyText(endpoint);
    showNotice("AI 接口地址已复制");
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    showNotice(`遇到错误：复制失败，返回内容：${detail}`, true);
  }
});

loadListings();
