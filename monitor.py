#!/usr/bin/env python3
"""
GitHub Actions 终极版
保留原始播报格式，末尾追加「建议 + 理由」
"""
import os
import time
import json
import logging
import requests
import pandas as pd
import ccxt
import talib
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# ---------- 代理 ----------
PROXY_URL = os.getenv("PROXY_URL")
WECOM_URL = os.getenv("WECOM_WEBHOOK_URL")
if not WECOM_URL:
    raise RuntimeError("缺少环境变量：WECOM_WEBHOOK_URL")
proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

# ---------- 交易所 ----------
spot_exchange   = ccxt.binance({'proxies': proxies, 'enableRateLimit': True})
future_exchange = ccxt.binance({'options': {'defaultType': 'future'},
                                'proxies': proxies, 'enableRateLimit': True})

CACHE_FILE = "cache.json"

# ---------- 缓存 ----------
def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ---------- 发送 ----------
def send(msg):
    try:
        r = requests.post(WECOM_URL,
                          json={"msgtype": "text", "text": {"content": msg}},
                          proxies=proxies, timeout=10)
        logging.info("推送结果 %s", r.status_code)
    except Exception as e:
        logging.error("推送失败 %s", e)

# ---------- 技术指标 ----------
def rsi(close, period=14):
    return talib.RSI(np.array(close, dtype='float64'), timeperiod=period)[-1]

def macd(close):
    macd, signal, _ = talib.MACD(np.array(close, dtype='float64'),
                                 fastperiod=12, slowperiod=26, signalperiod=9)
    return macd[-1], signal[-1]

def fmt(num):
    return f"{int(num):,}"

# ---------- 主 ----------
def run_once():
    now = int(time.time())
    cache = load_cache()

    symbols = [s for s in future_exchange.load_markets() if s.endswith('/USDT')]
    logging.info("开始扫描 %d 个合约", len(symbols))

    # 全局附加数据
    try:
        premium = future_exchange.fapiPublicGetPremiumIndex()
        funding_map = {d['symbol']: float(d['lastFundingRate']) for d in premium}
        ls_data = future_exchange.fapiPublic_get_futures_data_topLongShortPositionRatio(
            {'symbol': 'BTCUSDT', 'period': '5m'})
        long_ratio = float(ls_data[-1]['longAccount']) if ls_data else 0.5
    except Exception:
        funding_map, long_ratio = {}, 0.5

    for sym in symbols:
        try:
            base = sym.split('/')[0]
            spot_sym = f"{base}/USDT"

            # ---- 现货 1 min 放量 ----
            if spot_sym in spot_exchange.symbols:
                ticker = spot_exchange.fetch_ticker(spot_sym)
                price = float(ticker['last'])
                vol1m = float(ticker['quoteVolume']) / (24 * 60)

                spot_hist = cache.setdefault("spot", {}).setdefault(spot_sym, [])
                spot_hist.append({"ts": now, "price": price})
                spot_hist = [x for x in spot_hist if now - x["ts"] <= 60]
                cache["spot"][spot_sym] = spot_hist

                if len(spot_hist) >= 2:
                    prev_price = spot_hist[-2]["price"]
                    pct = (price - prev_price) / prev_price * 100
                    if vol1m >= 50000 and abs(pct) >= 2:
                        advice, reasons = _advice_and_reasons(
                            pct, vol1m, funding_map, long_ratio, price, sym)
                        msg = (f"警报：{spot_sym}\n"
                               f"类型：现货放量\n"
                               f"数据：1 分钟成交额: ${fmt(vol1m)}, 价格波动: {pct:.2f}%\n"
                               f"建议：{advice}\n"
                               f"理由：{'; '.join(reasons)}")
                        send(msg)

            # ---- 期货 5 min 加仓 ----
            oi = float(future_exchange.fetch_open_interest(sym)['openInterestAmount'])
            oi_hist = cache.setdefault("oi", {}).setdefault(sym, [])
            oi_hist.append({"ts": now, "oi": oi})
            oi_hist = [x for x in oi_hist if now - x["ts"] <= 300]
            cache["oi"][sym] = oi_hist

            if len(oi_hist) >= 2:
                prev_oi = oi_hist[0]["oi"]
                delta = (oi - prev_oi) / prev_oi * 100
                if abs(delta) >= 5:
                    advice, reasons = _advice_and_reasons(
                        delta, oi, funding_map, long_ratio, price, sym)
                    msg = (f"警报：{sym}\n"
                           f"类型：期货{'加仓' if delta>0 else '减仓'}\n"
                           f"数据：持仓增加: {delta:.2f}%, 当前持仓: {fmt(oi)}\n"
                           f"建议：{advice}\n"
                           f"理由：{'; '.join(reasons)}")
                    send(msg)

        except Exception as e:
            logging.debug("%s 跳过 %s", sym, e)

    save_cache(cache)

# ---------- 评分 ----------
def _advice_and_reasons(pct, vol_or_oi, funding_map, long_ratio, price, sym):
    reasons = []
    score = 0

    # 资金费率
    rate = funding_map.get(sym.replace("/", ""), 0) * 100
    if rate <= -0.15:
        score += 30
        reasons.append(f"费率恐慌 {rate:.2f}%")
    elif rate >= 0.15:
        score -= 25
        reasons.append(f"费率多头 {rate:.2f}%")

    # kline 5min 20 根做 RSI & MACD
    try:
        k = spot_exchange.fetch_ohlcv(f"{sym.split('/')[0]}/USDT", '5m', limit=20)
        close = [float(x[4]) for x in k]
        rsi14 = talib.RSI(np.array(close))[ -1 ]
        macd_val, macd_sig = talib.MACD(np.array(close))[-1][:2]

        if rsi14 <= 30:
            score += 20
            reasons.append(f"RSI{rsi14:.0f}")
        elif rsi14 >= 70:
            score -= 20
            reasons.append(f"RSI{rsi14:.0f}")

        if macd_val > macd_sig:
            score += 10
        else:
            score -= 10
    except:
        pass

    # 大户多空比
    if long_ratio > 3:
        score -= 15
        reasons.append("多空比极多")
    elif long_ratio < 0.33:
        score += 15
        reasons.append("多空比极空")

    # 映射
    if score >= 60:
        return "抄底", reasons
    elif score >= 40:
        return "买入", reasons
    elif score <= -60:
        return "逃顶", reasons
    elif score <= -40:
        return "卖出", reasons
    elif abs(score) <= 20:
        return "观望", reasons
    else:
        return "反向", reasons

if __name__ == "__main__":
    logging.info("GitHub Actions 完全扫描开始")
    run_once()
    logging.info("扫描结束")
