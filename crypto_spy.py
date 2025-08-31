#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crypto_spy.py
GitHub Actions 专用：只推送到企业微信
使用不受地域限制的域名 api.binance.cc
"""
import ccxt
import time
import logging
import os
import requests
from datetime import datetime
from collections import deque

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

WECOM_WEBHOOK_URL = os.getenv("WECOM_WEBHOOK_URL")
if not WECOM_WEBHOOK_URL:
    raise RuntimeError("请设置 Secrets：WECOM_WEBHOOK_URL")

# ------------- 关键：使用 api.binance.cc 域名 -------------
BINANCE_FUTURES = ccxt.binance({
    "options": {"defaultType": "future"},
    "hostname": "api.binance.cc"
})

# 现货交易所
EXCHANGES = {
    "binance": ccxt.binance({"hostname": "api.binance.cc"}),
    "bybit":   ccxt.bybit(),
    "okx":     ccxt.okx(),
    "bitget":  ccxt.bitget(),
    "mexc":    ccxt.mexc(),
    "gate":    ccxt.gate(),
    "kucoin":  ccxt.kucoin(),
}

HISTORY = {}

def send(msg: str):
    try:
        r = requests.post(
            WECOM_WEBHOOK_URL,
            json={"msgtype": "text", "text": {"content": msg}},
            timeout=10
        )
        r.raise_for_status()
    except Exception as e:
        logging.warning("企业微信发送失败: %s", e)
    else:
        logging.info("已发送: %s", msg)

def load_symbols():
    """获取 Binance 期货 USDT 本位交易对"""
    markets = BINANCE_FUTURES.load_markets()
    symbols = [
        s for s in markets
        if markets[s].get("quote") == "USDT"
        and markets[s].get("linear")
        and markets[s].get("active")
    ]
    logging.info("Binance 期货 USDT 本位有效交易对：%d 个", len(symbols))
    return symbols

def best_spot_ex(symbol):
    """找出 24h 成交量最大的现货交易所"""
    best, best_vol = None, 0
    for ex_name, ex in EXCHANGES.items():
        try:
            ex.load_markets()
            if symbol not in ex.symbols:
                continue
            ticker = ex.fetch_ticker(symbol)
            vol = ticker.get("quoteVolume", 0)
            if vol > best_vol:
                best_vol, best = vol, ex_name
        except Exception as e:
            logging.debug("best_spot_ex %s %s: %s", ex_name, symbol, e)
            continue
    return best

def spot_vol_price(ex_name, symbol):
    """从指定现货交易所获取最近 1 min 成交额 & 价格变化"""
    ex = EXCHANGES[ex_name]
    try:
        ohlcv = ex.fetch_ohlcv(symbol, "1m", limit=2)
        if len(ohlcv) < 2:
            return None, None
        vol = ohlcv[-1][5] * ohlcv[-1][4]  # quoteVolume
        chg = (ohlcv[-1][4] - ohlcv[-2][4]) / ohlcv[-2][4] * 100
        return vol, chg
    except Exception:
        return None, None

def oi(symbol):
    """获取持仓量"""
    try:
        return float(BINANCE_FUTURES.fetch_open_interest(symbol)["openInterest"])
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
            if o is None:
                continue
            HISTORY[symbol]["oi"].append(o)
            if len(HISTORY[symbol]["oi"]) == 6:
                old = HISTORY[symbol]["oi"][0]
                if old and (o - old) / old > 0.05:
                    send(
                        f"警报：{symbol}\n"
                        f"类型：期货加仓\n"
                        f"数据：持仓增加 {(o-old)/old*100:.2f}%，当前持仓 {o:,.0f}"
                    )

            # 现货
            if HISTORY[symbol]["spot_ex"] is None:
                HISTORY[symbol]["spot_ex"] = best_spot_ex(symbol)
            ex_name = HISTORY[symbol]["spot_ex"]
            if ex_name is None:
                continue
            vol, chg = spot_vol_price(ex_name, symbol)
            if vol is None:
                continue
            if vol >= 50_000 and abs(chg) >= 2:
                send(
                    f"警报：{symbol}\n"
                    f"类型：现货放量\n"
                    f"数据：1 分钟成交额 ${vol:,.0f}，价格波动 {chg:+.2f}%"
                )
        except Exception as e:
            logging.warning("处理 %s 异常: %s", symbol, e)

if __name__ == "__main__":
    main()
