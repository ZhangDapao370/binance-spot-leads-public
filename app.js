const DATA_PATHS = {
  spot: "data/listings.json",
  perpetual: "data/contracts.json",
};

const state = {
  spotItems: [],
  contractItems: [],
  generatedAt: { spot: "", perpetual: "" },
  view: "spot",
  query: "",
  tag: "全部",
  assetType: "全部",
  tradingStatus: "全部",
  expandedId: null,
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function currentItems() {
  return state.view === "spot" ? state.spotItems : state.contractItems;
}

function recordId(item) {
  if (state.view === "spot") {
    return `spot:${item.announcement_code || "announcement"}:${item.symbol || "symbol"}`;
  }
  return `perpetual:${item.announcement_code || "announcement"}:${item.contract_symbol || "symbol"}`;
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
  if (lowered.includes("scan") || lowered.includes("explorer")) return "合约地址";
  return fallback;
}

function validSpotLinks(item, includeContracts) {
  const candidates = [
    ...(Array.isArray(item.project_links) ? item.project_links.map((url) => ({ url, fallback: "官网" })) : []),
    ...(Array.isArray(item.contact_links) ? item.contact_links.map((url) => ({ url, fallback: "项目入口" })) : []),
    ...(includeContracts && Array.isArray(item.contract_links)
      ? item.contract_links.map((url) => ({ url, fallback: "合约地址" }))
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

function spotLinksNode(item, labeled = false, includeContracts = false) {
  const container = element("div", labeled ? "link-list link-list-labeled" : "link-list");
  const links = validSpotLinks(item, includeContracts);
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
    anchor.setAttribute("aria-label", `${item.project_name} ${label}`);
    container.append(anchor);
  });
  return container;
}

function pairNode(item, limit = 4) {
  const container = element("div", "pair-list");
  const pairs = Array.isArray(item.spot_pairs) ? item.spot_pairs : [];
  if (!pairs.length) {
    container.append(element("span", "muted-text", "待确认"));
    return container;
  }
  pairs.slice(0, limit).forEach((pair) => container.append(element("span", "pair-chip", pair)));
  if (pairs.length > limit) container.append(element("span", "muted-text", `+${pairs.length - limit}`));
  return container;
}

function spotTagNode(item) {
  return item.seed_tag
    ? element("span", "seed-badge", "Seed Tag")
    : element("span", "plain-badge", "普通");
}

function assetTypeNode(item) {
  const type = item.asset_type || "其他";
  return element("span", `asset-badge asset-${type}`, type);
}

function stageLabel(value) {
  if (value === "pre_ipo") return "Pre-IPO";
  if (value === "pre_market") return "盘前";
  return "标准";
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

function launchValue(item) {
  return state.view === "spot" ? item.trading_starts_at : item.launch_time;
}

function tradingStatus(item) {
  const value = launchValue(item);
  if (!value) return "时间待确认";
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "时间待确认";
  return timestamp > Date.now() ? "待开盘" : "已开盘";
}

function statusNode(item) {
  const status = tradingStatus(item);
  const className = status === "待开盘" ? "status-upcoming" : status === "已开盘" ? "status-open" : "status-unknown";
  return element("span", `trade-status ${className}`, status);
}

function timeNode(item, value, withStatus) {
  const container = element("div", "time-block");
  container.append(element("span", "", formatBeijingTime(value)));
  if (withStatus) {
    const status = element("div", "status-line");
    status.append(statusNode(item));
    container.append(status);
  }
  return container;
}

function spotProjectNode(item, includeDate = false) {
  const project = element("div", "project-cell");
  project.append(element("strong", "", item.project_name || "未知项目"));
  const suffix = includeDate ? ` · ${formatBeijingTime(item.published_at).slice(0, 10)}` : "";
  project.append(element("span", "", `$${item.symbol || "-"}${suffix}`));
  return project;
}

function contractNode(item, includeDate = false) {
  const contract = element("div", "project-cell contract-cell");
  contract.append(element("strong", "", item.contract_symbol || "未知合约"));
  const underlying = item.underlying || item.base_symbol || "标的待确认";
  const suffix = includeDate ? ` · ${formatBeijingTime(item.published_at).slice(0, 10)}` : "";
  contract.append(element("span", "", `${underlying}${suffix}`));
  return contract;
}

function sourceLink(url, label) {
  const safe = safeUrl(url);
  if (!safe) return null;
  const anchor = element("a", "announcement-link", `${label} ↗`);
  anchor.href = safe;
  anchor.target = "_blank";
  anchor.rel = "noreferrer";
  return anchor;
}

function spotDetailNode(item) {
  const detail = element("div", "listing-detail");
  const summary = element("div", "detail-section");
  summary.append(element("span", "detail-label", "项目简介"));
  summary.append(element("p", "", item.summary || "Binance 公告暂未提供可识别的项目简介。"));
  summary.append(element("p", "", item.reason || "项目方入口需要人工二次确认。"));

  const links = element("div", "detail-section");
  links.append(element("span", "detail-label", "项目与合约地址"));
  links.append(spotLinksNode(item, true, true));

  const source = element("div", "detail-section source-note");
  source.append(element("span", "detail-label", "官方来源与说明"));
  source.append(element("p", "", "这条记录已同时通过标题和公告正文的现货上币复核。公开页面只能读取，不能修改源数据。"));
  const announcement = sourceLink(item.announcement_url, "查看 Binance 原始公告");
  if (announcement) source.append(announcement);
  detail.append(summary, links, source);
  return detail;
}

function parameterLine(label, value) {
  const line = element("div", "parameter-line");
  line.append(element("span", "", label), element("strong", "", value || "-"));
  return line;
}

function contractDetailNode(item) {
  const detail = element("div", "listing-detail contract-detail");
  const summary = element("div", "detail-section");
  summary.append(element("span", "detail-label", "合约标的"));
  summary.append(element("p", "", item.underlying || item.base_symbol || "标的待确认"));
  if (item.project_info) summary.append(element("p", "", item.project_info));
  const stage = element("div", "detail-badges");
  stage.append(
    element("span", "stage-badge", stageLabel(item.launch_stage)),
    element("span", "margin-badge", item.margin_type || "永续合约"),
  );
  if (item.product_variant) stage.append(element("span", "variant-badge", item.product_variant));
  summary.append(stage);

  const parameters = element("div", "detail-section parameter-grid");
  parameters.append(element("span", "detail-label parameter-heading", "公告参数快照"));
  parameters.append(
    parameterLine("最高杠杆", item.maximum_leverage),
    parameterLine("资金费率上限", item.capped_funding_rate),
    parameterLine("资金费结算", item.funding_interval),
    parameterLine("Tick Size", item.tick_size),
    parameterLine("最小交易量", item.min_trade_amount),
    parameterLine("最小名义价值", item.min_notional_value),
  );

  const source = element("div", "detail-section source-note risk-note");
  source.append(element("span", "detail-label", "风险与官方入口"));
  source.append(element("p", "", "永续合约是杠杆衍生品，不等于持有现货。参数可能被 Binance 后续调整，交易前应以当前合约页面为准。"));
  const links = element("div", "source-actions");
  const trade = sourceLink(item.trade_url, "打开合约交易页");
  const announcement = sourceLink(item.announcement_url, "查看原始公告");
  if (trade) links.append(trade);
  if (announcement) links.append(announcement);
  source.append(links);
  detail.append(summary, parameters, source);
  return detail;
}

function detailNode(item) {
  return state.view === "spot" ? spotDetailNode(item) : contractDetailNode(item);
}

function cell(child, className = "") {
  const td = element("td", className);
  if (typeof child === "string") td.textContent = child;
  else td.append(child);
  return td;
}

function toggleRecord(id) {
  state.expandedId = state.expandedId === id ? null : id;
  renderRecords();
}

function filteredItems() {
  const query = state.query.trim().toLowerCase();
  return currentItems().filter((item) => {
    const spotPairs = Array.isArray(item.spot_pairs) ? item.spot_pairs.join(" ") : "";
    const haystack = state.view === "spot"
      ? [item.project_name, item.symbol, item.title, spotPairs]
      : [item.contract_symbol, item.base_symbol, item.underlying, item.asset_type, item.title];
    const queryMatch = !query || haystack.some((value) => String(value || "").toLowerCase().includes(query));
    const statusMatch = state.tradingStatus === "全部" || state.tradingStatus === tradingStatus(item);
    if (state.view === "spot") {
      const tag = item.seed_tag ? "Seed" : "普通";
      return queryMatch && statusMatch && (state.tag === "全部" || state.tag === tag);
    }
    return queryMatch && statusMatch && (state.assetType === "全部" || state.assetType === item.asset_type);
  });
}

function toggleButton(item, expanded) {
  const toggle = element("button", "icon-button", expanded ? "▴" : "▾");
  toggle.type = "button";
  const name = state.view === "spot" ? item.project_name : item.contract_symbol;
  toggle.title = expanded ? "收起详情" : "展开详情";
  toggle.setAttribute("aria-label", `${expanded ? "收起" : "展开"} ${name}`);
  toggle.addEventListener("click", () => toggleRecord(recordId(item)));
  return toggle;
}

function contractLeverageNode(item) {
  return element("span", "leverage-badge", item.maximum_leverage || "待确认");
}

function renderDesktop(items) {
  const tbody = document.querySelector("#market-table-body");
  const fragment = document.createDocumentFragment();
  items.forEach((item) => {
    const id = recordId(item);
    const expanded = state.expandedId === id;
    const row = element("tr", expanded ? "listing-row expanded" : "listing-row");
    if (state.view === "spot") {
      row.append(
        cell(spotProjectNode(item)),
        cell(pairNode(item)),
        cell(timeNode(item, item.published_at, false), "time-cell"),
        cell(timeNode(item, item.trading_starts_at, true), "time-cell"),
        cell(spotLinksNode(item)),
        cell(spotTagNode(item)),
        cell(toggleButton(item, expanded)),
      );
    } else {
      row.append(
        cell(contractNode(item)),
        cell(assetTypeNode(item)),
        cell(timeNode(item, item.published_at, false), "time-cell"),
        cell(timeNode(item, item.launch_time, true), "time-cell"),
        cell(contractLeverageNode(item)),
        cell(element("span", "settlement-badge", item.settlement_asset || "-")),
        cell(toggleButton(item, expanded)),
      );
    }
    fragment.append(row);
    if (expanded) {
      const detailRow = element("tr", "detail-row");
      const detailCell = cell(detailNode(item));
      detailCell.colSpan = 7;
      detailRow.append(detailCell);
      fragment.append(detailRow);
    }
  });
  tbody.replaceChildren(fragment);
}

function renderMobile(items) {
  const list = document.querySelector("#mobile-list");
  const fragment = document.createDocumentFragment();
  items.forEach((item) => {
    const id = recordId(item);
    const expanded = state.expandedId === id;
    const article = element("article", "mobile-listing");
    const summary = element("button", "mobile-summary");
    summary.type = "button";
    if (state.view === "spot") {
      summary.append(spotProjectNode(item, true), spotTagNode(item), element("span", "expand-symbol", expanded ? "▴" : "▾"));
    } else {
      summary.append(contractNode(item, true), assetTypeNode(item), element("span", "expand-symbol", expanded ? "▴" : "▾"));
    }
    summary.addEventListener("click", () => toggleRecord(id));
    const meta = element("div", "mobile-meta");
    if (state.view === "spot") {
      meta.append(pairNode(item, 3), statusNode(item));
    } else {
      const contractMeta = element("div", "contract-mobile-meta");
      contractMeta.append(contractLeverageNode(item), element("span", "settlement-badge", item.settlement_asset || "-"));
      meta.append(contractMeta, statusNode(item));
    }
    article.append(summary, meta);
    if (expanded) article.append(detailNode(item));
    fragment.append(article);
  });
  list.replaceChildren(fragment);
}

function updateColumns() {
  const spot = state.view === "spot";
  const labels = spot
    ? ["项目", "现货交易对", "公告时间", "开盘时间", "项目入口", "风险标签"]
    : ["永续合约", "标的类别", "公告时间", "开盘时间", "最高杠杆", "结算资产"];
  labels.forEach((label, index) => {
    document.querySelector(`#column-${index + 1}`).textContent = label;
  });
  document.querySelector("#market-table").dataset.view = state.view;
}

function renderRecords() {
  const items = filteredItems();
  document.querySelector("#result-count").textContent = `当前显示 ${items.length} 条，共 ${currentItems().length} 条`;
  renderDesktop(items);
  renderMobile(items);
  document.querySelector("#empty-state").classList.toggle("hidden", items.length !== 0);
  document.querySelector(".table-wrap").classList.toggle("hidden", items.length === 0);
  document.querySelector("#mobile-list").classList.toggle("hidden", items.length === 0);
}

function showNotice(message, isError = false) {
  const notice = document.querySelector("#notice");
  notice.textContent = message;
  notice.className = isError ? "notice error-notice" : "notice";
}

function updateViewControls() {
  const spot = state.view === "spot";
  document.querySelector("#workspace-title").textContent = spot ? "现货上币记录" : "永续合约上线记录";
  document.querySelector("#spot-tag-filter").classList.toggle("hidden", !spot);
  document.querySelector("#asset-filter").classList.toggle("hidden", spot);
  document.querySelector("#current-data-link").href = DATA_PATHS[state.view];
  document.querySelectorAll("button[data-view]").forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  updateColumns();
}

function switchView(view) {
  if (!DATA_PATHS[view] || state.view === view) return;
  state.view = view;
  state.query = "";
  state.tag = "全部";
  state.assetType = "全部";
  state.tradingStatus = "全部";
  document.querySelector("#search-input").value = "";
  document.querySelector("#asset-filter").value = "全部";
  document.querySelector("#status-filter").value = "全部";
  document.querySelectorAll("[data-tag]").forEach((button) => button.classList.toggle("active", button.dataset.tag === "全部"));
  const first = currentItems()[0];
  state.expandedId = first ? recordId(first) : null;
  updateViewControls();
  renderRecords();
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

async function loadPayload(path, expectedType) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path} HTTP ${response.status}`);
  const payload = await response.json();
  if (!payload || !Array.isArray(payload.items)) throw new Error(`${path} 的 items 不是列表`);
  if (payload.items.some((item) => item.listing_type !== expectedType)) {
    throw new Error(`${path} 出现非 ${expectedType} 记录`);
  }
  return payload;
}

async function loadData() {
  try {
    const [spotPayload, contractPayload] = await Promise.all([
      loadPayload(DATA_PATHS.spot, "spot"),
      loadPayload(DATA_PATHS.perpetual, "perpetual"),
    ]);
    state.spotItems = spotPayload.items;
    state.contractItems = contractPayload.items;
    state.generatedAt.spot = spotPayload.generated_at;
    state.generatedAt.perpetual = contractPayload.generated_at;
    const first = currentItems()[0];
    state.expandedId = first ? recordId(first) : null;

    document.querySelector("#stat-spot").textContent = String(state.spotItems.length);
    document.querySelector("#stat-contracts").textContent = String(state.contractItems.length);
    document.querySelector("#spot-tab-count").textContent = String(state.spotItems.length);
    document.querySelector("#contract-tab-count").textContent = String(state.contractItems.length);
    document.querySelector("#stat-upcoming").textContent = String(
      [...state.spotItems, ...state.contractItems].filter((item) => {
        const value = item.trading_starts_at || item.launch_time;
        return value && new Date(value).getTime() > Date.now();
      }).length,
    );
    const syncTimes = [spotPayload.generated_at, contractPayload.generated_at]
      .filter(Boolean)
      .sort();
    const newestSync = syncTimes[syncTimes.length - 1];
    document.querySelector("#sync-state").textContent = `最近同步：${formatSyncTime(newestSync)}`;
    updateViewControls();
    renderRecords();
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    showNotice(`遇到错误：读取公开数据失败，返回内容：${detail}`, true);
    document.querySelector("#result-count").textContent = "公开数据暂时无法读取";
  }
}

document.querySelector("#search-input").addEventListener("input", (event) => {
  state.query = event.target.value;
  renderRecords();
});

document.querySelector("#status-filter").addEventListener("change", (event) => {
  state.tradingStatus = event.target.value;
  renderRecords();
});

document.querySelector("#asset-filter").addEventListener("change", (event) => {
  state.assetType = event.target.value;
  renderRecords();
});

document.querySelectorAll("[data-tag]").forEach((button) => {
  button.addEventListener("click", () => {
    state.tag = button.dataset.tag;
    document.querySelectorAll("[data-tag]").forEach((item) => item.classList.toggle("active", item === button));
    renderRecords();
  });
});

document.querySelectorAll("button[data-view]").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});

document.querySelector("#copy-endpoint").addEventListener("click", async () => {
  const endpoint = new URL(DATA_PATHS[state.view], window.location.href).href;
  try {
    await copyText(endpoint);
    showNotice(`${state.view === "spot" ? "现货" : "永续合约"} AI 接口地址已复制`);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    showNotice(`遇到错误：复制失败，返回内容：${detail}`, true);
  }
});

loadData();
