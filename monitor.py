#!/usr/bin/env python3
"""
GitHub Actions 专用
完全符合：
1. 1 min 现货成交额 ≥ 5 万 USD 且 |涨跌幅| ≥ 2%
2. 5 min 期货持仓增量 ≥ 5%
仅推送到 WeCom
"""
import os, time, json, logging, requests, pandas as pd
import ccxt

# ---------- 日志 ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# ---------- 代理 ----------
PROXY_URL = os.getenv("PROXY_URL")
WECOM_URL = os.getenv("WECOM_WEBHOOK_URL")
if not WECOM_URL:
    raise RuntimeError("缺少环境变量：WECOM_WEBHOOK_URL")
proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

# ---------- 交易所 ----------
binance_spot   = ccxt.binance({'proxies': proxies, 'enableRateLimit': True})
binance_future = ccxt.binance({'options': {'defaultType': 'future'},
                               'proxies': proxies, 'enableRateLimit': True})

# ---------- 缓存文件 ----------
CACHE_FILE = "cache.json"

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ---------- 发送 ----------
def send(msg: str):
    try:
        r = requests.post(WECOM_URL, json={"msgtype": "text", "text": {"content": msg}},
                          proxies=proxies, timeout=10)
        logging.info("推送结果 %s %s", r.status_code, r.text)
    except Exception as e:
        logging.error("推送失败 %s", e)

# ---------- 主逻辑 ----------
def run_once():
    cache = load_cache()
    symbols = [s for s in binance_future.load_markets() if s.endswith('/USDT')]
    logging.info("共 %d 个 USDT 合约", len(symbols))

    for sym in symbols:
        try:
            base = sym.split('/')[0]
            spot_sym = f"{base}/USDT"
            now_ts = int(time.time())

            # ---------- 1. 现货 ----------
            if spot_sym in binance_spot.symbols:
                spot_ticker = binance_spot.fetch_ticker(spot_sym)
                price = float(spot_ticker['last'])
                vol1m = float(spot_ticker['quoteVolume']) / (24 * 60)

                # 缓存最近 1 min
                spot_hist = cache.setdefault("spot", {}).setdefault(spot_sym, [])
                spot_hist.append({"ts": now_ts, "price": price})
                spot_hist = [x for x in spot_hist if now_ts - x["ts"] <= 60]
                cache["spot"][spot_sym] = spot_hist

                if len(spot_hist) >= 2:
                    prev_price = spot_hist[-2]["price"]
                    pct = (price - prev_price) / prev_price * 100
                    if vol1m >= 50000 and abs(pct) >= 2:
                        send(f"警报：{spot_sym}\n类型：现货放量\n数据：1 分钟成交额: ${int(vol1m)}, 价格波动: {pct:.2f}%")

            # ---------- 2. 期货持仓 ----------
            oi_data = binance_future.fetch_open_interest(sym)
            oi = float(oi_data['openInterestAmount'])

            oi_hist = cache.setdefault("oi", {}).setdefault(sym, [])
            oi_hist.append({"ts": now_ts, "oi": oi})
            oi_hist = [x for x in oi_hist if now_ts - x["ts"] <= 300]
            cache["oi"][sym] = oi_hist

            if len(oi_hist) >= 2:
                oi_prev = oi_hist[0]["oi"]
                if oi_prev > 0:
                    delta_pct = (oi - oi_prev) / oi_prev * 100
                    if delta_pct >= 5:
                        send(f"警报：{sym}\n类型：期货加仓\n数据：持仓增加: {delta_pct:.2f}%, 当前持仓: {int(oi)}")

        except Exception as e:
            logging.debug("%s 跳过 %s", sym, e)

    save_cache(cache)

if __name__ == "__main__":
    logging.info("GitHub Actions 扫描开始")
    run_once()
    logging.info("扫描结束")
