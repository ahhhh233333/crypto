# -*- coding: utf-8 -*-
"""
加密货币监控脚本（现货 + 期货）
- 期货：Binance USDT 永续合约（默认）
- 现货：从候选交易所中动态选择 24h 估算成交额最大的交易所
- 报警条件：
  1) 现货：1分钟K线中，成交额(≈ close * volume) >= 50,000 USD 且 |涨跌幅| >= 2%
  2) 期货：5分钟内 OI 增幅 >= 5%

环境变量：
- WECHAT_WEBHOOK           企业微信机器人完整Webhook URL（可选）
- TELEGRAM_BOT_TOKEN       Telegram Bot Token（可选）
- TELEGRAM_CHAT_ID         Telegram Chat ID（可选）
- SYMBOLS_LIMIT            每轮扫描的最大币种数（默认 40，建议先 30~60 以防限速）
- LOOP_INTERVAL_SECONDS    主循环间隔秒数（默认 20）

依赖：
- pip install ccxt requests python-dateutil

备注：
- 该脚本仅使用 ccxt（非 ccxt.pro），避免对 WebSocket 的依赖。
- 对 open interest 采用多策略获取，尽量兼容不同 ccxt 版本。
"""

import os
import sys
import time
import logging
import threading
from collections import deque, defaultdict
from datetime import datetime, timezone
from typing import Dict, Optional

import requests
import ccxt


# ---------------------- 基础配置 ----------------------
SPOT_CANDIDATES = [
    "binance",
    "okx",
    "bybit",
    "kucoin",
]

QUOTE = "USDT"
SPOT_TIMEFRAME = "1m"
SPOT_LIMIT = 2
SPOT_MIN_USD_VALUE = 50_000.0
SPOT_MIN_MOVE = 0.02  # 2%

FUTURES_OI_WINDOW_MIN = 5
FUTURES_OI_MIN_GROWTH = 0.05

DEFAULT_LOOP_INTERVAL = int(os.getenv("LOOP_INTERVAL_SECONDS", "20"))
SYMBOLS_LIMIT = int(os.getenv("SYMBOLS_LIMIT", "40"))

DEDUP_TTL_SECONDS = 10 * 60  # 10分钟


# ---------------------- 日志配置 ----------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("monitor")


# ---------------------- 工具函数 ----------------------
def utcnow_ms() -> int:
    return int(time.time() * 1000)


