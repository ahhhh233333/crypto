#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
加密货币现货期货监控程序
监控 Binance 期货持仓变化和主要交易所现货交易量
当满足条件时发送警报到企业微信
"""

import ccxt
import time
import logging
import os
import requests
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('crypto_monitor.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class CryptoMonitor:
    def __init__(self):
        """初始化监控器"""
        self.wecom_webhook = os.getenv('WECOM_WEBHOOK_URL')
        if not self.wecom_webhook:
            logger.error("未找到 WECOM_WEBHOOK_URL 环境变量")
            raise ValueError("需要设置企业微信 webhook URL")
        
        # 初始化交易所
        self.exchanges = {
            'binance': ccxt.binance({'timeout': 30000}),
            'bybit': ccxt.bybit({'timeout': 30000}),
            'okx': ccxt.okx({'timeout': 30000}),
            'bitget': ccxt.bitget({'timeout': 30000}),
            'mexc': ccxt.mexc({'timeout': 30000}),
            'gate': ccxt.gate({'timeout': 30000}),
            'kucoin': ccxt.kucoin({'timeout': 30000})
        }
        
        # Binance 期货交易所
        self.binance_futures = ccxt.binance({
            'options': {'defaultType': 'future'},
            'timeout': 30000
        })
        
        # 数据存储
        self.price_history = {}  # 存储价格历史
        self.oi_history = {}     # 存储持仓历史
        self.futures_symbols = []  # 期货交易对列表
        
    def get_futures_symbols(self) -> List[str]:
        """获取 Binance 期货 USDT 交易对列表"""
        try:
            markets = self.binance_futures.load_markets()
            symbols = []
            for symbol, market in markets.items():
                if (market.get('type') == 'swap' and 
                    symbol.endswith('/USDT') and 
                    market.get('active', True)):
                    symbols.append(symbol)
            
            logger.info(f"找到 {len(symbols)} 个期货交易对")
            return symbols
        except Exception as e:
            logger.error(f"获取期货交易对失败: {e}")
            return []
    
    def find_max_volume_exchange(self, symbol: str) -> Optional[str]:
        """找到指定代币24小时成交量最大的交易所"""
        max_volume = 0
        max_exchange = None
        
        for exchange_name, exchange in self.exchanges.items():
            try:
                if symbol in exchange.markets:
                    ticker = exchange.fetch_ticker(symbol)
                    quote_volume = ticker.get('quoteVolume', 0)
                    if quote_volume and quote_volume > max_volume:
                        max_volume = quote_volume
                        max_exchange = exchange_name
            except Exception as e:
                logger.debug(f"{exchange_name} 获取 {symbol} ticker 失败: {e}")
                continue
        
        return max_exchange
    
    def get_spot_data(self, symbol: str, exchange_name: str) -> Optional[Dict]:
        """获取现货数据"""
        try:
            exchange = self.exchanges[exchange_name]
            ticker = exchange.fetch_ticker(symbol)
            
            # 获取1分钟K线数据计算成交额
            ohlcv = exchange.fetch_ohlcv(symbol, '1m', limit=2)
            if len(ohlcv) >= 2:
                current_candle = ohlcv[-1]  # 最新的1分钟K线
                volume_1m = current_candle[5]  # 成交量
                price = ticker['last']
                volume_usd_1m = volume_1m * price  # 1分钟成交额(美元)
                
                return {
                    'price': price,
                    'volume_1m_usd': volume_usd_1m,
                    'timestamp': datetime.now()
                }
        except Exception as e:
            logger.debug(f"获取 {exchange_name} {symbol} 现货数据失败: {e}")
        
        return None
    
    def get_futures_oi(self, symbol: str) -> Optional[Dict]:
        """获取期货持仓数据"""
        try:
            oi_data = self.binance_futures.fetch_open_interest(symbol)
            return {
                'open_interest': oi_data.get('openInterestAmount', 0),
                'timestamp': datetime.now()
            }
        except Exception as e:
            logger.debug(f"获取 {symbol} 期货持仓失败: {e}")
        
        return None
    
    def check_spot_alert(self, symbol: str, current_data: Dict) -> bool:
        """检查现货警报条件"""
        # 1分钟成交额超过5万美元
        if current_data['volume_1m_usd'] < 50000:
            return False
        
        # 检查价格波动
        if symbol not in self.price_history:
            return False
        
        # 获取1分钟前的价格
        one_min_ago = datetime.now() - timedelta(minutes=1)
        price_1m_ago = None
        
        for timestamp, data in self.price_history[symbol]:
            if abs((timestamp - one_min_ago).total_seconds()) < 30:  # 30秒容差
                price_1m_ago = data['price']
                break
        
        if price_1m_ago is None:
            return False
        
        # 计算价格波动
        price_change = (current_data['price'] - price_1m_ago) / price_1m_ago * 100
        
        # 价格波动超过2%
        if abs(price_change) > 2.0:
            current_data['price_change'] = price_change
            return True
        
        return False
    
    def check_futures_alert(self, symbol: str, current_oi: Dict) -> bool:
        """检查期货警报条件"""
        if symbol not in self.oi_history:
            return False
        
        # 获取5分钟前的持仓
        five_min_ago = datetime.now() - timedelta(minutes=5)
        oi_5m_ago = None
        
        for timestamp, data in self.oi_history[symbol]:
            if abs((timestamp - five_min_ago).total_seconds()) < 30:  # 30秒容差
                oi_5m_ago = data['open_interest']
                break
        
        if oi_5m_ago is None or oi_5m_ago == 0:
            return False
        
        # 计算持仓变化
        oi_change = (current_oi['open_interest'] - oi_5m_ago) / oi_5m_ago * 100
        
        # 持仓增加超过5%
        if oi_change > 5.0:
            current_oi['oi_change'] = oi_change
            return True
        
        return False
    
    def send_wecom_alert(self, message: str) -> bool:
        """发送企业微信警报"""
        try:
            data = {
                "msgtype": "text",
                "text": {
                    "content": message
                }
            }
            
            response = requests.post(
                self.wecom_webhook,
                json=data,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('errcode') == 0:
                    logger.info("企业微信消息发送成功")
                    return True
                else:
                    logger.error(f"企业微信消息发送失败: {result}")
            else:
                logger.error(f"企业微信 HTTP 错误: {response.status_code}")
            
        except Exception as e:
            logger.error(f"发送企业微信消息异常: {e}")
        
        return False
    
    def update_history(self, symbol: str, spot_data: Dict, oi_data: Dict):
        """更新历史数据，保留最近10分钟"""
        current_time = datetime.now()
        cutoff_time = current_time - timedelta(minutes=10)
        
        # 更新价格历史
        if symbol not in self.price_history:
            self.price_history[symbol] = []
        
        self.price_history[symbol].append((current_time, spot_data))
        # 清理旧数据
        self.price_history[symbol] = [
            (ts, data) for ts, data in self.price_history[symbol]
            if ts > cutoff_time
        ]
        
        # 更新持仓历史
        if symbol not in self.oi_history:
            self.oi_history[symbol] = []
        
        self.oi_history[symbol].append((current_time, oi_data))
        # 清理旧数据
        self.oi_history[symbol] = [
            (ts, data) for ts, data in self.oi_history[symbol]
            if ts > cutoff_time
        ]
    
    def monitor_symbol(self, symbol: str):
        """监控单个交易对"""
        try:
            # 找到成交量最大的现货交易所
            max_exchange = self.find_max_volume_exchange(symbol)
            if not max_exchange:
                return
            
            # 获取现货数据
            spot_data = self.get_spot_data(symbol, max_exchange)
            if not spot_data:
                return
            
            # 获取期货持仓数据
            oi_data = self.get_futures_oi(symbol)
            if not oi_data:
                return
            
            # 更新历史数据
            self.update_history(symbol, spot_data, oi_data)
            
            # 检查现货警报
            if self.check_spot_alert(symbol, spot_data):
                message = f"""🚨 现货放量警报
