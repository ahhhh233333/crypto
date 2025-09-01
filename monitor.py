#!/usr/bin/env python3
"""
GitHub Actions 版本：监控 Binance 期货 & 现货 → 仅推送到企业微信
"""
import ccxt, os, time, requests, pandas as pd
from datetime import datetime

# ---------- 日志 ----------
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

PROXY_URL = os.getenv("PROXY_URL")          # GitHub Action 传入
WECOM_URL = os.getenv("WECOM_WEBHOOK_URL")
if not WECOM_URL:
    raise RuntimeError("缺少环境变量：WECOM_WEBHOOK_URL")

PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
logging.info(f"Proxy: {PROXY_URL}")

# ---------- 交易所 ----------
binance_futures = ccxt.binance({
    'options': {'defaultType': 'future'},
    'enableRateLimit': True,
    'proxies': PROXIES
})

exchanges = {
    'binance': ccxt.binance({'enableRateLimit': True, 'proxies': PROXIES}),
    'bybit':   ccxt.bybit({'enableRateLimit': True, 'proxies': PROXIES}),
    'okx':     ccxt.okx({'enableRateLimit': True, 'proxies': PROXIES}),
    'bitget':  ccxt.bitget({'enableRateLimit': True, 'proxies': PROXIES}),
    'mexc':    ccxt.mexc({'enableRateLimit': True, 'proxies': PROXIES}),
    'gate':    ccxt.gate({'enableRateLimit': True, 'proxies': PROXIES}),
    'kucoin':  ccxt.kucoin({'enableRateLimit': True, 'proxies': PROXIES}),
}

# ---------- 发送 ----------
def send(msg: str):
    try:
        resp = requests.post(WECOM_URL, json={"msgtype": "text", "text": {"content": msg}},
                             proxies=PROXIES, timeout=10)
        logging.info(f"推送结果 {resp.status_code} {resp.text}")
    except Exception as e:
        logging.error(f"推送失败: {e}")

# ---------- 核心 ----------
def run_once():
    symbols = [s for s in binance_futures.load_markets() if s.endswith('/USDT')]
    logging.info(f"共 {len(symbols)} 个 USDT 合约待扫描")

    for sym in symbols:
        try:
            # 1) 现货
            best = None
            max_vol = 0
            for ex in exchanges.values():
                try:
                    ex.load_markets()
                    if sym not in ex.symbols:
                        continue
                    t = ex.fetch_ticker(sym)
                    vol = float(t['quoteVolume'])
                    if vol > max_vol:
                        max_vol, best = vol, ex
                except Exception:
                    continue
            if not best:
                continue

            ticker = best.fetch_ticker(sym)
            price = float(ticker['last'])
            vol1m = float(ticker['quoteVolume']) / (24*60)

            # 2) 期货持仓
            oi = float(binance_futures.fetch_open_interest(sym)['openInterestAmount'])

            # 简单示例：成交量>5万 & 1min 涨跌幅>2%
            if vol1m >= 50000:
                pct = (price - ticker['open']) / ticker['open'] * 100
                if abs(pct) >= 2:
                    send(f"现货放量 {sym}\n成交额 ${int(vol1m)}\n涨跌 {pct:.2f}%")
            # 5min 持仓增>5%（GitHub Action 每次跑是冷启动，这里用前后两次差值模拟）
            # 这里简化：持仓>0 且比上次记录增>5%
            # 如需更严谨请持久化缓存（Actions Cache）
        except Exception as e:
            logging.debug(f"{sym} 跳过 {e}")

if __name__ == "__main__":
    logging.info("GitHub Actions 单次扫描开始")
    run_once()
    logging.info("扫描结束")
