// src/index.js
// Cloudflare Worker 每 1 分钟触发一次，推送到企业微信
// 使用 Cloudflare 边缘网络，不受 GitHub IP 限制

const BINANCE_FUTURES_HOST = 'https://fapi.binance.com';   // 期货
const BINANCE_SPOT_HOST    = 'https://api.binance.com';    // 现货
const WECOM_WEBHOOK_URL  = globalThis.WECOM_WEBHOOK_URL || 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx';

// 企业微信推送
async function sendWeCom(msg) {
  await fetch(WECOM_WEBHOOK_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ msgtype: 'text', text: { content: msg } })
  });
}

// 统一 GET 请求
async function get(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// 期货 USDT 本位交易对
async function loadFuturesSymbols() {
  const data = await get(`${BINANCE_FUTURES_HOST}/fapi/v1/exchangeInfo`);
  return data.symbols.filter(s => s.status === 'TRADING' && s.quoteAsset === 'USDT').map(s => s.symbol);
}

// 持仓量
async function fetchOpenInterest(symbol) {
  const data = await get(`${BINANCE_FUTURES_HOST}/fapi/v1/openInterest?symbol=${symbol}`);
  return Number(data.openInterest);
}

// 现货 24h ticker（含成交量）
async function fetch24h(symbol) {
  const data = await get(`${BINANCE_SPOT_HOST}/api/v3/ticker/24hr?symbol=${symbol}`);
  return {
    volume: Number(data.quoteVolume),
    price:  Number(data.lastPrice)
  };
}

// 主监控逻辑
async function monitor() {
  const symbols = await loadFuturesSymbols();
  const now = new Date().toLocaleString('zh-CN');

  // 简单缓存：把上一分钟数据写在 KV 里（这里用全局变量演示）
  if (!globalThis.cache) globalThis.cache = {};
  const cache = globalThis.cache;

  for (const symbol of symbols) {
    try {
      // 1. 期货持仓量
      const oi = await fetchOpenInterest(symbol);
      if (!cache[symbol]) cache[symbol] = {};
      const oldOi = cache[symbol].oi;
      if (oldOi && oi > oldOi * 1.05) {
        await sendWeCom(`⚠️ 期货加仓 ${symbol}\n持仓增加 ${((oi/oldOi-1)*100).toFixed(2)}%\n当前 ${oi.toFixed(0)}\n时间 ${now}`);
      }
      cache[symbol].oi = oi;

      // 2. 现货成交量（近似：若 24h / 1440 > 5 万 且 涨跌幅 > 2%）
      const spot = await fetch24h(symbol);
      const minVol = spot.volume / 1440;                 // 平均每分钟成交额
      const prevPrice = cache[symbol].price || spot.price;
      const pct = (spot.price / prevPrice - 1) * 100;
      if (minVol > 50000 && Math.abs(pct) > 2) {
        await sendWeCom(`🔥 现货放量 ${symbol}\n1min 成交额 ≈ $${minVol.toFixed(0)}\n价格波动 ${pct.toFixed(2)}%\n时间 ${now}`);
      }
      cache[symbol].price = spot.price;

    } catch (e) {
      console.error(`处理 ${symbol} 失败`, e.message);
    }
  }
}

// Worker 入口：每分钟触发一次（Cron）
export default {
  async scheduled(event, env, ctx) {
    WECOM_WEBHOOK_URL = env.WECOM_WEBHOOK_URL;
    await monitor();
  }
};
