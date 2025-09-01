#!/usr/bin/env python3 
"""
GitHub Actions 终极版（空值安全）
"""
import os, time, json, logging, requests, math 
import ccxt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

PROXY_URL = os.getenv("PROXY_URL")
WECOM_URL = os.getenv("WECOM_WEBHOOK_URL")
// ... existing code ...
spot   = ccxt.binance({'proxies': {'http': PROXY_URL, 'https': PROXY_URL} if PROXY_URL else None, 'enableRateLimit': True})
future = ccxt.binance({'options': {'defaultType': 'future'},
                       'proxies': {'http': PROXY_URL, 'https': PROXY_URL} if PROXY_URL else None,
                       'enableRateLimit': True})

# 关键修复：预加载 markets，避免 spot.symbols 为 None 导致 in 判定报错
try:
    spot.load_markets()
    time.sleep(0.1)
    future.load_markets()
except Exception as e:
    logging.warning("load_markets error: %s", e)
// ... existing code ...
CACHE_FILE = "cache.json"
// ... existing code ...
def send(msg):
    try:
        if not WECOM_URL:
            logging.warning("WECOM_WEBHOOK_URL not set, skip send")
            return
        r = requests.post(WECOM_URL, json={"msgtype":"text","text":{"content":msg}},
                          proxies={'http': PROXY_URL, 'https': PROXY_URL} if PROXY_URL else None, timeout=10)
        logging.info("推送结果 %s %s", r.status_code, r.text[:200])
    except Exception as e:
        logging.error("推送失败 %s", e)
// ... existing code ...

# 新增：安全提取期货 OI（不同实现字段名不同）
def safe_fetch_open_interest(symbol: str) -> float | None:
    try:
        data = future.fetch_open_interest(symbol)
        if not data:
            return None
        # 先从顶层取
        for k in ("openInterestAmount", "openInterest", "amount", "value"):
            if k in data and data[k] is not None:
                return float(data[k])
        # 再从 info 里取
        info = data.get("info", {}) if isinstance(data, dict) else {}
        for k in ("openInterestAmount", "openInterest", "amount", "value"):
            v = info.get(k)
            if v is not None:
                return float(v)
        return None
    except Exception as e:
        logging.debug("safe_fetch_open_interest failed %s: %s", symbol, e)
        return None

# 新增：用 1m OHLCV 计算 1 分钟成交额（美元）与 1 分钟涨跌幅
def spot_1m_metrics(spot_symbol: str):
    try:
        if not spot.has.get("fetchOHLCV", False):
            return None
        ohlcvs = spot.fetch_ohlcv(spot_symbol, timeframe="1m", limit=2)
        if not ohlcvs or len(ohlcvs) < 2:
            return None
        prev, last = ohlcvs[-2], ohlcvs[-1]
        prev_close = float(prev[4])
        last_close = float(last[4])
        base_volume = float(last[5])
        usd_volume_1m = base_volume * last_close
        pct = ((last_close - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0
        return {"usd_volume_1m": usd_volume_1m, "price_change_1m_pct": pct, "last_close": last_close}
    except Exception as e:
        logging.debug("spot_1m_metrics %s failed: %s", spot_symbol, e)
        return None

# ---------- 主 ----------
def run_once():
    now = int(time.time())
    cache = load_cache()

    # 修复：从 futures markets 里筛选 USDT 线性合约，兼容 :USDT 符号
    try:
        markets = future.markets or future.load_markets()
        symbols = []
        for m in markets.values():
            if not m.get("contract"): 
                continue
            if not m.get("linear"):
                continue
            if m.get("quote") != "USDT":
                continue
            if m.get("active") is False:
                continue
            symbols.append(m["symbol"])  # 形如 "BTC/USDT:USDT"
    except Exception as e:
        logging.error("加载期货合约失败: %s", e)
        symbols = []

    logging.info("开始扫描 %d 个合约", len(symbols))

    funding_map = safe_funding_map()

    for sym in symbols:
        try:
            # 现货符号与支持性判断
            base = sym.split('/')[0]
            spot_sym = f"{base}/USDT"
            # 防御：spot.symbols 可能为 None，且即使支持 OHLCV 也可直接尝试
            if spot_sym in (spot.symbols or []):
                # 使用 1m OHLCV得出 1分钟成交额与涨跌幅
                m = spot_1m_metrics(spot_sym)
                if m:
                    usd_vol_1m = m["usd_volume_1m"]
                    pct = m["price_change_1m_pct"]
                    if usd_vol_1m >= 50_000 and abs(pct) >= 2.0:
                        advice, reasons = _advice(m["last_close"], pct, usd_vol_1m, funding_map, sym)
                        msg = (f"警报：{spot_sym}\n"
                               f"类型：现货放量\n"
                               f"数据：1 分钟成交额: ${int(usd_vol_1m):,}, 价格波动: {pct:.2f}%\n"
                               f"建议：{advice}\n"
                               f"理由：{'; '.join(reasons)}")
                        send(msg)

            # 期货持仓（5min对比）
            oi = safe_fetch_open_interest(sym)
            if oi is not None:
                oi_hist = cache.setdefault("oi", {}).setdefault(sym, [])
                oi_hist.append({"ts": now, "oi": float(oi)})
                # 仅保留最近 5 分钟
                oi_hist = [x for x in oi_hist if now - x["ts"] <= 300]
                cache["oi"][sym] = oi_hist

                if len(oi_hist) >= 2 and oi_hist[0]["oi"] > 0:
                    prev_oi = oi_hist[0]["oi"]
                    delta = (oi - prev_oi) / prev_oi * 100
                    if abs(delta) >= 5:
                        advice, reasons = _advice(oi, delta, oi, funding_map, sym)
                        msg = (f"警报：{sym}\n"
                               f"类型：期货{'加仓' if delta>0 else '减仓'}\n"
                               f"数据：持仓增加: {delta:.2f}%, 当前持仓: {int(oi):,}\n"
                               f"建议：{advice}\n"
                               f"理由：{'; '.join(reasons)}")
                        send(msg)

        except Exception as e:
            # 记录更明确的错误来源，便于排查
            logging.info("%s 跳过：%s", sym, str(e))
            continue

    save_cache(cache)
// ... existing code ...
if __name__ == "__main__":
    logging.info("GitHub Actions 无依赖扫描")
    run_once()
    logging.info("扫描结束")
