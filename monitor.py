#!/usr/bin/env python3
"""
GitHub Actions 完全版
保留原始播报格式 + 建议/理由
已修复 None 异常
"""
import os, time, json, logging, requests, math
import ccxt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

PROXY_URL = os.getenv("PROXY_URL")
WECOM_URL = os.getenv("WECOM_WEBHOOK_URL")
if not WECOM_URL:
    raise RuntimeError("缺少环境变量：WECOM_WEBHOOK_URL")
proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

spot   = ccxt.binance({'proxies': proxies, 'enableRateLimit': True})
future = ccxt.binance({'options': {'defaultType': 'future'},
                       'proxies': proxies, 'enableRateLimit': True})

CACHE_FILE = "cache.json"
def load_cache():
    return json.load(open(CACHE_FILE)) if os.path.exists(CACHE_FILE) else {}
def save_cache(d):
    json.dump(d, open(CACHE_FILE, "w"), indent=2)
def send(msg):
    try:
        r = requests.post(WECOM_URL, json={"msgtype":"text","text":{"content":msg}},
                          proxies=proxies, timeout=10)
        logging.info("推送结果 %s", r.status_code)
    except Exception as e:
        logging.error("推送失败 %s", e)

def fmt(n): return f"{int(n):,}"

# ---------- 手写 RSI ----------
def rsi(close, period=14):
    if len(close) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(close)):
        diff = close[i] - close[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    rs = avg_gain / (avg_loss + 1e-9)
    return 100 - (100 / (1 + rs))

# ---------- 主 ----------
def run_once():
    now = int(time.time())
    cache = load_cache()
    symbols = [s for s in future.load_markets() if s.endswith('/USDT')]
    logging.info("开始扫描 %d 个合约", len(symbols))

    # 全局数据
    funding_map = {}
    try:
        premiums = future.fapiPublicGetPremiumIndex()
        if premiums:
            funding_map = {d['symbol']: float(d['lastFundingRate']) for d in premiums}
    except Exception as e:
        logging.info("全局数据获取失败 %s", e)

    for sym in symbols:
        try:
            base = sym.split('/')[0]
            spot_sym = f"{base}/USDT"

            # 1) 现货放量
            if spot_sym in spot.symbols:
                ticker = spot.fetch_ticker(spot_sym)
                price = float(ticker['last'])
                vol1m = float(ticker['quoteVolume']) / (24 * 60)

                spot_hist = cache.setdefault("spot", {}).setdefault(spot_sym, [])
                spot_hist.append({"ts": now, "price": price})
                spot_hist = [x for x in spot_hist if now - x["ts"] <= 60]
                cache["spot"][spot_sym] = spot_hist

                if len(spot_hist) >= 2:
                    prev = spot_hist[-2]["price"]
                    pct = (price - prev) / prev * 100
                    if vol1m >= 50000 and abs(pct) >= 2:
                        advice, reasons = _advice(price, pct, vol1m, funding_map, sym)
                        msg = (f"警报：{spot_sym}\n"
                               f"类型：现货放量\n"
                               f"数据：1 分钟成交额: ${fmt(vol1m)}, 价格波动: {pct:.2f}%\n"
                               f"建议：{advice}\n"
                               f"理由：{'; '.join(reasons)}")
                        send(msg)

            # 2) 期货 5 min 持仓
            oi = float(future.fetch_open_interest(sym)['openInterestAmount'])
            oi_hist = cache.setdefault("oi", {}).setdefault(sym, [])
            oi_hist.append({"ts": now, "oi": oi})
            oi_hist = [x for x in oi_hist if now - x["ts"] <= 300]
            cache["oi"][sym] = oi_hist

            if len(oi_hist) >= 2:
                prev_oi = oi_hist[0]["oi"]
                delta = (oi - prev_oi) / prev_oi * 100
                if abs(delta) >= 5:
                    advice, reasons = _advice(oi, delta, oi, funding_map, sym)
                    msg = (f"警报：{sym}\n"
                           f"类型：期货{'加仓' if delta>0 else '减仓'}\n"
                           f"数据：持仓增加: {delta:.2f}%, 当前持仓: {fmt(oi)}\n"
                           f"建议：{advice}\n"
                           f"理由：{'; '.join(reasons)}")
                    send(msg)

        except Exception as e:
            logging.info("%s 跳过 %s", sym, str(e))

    save_cache(cache)

# ---------- 建议 ----------
def _advice(val, change, amount, funding_map, sym):
    reasons, score = [], 0
    rate = funding_map.get(sym.replace("/", ""), 0) * 100
    if rate <= -0.15:
        score += 30; reasons.append(f"费率恐慌 {rate:.2f}%")
    elif rate >= 0.15:
        score -= 25; reasons.append(f"费率多头 {rate:.2f}%")

    # 简易 MACD 方向（3 根均线）
    try:
        k = spot.fetch_ohlcv(f"{sym.split('/')[0]}/USDT", '5m', limit=5)
        close = [float(x[4]) for x in k]
        if len(close) >= 3:
            ma3 = sum(close[-3:]) / 3
            ma_prev = sum(close[-4:-1]) / 3
            if ma3 > ma_prev:
                score += 10
            else:
                score -= 10
    except Exception as e:
        reasons.append(str(e))

    # 映射
    if score >= 50:
        return "抄底", reasons
    elif score >= 30:
        return "买入", reasons
    elif score <= -50:
        return "逃顶", reasons
    elif score <= -30:
        return "卖出", reasons
    else:
        return "观望", reasons

if __name__ == "__main__":
    logging.info("GitHub Actions 无依赖扫描")
    run_once()
    logging.info("扫描结束")
