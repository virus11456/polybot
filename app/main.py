"""
Roan Arbitrage Machine — FastAPI main application.
Integrates scanner, Telegram bot, and exposes REST API + web dashboard.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Query, Request
from fastapi.responses import JSONResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.database import get_db, engine, Base
from app.models import Market, RoanSignal, RoanPerformance  # noqa: F401 — ensures models registered

logger = logging.getLogger(__name__)

# ─── Lazy-initialised singletons ─────────────────────────────────────────────

_scanner = None
_bot = None


def _get_scanner():
    global _scanner
    if _scanner is None:
        try:
            from app.core.roan_scanner import RoanScanner
            _scanner = RoanScanner()
        except Exception as e:
            logger.warning(f"Scanner not available: {e}")
    return _scanner


def _get_bot():
    global _bot
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if _bot is None and token and chat_id:
        try:
            from app.telegram.roan_bot import RoanTelegramBot
            _bot = RoanTelegramBot(token=token, chat_id=chat_id)
        except Exception as e:
            logger.warning(f"Telegram bot not available: {e}")
    return _bot


# ─── Lifecycle ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up Roan Arbitrage Machine...")

    scanner = _get_scanner()
    if scanner:
        asyncio.create_task(scanner.continuous_scan())
        logger.info("Scanner started.")
    else:
        logger.warning("Scanner unavailable — skipping.")

    bot = _get_bot()
    if bot:
        logger.info("Telegram bot initialised.")
        webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL")
        if webhook_url:
            asyncio.create_task(_register_webhook(bot, webhook_url))
        else:
            logger.warning(
                "TELEGRAM_WEBHOOK_URL not set — webhook not registered. "
                "Commands from users will not be received."
            )

    yield

    # Shutdown
    bot = _get_bot()
    if bot:
        await bot.close()
        logger.info("Telegram bot session closed.")
    await engine.dispose()
    logger.info("Database engine disposed.")


async def _register_webhook(bot, webhook_url: str):
    """在啟動後台注冊 Telegram webhook（避免阻塞啟動）。"""
    info = await bot.get_webhook_info()
    current_url = info.get("result", {}).get("url", "")
    if current_url == webhook_url:
        logger.info(f"Telegram webhook 已是最新，無需重新設定：{webhook_url}")
        return
    ok = await bot.set_webhook(webhook_url)
    if ok:
        logger.info(f"Telegram webhook 設定成功：{webhook_url}")
    else:
        logger.error("Telegram webhook 設定失敗，請手動呼叫 POST /api/telegram/setup-webhook")


app = FastAPI(title="Roan Arbitrage Machine", version="1.0.0", lifespan=lifespan)


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    scanner = _get_scanner()
    bot = _get_bot()
    return {
        "status": "running",
        "scanner": "active" if scanner else "unavailable",
        "telegram_bot": "active" if bot else "unavailable",
        "last_scan": scanner._last_scan_time if scanner else None,
    }


@app.get("/api/signals")
async def get_signals(
    limit: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent arbitrage signals."""
    result = await db.execute(
        text(
            "SELECT rs.id, rs.signal_type, rs.profit_pct, rs.confidence, "
            "rs.suggested_position, rs.status, rs.created_at, "
            "m.title as market_title, m.category "
            "FROM roan_signals rs "
            "LEFT JOIN markets m ON m.id = rs.market_id "
            "ORDER BY rs.created_at DESC LIMIT :limit"
        ),
        {"limit": limit},
    )
    rows = result.mappings().all()
    return [dict(row) for row in rows]


@app.get("/api/performance")
async def get_performance(db: AsyncSession = Depends(get_db)):
    """Return cumulative performance statistics."""
    result = await db.execute(
        text(
            "SELECT date, signals_sent, signals_profitable, "
            "total_profit_usd, capital_used "
            "FROM roan_performance ORDER BY date DESC LIMIT 30"
        )
    )
    rows = result.mappings().all()
    return [dict(row) for row in rows]


