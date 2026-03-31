"""
RoanTelegramBot — Telegram 機器人

功能：
1. 發送套利信號通知（含進場/出場/停損建議）
2. Bot UI 市場選擇（用戶可選擇關注類別/市場）
3. 每日報告發送
4. 查詢信號與績效
"""

import asyncio
import logging
import os
from datetime import datetime, date
from typing import Optional, Dict, List

import aiohttp

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"

# 可選類別列表
AVAILABLE_CATEGORIES = {
    "macro": "📈 總體經濟",
    "weather": "🌦️ 天氣",
    "politics": "🗳️ 政治",
    "earnings": "💰 財報",
    "regulatory": "⚖️ 監管",
    "geopolitical": "🌍 地緣政治",
}


class RoanTelegramBot:
    """
    Telegram 機器人：發送信號、提供市場選擇 UI、每日報告。
    """

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._http: Optional[aiohttp.ClientSession] = None

        # 用戶偏好：每個 chat_id 訂閱的類別（預設全部）
        self._subscriptions: Dict[str, List[str]] = {
            chat_id: list(AVAILABLE_CATEGORIES.keys())
        }

    async def _get_http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._http

    async def close(self):
        if self._http and not self._http.closed:
            await self._http.close()

    async def set_webhook(self, webhook_url: str) -> bool:
        """向 Telegram 注冊 webhook URL，讓 Telegram 將更新推送到本服務。"""
        http = await self._get_http()
        try:
            async with http.post(
                f"{self._base_url}/setWebhook",
                json={
                    "url": webhook_url,
                    "allowed_updates": ["message", "callback_query"],
                    "drop_pending_updates": False,
                }
            ) as resp:
                data = await resp.json()
                if data.get("ok"):
                    logger.info(f"Telegram webhook 已設定：{webhook_url}")
                    return True
                else:
                    logger.error(f"Telegram webhook 設定失敗：{data}")
                    return False
        except Exception as e:
            logger.error(f"Telegram setWebhook HTTP 錯誤：{e}")
            return False

    async def get_webhook_info(self) -> dict:
        """取得目前 webhook 狀態。"""
        http = await self._get_http()
        try:
            async with http.get(f"{self._base_url}/getWebhookInfo") as resp:
                return await resp.json()
        except Exception as e:
            logger.error(f"getWebhookInfo 失敗：{e}")
            return {}

    # ─── 發送訊息 ────────────────────────────────────────────────────────────

    async def send_message(self, text: str, chat_id: Optional[str] = None,
                           reply_markup: Optional[dict] = None) -> Optional[dict]:
        """發送 Telegram 訊息。"""
        target = chat_id or self.chat_id
        payload: dict = {
            "chat_id": target,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        http = await self._get_http()
        try:
            async with http.post(f"{self._base_url}/sendMessage", json=payload) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logger.error(f"Telegram 發送失敗：{data}")
                    return None
                return data.get("result")
        except Exception as e:
            logger.error(f"Telegram HTTP 錯誤：{e}")
            return None

    async def send_signal(self, signal: dict) -> Optional[dict]:
        """
        發送套利信號訊息（含進場/出場/停損建議）。
        高機率信號直接推送；其他依訂閱類別過濾。
        """
        signal_type = signal.get("signal_type", "")
        target_market = signal.get("target_market", {})
        category = target_market.get("category", "other")

        # 高機率信號不過濾類別，直接推送
        if signal_type not in ("high_prob_yes", "high_prob_no"):
            subscribed_cats = self._subscriptions.get(self.chat_id, [])
            if category not in subscribed_cats:
                logger.debug(f"Chat {self.chat_id} 未訂閱 {category}，跳過信號")
                return None

        emoji_map = {
            "logic_arb": "🔵",
            "combo_arb": "🟣",
            "high_prob_yes": "🟢",
            "high_prob_no": "🔴",
        }
        label_map = {
            "logic_arb": "邏輯依賴套利",
            "combo_arb": "多條件組合套利",
            "high_prob_yes": "高機率 YES 直接進場",
            "high_prob_no": "高機率 NO 直接進場",
        }
        emoji = emoji_map.get(signal_type, "⚪")
        type_label = label_map.get(signal_type, signal_type)

        profit_pct = signal.get("profit_pct", 0)
        confidence = signal.get("confidence", 0)
        suggested_position = signal.get("suggested_position", 0)
        detail = signal.get("detail", "")
        rule_desc = signal.get("rule_desc", "")
        entry_price = signal.get("entry_price")
        target_price = signal.get("target_price")
        stop_loss = signal.get("stop_loss")
        direction = signal.get("direction", "YES")

        # 信心評級
        if confidence >= 0.80:
            conf_label = "🔥 極高"
        elif confidence >= 0.65:
            conf_label = "✅ 高"
        elif confidence >= 0.55:
            conf_label = "⚠️ 中"
        else:
            conf_label = "❓ 低"

        price_lines = ""
        if entry_price is not None:
            price_lines = (
                f"📈 進場：${entry_price:.3f}（買 {direction}）  "
                f"🎯 目標：${target_price:.3f}（+{profit_pct:.1%}）  "
                f"🛡 停損：${stop_loss:.3f}\n"
            )

        text = (
            f"{emoji} <b>{type_label}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 {rule_desc}\n"
            f"{price_lines}"
            f"🎯 置信度：{confidence:.1%} {conf_label}\n"
            f"💵 建議倉位：${suggested_position:.0f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<pre>{detail[:400]}</pre>"
        )

        return await self.send_message(text)

    # ─── Bot UI 市場選擇 ─────────────────────────────────────────────────────

    async def send_category_selector(self, chat_id: Optional[str] = None) -> Optional[dict]:
        """
        發送市場類別選擇 UI（Inline Keyboard）。
        用戶可點選切換訂閱的類別。
        """
        target = chat_id or self.chat_id
        subscribed = self._subscriptions.get(target, list(AVAILABLE_CATEGORIES.keys()))

        buttons = []
        for cat_key, cat_label in AVAILABLE_CATEGORIES.items():
            is_on = cat_key in subscribed
            btn_text = f"{'✅' if is_on else '❌'} {cat_label}"
            buttons.append([{
                "text": btn_text,
                "callback_data": f"toggle_cat:{cat_key}"
            }])

        buttons.append([{
            "text": "✔️ 確認選擇",
            "callback_data": "confirm_cats"
        }])

        reply_markup = {"inline_keyboard": buttons}

        text = (
            "📊 <b>市場類別選擇</b>\n\n"
            "點選類別可開關訂閱。✅ 表示已訂閱，❌ 表示已關閉。\n"
            "選完後點「確認選擇」。"
        )

        return await self.send_message(text, chat_id=target, reply_markup=reply_markup)

    async def handle_callback(self, callback_query: dict) -> None:
        """
        處理 Inline Keyboard 回調（市場選擇 UI 互動）。
        """
        chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))
        data = callback_query.get("data", "")
        callback_id = callback_query.get("id", "")

        if data.startswith("toggle_cat:"):
            cat_key = data.split(":", 1)[1]
            subscribed = self._subscriptions.setdefault(
                chat_id, list(AVAILABLE_CATEGORIES.keys())
            )
            if cat_key in subscribed:
                subscribed.remove(cat_key)
                action = "已取消訂閱"
            else:
                subscribed.append(cat_key)
                action = "已訂閱"

            await self._answer_callback(callback_id, f"{action} {AVAILABLE_CATEGORIES.get(cat_key, cat_key)}")
            await self.send_category_selector(chat_id=chat_id)

        elif data == "confirm_cats":
            subscribed = self._subscriptions.get(chat_id, [])
            labels = [AVAILABLE_CATEGORIES.get(c, c) for c in subscribed]
            await self._answer_callback(callback_id, "設定已儲存")
            await self.send_message(
                f"✅ <b>訂閱設定已確認</b>\n已訂閱類別：{', '.join(labels) if labels else '（無）'}",
                chat_id=chat_id
            )

    async def _answer_callback(self, callback_id: str, text: str):
        """回應 Telegram callback query。"""
        http = await self._get_http()
        try:
            async with http.post(
                f"{self._base_url}/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": text, "show_alert": False}
            ) as resp:
                pass
        except Exception as e:
            logger.error(f"answerCallbackQuery 失敗：{e}")

    # ─── 每日報告 ────────────────────────────────────────────────────────────

    async def send_daily_report(self, report_date: Optional[date] = None):
        """
        發送每日績效報告。
        從 roan_performance 表取今日（或指定日期）數據。
        """
        target_date = report_date or date.today()

        try:
            from app.database import AsyncSessionLocal
            from sqlalchemy import text

            async with AsyncSessionLocal() as session:
                perf_result = await session.execute(
                    text("""
                        SELECT signals_sent, signals_profitable, total_profit_usd, capital_used
                        FROM roan_performance
                        WHERE date = :d
                    """),
                    {"d": target_date}
                )
                perf = perf_result.mappings().first()

                sig_result = await session.execute(
                    text("""
                        SELECT signal_type, COUNT(*) as cnt, AVG(profit_pct) as avg_profit
                        FROM roan_signals
                        WHERE DATE(created_at) = :d
                        GROUP BY signal_type
                    """),
                    {"d": target_date}
                )
                sig_rows = sig_result.mappings().all()

        except Exception as e:
            logger.error(f"取報告數據失敗：{e}")
            await self.send_message(f"⚠️ 每日報告取得失敗：{e}")
            return

        date_str = target_date.strftime("%Y-%m-%d")

        if perf:
            hit_rate = (
                perf["signals_profitable"] / perf["signals_sent"] * 100
                if perf["signals_sent"] > 0 else 0
            )
            perf_text = (
                f"📊 今日統計：\n"
                f"• 發出信號：{perf['signals_sent']} 個\n"
                f"• 獲利信號：{perf['signals_profitable']} 個（命中率 {hit_rate:.1f}%）\n"
                f"• 總獲利：${perf['total_profit_usd']:.2f}\n"
                f"• 動用資本：${perf['capital_used']:.2f}\n"
            )
        else:
            perf_text = "📊 今日尚無績效紀錄。\n"

        sig_text = ""
        if sig_rows:
            sig_text = "\n🔍 信號分布：\n"
            for row in sig_rows:
                label_map = {
                    "logic_arb": "邏輯依賴",
                    "combo_arb": "多條件組合",
                    "high_prob_yes": "高機率YES",
                    "high_prob_no": "高機率NO",
                }
                type_label = label_map.get(row["signal_type"], row["signal_type"])
                sig_text += f"• {type_label}：{row['cnt']} 個，平均獲利 {float(row['avg_profit'] or 0):.2%}\n"
        else:
            sig_text = "\n🔍 今日無套利信號偵測。\n"

        text = (
            f"📅 <b>每日報告 — {date_str}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{perf_text}"
            f"{sig_text}"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 Roan 套利機器 自動生成"
        )

        await self.send_message(text)
        logger.info(f"每日報告已發送（{date_str}）")

    # ─── 最新信號查詢 ─────────────────────────────────────────────────────────

    async def send_recent_signals(self, chat_id: Optional[str] = None, limit: int = 10) -> None:
        """
        發送最新套利信號清單（從資料庫取最近 N 筆）。
        """
        target = chat_id or self.chat_id

        try:
            from app.database import AsyncSessionLocal
            from sqlalchemy import text

            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    text("""
                        SELECT rs.signal_type, rs.profit_pct, rs.confidence,
                               rs.suggested_position, rs.status, rs.created_at,
                               m.title, m.yes_price, m.category
                        FROM roan_signals rs
                        LEFT JOIN markets m ON m.id = rs.market_id
                        ORDER BY rs.created_at DESC
                        LIMIT :limit
                    """),
                    {"limit": limit}
                )
                rows = result.mappings().all()

        except Exception as e:
            logger.error(f"取信號數據失敗：{e}")
            await self.send_message(f"⚠️ 無法取得信號資料：{e}", chat_id=target)
            return

        if not rows:
            await self.send_message(
                "🔍 <b>最新套利信號</b>\n\n目前尚無信號紀錄。\n掃描器持續運行中，偵測到信號將自動推送。",
                chat_id=target
            )
            return

        emoji_map = {
            "logic_arb": "🔵",
            "combo_arb": "🟣",
            "high_prob_yes": "🟢",
            "high_prob_no": "🔴",
        }
        label_map = {
            "logic_arb": "邏輯依賴",
            "combo_arb": "多條件組合",
            "high_prob_yes": "高機率YES",
            "high_prob_no": "高機率NO",
        }

        lines = [f"🔍 <b>最新 {len(rows)} 筆套利信號</b>", "━━━━━━━━━━━━━━━━━━━━"]

        for row in rows:
            stype = row["signal_type"]
            emoji = emoji_map.get(stype, "⚪")
            type_label = label_map.get(stype, stype)
            title = (row["title"] or "（未知市場）")[:50]
            profit = float(row["profit_pct"] or 0)
            conf = float(row["confidence"] or 0)
            status = row["status"] or "pending"
            created = row["created_at"]
            time_str = created.strftime("%m/%d %H:%M") if created else ""

            lines.append(
                f"\n{emoji} {type_label} | {time_str}\n"
                f"市場：{title}\n"
                f"獲利：{profit:.2%}  置信度：{conf:.0%}  狀態：{status}"
            )

        lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        lines.append("使用 /report 查看今日績效總覽。")

        full_text = "\n".join(lines)
        chunk_size = 3800
        for i in range(0, len(full_text), chunk_size):
            await self.send_message(full_text[i:i + chunk_size], chat_id=target)

    # ─── 市場清單 ────────────────────────────────────────────────────────────

    async def send_market_list(self, chat_id: Optional[str] = None) -> None:
        """
        發送目前掃描中的市場清單（從 scanner 取最新快取）。
        按類別列出，每類最多顯示 5 個流動性最高的市場。
        """
        target = chat_id or self.chat_id
        try:
            import app.main as _main
            scanner = _main._get_scanner()
        except Exception:
            scanner = None

        if not scanner:
            await self.send_message("⚠️ 掃描器未啟動，無法取得市場清單。", chat_id=target)
            return

        summary = scanner.get_last_markets_summary()
        scan_time = summary.get("scan_time") or "尚未掃描"
        total = summary.get("total", 0)
        by_category = summary.get("by_category", {})

        CATEGORY_LABELS = {
            "macro": "📈 總體經濟",
            "weather": "🌦️ 天氣",
            "politics": "🗳️ 政治",
            "earnings": "💰 財報",
            "regulatory": "⚖️ 監管",
            "geopolitical": "🌍 地緣政治",
            "other": "🔹 其他",
        }

        lines = [
            f"🗂 <b>目前掃描中的市場</b>",
            f"更新時間：{scan_time}　共 {total} 個市場",
            "━━━━━━━━━━━━━━━━━━━━",
        ]

        if not by_category:
            lines.append("（尚無市場資料，請稍後再試）")
        else:
            for cat, mkts in sorted(by_category.items()):
                label = CATEGORY_LABELS.get(cat, cat)
                lines.append(f"\n{label}（{len(mkts)} 個）")
                for m in mkts:
                    yes = m.get("yes_price")
                    liq = m.get("liquidity", 0)
                    yes_str = f"{yes:.0%}" if yes is not None else "N/A"
                    liq_str = f"${liq:,.0f}"
                    lines.append(f"  • {m['title'][:60]}")
                    lines.append(f"    YES={yes_str}  流動性={liq_str}")

        lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        lines.append("使用 /markets 可按類別篩選通知。")

        full_text = "\n".join(lines)
        chunk_size = 3800
        for i in range(0, len(full_text), chunk_size):
            await self.send_message(full_text[i:i + chunk_size], chat_id=target)

    # ─── 指令處理 ─────────────────────────────────────────────────────────────

    async def handle_update(self, update: dict) -> None:
        """
        處理 Telegram webhook 更新事件。
        支援指令：/start, /markets, /marketlist, /signals, /report, /help
        """
        message = update.get("message")
        callback_query = update.get("callback_query")

        if callback_query:
            await self.handle_callback(callback_query)
            return

        if not message:
            return

        text = message.get("text", "")
        chat_id = str(message.get("chat", {}).get("id", ""))

        if text.startswith("/start"):
            await self.send_message(
                "👋 <b>歡迎使用 Roan 套利機器！</b>\n\n"
                "可用指令：\n"
                "/marketlist — 列出目前掃描中的市場\n"
                "/markets — 選擇關注的市場類別\n"
                "/signals — 查看最新套利信號\n"
                "/report — 取得今日報告\n"
                "/help — 顯示說明\n\n"
                "📊 <b>網頁 Dashboard：</b>\nhttps://polybot-production-7c05.up.railway.app",
                chat_id=chat_id
            )

        elif text.startswith("/marketlist"):
            await self.send_market_list(chat_id=chat_id)

        elif text.startswith("/markets"):
            await self.send_category_selector(chat_id=chat_id)

        elif text.startswith("/signals"):
            await self.send_recent_signals(chat_id=chat_id)

        elif text.startswith("/report"):
            await self.send_daily_report()

        elif text.startswith("/help"):
            await self.send_message(
                "📖 <b>Roan 套利機器說明</b>\n\n"
                "本機器人偵測 Polymarket 上的套利機會：\n\n"
                "🟢 <b>高機率 YES 進場</b>：YES > 70% 且流動性高，直接買入 YES，等結算獲利。\n\n"
                "🔴 <b>高機率 NO 進場</b>：YES < 15%（NO > 85%），買入 NO，等結算獲利。\n\n"
                "🔵 <b>邏輯依賴套利</b>：基於事件因果關係（如雷雨→下雨），"
                "若觸發市場 YES 高但依賴市場 YES 偏低，則依賴市場被低估。\n\n"
                "🟣 <b>多條件組合套利</b>：同類別多市場相關性修正。\n\n"
                "📋 <b>每個信號均包含：</b>\n"
                "• 進場價、目標價、停損價\n"
                "• 置信度評級（🔥極高 / ✅高 / ⚠️中）\n"
                "• 建議倉位大小\n\n"
                "📋 <b>指令：</b>\n"
                "/marketlist — 列出目前掃描中的市場\n"
                "/markets — 按類別篩選通知\n"
                "/signals — 查看最新信號\n"
                "/report — 查看今日績效報告\n\n"
                "📊 Dashboard：https://polybot-production-7c05.up.railway.app",
                chat_id=chat_id
            )
