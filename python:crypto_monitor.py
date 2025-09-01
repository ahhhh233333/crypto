#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
加密货币短线交易监控程序
- 使用 CCXT 获取多交易所现货数据与 Binance 期货持仓，按分钟轮询
- 现货放量与期货加仓触发后，通过 Telegram 与企业微信推送告警
- 日志与错误文案使用英文；消息正文按需求使用中文格式
"""

import os
import time
import logging
from datetime import datetime, timezone
from collections import deque, defaultdict
from typing import Dict, Any, Tuple, Optional

import requests
import ccxt


# ============ 基本配置 ============
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("crypto-monitor")

# 环境变量（在 run.sh 中可直接导出）
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
WECOM_WEBHOOK_URL = os.getenv("WECOM_WEBHOOK_URL", "").strip()

# 可调参数（可通过环境变量配置）
LOOP_INTERVAL = int(os.getenv("LOOP_INTERVAL", "60"))        # 轮询周期（秒）
REQUEST_GAP = float(os.getenv("REQUEST_GAP", "0.15"))         # 单次请求之间轻微延时，降低限频风险
SYMBOLS_LIMIT = int(os.getenv("SYMBOLS_LIMIT", "0"))          # 限制处理的代币数量，0 表示不限制

# 现货与期货阈值
SPOT_USD_VOLUME_THRESHOLD = 50_000         # 1 分钟成交额阈值（美元）
SPOT_PRICE_CHANGE_THRESHOLD = 2.0          # 1 分钟价格波动阈值（百分比）
FUTURE_OI_INCREASE_THRESHOLD = 5.0         # 5 分钟持仓增长阈值（百分比）

# ============ 交易所初始化 ============
def create_exchange(exchange_cls, spot: bool = True):
    """创建交易所实例（现货：默认；期货需设置 options）"""
    try:
        if spot:
            ex = exchange_cls({
                "enableRateLimit": True,
                "timeout": 15000,
            })
        else:
            # Binance USDT 合约期货
            ex = exchange_cls({
                "enableRateLimit": True,
                "timeout": 20000,
                "options": {
                    "defaultType": "future",  # 期货
                },
            })
        return ex
    except Exception as e:
        logger.error(f"Failed to init exchange {exchange_cls.__name__}: {e}")
        return None


def init_exchanges():
    """初始化现货交易所与 Binance 期货交易所"""
    # 现货交易所
    exchanges = {
        'binance': create_exchange(ccxt.binance),
        'bybit': create_exchange(ccxt.bybit),
        'okx': create_exchange(ccxt.okx),
        'bitget': create_exchange(ccxt.bitget),
        'mexc': create_exchange(ccxt.mexc),
        'gate': create_exchange(ccxt.gate),
        'kucoin': create_exchange(ccxt.kucoin),
    }
    # 移除初始化失败的
    exchanges = {k: v for k, v in exchanges.items() if v is not None}

    # Binance 期货
    binance_futures = create_exchange(ccxt.binance, spot=False)
    if not binance_futures:
        raise RuntimeError("Failed to initialize Binance futures exchange")

    # 预加载 markets
    try:
        for name, ex in exchanges.items():
            ex.load_markets()
            time.sleep(REQUEST_GAP)
        binance_futures.load_markets()
    except Exception as e:
        logger.error(f"Error loading markets: {e}")
        # 不中断，后续操作尽量容错

    return exchanges, binance_futures


# ============ 币种列表（Binance 期货 USDT 合约） ============
def get_binance_usdt_futures_symbols(binance_futures) -> Dict[str, Dict[str, Any]]:
    """
    获取 Binance USDT 合约期货标的列表：
    返回 { base: {"futures_symbol": "...", "spot_symbol": "..."} }
    """
    result = {}
    try:
        markets = binance_futures.markets or binance_futures.load_markets()
        for m in markets.values():
            # 仅选择 USDT 线性合约、合约市场、活跃
            if not m.get("contract"):
                continue
            if not m.get("linear"):
                continue
            if m.get("quote") != "USDT":
                continue
            if m.get("active") is False:
                continue

            base = m.get("base")
            fut_symbol = m.get("symbol")  # 可能为 "BTC/USDT:USDT"
            spot_symbol = f"{base}/USDT"
            if base and fut_symbol:
                result[base] = {
                    "futures_symbol": fut_symbol,
                    "spot_symbol": spot_symbol,
                }
    except Exception as e:
        logger.error(f"Failed to load Binance futures symbols: {e}")
    return result


# ============ 批量拉取现货 tickers ============
def fetch_all_spot_tickers(exchanges: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    尝试对每家现货交易所执行 fetch_tickers()，减少逐币种调用开销。
    返回 { exchange_name: { symbol: ticker } }
    """
    res: Dict[str, Dict[str, Any]] = {}
    for name, ex in exchanges.items():
        tickers = {}
        try:
            if ex.has.get("fetchTickers", False):
                tickers = ex.fetch_tickers()
                logger.info(f"[{name}] fetch_tickers OK ({len(tickers)} symbols)")
            else:
                logger.warning(f"[{name}] fetchTickers not supported, will fallback per-symbol fetch")
        except Exception as e:
            logger.warning(f"[{name}] fetch_tickers failed: {e}")
        res[name] = tickers or {}
        time.sleep(REQUEST_GAP)
    return res