def ms_to_str(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def safe_float(v, default=None) -> Optional[float]:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


# ---------------------- 通知模块 ----------------------
class Notifier:
    def __init__(self):
        self.wechat_webhook = os.getenv("WECHAT_WEBHOOK", "").strip()
        self.tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    def send_wechat(self, text: str):
        if not self.wechat_webhook:
            return
        try:
            payload = {"msgtype": "text", "text": {"content": text}}
            r = requests.post(self.wechat_webhook, json=payload, timeout=10)
            if r.status_code != 200:
                logger.warning(f"WeCom notify failed: HTTP {r.status_code} {r.text[:180]}")
        except Exception as e:
            logger.exception(f"WeCom notify exception: {e}")

    def send_telegram(self, text: str):
        if not (self.tg_token and self.tg_chat_id):
            return
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            r = requests.post(url, data={"chat_id": self.tg_chat_id, "text": text}, timeout=10)
            if r.status_code != 200:
                logger.warning(f"Telegram notify failed: HTTP {r.status_code} {r.text[:180]}")
        except Exception as e:
            logger.exception(f"Telegram notify exception: {e}")

    def notify_all(self, text: str):
        threading.Thread(target=self.send_wechat, args=(text,), daemon=True).start()
        threading.Thread(target=self.send_telegram, args=(text,), daemon=True).start()


# ---------------------- 现货交易所选择 ----------------------
def estimate_exchange_24h_quote_volume(ex: ccxt.Exchange, quote: str) -> float:
    """估算交易所 24h quote 成交额之和（仅 quote=USDT 的现货对）"""
    try:
        tickers = ex.fetch_tickers()
    except Exception as e:
        logger.warning(f"{ex.id} fetch_tickers failed: {e}")
        return 0.0

    total = 0.0
    for sym, t in tickers.items():
        if not isinstance(sym, str) or f"/{quote}" not in sym:
            continue
        qv = None
        if isinstance(t, dict):
            qv = safe_float(t.get("quoteVolume"))
            if qv is None and isinstance(t.get("info"), dict):
                qv = safe_float(t["info"].get("quoteVolume"))
            if qv is None:
                close = safe_float(t.get("last")) or safe_float(t.get("close"))
                base_vol = safe_float(t.get("baseVolume"))
                if close is not None and base_vol is not None:
                    qv = close * base_vol
        if qv:
            total += qv
    return total


def pick_top_spot_exchange(quote: str = QUOTE) -> ccxt.Exchange:
    """从候选中选择 24h 成交额最大的现货交易所"""
    best_ex = None
    best_vol = -1.0

    for ex_id in SPOT_CANDIDATES:
        try:
            ex = getattr(ccxt, ex_id)({"enableRateLimit": True})
            ex.load_markets()
            vol = estimate_exchange_24h_quote_volume(ex, quote)
            logger.info(f"Spot candidate {ex.id}: est 24h quoteVolume={vol:,.0f} {quote}")
            if vol > best_vol:
                best_vol = vol
                best_ex = ex
        except Exception as e:
            logger.warning(f"Init spot exchange {ex_id} failed: {e}")

    if best_ex is None:
        logger.warning("No spot exchange available, fallback to binance spot.")
        best_ex = ccxt.binance({"enableRateLimit": True})
        best_ex.load_markets()

    logger.info(f"Pick spot exchange: {best_ex.id} (est 24h {best_vol:,.0f} {quote})")
    return best_ex


# ---------------------- 期货 OI 获取 ----------------------
def fetch_open_interest_binance(binance_future: ccxt.binance, symbol: str) -> Optional[float]:
    """
    获取 Binance USDT 永续合约的 OI，多策略尝试以兼容不同 ccxt 版本。
    返回值单位（张/币）对“百分比变化”比较足够使用。
    """
    market = binance_future.market(symbol)
    # 1) 统一 API（如果版本支持）
    try:
        if hasattr(binance_future, "fetch_open_interest"):
            oi = binance_future.fetch_open_interest(symbol)
            if isinstance(oi, dict):
                val = safe_float(oi.get("openInterest"))
                if val is not None:
                    return val
    except Exception:
        pass

    # 2) 公共端点兜底
    id_ = market.get("id") or market.get("symbol") or symbol.replace("/", "")
    for method in ("fapiPublicGetOpenInterest", "publicFapiGetOpenInterest", "fapiPublicV1GetOpenInterest"):
        try:
            if hasattr(binance_future, method):
                fn = getattr(binance_future, method)
                data = fn({"symbol": id_})
                if isinstance(data, dict):
                    val = safe_float(data.get("openInterest"))
                    if val is not None:
                        return val
        except Exception:
            continue

    return None


# ---------------------- 主监控逻辑 ----------------------
class Monitor:
    def __init__(self):
        self.notifier = Notifier()

        # 选择现货监控源
        self.spot = pick_top_spot_exchange(QUOTE)
        # Binance 期货（USDT 永续）
        self.futures = ccxt.binance({
            "enableRateLimit": True,
            "options": {
                "defaultType": "future",
                "adjustForTimeDifference": True,
            }
        })

        self.spot.load_markets()
        self.futures.load_markets()

        # 合约列表（USDT 线性合约）
        self.futures_symbols = [
            s for s, m in self.futures.markets.items()
            if m.get("contract") and m.get("linear") and m.get("quote") == QUOTE and m.get("active", True)
        ]
        self.futures_symbols.sort()

        # 记录现货可用 symbols，避免 fetch_ohlcv 不支持时报错
        self.spot_symbols = set(self.spot.symbols or [])
        self.dedup_cache: Dict[str, int] = {}
        self.oi_history = defaultdict(lambda: deque(maxlen=60))  # symbol -> deque[(ts_ms, oi)]

        logger.info(f"Futures symbols (USDT linear) total={len(self.futures_symbols)}; spot={self.spot.id}")

    def _dedup(self, key: str) -> bool:
        now = int(time.time())
        ts = self.dedup_cache.get(key)
        if ts and now - ts < DEDUP_TTL_SECONDS:
            return True
        self.dedup_cache[key] = now
        # 清理过期
        to_del = [k for k, v in self.dedup_cache.items() if now - v >= DEDUP_TTL_SECONDS]
        for k in to_del:
            self.dedup_cache.pop(k, None)
        return False

    def _format_spot_msg(self, symbol: str, o: float, c: float, vol_base: float, vol_quote: float, ts_ms: int) -> str:
        move = (c - o) / o if o else 0.0
        return (
            f"[SPOT ALERT]\n"
            f"Exchange: {self.spot.id}\n"
            f"Symbol: {symbol}\n"
            f"Time: {ms_to_str(ts_ms)}\n"
            f"Price: {c:.6g} (open {o:.6g}, change {move*100:.2f}%)\n"
            f"1m Volume: {vol_base:.6g} base ≈ ${vol_quote:,.0f}\n"
            f"Rule: 1m value >= $50,000 AND |move| >= 2%\n"
        )

    def _format_oi_msg(self, symbol: str, oi_old: float, oi_new: float, ts_ms: int, mins: int) -> str:
        growth = (oi_new - oi_old) / oi_old if oi_old else 0.0
        return (
            f"[FUTURES OI ALERT]\n"
            f"Exchange: binance (USDT-M)\n"
            f"Symbol: {symbol}\n"
            f"Time: {ms_to_str(ts_ms)}\n"
            f"Open Interest: {oi_old:.6g} -> {oi_new:.6g} ({growth*100:.2f}% in {mins}m)\n"
            f"Rule: OI growth >= 5% within {mins} minutes\n"
        )

    def check_spot_alert(self, symbol: str):
        if symbol not in self.spot_symbols:
            return

        try:
            ohlcvs = self.spot.fetch_ohlcv(symbol, timeframe=SPOT_TIMEFRAME, limit=SPOT_LIMIT)
        except Exception as e:
            logger.debug(f"fetch_ohlcv failed {self.spot.id}:{symbol}: {e}")
            return

        if not ohlcvs or len(ohlcvs) < 2:
            return

        ts, o, h, l, c, vol_base = ohlcvs[-2]
        o = safe_float(o)
        c = safe_float(c)
        vol_base = safe_float(vol_base)

        if not (o and c and vol_base):
            return

        vol_quote = c * vol_base
        move = abs((c - o) / o)

        if vol_quote >= SPOT_MIN_USD_VALUE and move >= SPOT_MIN_MOVE:
            key = f"spot:{self.spot.id}:{symbol}:{ts}"
            if not self._dedup(key):
                msg = self._format_spot_msg(symbol, o, c, vol_base, vol_quote, ts)
                self.notifier.notify_all(msg)
                logger.info(f"SPOT ALERT sent: {symbol} {vol_quote:,.0f}USD {move*100:.2f}%")

    def check_futures_oi_alert(self, symbol: str):
        try:
            oi_now = fetch_open_interest_binance(self.futures, symbol)
        except Exception as e:
            logger.debug(f"fetch_open_interest failed {symbol}: {e}")
            return

        if oi_now is None:
            return

        ts = utcnow_ms()
        q: deque = self.oi_history[symbol]
        q.append((ts, oi_now))

        cutoff = ts - FUTURES_OI_WINDOW_MIN * 60 * 1000
        older = None
        for t0, oi0 in q:
            if t0 <= cutoff:
                older = (t0, oi0)
        if not older:
            return

        _, oi_old = older
        if oi_old:
            growth = (oi_now - oi_old) / oi_old
            if growth >= FUTURES_OI_MIN_GROWTH:
                window_key = int(cutoff / (FUTURES_OI_WINDOW_MIN * 60 * 1000))
                key = f"oi:{symbol}:{window_key}"
                if not self._dedup(key):
                    msg = self._format_oi_msg(symbol, oi_old, oi_now, ts, FUTURES_OI_WINDOW_MIN)
                    self.notifier.notify_all(msg)
                    logger.info(f"FUTURES OI ALERT sent: {symbol} +{growth*100:.2f}% in {FUTURES_OI_WINDOW_MIN}m")

    def run_once(self):
        symbols = self.futures_symbols[:SYMBOLS_LIMIT] if SYMBOLS_LIMIT > 0 else self.futures_symbols
        for sym in symbols:
            try:
                self.check_spot_alert(sym)
                self.check_futures_oi_alert(sym)
            except Exception as e:
                logger.debug(f"run_once symbol {sym} error: {e}")

    def run_forever(self):
        logger.info(f"Start loop: interval={DEFAULT_LOOP_INTERVAL}s, symbols_limit={SYMBOLS_LIMIT}")
        while True:
            start = time.time()
            try:
                self.run_once()
            except Exception as e:
                logger.exception(f"loop error: {e}")

            elapsed = time.time() - start
            sleep_sec = max(1.0, DEFAULT_LOOP_INTERVAL - elapsed)
            time.sleep(sleep_sec)


def main():
    logger.info("Initializing monitor...")
    try:
        m = Monitor()
        m.run_forever()
    except KeyboardInterrupt:
        logger.info("Interrupted by user, exit.")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
