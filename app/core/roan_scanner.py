"""
RoanScanner — 套利信號偵測引擎

功能：
1. 邏輯依賴套利：偵測有因果依賴關係的市場對（如雷雨→下雨）
2. 多條件組合掃描：找出多個市場共同條件下的聯合套利機會
3. 高機率市場掃描：偵測 YES/NO 機率極高的市場（直接進場）
4. 持續掃描循環：定期從 Polymarket 拉取市場並發出信號
5. 每小時狀態更新：無信號時也推送掃描摘要
"""

import asyncio
import logging
import os
from datetime import datetime as _dt
from typing import List, Optional, Dict, Tuple, Any

from sqlalchemy import text

from app.database import get_db, AsyncSessionLocal
from app.data.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)

# ─── 邏輯依賴關係規則表 ─────────────────────────────────────────────────────────
LOGIC_DEPENDENCY_RULES: List[Tuple[List[str], List[str], str]] = [
    # 天氣邏輯依賴
    (["thunderstorm", "lightning", "severe storm", "雷雨"], ["rain", "rainfall", "precipitation", "下雨"], "雷雨→降雨依賴"),
    (["hurricane", "typhoon", "cyclone"], ["storm", "wind", "rainfall", "flooding"], "颶風→風暴依賴"),
    (["tornado", "twister"], ["storm", "wind", "damage"], "龍捲風→風暴依賴"),
    (["blizzard", "snowstorm"], ["snow", "snowfall", "winter storm"], "暴風雪→降雪依賴"),

    # 政治邏輯依賴
    (["impeached", "impeachment"], ["resign", "removed", "convicted"], "彈劾→下台依賴"),
    (["primary", "nomination"], ["election", "general election", "vote"], "初選→大選依賴"),
    (["indicted", "indictment"], ["convicted", "guilty", "prison"], "起訴→定罪依賴"),

    # 經濟邏輯依賴
    (["recession", "GDP contraction"], ["unemployment", "job loss", "layoffs"], "衰退→失業依賴"),
    (["rate hike", "rate increase", "Fed hike"], ["dollar", "USD", "bond yield"], "升息→美元依賴"),
    (["bankruptcy", "default"], ["stock crash", "market fall", "collapse"], "破產→股市依賴"),

    # 地緣政治依賴
    (["ceasefire", "peace deal"], ["troops withdraw", "military withdraw", "end war"], "停火→撤軍依賴"),
    (["sanctions", "embargo"], ["trade", "export", "import", "supply"], "制裁→貿易依賴"),
]

# 多條件組合掃描：相同類別下市場聯合機率異常
COMBO_SCAN_CATEGORIES = ["weather", "politics", "macro", "geopolitical"]

# 套利信號觸發閾值
MIN_LIQUIDITY = 500.0
MIN_PROFIT_PCT = 0.015
MIN_CONFIDENCE = 0.50
LOGIC_ARB_THRESHOLD = 0.05
COMBO_PRICE_DIFF_THRESHOLD = 0.08

# 高機率直接進場閾值
HIGH_PROB_YES_THRESHOLD = 0.70
HIGH_PROB_NO_THRESHOLD = 0.15
HIGH_PROB_MIN_LIQUIDITY = 50000

# 每小時狀態更新間隔（秒）
HOURLY_STATUS_INTERVAL = 3600