@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    """
    Telegram webhook 端點：接收 Telegram 更新事件並路由至 bot。
    """
    bot = _get_bot()
    if not bot:
        return JSONResponse({"ok": False, "error": "bot not configured"}, status_code=503)

    try:
        update = await request.json()
        await bot.handle_update(update)
        return {"ok": True}
    except Exception as e:
        logger.error(f"Webhook 處理失敗：{e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/telegram/setup-webhook")
async def setup_webhook():
    """手動（重新）向 Telegram 注冊 webhook URL。"""
    bot = _get_bot()
    if not bot:
        return {"ok": False, "error": "bot not configured"}
    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL")
    if not webhook_url:
        return {"ok": False, "error": "TELEGRAM_WEBHOOK_URL env var not set"}
    ok = await bot.set_webhook(webhook_url)
    info = await bot.get_webhook_info()
    return {"ok": ok, "webhook_info": info}


@app.get("/api/telegram/webhook-info")
async def get_webhook_info():
    """查詢目前 Telegram webhook 狀態。"""
    bot = _get_bot()
    if not bot:
        return {"ok": False, "error": "bot not configured"}
    return await bot.get_webhook_info()


@app.post("/api/report/daily")
async def trigger_daily_report():
    """手動觸發每日報告（測試用）。"""
    bot = _get_bot()
    if not bot:
        return {"ok": False, "error": "bot not configured"}
    await bot.send_daily_report()
    return {"ok": True, "message": "Daily report sent"}


@app.post("/api/scan/trigger")
async def trigger_scan():
    """手動觸發一次掃描週期（測試用）。"""
    scanner = _get_scanner()
    if not scanner:
        return {"ok": False, "error": "scanner not available"}
    try:
        signals = await scanner.run_scan_cycle()
        return {"ok": True, "signals_detected": len(signals)}
    except Exception as e:
        logger.error(f"手動掃描失敗：{e}")
        return {"ok": False, "error": str(e)}


@app.get("/api/markets")
async def get_markets():
    """回傳最近一次掃描到的市場清單（按類別分組）。"""
    scanner = _get_scanner()
    if not scanner:
        return {"ok": False, "error": "scanner not available", "markets": []}
    summary = scanner.get_last_markets_summary()
    return {"ok": True, **summary}


