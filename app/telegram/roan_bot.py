import logging
from typing import Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from app.config import settings
from app.core.roan_detector import RoanSignal
from app.core.roan_capital import RoanCapitalManager

logger = logging.getLogger(__name__)

SIGNAL_TYPE_EMOJI = {
    "price_sum": "📊 YES+NO<1",
    "logic_arb": "🧩 邏輯錯價",
    "info_lead": "📰 資訊預購",
    "multi_market": "🔗 多市場",
}

CATEGORY_EMOJI = {
    "macro": "📈 宏觀經濟",
    "weather": "🌪️ 天氣",
    "politics": "🏛️ 政治",
    "earnings": "💼 財報",
    "regulation": "⚖️ 監管",
    "geopolitics": "🌍 地緣政治",
}


class RoanTelegramBot:
    """
    Telegram bot for Roan Arbitrage Machine.

    Modes:
    - manual: Send notifications only, user trades manually
    - semi: Send notifications with one-click execute button
    - auto: Auto-execute high-confidence signals (>95%)
    """

    def __init__(self):
        self.token = settings.telegram_token
        self.chat_id = settings.telegram_chat_id
        self.mode = settings.trading_mode
        self.capital_mgr = RoanCapitalManager()
        self._signal_counter = 0
        self._app: Optional[Application] = None
        self._bot: Optional[Bot] = None

    async def initialize(self):
        """Initialize the bot."""
        if not self.token:
            logger.warning("Telegram token not configured, bot disabled")
            return

        self._app = Application.builder().token(self.token).build()
        self._bot = self._app.bot

        # Register command handlers
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("roan_status", self._cmd_status))
        self._app.add_handler(CommandHandler("roan_history", self._cmd_history))
        self._app.add_handler(CommandHandler("roan_pause", self._cmd_pause))
        self._app.add_handler(CommandHandler("roan_capital", self._cmd_capital))
        self._app.add_handler(CommandHandler("mode", self._cmd_mode))
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))

        await self._app.initialize()
        logger.info("Telegram bot initialized in '%s' mode", self.mode)

    async def send_roan_signal(self, signal: RoanSignal) -> Optional[int]:
        """Send a Roan signal notification to Telegram."""
        if not self._bot or not self.chat_id:
            logger.warning("Bot not configured, skipping signal notification")
            return None

        self._signal_counter += 1

        # Build message
        signal_emoji = SIGNAL_TYPE_EMOJI.get(signal.signal_type, "📊")
        category_emoji = CATEGORY_EMOJI.get(signal.category, "📊")

        # Approve position through capital manager
        approved_size = self.capital_mgr.approve_position(signal)
        size_text = f"${approved_size:,.0f}" if approved_size else "⚠️ 超出限額"

        msg = (
            f"🚀 *Roan 信號 #{self._signal_counter}*\n"
            f"\n"
            f"💰 *預期利潤*：{signal.profit_pct:.2%}\n"
            f"⭐ *信心*：{signal.confidence:.1%}\n"
            f"💵 *建議部位*：{size_text}\n"
            f"📈 *策略*：{signal_emoji}\n"
            f"🏷️ *類別*：{category_emoji}\n"
            f"\n"
            f"📊 *{signal.title}*\n"
            f"\n"
            f"📝 {signal.details}\n"
            f"\n"
            f"💧 流動性：${signal.liquidity:,.0f}\n"
            f"YES: {signal.yes_price:.4f} | NO: {signal.no_price:.4f}\n"
            f"\n"
            f"_Roan 機器持續運轉中..._"
        )

        # Build keyboard
        keyboard = self._build_keyboard(signal)

        try:
            sent = await self._bot.send_message(
                chat_id=self.chat_id,
                text=msg,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            logger.info("Signal #%d sent to Telegram", self._signal_counter)
            return sent.message_id
        except Exception as e:
            logger.error("Failed to send Telegram message: %s", e)
            return None

    def _build_keyboard(self, signal: RoanSignal) -> list:
        """Build inline keyboard based on trading mode."""
        keyboard = []

        # Always show market link
        if signal.market_url:
            keyboard.append(
                [InlineKeyboardButton("📊 查看市場", url=signal.market_url)]
            )

        if self.mode in ("semi", "auto"):
            approved = self.capital_mgr.approve_position(signal)
            if approved:
                keyboard.append([
                    InlineKeyboardButton(
                        f"🚀 一鍵執行 ${approved:,.0f}",
                        callback_data=f"exec_{signal.market_id}_{approved}",
                    )
                ])

        keyboard.append([
            InlineKeyboardButton("✅ 已手動執行", callback_data=f"done_{signal.market_id}"),
            InlineKeyboardButton("⏭️ 忽略", callback_data=f"ignore_{signal.market_id}"),
        ])

        return keyboard

    async def send_daily_summary(self, stats: dict):
        """Send daily performance summary."""
        if not self._bot or not self.chat_id:
            return

        msg = (
            f"📊 *Roan 每日報告*\n"
            f"\n"
            f"📡 信號數量：{stats.get('signals_sent', 0)}\n"
            f"✅ 已執行：{stats.get('signals_executed', 0)}\n"
            f"💰 獲利信號：{stats.get('signals_profitable', 0)}\n"
            f"💵 總利潤：${stats.get('total_profit', 0):,.2f}\n"
            f"📈 勝率：{stats.get('win_rate', 0):.1%}\n"
            f"\n"
            f"_Roan 機器明天繼續運轉！_"
        )

        try:
            await self._bot.send_message(
                chat_id=self.chat_id, text=msg, parse_mode="Markdown"
            )
        except Exception as e:
            logger.error("Failed to send daily summary: %s", e)

    # --- Command handlers ---

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🚀 *Roan 套利機器已啟動！*\n\n"
            "監控 6 類市場：宏觀經濟 | 天氣 | 政治 | 財報 | 監管 | 地緣政治\n\n"
            f"目前模式：*{self.mode}*\n\n"
            "指令：\n"
            "/roan\\_status - 今日績效\n"
            "/roan\\_history - 歷史信號\n"
            "/roan\\_capital - 資金狀態\n"
            "/roan\\_pause - 暫停/恢復\n"
            "/mode manual|semi|auto - 切換模式",
            parse_mode="Markdown",
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            f"📊 *今日 Roan 狀態*\n\n"
            f"信號數：{self._signal_counter}\n"
            f"模式：{self.mode}\n"
            f"運行中：✅",
            parse_mode="Markdown",
        )

    async def _cmd_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("📜 歷史信號功能開發中...")

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("⏸️ 暫停功能開發中...")

    async def _cmd_capital(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        summary = self.capital_mgr.summary
        await update.message.reply_text(
            f"💰 *資金狀態*\n\n"
            f"總資金：${summary['total_capital']:,.2f}\n"
            f"可用：${summary['available_capital']:,.2f}\n"
            f"持倉數：{summary['open_positions']}\n"
            f"已平倉：{summary['closed_positions']}\n"
            f"總損益：${summary['total_pnl']:,.2f}\n"
            f"勝率：{summary['win_rate']:.1%}",
            parse_mode="Markdown",
        )

    async def _cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.args and context.args[0] in ("manual", "semi", "auto"):
            self.mode = context.args[0]
            await update.message.reply_text(f"✅ 模式切換為：*{self.mode}*", parse_mode="Markdown")
        else:
            await update.message.reply_text(
                f"目前模式：*{self.mode}*\n用法：/mode manual|semi|auto",
                parse_mode="Markdown",
            )

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data

        if data.startswith("exec_"):
            parts = data.split("_")
            market_id = parts[1]
            amount = float(parts[2]) if len(parts) > 2 else 0
            await query.edit_message_text(
                f"🚀 *執行中...*\n\n市場：{market_id}\n金額：${amount:,.0f}\n\n"
                f"⚠️ 請在 Polymarket 確認交易",
                parse_mode="Markdown",
            )
        elif data.startswith("done_"):
            market_id = data.replace("done_", "")
            await query.edit_message_text(
                f"✅ *已標記為已執行*\n市場：{market_id}",
                parse_mode="Markdown",
            )
        elif data.startswith("ignore_"):
            market_id = data.replace("ignore_", "")
            await query.edit_message_text(
                f"⏭️ *已忽略*\n市場：{market_id}",
                parse_mode="Markdown",
            )

    async def shutdown(self):
        if self._app:
            await self._app.shutdown()
