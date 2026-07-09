const fmtMoney = (value) => {
  const n = Number(value || 0);
  const sign = n >= 0 ? "" : "-";
  const abs = Math.abs(n);
  if (abs >= 100000000) return `${sign}${(abs / 100000000).toFixed(2)}亿`;
  if (abs >= 10000) return `${sign}${(abs / 10000).toFixed(0)}万`;
  return `${sign}${abs.toFixed(0)}`;
};

const fmtPct = (value) => value === null || value === undefined || value === "" ? "--" : `${Number(value).toFixed(2)}%`;
const clsMoney = (value) => Number(value || 0) >= 0 ? "pos" : "neg";

async function loadJson(path, fallback) {
  try {
    const res = await fetch(`${path}?v=${Date.now()}`);
    if (!res.ok) throw new Error(`${path} ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn("load failed", path, err);
    return fallback;
  }
}

function priceText(item) {
  const p = item.price || {};
  if (p.price_confirmed) return `收盘 ${p.close} / ${fmtPct(p.pct_change)}`;
  return "收盘价未确认";
}

function renderRank(container, items, type = "stock") {
  container.innerHTML = (items || []).slice(0, 20).map((item, idx) => {
    const name = type === "stock" ? `${item.gpmc || "--"} ${item.gpdm || ""}` : item.name;
    const sub = type === "stock" ? priceText(item) : `出现 ${item.count || 0} 次`;
    return `<article class="card">
      <div class="row">
        <div>
          <div class="name">${idx + 1}. ${name || "--"}</div>
          <div class="sub">${sub}</div>
        </div>
        <div class="money ${clsMoney(item.jmr)}">${fmtMoney(item.jmr)}</div>
      </div>
    </article>`;
  }).join("") || `<article class="card sub">暂无真实数据</article>`;
}

function renderPool(pool) {
  document.querySelector("#poolUpdated").textContent = pool.updatedAt ? pool.updatedAt.slice(5, 16).replace("T", " ") : "--";
  const milestones = pool.summary?.milestones || {};
  document.querySelector("#poolStats").innerHTML = ["T+1", "T+3", "T+5", "T+10"].map((key) => {
    const item = milestones[key] || {};
    const winRate = item.winRate === null || item.winRate === undefined ? "--" : `${item.winRate}%`;
    const avg = item.avgReturnPct === null || item.avgReturnPct === undefined ? "--" : fmtPct(item.avgReturnPct);
    return `<div class="stat"><div class="sub">${key}胜率</div><strong>${winRate}</strong><div class="sub">样本 ${item.confirmedCount || 0} / 均值 ${avg}</div></div>`;
  }).join("");

  document.querySelector("#poolList").innerHTML = (pool.entries || []).slice(0, 40).map((item) => {
    const risk = item.riskAssessment || {};
    const riskClass = risk.level === "高" ? "risk-high" : risk.level === "中" ? "risk-mid" : "risk-low";
    const returns = ["T+1", "T+3", "T+5", "T+10"].map((key) => {
      const ret = item.holdingReturns?.[key] || {};
      const val = ret.confirmed ? fmtPct(ret.returnPct) : "等待";
      const klass = ret.returnPct >= 0 ? "pos" : "neg";
      return `<div class="retbox"><div class="sub">${key}</div><strong class="ret ${klass}">${val}</strong></div>`;
    }).join("");
    return `<article class="card">
      <div class="row">
        <div>
          <div class="name">${item.gpmc} ${item.gpdm}</div>
          <div class="sub">入选 ${item.date} / 基准收盘 ${item.entryPrice ?? "未确认"}</div>
        </div>
        <div class="money ${clsMoney(item.jmr)}">${fmtMoney(item.jmr)}</div>
      </div>
      <div class="chips">${(item.labels || []).map(label => `<span class="chip">${label}</span>`).join("")}</div>
      <div class="returns">${returns}</div>
      <p class="risk ${riskClass}">风险：${risk.level || "--"} / 追高风险：${risk.chaseRisk || "--"}</p>
      <p class="meta">${(risk.notes || []).join(" ") || "等待风险评估"}</p>
    </article>`;
  }).join("") || `<article class="card sub">预选池暂无确认数据</article>`;
}

function setupTabs() {
  document.querySelectorAll(".segmented button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".segmented button").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
      button.classList.add("active");
      document.querySelector(`#${button.dataset.view}`).classList.add("active");
    });
  });
}

function renderQuality(summary) {
  const policy = summary.pricePolicy || {};
  const confirmed = policy.confirmedRows ?? 0;
  const total = summary.rowCount ?? 0;
  const missing = policy.unconfirmedRows ?? 0;
  const strict = "通达信龙虎榜 + 东方财富不复权日K；不估算、不倒推、不虚标。";
  document.querySelector("#qualityNotice").innerHTML = missing > 0
    ? `数据规则：${strict}<br>当前收盘价确认 ${confirmed}/${total}，未确认 ${missing} 条会显示为“收盘价未确认”。`
    : `数据规则：${strict}<br>当前收盘价已全部确认：${confirmed}/${total}。`;
}

function renderDashboard(data) {
  const s = data.summary || {};
  document.querySelector("#updatedText").textContent = s.updatedAt ? `更新时间 ${s.updatedAt}` : "暂无更新";
  document.querySelector("#datePill").textContent = s.date || "--";
  document.querySelector("#rowCount").textContent = s.rowCount ?? "--";
  document.querySelector("#yzCount").textContent = s.activeYzCount ?? "--";
  document.querySelector("#yybCount").textContent = s.activeYybCount ?? "--";
  document.querySelector("#priceCount").textContent = `${s.pricePolicy?.confirmedRows ?? 0}/${s.rowCount ?? 0}`;
  renderQuality(s);
  renderRank(document.querySelector("#topStocks"), s.topStocksByNetBuy, "stock");
  renderRank(document.querySelector("#sellStocks"), s.topStocksByNetSell, "stock");
  renderRank(document.querySelector("#topYz"), s.topYzByNetBuy, "yz");
  renderRank(document.querySelector("#topYyb"), s.topYybByNetBuy, "yyb");
}

async function loadByDate(value) {
  const path = value && value !== "latest" ? `./data/daily/${value}.json` : "./data/latest.json";
  const data = await loadJson(path, { summary: {}, rows: [] });
  renderDashboard(data);
}

async function setupDateSelect(index) {
  const select = document.querySelector("#dateSelect");
  const dates = (index.dates || []).slice().reverse();
  select.innerHTML = `<option value="latest">最新数据${index.latestDate ? `（${index.latestDate}）` : ""}</option>` +
    dates.map(date => `<option value="${date}">${date}</option>`).join("");
  select.addEventListener("change", () => loadByDate(select.value));
}

async function main() {
  setupTabs();
  const index = await loadJson("./data/index.json", { dates: [], latestDate: "" });
  await setupDateSelect(index);
  await loadByDate("latest");
  const pool = await loadJson("./data/preselect-pool.json", { entries: [], summary: {} });
  renderPool(pool);
}

main();