class RoanScanner:
    """
    持續掃描 Polymarket 市場，偵測邏輯依賴套利與多條件組合套利機會。
    """

    def __init__(self):
        db_url = os.getenv("DATABASE_URL", "")
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)

        self._client = PolymarketClient(db_url=db_url if db_url else None)
        self._scan_interval = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))

        # 快取最新掃描到的市場列表（供 TG /marketlist 查詢）
        self._last_markets: List[dict] = []
        self._last_scan_time: Optional[str] = None
        # 已發送過的高機率市場（避免重複推送）
        self._sent_high_prob: set = set()
        # 每小時狀態更新追蹤
        self._last_hourly_status_time: float = 0.0
        self._signals_since_last_hourly: int = 0

    async def continuous_scan(self):
        """持續掃描循環（在後台 task 中執行）。"""
        logger.info(f"RoanScanner 啟動，掃描間隔 {self._scan_interval}s")
        while True:
            try:
                await self.run_scan_cycle()
            except Exception as e:
                logger.error(f"掃描循環發生錯誤：{e}", exc_info=True)
            await asyncio.sleep(self._scan_interval)

    async def run_scan_cycle(self) -> List[dict]:
        """
        執行一次完整掃描週期。
        回傳偵測到的信號列表。
        """
        logger.info("開始掃描週期...")

        # 取得最新市場資料
        markets = await self._client.get_active_markets()
        logger.info(f"取得 {len(markets)} 個市場")

        # 快取最新市場供 TG 查詢
        self._last_markets = markets
        self._last_scan_time = _dt.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        # 市場資料寫入 DB（供信號 FK 使用）
        if markets:
            try:
                await self._client.upsert_markets(markets)
                logger.info(f"Upserted {len(markets)} markets to DB")
            except Exception as e:
                logger.warning(f"Market upsert failed (signals may not store): {e}")

        all_signals = []

        # 1. 高機率直接進場掃描（最優先）
        high_prob_signals = await self._scan_high_probability(markets)
        all_signals.extend(high_prob_signals)
        logger.info(f"高機率直接進場：偵測到 {len(high_prob_signals)} 個信號")

        # 2. 邏輯依賴套利掃描
        logic_signals = await self._scan_logic_dependency(markets)
        all_signals.extend(logic_signals)
        logger.info(f"邏輯依賴套利：偵測到 {len(logic_signals)} 個信號")

        # 3. 多條件組合掃描
        combo_signals = await self._scan_multi_condition(markets)
        all_signals.extend(combo_signals)
        logger.info(f"多條件組合：偵測到 {len(combo_signals)} 個信號")

        # 4. 儲存信號至 DB
        if all_signals:
            await self._store_signals(all_signals)

        # 5. 發送 Telegram 信號通知
        if all_signals:
            await self._send_telegram_signals(all_signals)

        # 6. 累計自上次每小時更新以來的信號數
        self._signals_since_last_hourly += len(all_signals)

        # 7. 每小時推送一次狀態更新（不論有無信號）
        import time
        now = time.monotonic()
        if now - self._last_hourly_status_time >= HOURLY_STATUS_INTERVAL:
            await self._send_hourly_status(len(markets), all_signals)
            self._last_hourly_status_time = now
            self._signals_since_last_hourly = 0

        return all_signals

    # ─── 每小時狀態更新 ───────────────────────────────────────────────────────

    async def _send_hourly_status(self, market_count: int, latest_signals: List[dict]):
        """每小時向 Telegram 發送掃描狀態更新（不論是否有信號）。"""
        token = os.getenv("TELEGRAM_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return

        try:
            import app.main as _main
            bot = _main._get_bot()
            if bot is None:
                return

            now_str = _dt.utcnow().strftime("%Y-%m-%d %H:%M UTC")

            if self._signals_since_last_hourly > 0:
                # 這小時有信號，摘要顯示
                sig_count = self._signals_since_last_hourly
                text = (
                    f"⏰ <b>每小時掃描摘要</b> | {now_str}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 監控市場：{market_count:,} 個\n"
                    f"✅ 本小時套利信號：{sig_count} 個\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔗 <a href='https://polybot-production-7c05.up.railway.app'>Dashboard</a>"
                )
            else:
                # 這小時無匹配信號
                text = (
                    f"⏰ <b>每小時掃描摘要</b> | {now_str}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 監控市場：{market_count:,} 個\n"
                    f"❌ 無匹配套利項目\n"
                    f"（持續監控中，偵測到機會立即推送）\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔗 <a href='https://polybot-production-7c05.up.railway.app'>Dashboard</a>"
                )

            await bot.send_message(text)
            logger.info(f"每小時狀態更新已發送（信號數：{self._signals_since_last_hourly}）")
        except Exception as e:
            logger.error(f"每小時狀態更新發送失敗：{e}")

    # ─── 高機率直接進場掃描 ──────────────────────────────────────────────────

    async def _scan_high_probability(self, markets: List[dict]) -> List[dict]:
        """
        掃描高機率直接進場機會。
        - YES > 70%：機率偏高，買入 YES，預期持有到結算
        - YES < 15%（NO > 85%）：機率偏低，買入 NO
        只針對流動性 > $50,000 的市場，避免低流動性陷阱。
        """
        signals = []

        for mkt in markets:
            pid = mkt.get("polymarket_id", "")
            if pid in self._sent_high_prob:
                continue

            yes = mkt.get("yes_price")
            liq = mkt.get("liquidity", 0)
            if yes is None or liq < HIGH_PROB_MIN_LIQUIDITY:
                continue

            title = mkt.get("title", "")[:80]

            if yes >= HIGH_PROB_YES_THRESHOLD:
                entry_price = yes
                target_price = min(0.99, yes + (1.0 - yes) * 0.5)
                stop_loss = max(0.01, yes - (1.0 - yes) * 0.3)
                profit_pct = (target_price - entry_price) / entry_price
                confidence = min(0.90, 0.60 + (yes - 0.70) * 1.0)
                position_size = self._calc_position(liq, confidence)

                if profit_pct >= MIN_PROFIT_PCT and confidence >= MIN_CONFIDENCE:
                    self._sent_high_prob.add(pid)
                    signals.append({
                        "signal_type": "high_prob_yes",
                        "trigger_market": mkt,
                        "target_market": mkt,
                        "profit_pct": round(profit_pct, 4),
                        "confidence": round(confidence, 3),
                        "rule_desc": f"高機率 YES 直接進場（YES={yes:.1%}）",
                        "entry_price": round(entry_price, 4),
                        "target_price": round(target_price, 4),
                        "stop_loss": round(stop_loss, 4),
                        "position_size": round(position_size, 2),
                        "direction": "YES",
                        "detail": (
                            f"[高機率 YES] {title}\n"
                            f"現價：YES={yes:.2%}  流動性：${liq:,.0f}\n"
                            f"📈 進場：${entry_price:.3f}\n"
                            f"🎯 目標：${target_price:.3f}（+{profit_pct:.1%}）\n"
                            f"🛡 停損：${stop_loss:.3f}\n"
                            f"💰 建議倉位：${position_size:.0f}"
                        ),
                        "suggested_position": position_size,
                    })

            elif yes <= HIGH_PROB_NO_THRESHOLD:
                no_price = 1.0 - yes
                entry_price = no_price
                target_price = min(0.99, no_price + (1.0 - no_price) * 0.5)
                stop_loss = max(0.01, no_price - (1.0 - no_price) * 0.3)
                profit_pct = (target_price - entry_price) / entry_price
                confidence = min(0.88, 0.55 + (HIGH_PROB_NO_THRESHOLD - yes) * 2.0)
                position_size = self._calc_position(liq, confidence)

                if profit_pct >= MIN_PROFIT_PCT and confidence >= MIN_CONFIDENCE:
                    self._sent_high_prob.add(pid)
                    signals.append({
                        "signal_type": "high_prob_no",
                        "trigger_market": mkt,
                        "target_market": mkt,
                        "profit_pct": round(profit_pct, 4),
                        "confidence": round(confidence, 3),
                        "rule_desc": f"高機率 NO 直接進場（NO={no_price:.1%}）",
                        "entry_price": round(entry_price, 4),
                        "target_price": round(target_price, 4),
                        "stop_loss": round(stop_loss, 4),
                        "position_size": round(position_size, 2),
                        "direction": "NO",
                        "detail": (
                            f"[高機率 NO] {title}\n"
                            f"現價：NO={no_price:.2%}  流動性：${liq:,.0f}\n"
                            f"📈 進場：${entry_price:.3f}\n"
                            f"🎯 目標：${target_price:.3f}（+{profit_pct:.1%}）\n"
                            f"🛡 停損：${stop_loss:.3f}\n"
                            f"💰 建議倉位：${position_size:.0f}"
                        ),
                        "suggested_position": position_size,
                    })

        return signals

    # ─── 邏輯依賴套利 ──────────────────────────────────────────────────────────

    async def _scan_logic_dependency(self, markets: List[dict]) -> List[dict]:
        """
        掃描邏輯依賴套利。
        若市場 A（觸發詞）YES 機率高，而市場 B（依賴詞）YES 機率過低，
        則存在邏輯套利機會：B 的 YES 應被低估。
        """
        signals = []

        for rule in LOGIC_DEPENDENCY_RULES:
            trigger_keywords, dependent_keywords, rule_desc = rule

            trigger_markets = self._filter_markets_by_keywords(markets, trigger_keywords)
            dependent_markets = self._filter_markets_by_keywords(markets, dependent_keywords)

            for t_mkt in trigger_markets:
                t_yes = t_mkt.get("yes_price")
                t_liquidity = t_mkt.get("liquidity", 0)
                if t_yes is None or t_yes < 0.5 or t_liquidity < MIN_LIQUIDITY:
                    continue

                for d_mkt in dependent_markets:
                    if d_mkt.get("polymarket_id") == t_mkt.get("polymarket_id"):
                        continue

                    d_yes = d_mkt.get("yes_price")
                    d_liquidity = d_mkt.get("liquidity", 0)
                    if d_yes is None or d_liquidity < MIN_LIQUIDITY:
                        continue

                    expected_d_yes = t_yes * 0.85
                    price_gap = expected_d_yes - d_yes

                    if price_gap >= LOGIC_ARB_THRESHOLD:
                        profit_pct = float(price_gap)
                        confidence = min(0.9, 0.6 + (float(t_yes) - 0.5) * 0.5 + price_gap * 0.3)
                        entry_price = d_yes
                        target_price = min(0.99, expected_d_yes)
                        stop_loss = max(0.01, d_yes - price_gap * 0.5)

                        if confidence >= MIN_CONFIDENCE and profit_pct >= MIN_PROFIT_PCT:
                            position_size = self._calc_position(d_liquidity, confidence)
                            signals.append({
                                "signal_type": "logic_arb",
                                "trigger_market": t_mkt,
                                "target_market": d_mkt,
                                "profit_pct": round(profit_pct, 4),
                                "confidence": round(confidence, 3),
                                "rule_desc": rule_desc,
                                "entry_price": round(entry_price, 4),
                                "target_price": round(target_price, 4),
                                "stop_loss": round(stop_loss, 4),
                                "position_size": round(position_size, 2),
                                "direction": "YES",
                                "detail": (
                                    f"[邏輯依賴] {rule_desc}\n"
                                    f"觸發：{t_mkt.get('title', '')[:50]} YES={t_yes:.2%}\n"
                                    f"依賴：{d_mkt.get('title', '')[:50]} YES={d_yes:.2%}\n"
                                    f"預期 YES≥{expected_d_yes:.2%}，差距 {price_gap:.2%}\n"
                                    f"📈 進場：${entry_price:.3f}  🎯 目標：${target_price:.3f}  🛡 停損：${stop_loss:.3f}\n"
                                    f"💰 建議倉位：${position_size:.0f}"
                                ),
                                "suggested_position": position_size,
                            })

        return signals

    # ─── 多條件組合掃描 ────────────────────────────────────────────────────────

    async def _scan_multi_condition(self, markets: List[dict]) -> List[dict]:
        """
        多條件組合掃描：
        在同一類別下，若多個市場 YES 均偏高但彼此 YES 價格之積遠低於個別 YES，
        則可能存在聯合套利機會（市場間相關性被低估）。
        """
        signals = []

        by_category: Dict[str, List[dict]] = {}
        for mkt in markets:
            cat = mkt.get("category", "other")
            if cat not in COMBO_SCAN_CATEGORIES:
                continue
            if mkt.get("liquidity", 0) < MIN_LIQUIDITY:
                continue
            yes = mkt.get("yes_price")
            if yes is None or yes < 0.4 or yes > 0.95:
                continue
            by_category.setdefault(cat, []).append(mkt)

        for cat, cat_markets in by_category.items():
            if len(cat_markets) < 2:
                continue

            top = sorted(cat_markets, key=lambda m: m.get("yes_price", 0), reverse=True)[:10]

            for i in range(len(top)):
                for j in range(i + 1, len(top)):
                    m1, m2 = top[i], top[j]
                    yes1 = m1.get("yes_price", 0)
                    yes2 = m2.get("yes_price", 0)

                    overlap = self._keyword_overlap(
                        m1.get("title", ""), m2.get("title", "")
                    )
                    if overlap < 1:
                        continue

                    independent_joint = yes1 * yes2
                    correlated_joint = yes1 * yes2 + 0.4 * (1 - yes1) * (1 - yes2) * min(yes1, yes2)
                    price_gap = correlated_joint - independent_joint

                    if price_gap >= COMBO_PRICE_DIFF_THRESHOLD * 0.5:
                        avg_liquidity = (m1.get("liquidity", 0) + m2.get("liquidity", 0)) / 2
                        profit_pct = float(price_gap)
                        confidence = min(0.85, 0.55 + overlap * 0.1 + price_gap * 2)
                        avg_yes = (yes1 + yes2) / 2
                        entry_price = avg_yes
                        target_price = min(0.99, avg_yes + price_gap)
                        stop_loss = max(0.01, avg_yes - price_gap * 0.5)

                        if confidence >= MIN_CONFIDENCE and profit_pct >= MIN_PROFIT_PCT:
                            position_size = self._calc_position(avg_liquidity, confidence)
                            signals.append({
                                "signal_type": "combo_arb",
                                "trigger_market": m1,
                                "target_market": m2,
                                "profit_pct": round(profit_pct, 4),
                                "confidence": round(confidence, 3),
                                "rule_desc": f"{cat} 類別組合套利",
                                "entry_price": round(entry_price, 4),
                                "target_price": round(target_price, 4),
                                "stop_loss": round(stop_loss, 4),
                                "position_size": round(position_size, 2),
                                "direction": "YES",
                                "detail": (
                                    f"[多條件組合] {cat} 類別\n"
                                    f"市場1：{m1.get('title', '')[:50]} YES={yes1:.2%}\n"
                                    f"市場2：{m2.get('title', '')[:50]} YES={yes2:.2%}\n"
                                    f"相關修正聯合機率 {correlated_joint:.2%} vs 獨立 {independent_joint:.2%}\n"
                                    f"📈 進場：${entry_price:.3f}  🎯 目標：${target_price:.3f}  🛡 停損：${stop_loss:.3f}\n"
                                    f"💰 建議倉位：${position_size:.0f}"
                                ),
                                "suggested_position": position_size,
                            })

        return signals

    # ─── 工具方法 ───────────────────────────────────────────────────────────────

    def get_last_markets_summary(self) -> dict:
        """回傳最近一次掃描的市場摘要（供 Telegram /marketlist 指令使用）。"""
        by_category: Dict[str, List[dict]] = {}
        for mkt in self._last_markets:
            cat = mkt.get("category", "other")
            by_category.setdefault(cat, []).append(mkt)
        return {
            "scan_time": self._last_scan_time,
            "total": len(self._last_markets),
            "by_category": {
                cat: [
                    {
                        "title": m.get("title", "")[:80],
                        "yes_price": m.get("yes_price"),
                        "liquidity": m.get("liquidity", 0),
                    }
                    for m in sorted(mkts, key=lambda m: m.get("liquidity", 0), reverse=True)[:5]
                ]
                for cat, mkts in by_category.items()
            },
        }

    def _filter_markets_by_keywords(self, markets: List[dict], keywords: List[str]) -> List[dict]:
        """依關鍵字過濾市場（標題匹配）。"""
        result = []
        for mkt in markets:
            title = (mkt.get("title") or "").upper()
            for kw in keywords:
                if kw.upper() in title:
                    result.append(mkt)
                    break
        return result

    def _keyword_overlap(self, title1: str, title2: str) -> int:
        """計算兩個標題的關鍵字重疊數（忽略停用詞）。"""
        STOPWORDS = {"the", "a", "an", "in", "on", "at", "of", "to", "will", "by",
                     "is", "be", "or", "and", "for", "with", "than", "does", "do"}
        words1 = {w.lower() for w in title1.split() if len(w) > 3 and w.lower() not in STOPWORDS}
        words2 = {w.lower() for w in title2.split() if len(w) > 3 and w.lower() not in STOPWORDS}
        return len(words1 & words2)

    def _calc_position(self, liquidity: float, confidence: float) -> float:
        """計算建議倉位（流動性的一定比例，依置信度縮放）。"""
        base = min(liquidity * 0.01, 500.0)
        return round(base * confidence, 2)

    async def _store_signals(self, signals: List[dict]):
        """將信號儲存至 roan_signals 表（含進出場價格與方向）。"""
        insert_sql = text("""
            INSERT INTO roan_signals
                (market_id, signal_type, profit_pct, confidence, suggested_position,
                 entry_price, target_price, stop_loss, direction, status)
            SELECT m.id, :signal_type, :profit_pct, :confidence, :suggested_position,
                   :entry_price, :target_price, :stop_loss, :direction, 'pending'
            FROM markets m
            WHERE m.polymarket_id = :polymarket_id
            LIMIT 1
        """)

        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    for sig in signals:
                        target = sig.get("target_market", {})
                        await session.execute(insert_sql, {
                            "polymarket_id": target.get("polymarket_id", ""),
                            "signal_type": sig["signal_type"],
                            "profit_pct": sig["profit_pct"],
                            "confidence": sig["confidence"],
                            "suggested_position": sig["suggested_position"],
                            "entry_price": sig.get("entry_price"),
                            "target_price": sig.get("target_price"),
                            "stop_loss": sig.get("stop_loss"),
                            "direction": sig.get("direction", "YES"),
                        })
            logger.info(f"儲存 {len(signals)} 個信號")
        except Exception as e:
            logger.error(f"儲存信號失敗：{e}")

    async def _send_telegram_signals(self, signals: List[dict]):
        """發送 Telegram 通知（透過 RoanTelegramBot singleton）。"""
        token = os.getenv("TELEGRAM_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return

        try:
            import app.main as _main
            bot = _main._get_bot()
            if bot is None:
                logger.warning("Telegram bot singleton not available, skipping signals")
                return
            for sig in signals[:5]:  # 每次最多發 5 個信號避免刷屏
                await bot.send_signal(sig)
        except Exception as e:
            logger.error(f"Telegram 發送失敗：{e}")