# ============ 选出现货 24h 成交额最大交易所 ============
def get_best_spot_exchange_for_symbol(
    spot_symbol: str,
    exchanges: Dict[str, Any],
    all_tickers: Dict[str, Dict[str, Any]],
) -> Optional[Tuple[str, Any]]:
    """
    在 7 家现货交易所中，基于 24h quoteVolume 选出成交额最大的交易所。
    若 fetch_tickers 缺失或未覆盖该 symbol，则回退至 fetch_ticker。
    返回 (exchange_name, exchange_instance) 或 None
    """
    best_name = None
    best_ex = None
    best_qvol = 0.0

    for name, ex in exchanges.items():
        qvol = 0.0
        ticker = None
        try:
            # 如果批量 tickers 有该 symbol，直接读取
            ticker = all_tickers.get(name, {}).get(spot_symbol)
            if ticker is None:
                # 若没有，二次确认该交易所是否支持该现货 symbol
                if spot_symbol not in (ex.symbols or ex.load_markets() or {}):
                    continue
                # 回退单币种 ticker
                ticker = ex.fetch_ticker(spot_symbol)
                time.sleep(REQUEST_GAP)

            # 优先使用统一的 quoteVolume
            if ticker is not None:
                qvol = float(ticker.get("quoteVolume") or 0.0)

        except Exception as e:
            logger.debug(f"[{name}] get ticker for {spot_symbol} failed: {e}")
            continue

        if qvol > best_qvol:
            best_qvol = qvol
            best_name = name
            best_ex = ex

    if best_name:
        logger.debug(f"Best spot exchange for {spot_symbol}: {best_name}, qVol={best_qvol}")
        return best_name, best_ex
    return None


# ============ 获取 1m OHLCV 并计算 1 分钟成交额与价格变动 ============
def get_spot_1m_metrics(ex, spot_symbol: str) -> Optional[Dict[str, float]]:
    """
    返回 {
        "usd_volume_1m": float,
        "price_change_1m_pct": float,
        "last_close": float,
    }
    """
    try:
        if not ex.has.get("fetchOHLCV", False):
            return None
        ohlcvs = ex.fetch_ohlcv(spot_symbol, timeframe="1m", limit=2)
        time.sleep(REQUEST_GAP)
        if not ohlcvs or len(ohlcvs) < 2:
            return None
        prev_candle = ohlcvs[-2]
        last_candle = ohlcvs[-1]
        # OHLCV: [timestamp, open, high, low, close, volume(base)]
        prev_close = float(prev_candle[4])
        last_close = float(last_candle[4])
        base_volume = float(last_candle[5])
        usd_volume_1m = base_volume * last_close if last_close is not None else 0.0
        if prev_close > 0:
            pct_change = (last_close - prev_close) / prev_close * 100.0
        else:
            pct_change = 0.0
        return {
            "usd_volume_1m": usd_volume_1m,
            "price_change_1m_pct": pct_change,
            "last_close": last_close,
        }
    except Exception as e:
        logger.debug(f"fetch_ohlcv failed for {spot_symbol}: {e}")
        return None


# ============ 获取 Binance 期货持仓量 ============
def get_binance_open_interest(binance_futures, futures_symbol: str) -> Optional[float]:
    """
    获取 Binance 期货持仓量（返回归一化后的 open interest 数值，单位以交易所返回为准）
    """
    try:
        # ccxt 规范：fetch_open_interest(symbol) 返回 dict
        data = binance_futures.fetch_open_interest(futures_symbol)
        time.sleep(REQUEST_GAP)
        if not data:
            return None

        # 兼容不同字段命名
        for key in ("openInterestAmount", "openInterest", "amount", "value"):
            if key in data and data[key] is not None:
                try:
                    return float(data[key])
                except Exception:
                    continue

        # 某些实现可能把数值塞在 info 里
        info = data.get("info", {})
        for key in ("openInterest", "openInterestAmount", "amount", "value"):
            if key in info and info[key] is not None:
                try:
                    return float(info[key])
                except Exception:
                    continue

        return None
    except Exception as e:
        logger.debug(f"fetch_open_interest failed for {futures_symbol}: {e}")
        return None