代币: {symbol}
交易所: {max_exchange.upper()}
1分钟成交额: ${spot_data['volume_1m_usd']:,.0f}
价格波动: {spot_data['price_change']:.2f}%
当前价格: ${spot_data['price']:.6f}
时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                
                logger.info(f"现货警报触发: {symbol}")
                self.send_wecom_alert(message)
            
            # 检查期货警报
            if self.check_futures_alert(symbol, oi_data):
                message = f"""📈 期货加仓警报
代币: {symbol}
持仓增加: {oi_data['oi_change']:.2f}%
当前持仓: {oi_data['open_interest']:,.0f} USDT
时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                
                logger.info(f"期货警报触发: {symbol}")
                self.send_wecom_alert(message)
                
        except Exception as e:
            logger.error(f"监控 {symbol} 时发生错误: {e}")
    
    def run(self):
        """主运行循环"""
        logger.info("加密货币监控程序启动")
        
        # 获取期货交易对列表
        self.futures_symbols = self.get_futures_symbols()
        if not self.futures_symbols:
            logger.error("未找到有效的期货交易对，程序退出")
            return
        
        logger.info(f"开始监控 {len(self.futures_symbols)} 个交易对")
        
        while True:
            try:
                start_time = time.time()
                
                for symbol in self.futures_symbols:
                    self.monitor_symbol(symbol)
                    # 添加小延迟避免API限频
                    time.sleep(0.1)
                
                # 计算处理时间
                process_time = time.time() - start_time
                logger.info(f"本轮监控完成，耗时 {process_time:.2f}秒")
                
                # 等待下一轮（60秒间隔）
                sleep_time = max(0, 60 - process_time)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                
            except KeyboardInterrupt:
                logger.info("程序被用户中断")
                break
            except Exception as e:
                logger.error(f"主循环发生错误: {e}")
                time.sleep(60)  # 发生错误时等待1分钟再继续

if __name__ == "__main__":
    monitor = CryptoMonitor()
    monitor.run()
