#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crypto_spy.py
GitHub Actions 专用：只推送到企业微信
"""
import ccxt, time, logging, os, requests
from datetime import datetime
from collections import deque

# 日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# 环境变量（仅企业微信）
WECOM_WEBHOOK_URL = os.getenv("WECOM_WEBHOOK_URL")
if not WECOM_WEBHOOK_URL:
    raise RuntimeError("请设置 Secrets：WECOM_WEBHOOK_URL")

# 交易所
EXCHANGES = {k: getattr(ccxt, k)() for k in ["binance", "bybit", "okx", "bitget", "mexc", "gate", "kucoin"]}
BINANCE_FUTURES = ccxt.binance({"options": {"defaultType": "future"}})

# 历史
HISTORY = {}

def send(msg: str):
    """推送到企业微信机器人"""
    try:
        r = requests.post(WECOM_WEBHOOK_URL, json={"msgtype": "text", "text": {"content": msg}}, timeout=10)
        r.raise_for_status()
    except Exception as e:
        logging.warning("企业微信发送失败: %s", e)
    else:
        logging.info("已发送: %s", msg)

def load_symbols():
    """Binance USDT 本位合约"""
    m = BINANCE_FUTURES.load_markets()
    return [s for s in m if m[s].get("quote") == "USDT" and m[s].get("linear")]

def best_spot_ex(sym):
    best, best_vol = None, 0
    for ex in EXCHANGES.values():
        try:
            ex.load_markets()
            if sym not in ex.symbols: continue
            t = ex.fetch_ticker(sym)
            vol = t.get("quoteVolume", 0)
            if vol > best_vol:
                best_vol, best = vol, ex
        except Exception:
            continue
    return best

def spot_vol_price(ex, sym):
    try:
        ohlcv = ex.fetch_ohlcv(sym, "1m", limit=2)
        if len(ohlcv) < 2: return None, None
        vol = ohlcv[-1][5] * ohlcv[-1][4]
        chg = (ohlcv[-1][4] - ohlcv[-2][4]) / ohlcv[-2][4] * 100
        return vol, chg
    except Exception:
        return None, None

def oi(sym):
    try:
        return float(BINANCE_FUTURES.fetch_open_interest(sym)["openInterest"])
    except Exception:
        return None

def main():
    symbols = load_symbols()
    for s in symbols:
        HISTORY[s] = {"oi": deque(maxlen=6), "spot_ex": None}

    for symbol in symbols:
        try:
            # 期货持仓
            o = oi(symbol)
            if o is None: continue
            HISTORY[symbol]["oi"].append(o)
            if len(HISTORY[symbol]["oi"]) == 6:
                old = HISTORY[symbol]["oi"][0]
                if old and (o - old) / old > 0.05:
                    send(f"警报：{symbol}\n类型：期货加仓\n数据：持仓增加 {(o-old)/old*100:.2f}%，当前持仓 {o:,.0f}")

            # 现货
            if HISTORY[symbol]["spot_ex"] is None:
                ex = best_spot_ex(symbol)
                HISTORY[symbol]["spot_ex"] = ex
            ex = HISTORY[symbol]["spot_ex"]
            if ex is None: continue
            vol, chg = spot_vol_price(ex, symbol)
            if vol is None: continue
            if vol >= 50_000 and abs(chg) >= 2:
                send(f"警报：{symbol}\n类型：现货放量\n数据：1分钟成交额 ${vol:,.0f}，价格波动 {chg:+.2f}%")
        except Exception as e:
            logging.warning("处理 %s 异常: %s", symbol, e)

if __name__ == "__main__":
    main()