# ============ 推送 ============
def send_telegram(message: str):
    """发送 Telegram 消息（若缺少配置则跳过）"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug("Telegram config missing, skip sending")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        resp = requests.post(url, json=data, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"Telegram send failed: HTTP {resp.status_code}, {resp.text}")
    except Exception as e:
        logger.warning(f"Telegram send error: {e}")


def send_wecom(message: str):
    """发送企业微信消息（若缺少配置则跳过）"""
    if not WECOM_WEBHOOK_URL:
        logger.debug("WeCom webhook missing, skip sending")
        return
    payload = {
        "msgtype": "text",
        "text": {"content": message}
    }
    try:
        resp = requests.post(WECOM_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"WeCom send failed: HTTP {resp.status_code}, {resp.text}")
        else:
            # 企业微信若 JSON 有误会返回 errcode/errmsg
            try:
                rj = resp.json()
                if rj.get("errcode") != 0:
                    logger.warning(f"WeCom returns error: {rj}")
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"WeCom send error: {e}")


def send_alert(message: str):
    """统一发送到 Telegram 与 WeCom"""
    logger.info(f"ALERT: {message}")
    send_telegram(message)
    send_wecom(message)


# ============ 主流程 ============
def main():
    logger.info("Starting crypto monitor (spot & futures) ...")
    exchanges, binance_futures = init_exchanges()

    # 获取期货标的列表（USDT 永续）
    fut_map = get_binance_usdt_futures_symbols(binance_futures)
    if not fut_map:
        logger.error("No Binance USDT futures symbols found. Exiting.")
        return

    # 可选限制处理数量（测试/减载）
    bases = sorted(fut_map.keys())
    if SYMBOLS_LIMIT > 0:
        bases = bases[:SYMBOLS_LIMIT]

    logger.info(f"Monitoring {len(bases)} symbols from Binance USDT futures")

    # 历史数据：每个 base 存储最近 6 个点（约等于 6 分钟）
    history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=6))

    while True:
        loop_start = time.time()
        try:
            # 批量拉取现货 24h tickers
            all_tickers = fetch_all_spot_tickers(exchanges)

            # 遍历每个代币
            for base in bases:
                try:
                    fut_symbol = fut_map[base]["futures_symbol"]
                    spot_symbol = fut_map[base]["spot_symbol"]

                    # 选择 24h 成交额最大的现货交易所
                    best = get_best_spot_exchange_for_symbol(
                        spot_symbol=spot_symbol,
                        exchanges=exchanges,
                        all_tickers=all_tickers,
                    )
                    # 若无合适现货源，跳过本代币
                    if not best:
                        continue
                    best_name, best_ex = best

                    # 获取 1 分钟现货指标
                    spot_metrics = get_spot_1m_metrics(best_ex, spot_symbol)
                    if spot_metrics:
                        usd_volume_1m = spot_metrics["usd_volume_1m"]
                        price_change_pct = spot_metrics["price_change_1m_pct"]
                        last_close = spot_metrics["last_close"]

                        # 现货报警判断
                        if usd_volume_1m >= SPOT_USD_VOLUME_THRESHOLD and abs(price_change_pct) >= SPOT_PRICE_CHANGE_THRESHOLD:
                            msg = (
                                f"警报：{base}/USDT\n"
                                f"类型：现货放量\n"
                                f"数据：1 分钟成交额: ${usd_volume_1m:,.0f}, 价格波动: {price_change_pct:.2f}%\n"
                                f"交易所：{best_name}\n"
                                f"时间：{datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}"
                            )
                            send_alert(msg)

                    # 获取期货持仓量（Binance 期货）
                    oi = get_binance_open_interest(binance_futures, fut_symbol)
                    # 记录历史
                    history[base].append({
                        "ts": int(time.time()),
                        "oi": oi if oi is not None else None,
                        "price": spot_metrics["last_close"] if spot_metrics else None,
                    })
                    # 期货报警判断（与 5 分钟前比较）
                    # 需要至少 6 个点（0..5），当前为 -1，5 分钟前为 -6
                    if len(history[base]) >= 6:
                        now_item = history[base][-1]
                        prev_item = history[base][0]  # 最早的（约 5 分钟前）
                        if now_item["oi"] is not None and prev_item["oi"] is not None and prev_item["oi"] > 0:
                            change_pct = (now_item["oi"] - prev_item["oi"]) / prev_item["oi"] * 100.0
                            if change_pct >= FUTURE_OI_INCREASE_THRESHOLD:
                                msg = (
                                    f"警报：{base}/USDT\n"
                                    f"类型：期货加仓\n"
                                    f"数据：持仓增加: {change_pct:.2f}%, 当前持仓: {now_item['oi']:,.4f}\n"
                                    f"交易所：Binance Futures\n"
                                    f"时间：{datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}"
                                )
                                send_alert(msg)

                except Exception as e:
                    logger.debug(f"Per-symbol error for {base}: {e}")
                    continue

        except Exception as e:
            logger.warning(f"Main loop error: {e}")

        # 控制轮询节奏（确保总周期约等于 LOOP_INTERVAL）
        elapsed = time.time() - loop_start
        sleep_time = max(1.0, LOOP_INTERVAL - elapsed)
        logger.info(f"Loop finished in {elapsed:.2f}s, sleeping {sleep_time:.2f}s")
        time.sleep(sleep_time)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Stopped by user (KeyboardInterrupt)")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
