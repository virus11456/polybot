"""
Roan Arbitrage Machine — FastAPI main application.
Integrates scanner, Telegram bot, and exposes REST API + Web Dashboard.
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


# ─── Web Dashboard ────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Roan 套利機器 Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  .header { background: #1e293b; border-bottom: 1px solid #334155; padding: 16px 24px; display: flex; align-items: center; gap: 12px; }
  .header h1 { font-size: 20px; font-weight: 700; color: #f1f5f9; }
  .header .subtitle { font-size: 13px; color: #64748b; }
  .status-dot { width: 10px; height: 10px; border-radius: 50%; background: #22c55e; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; }
  .card-title { font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 8px; }
  .card-value { font-size: 28px; font-weight: 700; color: #f1f5f9; }
  .card-sub { font-size: 12px; color: #64748b; margin-top: 4px; }
  .section-title { font-size: 16px; font-weight: 600; color: #f1f5f9; margin-bottom: 12px; }
  .table-wrap { background: #1e293b; border: 1px solid #334155; border-radius: 12px; overflow: hidden; margin-bottom: 24px; }
  table { width: 100%; border-collapse: collapse; }
  th { background: #0f172a; font-size: 11px; color: #64748b; text-transform: uppercase; padding: 10px 16px; text-align: left; }
  td { padding: 12px 16px; border-top: 1px solid #1e293b; font-size: 13px; }
  tr:hover td { background: #0f172a22; }
  .badge { display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 600; }
  .badge-yes { background: #16a34a22; color: #4ade80; border: 1px solid #16a34a44; }
  .badge-no { background: #dc262622; color: #f87171; border: 1px solid #dc262644; }
  .badge-logic { background: #3b82f622; color: #60a5fa; border: 1px solid #3b82f644; }
  .badge-combo { background: #a855f722; color: #c084fc; border: 1px solid #a855f744; }
  .conf-high { color: #4ade80; }
  .conf-med { color: #fbbf24; }
  .conf-low { color: #94a3b8; }
  .markets-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .market-card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 16px; }
  .market-cat { font-size: 11px; color: #64748b; text-transform: uppercase; margin-bottom: 8px; }
  .market-item { padding: 8px 0; border-top: 1px solid #334155; }
  .market-item:first-of-type { border-top: none; }
  .market-title { font-size: 13px; color: #e2e8f0; margin-bottom: 4px; }
  .market-title a { color: #e2e8f0; text-decoration: none; }
  .market-title a:hover { color: #7c83fd; text-decoration: underline; }
  .market-meta { font-size: 11px; color: #64748b; display: flex; gap: 12px; }
  .yes-high { color: #4ade80; font-weight: 600; }
  .yes-low { color: #f87171; font-weight: 600; }
  .refresh-btn { background: #3b82f6; color: white; border: none; border-radius: 8px; padding: 8px 16px; font-size: 13px; cursor: pointer; }
  .refresh-btn:hover { background: #2563eb; }
  .last-update { font-size: 11px; color: #64748b; }
  .empty { text-align: center; padding: 40px; color: #64748b; }
  .signal-link { color: #e2e8f0; text-decoration: none; }
  .signal-link:hover { color: #7c83fd; text-decoration: underline; }
</style>
</head>
<body>
<div class="header">
  <div class="status-dot"></div>
  <div>
    <h1>🎯 Roan 套利機器 Dashboard</h1>
    <div class="subtitle">Polymarket 套利信號即時監控</div>
  </div>
  <div style="margin-left:auto;display:flex;align-items:center;gap:12px;">
    <span class="last-update" id="lastUpdate">載入中...</span>
    <button class="refresh-btn" onclick="loadAll()">🔄 刷新</button>
  </div>
</div>
<div class="container">
  <div class="grid" id="stats">
    <div class="card"><div class="card-title">掃描市場數</div><div class="card-value" id="totalMarkets">-</div><div class="card-sub">最後掃描：<span id="lastScan">-</span></div></div>
    <div class="card"><div class="card-title">今日信號數</div><div class="card-value" id="todaySignals">-</div><div class="card-sub">總計信號數</div></div>
    <div class="card"><div class="card-title">掃描狀態</div><div class="card-value" id="scannerStatus" style="font-size:18px">-</div><div class="card-sub">Bot 狀態：<span id="botStatus">-</span></div></div>
    <div class="card"><div class="card-title">最新信號時間</div><div class="card-value" id="latestSignalTime" style="font-size:18px">-</div><div class="card-sub">最新套利機會</div></div>
  </div>

  <div class="section-title">📊 最新套利信號</div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>類型</th><th>市場</th><th>進場</th><th>目標</th><th>停損</th><th>獲利</th><th>置信度</th><th>倉位</th><th>時間</th></tr></thead>
      <tbody id="signalsTable"><tr><td colspan="9" class="empty">載入中...</td></tr></tbody>
    </table>
  </div>

  <div class="section-title">🌐 市場概況（各類別前5名）</div>
  <div class="markets-grid" id="marketsGrid">載入中...</div>
</div>

<script>
const BASE = '';
const POLY_BASE = 'https://polymarket.com/event/';
const TYPE_MAP = {
  logic_arb: ['🔵', '邏輯依賴', 'badge-logic'],
  combo_arb: ['🟣', '多條件組合', 'badge-combo'],
  high_prob_yes: ['🟢', '高機率YES', 'badge-yes'],
  high_prob_no: ['🔴', '高機率NO', 'badge-no'],
};

async function fetchJSON(url) {
  const r = await fetch(url);
  return r.json();
}

function confClass(c) {
  return c >= 0.80 ? 'conf-high' : c >= 0.55 ? 'conf-med' : 'conf-low';
}

function fmtTime(ts) {
  if (!ts) return '-';
  const d = new Date(ts);
  return d.toLocaleString('zh-TW', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' });
}

function polyUrl(slug, polymarket_id) {
  if (slug) return POLY_BASE + slug;
  if (polymarket_id) return 'https://polymarket.com/market/' + polymarket_id;
  return null;
}

async function loadHealth() {
  try {
    const h = await fetchJSON(BASE + '/health');
    document.getElementById('scannerStatus').textContent = h.scanner === 'active' ? '✅ 運行中' : '❌ 停止';
    document.getElementById('botStatus').textContent = h.telegram_bot === 'active' ? '✅ 活躍' : '❌ 停止';
    document.getElementById('lastScan').textContent = h.last_scan || '-';
  } catch(e) { console.error(e); }
}

async function loadSignals() {
  try {
    const data = await fetchJSON(BASE + '/api/signals?limit=50');
    const tbody = document.getElementById('signalsTable');
    document.getElementById('todaySignals').textContent = data.length;
    if (data.length === 0) {
      tbody.innerHTML = '<tr><td colspan="9" class="empty">目前尚無信號。掃描器持續監控中...</td></tr>';
      document.getElementById('latestSignalTime').textContent = '尚無';
      return;
    }
    document.getElementById('latestSignalTime').textContent = fmtTime(data[0].created_at);
    tbody.innerHTML = data.map(s => {
      const [em, label, cls] = TYPE_MAP[s.signal_type] || ['⚪', s.signal_type, ''];
      const conf = parseFloat(s.confidence || 0);
      const url = polyUrl(s.slug, s.polymarket_id);
      const titleText = (s.title || '').slice(0, 40) || '-';
      const titleCell = url
        ? `<a class="signal-link" href="${url}" target="_blank" rel="noopener" title="${s.title || ''}">${titleText}</a>`
        : `<span title="${s.title || ''}">${titleText}</span>`;
      return `<tr>
        <td><span class="badge ${cls}">${em} ${label}</span></td>
        <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${titleCell}</td>
        <td>${s.entry_price != null ? '$' + parseFloat(s.entry_price).toFixed(3) : '-'}</td>
        <td>${s.target_price != null ? '$' + parseFloat(s.target_price).toFixed(3) : '-'}</td>
        <td>${s.stop_loss != null ? '$' + parseFloat(s.stop_loss).toFixed(3) : '-'}</td>
        <td>${(parseFloat(s.profit_pct || 0) * 100).toFixed(1)}%</td>
        <td class="${confClass(conf)}">${(conf * 100).toFixed(0)}%</td>
        <td>$${parseFloat(s.suggested_position || 0).toFixed(0)}</td>
        <td>${fmtTime(s.created_at)}</td>
      </tr>`;
    }).join('');
  } catch(e) { console.error(e); }
}

async function loadMarkets() {
  try {
    const data = await fetchJSON(BASE + '/api/markets');
    document.getElementById('totalMarkets').textContent = data.total || 0;
    const by_cat = data.by_category || {};
    const CAT_LABELS = {
      macro: '📈 總體經濟', weather: '🌦️ 天氣', politics: '🗳️ 政治',
      earnings: '💰 財報', regulatory: '⚖️ 監管', geopolitical: '🌍 地緣政治',
    };
    const grid = document.getElementById('marketsGrid');
    if (Object.keys(by_cat).length === 0) {
      grid.innerHTML = '<div class="empty" style="grid-column:1/-1">尚無市場資料</div>';
      return;
    }
    grid.innerHTML = Object.entries(by_cat).map(([cat, mkts]) => `
      <div class="market-card">
        <div class="market-cat">${CAT_LABELS[cat] || cat}</div>
        ${mkts.map(m => {
          const yes = m.yes_price;
          const yesStr = yes != null ? (yes * 100).toFixed(0) + '%' : 'N/A';
          const yesClass = yes >= 0.7 ? 'yes-high' : yes <= 0.15 ? 'yes-low' : '';
          const url = m.slug ? POLY_BASE + m.slug : null;
          const titleHtml = url
            ? `<a href="${url}" target="_blank" rel="noopener">${m.title.slice(0, 65)}</a>`
            : m.title.slice(0, 65);
          return `<div class="market-item">
            <div class="market-title">${titleHtml}</div>
            <div class="market-meta">
              <span class="${yesClass}">YES=${yesStr}</span>
              <span>流動性=$${(m.liquidity || 0).toLocaleString()}</span>
            </div>
          </div>`;
        }).join('')}
      </div>
    `).join('');
  } catch(e) { console.error(e); }
}

async function loadAll() {
  document.getElementById('lastUpdate').textContent = '更新中...';
  await Promise.all([loadHealth(), loadSignals(), loadMarkets()]);
  document.getElementById('lastUpdate').textContent = '更新：' + new Date().toLocaleTimeString('zh-TW');
}

loadAll();
setInterval(loadAll, 60000); // auto-refresh every 60s
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Web Dashboard — 套利信號監控介面。"""
    return HTMLResponse(content=DASHBOARD_HTML)


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
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent arbitrage signals with market titles and Polymarket links."""
    result = await db.execute(
        text(
            "SELECT rs.id, rs.market_id, rs.signal_type, rs.profit_pct, rs.confidence, "
            "rs.suggested_position, rs.entry_price, rs.target_price, rs.stop_loss, "
            "rs.direction, rs.status, rs.created_at, "
            "m.title, m.yes_price, m.category, m.slug, m.polymarket_id "
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
