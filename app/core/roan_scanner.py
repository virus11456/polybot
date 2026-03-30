"""
RoanScanner — 套利信號偵測引擎

功能：
1. 邏輯依賴套利：偵測有因果依賴關係的市場對（如雷雨→下雨）
2. 多條件組合掃描：找出多個市場共同條件下的聯合套利機會
3. 持續掃描循環：定期從 Polymarket 拉取市場並發出信號
"""

import asyncio
import logging
import os
from decimal import Decimal
from typing import List, Optional, Dict, Tuple, Any

from sqlalchemy import text

from app.database import get_db, AsyncSessionLocal
from app.data.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)

# ─── 邏輯依賴關係規則表 ─────────────────────────────────────────────────────────
# 格式：(觸發關鍵字組, 依賴關鍵字組, 描述)
# 若市場 A 包含觸發詞且市場 B 包含依賴詞，則 A YES => B YES（機率應更高）
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
MIN_LIQUIDITY = 1000.0       # 最低流動性（USD）
MIN_PROFIT_PCT = 0.02        # 最低套利空間 2%
MIN_CONFIDENCE = 0.55        # 最低置信度
LOGIC_ARB_THRESHOLD = 0.08   # 邏輯依賴套利價差閾值：依賴市場 YES 偏低超過 8%
COMBO_PRICE_DIFF_THRESHOLD = 0.10  # 多條件組合套利價差閾值