# ─── Web Dashboard ────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Roan 套利機器 Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f0f1a; color: #e0e0e0; min-height: 100vh; }
  header { background: linear-gradient(135deg, #1a1a2e, #16213e);
           padding: 20px 32px; border-bottom: 1px solid #2a2a4a;
           display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 1.5rem; color: #7c83fd; }
  header .status-badge { padding: 4px 10px; border-radius: 12px; font-size: 0.75rem;
                          background: #1e3a2f; color: #4ade80; border: 1px solid #4ade80; }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px 16px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card { background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 12px; padding: 20px; }
  .card h3 { font-size: 0.8rem; color: #888; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }
  .card .value { font-size: 2rem; font-weight: 700; color: #7c83fd; }
  .card .sub { font-size: 0.8rem; color: #666; margin-top: 4px; }
  .section { background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 12px;
             padding: 20px; margin-bottom: 24px; }
  .section h2 { font-size: 1rem; color: #ccc; margin-bottom: 16px;
                border-bottom: 1px solid #2a2a4a; padding-bottom: 10px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th { text-align: left; padding: 8px 12px; color: #888; font-size: 0.75rem;
       text-transform: uppercase; border-bottom: 1px solid #2a2a4a; }
  td { padding: 10px 12px; border-bottom: 1px solid #1e1e30; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #1e1e35; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 8px; font-size: 0.75rem; font-weight: 600; }
  .badge-logic { background: #1e3a5f; color: #60a5fa; }
  .badge-combo { background: #2d1b4e; color: #c084fc; }
  .badge-high { background: #1e3a2f; color: #4ade80; }
  .badge-mid { background: #3a2e1a; color: #fbbf24; }
  .badge-low { background: #3a1a1a; color: #f87171; }
  .profit { color: #4ade80; font-weight: 600; }
  .actions { display: flex; gap: 10px; margin-bottom: 24px; flex-wrap: wrap; }
  button { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer;
           font-size: 0.875rem; font-weight: 600; transition: all 0.2s; }
  .btn-primary { background: #7c83fd; color: #fff; }
  .btn-primary:hover { background: #6366f1; }
  .btn-secondary { background: #2a2a4a; color: #ccc; border: 1px solid #3a3a5a; }
  .btn-secondary:hover { background: #3a3a5a; }
  .btn-danger { background: #1e1e30; color: #f87171; border: 1px solid #f87171; }
  .btn-danger:hover { background: #2a1a1a; }
  #toast { position: fixed; bottom: 24px; right: 24px; background: #1e3a2f;
           color: #4ade80; padding: 12px 20px; border-radius: 8px; border: 1px solid #4ade80;
           display: none; z-index: 9999; font-size: 0.875rem; }
  .loading { color: #666; font-style: italic; text-align: center; padding: 20px; }
  .perf-chart { display: flex; align-items: flex-end; gap: 6px; height: 80px; margin-top: 12px; }
  .bar-wrap { display: flex; flex-direction: column; align-items: center; flex: 1; }
  .bar { width: 100%; background: #7c83fd44; border-radius: 4px 4px 0 0; min-height: 2px;
         transition: height 0.3s; }
  .bar-label { font-size: 0.6rem; color: #666; margin-top: 4px; text-align: center; }
  .scanner-cats { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
  .cat-chip { padding: 4px 10px; border-radius: 12px; font-size: 0.75rem;
              background: #1e2a4a; color: #7c83fd; border: 1px solid #2a3a5a; }
  @media (max-width: 600px) { .grid { grid-template-columns: 1fr 1fr; } }
</style>
</head>
<body>
<header>
  <span style="font-size:1.5rem">🤖</span>
  <h1>Roan 套利機器</h1>
  <span class="status-badge" id="statusBadge">載入中...</span>
  <span style="margin-left:auto; font-size:0.75rem; color:#666;" id="lastScan"></span>
</header>

<div class="container">
  <div class="actions">
    <button class="btn-primary" onclick="triggerScan()">⚡ 立即掃描</button>
    <button class="btn-secondary" onclick="loadAll()">🔄 重新整理</button>
    <button class="btn-secondary" onclick="setupWebhook()">🔗 設定 Webhook</button>
    <button class="btn-danger" onclick="sendDailyReport()">📊 發送日報</button>
  </div>

  <div class="grid">
    <div class="card">
      <h3>今日信號</h3>
      <div class="value" id="todaySignals">—</div>
      <div class="sub">套利機會偵測</div>
    </div>
    <div class="card">
      <h3>平均信心度</h3>
      <div class="value" id="avgConfidence">—</div>
      <div class="sub">最近 20 筆</div>
    </div>
    <div class="card">
      <h3>平均預期獲利</h3>
      <div class="value" id="avgProfit">—</div>
      <div class="sub">最近 20 筆</div>
    </div>
    <div class="card">
      <h3>掃描市場數</h3>
      <div class="value" id="marketCount">—</div>
      <div class="sub" id="categoriesChips"></div>
    </div>
  </div>

  <div class="section">
    <h2>📈 近期績效（最近 7 天）</h2>
    <div class="perf-chart" id="perfChart"><div class="loading">載入中...</div></div>
    <div id="perfTable"></div>
  </div>

  <div class="section">
    <h2>🔍 最新套利信號</h2>
    <div id="signalsTable"><div class="loading">載入中...</div></div>
  </div>

  <div class="section">
    <h2>🗂 市場分類概覽</h2>
    <div id="marketsTable"><div class="loading">載入中...</div></div>
  </div>
</div>

<div id="toast"></div>

<script>
const API = '';

function showToast(msg, ok=true) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.background = ok ? '#1e3a2f' : '#3a1a1a';
  t.style.color = ok ? '#4ade80' : '#f87171';
  t.style.border = ok ? '1px solid #4ade80' : '1px solid #f87171';
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
}

async function fetchJSON(url) {
  const r = await fetch(url);
  return r.json();
}

async function loadHealth() {
  try {
    const h = await fetchJSON('/health');
    const badge = document.getElementById('statusBadge');
    badge.textContent = h.scanner === 'active' ? '● 運行中' : '● 掃描器離線';
    badge.style.color = h.scanner === 'active' ? '#4ade80' : '#f87171';
    if (h.last_scan) document.getElementById('lastScan').textContent = '上次掃描：' + h.last_scan;
  } catch(e) {}
}

async function loadSignals() {
  try {
    const signals = await fetchJSON('/api/signals?limit=20');
    const today = new Date().toISOString().slice(0, 10);
    const todayCount = signals.filter(s => s.created_at && s.created_at.startsWith(today)).length;
    document.getElementById('todaySignals').textContent = todayCount;

    const avgConf = signals.length ? (signals.reduce((s,r) => s + parseFloat(r.confidence||0), 0) / signals.length) : 0;
    const avgProfit = signals.length ? (signals.reduce((s,r) => s + parseFloat(r.profit_pct||0), 0) / signals.length) : 0;
    document.getElementById('avgConfidence').textContent = (avgConf*100).toFixed(1) + '%';
    document.getElementById('avgProfit').textContent = (avgProfit*100).toFixed(2) + '%';

    const container = document.getElementById('signalsTable');
    if (!signals.length) { container.innerHTML = '<div class="loading">尚無信號紀錄</div>'; return; }

    let html = '<table><thead><tr><th>時間</th><th>類型</th><th>市場</th><th>信心度</th><th>預期獲利</th><th>建議倉位</th><th>狀態</th></tr></thead><tbody>';
    for (const s of signals) {
      const typeBadge = s.signal_type === 'logic_arb'
        ? '<span class="badge badge-logic">邏輯依賴</span>'
        : '<span class="badge badge-combo">多條件組合</span>';
      const conf = parseFloat(s.confidence||0);
      const confBadge = conf >= 0.8 ? `<span class="badge badge-high">${(conf*100).toFixed(1)}%</span>`
        : conf >= 0.65 ? `<span class="badge badge-mid">${(conf*100).toFixed(1)}%</span>`
        : `<span class="badge badge-low">${(conf*100).toFixed(1)}%</span>`;
      const profit = parseFloat(s.profit_pct||0);
      const time = (s.created_at||'').slice(0, 16).replace('T', ' ');
      const market = (s.market_title || s.market_id || 'N/A').slice(0, 50);
      html += `<tr>
        <td style="color:#888;font-size:0.8rem">${time}</td>
        <td>${typeBadge}</td>
        <td title="${market}">${market}</td>
        <td>${confBadge}</td>
        <td class="profit">+${(profit*100).toFixed(2)}%</td>
        <td>$${parseFloat(s.suggested_position||0).toFixed(2)}</td>
        <td><span class="badge" style="background:#1e2a3a;color:#60a5fa">${s.status||'-'}</span></td>
      </tr>`;
    }
    html += '</tbody></table>';
    container.innerHTML = html;
  } catch(e) {
    document.getElementById('signalsTable').innerHTML = '<div class="loading">載入失敗</div>';
  }
}

async function loadPerformance() {
  try {
    const perf = await fetchJSON('/api/performance');
    const recent = perf.slice(0, 7).reverse();
    const chart = document.getElementById('perfChart');
    if (!recent.length) { chart.innerHTML = '<div class="loading">尚無績效紀錄</div>'; return; }

    const maxProfit = Math.max(...recent.map(p => parseFloat(p.total_profit_usd||0)), 1);
    let chartHtml = '';
    for (const p of recent) {
      const profit = parseFloat(p.total_profit_usd||0);
      const pct = Math.max((profit / maxProfit) * 70, 2);
      const date = (p.date||'').slice(5);
      chartHtml += `<div class="bar-wrap">
        <div class="bar" style="height:${pct}px" title="${date}: $${profit.toFixed(2)}"></div>
        <div class="bar-label">${date}</div>
      </div>`;
    }
    chart.innerHTML = chartHtml;

    let tableHtml = '<table style="margin-top:16px"><thead><tr><th>日期</th><th>信號數</th><th>獲利信號</th><th>命中率</th><th>總獲利</th><th>動用資本</th></tr></thead><tbody>';
    for (const p of perf.slice(0, 7)) {
      const hitRate = p.signals_sent > 0 ? (p.signals_profitable / p.signals_sent * 100).toFixed(1) : '0.0';
      tableHtml += `<tr>
        <td>${p.date}</td><td>${p.signals_sent}</td><td>${p.signals_profitable}</td>
        <td>${hitRate}%</td>
        <td class="profit">$${parseFloat(p.total_profit_usd||0).toFixed(2)}</td>
        <td>$${parseFloat(p.capital_used||0).toFixed(2)}</td>
      </tr>`;
    }
    tableHtml += '</tbody></table>';
    document.getElementById('perfTable').innerHTML = tableHtml;
  } catch(e) {}
}

async function loadMarkets() {
  try {
    const data = await fetchJSON('/api/markets');
    document.getElementById('marketCount').textContent = data.total || '—';
    const by_cat = data.by_category || {};
    const cats = Object.keys(by_cat);
    if (cats.length) {
      document.getElementById('categoriesChips').innerHTML = cats.map(c =>
        `<span class="cat-chip">${c}</span>`
      ).join('');
    }

    const CAT_LABELS = {
      macro: '📈 總體經濟', weather: '🌦️ 天氣', politics: '🗳️ 政治',
      earnings: '💰 財報', regulatory: '⚖️ 監管', geopolitical: '🌍 地緣政治', other: '🔹 其他'
    };
    let html = '';
    for (const [cat, mkts] of Object.entries(by_cat)) {
      const label = CAT_LABELS[cat] || cat;
      html += `<div style="margin-bottom:16px"><h3 style="color:#7c83fd;font-size:0.85rem;margin-bottom:8px">${label}（${mkts.length} 個）</h3>`;
      html += '<table><thead><tr><th>市場名稱</th><th>YES 機率</th><th>流動性</th></tr></thead><tbody>';
      for (const m of mkts) {
        const yes = m.yes_price != null ? (m.yes_price*100).toFixed(1)+'%' : 'N/A';
        const liq = '$' + (m.liquidity||0).toLocaleString();
        html += `<tr><td>${(m.title||'').slice(0,80)}</td><td class="profit">${yes}</td><td>${liq}</td></tr>`;
      }
      html += '</tbody></table></div>';
    }
    document.getElementById('marketsTable').innerHTML = html || '<div class="loading">尚無市場資料</div>';
  } catch(e) {}
}

async function triggerScan() {
  try {
    const r = await fetch('/api/scan/trigger', {method:'POST'});
    const d = await r.json();
    if (d.ok) { showToast(`掃描完成，偵測到 ${d.signals_detected} 個信號`); loadAll(); }
    else showToast('掃描失敗：' + d.error, false);
  } catch(e) { showToast('請求失敗', false); }
}

async function setupWebhook() {
  try {
    const r = await fetch('/api/telegram/setup-webhook', {method:'POST'});
    const d = await r.json();
    if (d.ok) showToast('Webhook 設定成功！');
    else showToast('Webhook 設定失敗：' + d.error, false);
  } catch(e) { showToast('請求失敗', false); }
}

async function sendDailyReport() {
  try {
    const r = await fetch('/api/report/daily', {method:'POST'});
    const d = await r.json();
    if (d.ok) showToast('每日報告已發送至 Telegram');
    else showToast('發送失敗：' + d.error, false);
  } catch(e) { showToast('請求失敗', false); }
}

async function loadAll() {
  await Promise.all([loadHealth(), loadSignals(), loadPerformance(), loadMarkets()]);
}

loadAll();
setInterval(loadAll, 60000);  // 每分鐘自動刷新
</script>
</body>
</html>"""


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """網頁版 Dashboard — 顯示信號、績效、市場概覽。"""
    return HTMLResponse(content=DASHBOARD_HTML)
