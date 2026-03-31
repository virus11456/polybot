"""
RoanTelegramBot — Telegram 機器人

功能：
1. 發送套利信號通知（含進出場價格與信心度）
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
        發送套利信號訊息（含進出場價格、信心度等級）。
        訂閱特定類別時只發該類別；若 category 為 'other' 則仍發送（不過濾）。
        """
        signal_type = signal.get("signal_type", "")
        target_market = signal.get("target_market", {})
        trigger_market = signal.get("trigger_market", {})
        category = target_market.get("category", "other")

        # 只有明確分類且用戶選擇了特定訂閱時才過濾；"other" 不過濾
        subscribed_cats = self._subscriptions.get(self.chat_id, list(AVAILABLE_CATEGORIES.keys()))
        if category != "other" and category not in subscribed_cats:
            logger.debug(f"Chat {self.chat_id} 未訂閱 {category}，跳過信號")
            return None

        emoji = "🔵" if signal_type == "logic_arb" else "🟣"
        type_label = "邏輯依賴套利" if signal_type == "logic_arb" else "多條件組合套利"

        profit_pct = signal.get("profit_pct", 0)
        confidence = signal.get("confidence", 0)
        suggested_position = signal.get("suggested_position", 0)
        rule_desc = signal.get("rule_desc", "")

        # 進出場資訊
        target_yes = target_market.get("yes_price")
        trigger_yes = trigger_market.get("yes_price")
        entry_price = target_yes  # 進場：買入目標市場 YES
        # 出場目標：目標市場 YES 漲至觸發市場水準的 85%（邏輯依賴閾值）
        exit_target = trigger_yes * 0.85 if trigger_yes else (
            (entry_price + profit_pct) if entry_price is not None else None
        )

        target_title = (target_market.get("title") or "")[:60]
        trigger_title = (trigger_market.get("title") or "")[:60]

        entry_str = f"{entry_price:.2%}" if entry_price is not None else "N/A"
        exit_str = f"{exit_target:.2%}" if exit_target is not None else "N/A"

        # 信心等級標示
        if confidence >= 0.80:
            confidence_label = "🟢 高"
        elif confidence >= 0.65:
            confidence_label = "🟡 中"
        else:
            confidence_label = "🔴 低"

        cat_label = AVAILABLE_CATEGORIES.get(category, category)

        text = (
            f"{emoji} <b>{type_label}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 規則：{rule_desc}\n"
            f"🏷 類別：{cat_label}\n"
            f"🎯 信心度：{confidence_label}（{confidence:.1%}）\n"
            f"💹 預期獲利：{profit_pct:.2%}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📥 <b>進場</b>：買入 YES @ {entry_str}\n"
            f"📤 <b>出場目標</b>：YES 漲至 {exit_str}\n"
            f"💵 建議倉位：${suggested_position:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔔 目標市場：{target_title}\n"
            f"⚡ 觸發市場：{trigger_title}\n"
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

            # 回應 callback
            await self._answer_callback(callback_id, f"{action} {AVAILABLE_CATEGORIES.get(cat_key, cat_key)}")

            # 更新選擇 UI
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
                # 取績效數據
                perf_result = await session.execute(
                    text("""
                        SELECT signals_sent, signals_profitable, total_profit_usd, capital_used
                        FROM roan_performance
                        WHERE date = :d
                    """),
                    {"d": target_date}
                )
                perf = perf_result.mappings().first()

                # 取今日信號數量（按類型分組）
                sig_result = await session.execute(
                    text("""
                        SELECT signal_type, COUNT(*) as cnt, AVG(profit_pct) as avg_profit,
                               AVG(confidence) as avg_confidence
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
                type_label = "邏輯依賴" if row["signal_type"] == "logic_arb" else "多條件組合"
                avg_conf = float(row["avg_confidence"] or 0)
                sig_text += (
                    f"• {type_label}：{row['cnt']} 個，"
                    f"平均獲利 {float(row['avg_profit'] or 0):.2%}，"
                    f"平均信心 {avg_conf:.1%}\n"
                )
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

        # Split into chunks to avoid Telegram 4096-char limit
        full_text = "\n".join(lines)
        chunk_size = 3800
        for i in range(0, len(full_text), chunk_size):
            await self.send_message(full_text[i:i + chunk_size], chat_id=target)

    # ─── 最新信號查詢 ────────────────────────────────────────────────────────

    async def send_recent_signals(self, chat_id: Optional[str] = None, limit: int = 5) -> None:
        """查詢並發送最近的套利信號（/signals 指令）。"""
        target = chat_id or self.chat_id
        try:
            from app.database import AsyncSessionLocal
            from sqlalchemy import text

            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    text("""
                        SELECT rs.signal_type, rs.profit_pct, rs.confidence,
                               rs.suggested_position, rs.status, rs.created_at,
                               m.title as market_title, m.category
                        FROM roan_signals rs
                        LEFT JOIN markets m ON m.id = rs.market_id
                        ORDER BY rs.created_at DESC
                        LIMIT :limit
                    """),
                    {"limit": limit}
                )
                rows = result.mappings().all()
        except Exception as e:
            await self.send_message(f"⚠️ 無法取得信號：{e}", chat_id=target)
            return

        if not rows:
            await self.send_message("📭 目前尚無套利信號紀錄。", chat_id=target)
            return

        lines = [f"📋 <b>最新 {limit} 筆套利信號</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for row in rows:
            sig_type = "邏輯依賴" if row["signal_type"] == "logic_arb" else "多條件組合"
            conf = float(row["confidence"] or 0)
            if conf >= 0.80:
                conf_label = "🟢"
            elif conf >= 0.65:
                conf_label = "🟡"
            else:
                conf_label = "🔴"
            created = str(row["created_at"])[:16]
            lines.append(
                f"\n{conf_label} [{created}] {sig_type}\n"
                f"  市場：{(row['market_title'] or 'N/A')[:50]}\n"
                f"  信心度：{conf:.1%}  預期獲利：{float(row['profit_pct'] or 0):.2%}\n"
                f"  建議倉位：${float(row['suggested_position'] or 0):.2f}  狀態：{row['status']}"
            )

        await self.send_message("\n".join(lines), chat_id=target)

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
                "/help — 顯示說明",
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
                "🔵 <b>邏輯依賴套利</b>：基於事件因果關係（如雷雨→下雨），"
                "若觸發市場 YES 高但依賴市場 YES 偏低，則依賴市場被低估。\n\n"
                "🟣 <b>多條件組合套利</b>：同類別多市場相關性修正，"
                "若多個相關市場 YES 均偏高但聯合機率被低估，則存在組合套利機會。\n\n"
                "📋 <b>指令：</b>\n"
                "/marketlist — 列出目前鎖定掃描中的所有市場（按類別）\n"
                "/markets — 按類別篩選要接收的通知\n"
                "/signals — 查看最新 5 筆套利信號（含進出場價格）\n"
                "/report — 查看今日績效報告\n\n"
                "📊 <b>信號說明：</b>\n"
                "🟢 高信心（≥80%）  🟡 中信心（65-80%）  🔴 低信心（<65%）\n\n"
                "掃描頻率：每 60 秒一次（可透過 SCAN_INTERVAL_SECONDS 環境變數調整）",
                chat_id=chat_id
            )