class RoanScanner:
    """
    持續掃描 Polymarket 市場，偵測邏輯依賴套利與多條件組合套利機會。
    """

    def __init__(self):
        db_url = os.getenv("DATABASE_URL", "")
        # 轉換 asyncpg driver
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)

        self._client = PolymarketClient(db_url=db_url if db_url else None)
        self._scan_interval = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))  # 預設 5 分鐘

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

        all_signals = []

        # 1. 邏輯依賴套利掃描
        logic_signals = await self._scan_logic_dependency(markets)
        all_signals.extend(logic_signals)
        logger.info(f"邏輯依賴套利：偵測到 {len(logic_signals)} 個信號")

        # 2. 多條件組合掃描
        combo_signals = await self._scan_multi_condition(markets)
        all_signals.extend(combo_signals)
        logger.info(f"多條件組合：偵測到 {len(combo_signals)} 個信號")

        # 3. 儲存信號至 DB
        if all_signals:
            await self._store_signals(all_signals)

        # 4. 發送 Telegram 通知
        if all_signals:
            await self._send_telegram_signals(all_signals)

        return all_signals

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

            # 找到觸發市場與依賴市場
            trigger_markets = self._filter_markets_by_keywords(markets, trigger_keywords)
            dependent_markets = self._filter_markets_by_keywords(markets, dependent_keywords)

            for t_mkt in trigger_markets:
                t_yes = t_mkt.get("yes_price")
                t_liquidity = t_mkt.get("liquidity", 0)
                if t_yes is None or t_yes < 0.5 or t_liquidity < MIN_LIQUIDITY:
                    continue  # 觸發市場 YES 不夠高，跳過

                for d_mkt in dependent_markets:
                    if d_mkt.get("polymarket_id") == t_mkt.get("polymarket_id"):
                        continue  # 同一市場跳過

                    d_yes = d_mkt.get("yes_price")
                    d_liquidity = d_mkt.get("liquidity", 0)
                    if d_yes is None or d_liquidity < MIN_LIQUIDITY:
                        continue

                    # 邏輯套利：若 A YES 高（例如0.75），B YES 應至少等於 A YES 乘上一個合理係數
                    # 若 B YES 低於 t_yes - threshold，則 B YES 被低估
                    expected_d_yes = t_yes * 0.85  # 依賴市場至少應有觸發市場 85% 的機率
                    price_gap = expected_d_yes - d_yes

                    if price_gap >= LOGIC_ARB_THRESHOLD:
                        profit_pct = float(price_gap)
                        confidence = min(0.9, 0.6 + (float(t_yes) - 0.5) * 0.5 + price_gap * 0.3)

                        if confidence >= MIN_CONFIDENCE and profit_pct >= MIN_PROFIT_PCT:
                            signals.append({
                                "signal_type": "logic_arb",
                                "trigger_market": t_mkt,
                                "target_market": d_mkt,
                                "profit_pct": round(profit_pct, 4),
                                "confidence": round(confidence, 3),
                                "rule_desc": rule_desc,
                                "detail": (
                                    f"[邏輯依賴] {rule_desc}\n"
                                    f"觸發：{t_mkt.get('title', '')[:50]} YES={t_yes:.2%}\n"
                                    f"依賴：{d_mkt.get('title', '')[:50]} YES={d_yes:.2%}\n"
                                    f"預期 YES≥{expected_d_yes:.2%}，差距 {price_gap:.2%}"
                                ),
                                "suggested_position": self._calc_position(d_liquidity, confidence),
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

        # 按類別分組
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

            # 找出 YES 最高的前 10 個市場
            top = sorted(cat_markets, key=lambda m: m.get("yes_price", 0), reverse=True)[:10]

            # 掃描兩兩組合
            for i in range(len(top)):
                for j in range(i + 1, len(top)):
                    m1, m2 = top[i], top[j]
                    yes1 = m1.get("yes_price", 0)
                    yes2 = m2.get("yes_price", 0)

                    # 若兩市場有相關關鍵字重疊，聯合機率應更高
                    overlap = self._keyword_overlap(
                        m1.get("title", ""), m2.get("title", "")
                    )
                    if overlap < 1:
                        continue  # 無足夠關鍵字重疊

                    # 聯合機率（獨立假設下）= yes1 * yes2
                    # 若市場相關性高，實際聯合機率 > yes1 * yes2
                    # 套利：各自買 YES，預期聯合獲利
                    independent_joint = yes1 * yes2
                    # 假設相關係數 0.4 修正
                    correlated_joint = yes1 * yes2 + 0.4 * (1 - yes1) * (1 - yes2) * min(yes1, yes2)
                    price_gap = correlated_joint - independent_joint

                    if price_gap >= COMBO_PRICE_DIFF_THRESHOLD * 0.5:
                        avg_liquidity = (m1.get("liquidity", 0) + m2.get("liquidity", 0)) / 2
                        profit_pct = float(price_gap)
                        confidence = min(0.85, 0.55 + overlap * 0.1 + price_gap * 2)

                        if confidence >= MIN_CONFIDENCE and profit_pct >= MIN_PROFIT_PCT:
                            signals.append({
                                "signal_type": "combo_arb",
                                "trigger_market": m1,
                                "target_market": m2,
                                "profit_pct": round(profit_pct, 4),
                                "confidence": round(confidence, 3),
                                "rule_desc": f"{cat} 類別組合套利",
                                "detail": (
                                    f"[多條件組合] {cat} 類別\n"
                                    f"市場1：{m1.get('title', '')[:50]} YES={yes1:.2%}\n"
                                    f"市場2：{m2.get('title', '')[:50]} YES={yes2:.2%}\n"
                                    f"相關修正聯合機率 {correlated_joint:.2%} vs 獨立 {independent_joint:.2%}"
                                ),
                                "suggested_position": self._calc_position(avg_liquidity, confidence),
                            })

        return signals

    # ─── 工具方法 ───────────────────────────────────────────────────────────────

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
        base = min(liquidity * 0.01, 500.0)  # 最多流動性 1%，上限 500 USD
        return round(base * confidence, 2)

    async def _store_signals(self, signals: List[dict]):
        """將信號儲存至 roan_signals 表。"""
        insert_sql = text("""
            INSERT INTO roan_signals
                (market_id, signal_type, profit_pct, confidence, suggested_position, status)
            SELECT m.id, :signal_type, :profit_pct, :confidence, :suggested_position, 'pending'
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
                        })
            logger.info(f"儲存 {len(signals)} 個信號")
        except Exception as e:
            logger.error(f"儲存信號失敗：{e}")

    async def _send_telegram_signals(self, signals: List[dict]):
        """發送 Telegram 通知（透過 RoanTelegramBot）。"""
        token = os.getenv("TELEGRAM_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return

        try:
            from app.telegram.roan_bot import RoanTelegramBot
            bot = RoanTelegramBot(token=token, chat_id=chat_id)
            for sig in signals[:5]:  # 每次最多發 5 個信號避免刷屏
                await bot.send_signal(sig)
        except Exception as e:
            logger.error(f"Telegram 發送失敗：{e}")
