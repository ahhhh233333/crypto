#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
加密货币交易监控系统
实时监控现货和期货交易数据，提供智能交易提醒

作者: AI Assistant
版本: 1.0.0
"""

import ccxt
import time
import logging
import os
import requests
import json
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# 配置日志
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
    """加密货币监控主类"""
    
    def __init__(self):
        """初始化监控系统"""
        # 获取环境变量
        self.wecom_webhook_url = os.getenv('WECOM_WEBHOOK_URL')
        self.telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        
        # 验证必要的环境变量
        if not self.wecom_webhook_url:
            logger.error("WECOM_WEBHOOK_URL 环境变量未设置")
            raise ValueError("请设置 WECOM_WEBHOOK_URL 环境变量")
        
        # 初始化交易所实例
        self.exchanges = self._initialize_exchanges()
        self.binance_futures = ccxt.binance({'options': {'defaultType': 'future'}})
        
        # 数据存储
        self.historical_data = defaultdict(dict)  # 存储历史数据
        self.futures_symbols = []  # Binance期货代币列表
        
        # 监控参数
        self.SPOT_VOLUME_THRESHOLD = 50000  # 现货成交额阈值（美元）
        self.SPOT_PRICE_CHANGE_THRESHOLD = 2.0  # 现货价格波动阈值（%）
        self.FUTURES_OI_CHANGE_THRESHOLD = 5.0  # 期货持仓变化阈值（%）
        
        logger.info("加密货币监控系统初始化完成")
    
    def _initialize_exchanges(self) -> Dict[str, ccxt.Exchange]:
        """初始化所有交易所实例"""
        exchanges = {
            'binance': ccxt.binance(),
            'bybit': ccxt.bybit(),
            'okx': ccxt.okx(),
            'bitget': ccxt.bitget(),
            'mexc': ccxt.mexc(),
            'gate': ccxt.gate(),
            'kucoin': ccxt.kucoin()
        }
        
        # 设置交易所参数
        for name, exchange in exchanges.items():
            exchange.enableRateLimit = True
            exchange.timeout = 30000  # 30秒超时
            
        logger.info(f"已初始化 {len(exchanges)} 个交易所")
        return exchanges
    
    def get_binance_futures_symbols(self) -> List[str]:
        """获取Binance期货USDT交易对列表"""
        try:
            markets = self.binance_futures.load_markets()
            futures_symbols = [
                symbol for symbol, market in markets.items()
                if market['type'] == 'future' and symbol.endswith('/USDT')
            ]
            logger.info(f"获取到 {len(futures_symbols)} 个Binance期货USDT交易对")
            return futures_symbols
        except Exception as e:
            logger.error(f"获取Binance期货交易对失败: {e}")
            return []
    
    def find_max_volume_exchange(self, symbol: str) -> Optional[Tuple[str, float]]:
        """找出指定代币当日成交额最大的现货交易所"""
        max_volume = 0
        max_exchange = None
        
        for name, exchange in self.exchanges.items():
            try:
                # 检查交易所是否支持该交易对
                markets = exchange.load_markets()
                if symbol not in markets:
                    continue
                
                # 获取24小时交易数据
                ticker = exchange.fetch_ticker(symbol)
                quote_volume = ticker.get('quoteVolume', 0) or 0
                
                if quote_volume > max_volume:
                    max_volume = quote_volume
                    max_exchange = name
                    
                time.sleep(0.1)  # 避免API限频
                
            except Exception as e:
                logger.warning(f"获取 {name} 交易所 {symbol} 数据失败: {e}")
                continue
        
        if max_exchange:
            logger.debug(f"{symbol} 最大成交额交易所: {max_exchange} (${max_volume:,.2f})")
            return max_exchange, max_volume
        
        return None
    
    def get_spot_data(self, exchange_name: str, symbol: str) -> Optional[Dict]:
        """获取现货交易数据"""
        try:
            exchange = self.exchanges[exchange_name]
            
            # 获取最新ticker数据
            ticker = exchange.fetch_ticker(symbol)
            
            # 获取1分钟K线数据计算成交额
            ohlcv = exchange.fetch_ohlcv(symbol, '1m', limit=2)
            if len(ohlcv) >= 2:
                current_candle = ohlcv[-1]
                prev_candle = ohlcv[-2]
                
                current_price = current_candle[4]  # 收盘价
                prev_price = prev_candle[4]
                volume_1m = current_candle[5]  # 成交量
                
                # 计算1分钟成交额（美元）
                volume_usd = volume_1m * current_price
                
                # 计算价格波动百分比
                price_change_pct = ((current_price - prev_price) / prev_price) * 100
                
                return {
                    'symbol': symbol,
                    'exchange': exchange_name,
                    'current_price': current_price,
                    'prev_price': prev_price,
                    'volume_1m_usd': volume_usd,
                    'price_change_pct': price_change_pct,
                    'timestamp': datetime.now()
                }
            
        except Exception as e:
            logger.error(f"获取 {exchange_name} {symbol} 现货数据失败: {e}")
        
        return None
    
    def get_futures_open_interest(self, symbol: str) -> Optional[Dict]:
        """获取期货持仓量数据"""
        try:
            # 获取当前持仓量
            oi_data = self.binance_futures.fetch_open_interest(symbol)
            current_oi = oi_data['openInterestAmount']
            
            # 获取历史持仓量（5分钟前）
            key = f"{symbol}_oi"
            current_time = datetime.now()
            
            # 存储当前数据
            if key not in self.historical_data:
                self.historical_data[key] = []
            
            self.historical_data[key].append({
                'timestamp': current_time,
                'open_interest': current_oi
            })
            
            # 清理超过10分钟的历史数据
            cutoff_time = current_time - timedelta(minutes=10)
            self.historical_data[key] = [
                data for data in self.historical_data[key]
                if data['timestamp'] > cutoff_time
            ]
            
            # 计算5分钟前的持仓量变化
            five_min_ago = current_time - timedelta(minutes=5)
            prev_oi = None
            
            for data in self.historical_data[key]:
                if data['timestamp'] <= five_min_ago:
                    prev_oi = data['open_interest']
                else:
                    break
            
            oi_change_pct = 0
            if prev_oi and prev_oi > 0:
                oi_change_pct = ((current_oi - prev_oi) / prev_oi) * 100
            
            return {
                'symbol': symbol,
                'current_oi': current_oi,
                'prev_oi': prev_oi,
                'oi_change_pct': oi_change_pct,
                'timestamp': current_time
            }
            
        except Exception as e:
            logger.error(f"获取 {symbol} 期货持仓数据失败: {e}")
        
        return None
    
    def analyze_trading_signal(self, spot_data: Dict, futures_data: Dict) -> Optional[Dict]:
        """分析交易信号并生成建议"""
        signals = []
        recommendations = []
        
        # 现货放量信号检查
        if (spot_data['volume_1m_usd'] > self.SPOT_VOLUME_THRESHOLD and 
            abs(spot_data['price_change_pct']) > self.SPOT_PRICE_CHANGE_THRESHOLD):
            
            signal_type = "现货放量上涨" if spot_data['price_change_pct'] > 0 else "现货放量下跌"
            signals.append(signal_type)
            
            # 生成交易建议
            if spot_data['price_change_pct'] > 0:
                if futures_data and futures_data['oi_change_pct'] > 0:
                    recommendations.append("💰 强烈买入信号 - 量价齐升，期货加仓")
                else:
                    recommendations.append("📈 买入信号 - 现货放量上涨")
            else:
                if futures_data and futures_data['oi_change_pct'] < -3:
                    recommendations.append("🛒 抄底信号 - 放量下跌，持仓减少")
                else:
                    recommendations.append("⚠️ 观望信号 - 放量下跌，谨慎操作")
        
        # 期货持仓信号检查
        if futures_data and futures_data['oi_change_pct'] > self.FUTURES_OI_CHANGE_THRESHOLD:
            signals.append("期货加仓")
            
            # 结合现货价格生成建议
            if spot_data['price_change_pct'] > 1:
                recommendations.append("🚀 追涨信号 - 价格上涨，持仓增加")
            elif spot_data['price_change_pct'] < -1:
                recommendations.append("⚡ 反弹信号 - 价格下跌但资金加仓")
            else:
                recommendations.append("👀 关注信号 - 持仓增加，等待价格突破")
        
        # 逃顶信号检查
        if (spot_data['price_change_pct'] > 3 and 
            futures_data and futures_data['oi_change_pct'] > 10):
            recommendations.append("🔴 逃顶信号 - 价格暴涨，持仓异常增加")
        
        if signals:
            return {
                'signals': signals,
                'recommendations': recommendations,
                'spot_data': spot_data,
                'futures_data': futures_data
            }
        
        return None
    
    def format_alert_message(self, analysis: Dict) -> str:
        """格式化警报消息"""
        spot_data = analysis['spot_data']
        futures_data = analysis['futures_data']
        signals = analysis['signals']
        recommendations = analysis['recommendations']
        
        message_parts = [
            "🚨 交易警报 🚨",
            f"代币: {spot_data['symbol']}",
            f"交易所: {spot_data['exchange']}",
            f"信号: {' + '.join(signals)}",
            "",
            "📊 现货数据:",
            f"• 1分钟成交额: ${spot_data['volume_1m_usd']:,.0f}",
            f"• 价格波动: {spot_data['price_change_pct']:+.2f}%",
            f"• 当前价格: ${spot_data['current_price']:.6f}"
        ]
        
        if futures_data:
            message_parts.extend([
                "",
                "📈 期货数据:",
                f"• 持仓变化: {futures_data['oi_change_pct']:+.2f}%",
                f"• 当前持仓: {futures_data['current_oi']:,.0f}"
            ])
        
        if recommendations:
            message_parts.extend([
                "",
                "💡 交易建议:",
                *[f"• {rec}" for rec in recommendations]
            ])
        
        message_parts.extend([
            "",
            f"⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ])
        
        return "\n".join(message_parts)
    
    def send_wecom_alert(self, message: str) -> bool:
        """发送企业微信警报"""
        try:
            payload = {
                "msgtype": "text",
                "text": {
                    "content": message
                }
            }
            
            response = requests.post(
                self.wecom_webhook_url,
                json=payload,
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
                logger.error(f"企业微信API请求失败: {response.status_code}")
                
        except Exception as e:
            logger.error(f"发送企业微信消息异常: {e}")
        
        return False
    
    def send_telegram_alert(self, message: str) -> bool:
        """发送Telegram警报（可选）"""
        if not self.telegram_bot_token or not self.telegram_chat_id:
            return True  # 如果未配置则跳过
        
        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            payload = {
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                logger.info("Telegram消息发送成功")
                return True
            else:
                logger.error(f"Telegram消息发送失败: {response.status_code}")
                
        except Exception as e:
            logger.error(f"发送Telegram消息异常: {e}")
        
        return False
    
    def send_alert(self, message: str) -> bool:
        """发送警报到所有配置的平台"""
        success = True
        
        # 发送到企业微信
        if not self.send_wecom_alert(message):
            success = False
        
        # 发送到Telegram（如果配置了）
        if not self.send_telegram_alert(message):
            success = False
        
        return success
    
    def monitor_symbol(self, symbol: str) -> None:
        """监控单个交易对"""
        try:
            # 找出最大成交额的现货交易所
            max_exchange_info = self.find_max_volume_exchange(symbol)
            if not max_exchange_info:
                logger.warning(f"未找到 {symbol} 的有效现货交易所")
                return
            
            exchange_name, _ = max_exchange_info
            
            # 获取现货数据
            spot_data = self.get_spot_data(exchange_name, symbol)
            if not spot_data:
                logger.warning(f"获取 {symbol} 现货数据失败")
                return
            
            # 获取期货持仓数据
            futures_data = self.get_futures_open_interest(symbol)
            
            # 分析交易信号
            analysis = self.analyze_trading_signal(spot_data, futures_data)
            
            if analysis:
                # 格式化并发送警报
                message = self.format_alert_message(analysis)
                logger.info(f"检测到交易信号: {symbol}")
                
                if self.send_alert(message):
                    logger.info(f"警报发送成功: {symbol}")
                else:
                    logger.error(f"警报发送失败: {symbol}")
            
        except Exception as e:
            logger.error(f"监控 {symbol} 时发生异常: {e}")
    
    def run_monitoring_cycle(self) -> None:
        """执行一次完整的监控周期"""
        logger.info("开始监控周期")
        
        # 获取Binance期货交易对列表
        if not self.futures_symbols:
            self.futures_symbols = self.get_binance_futures_symbols()
            if not self.futures_symbols:
                logger.error("无法获取期货交易对列表，跳过本次监控")
                return
        
        # 监控每个交易对
        for symbol in self.futures_symbols:
            try:
                self.monitor_symbol(symbol)
                time.sleep(1)  # 避免API限频
            except Exception as e:
                logger.error(f"监控 {symbol} 时发生异常: {e}")
                continue
        
        logger.info(f"监控周期完成，共监控 {len(self.futures_symbols)} 个交易对")
    
    def run(self) -> None:
        """运行监控系统主循环"""
        logger.info("🚀 加密货币监控系统启动")
        logger.info(f"监控参数: 现货成交额阈值=${self.SPOT_VOLUME_THRESHOLD:,}, 价格波动阈值={self.SPOT_PRICE_CHANGE_THRESHOLD}%, 持仓变化阈值={self.FUTURES_OI_CHANGE_THRESHOLD}%")
        
        while True:
            try:
                start_time = time.time()
                
                # 执行监控周期
                self.run_monitoring_cycle()
                
                # 计算执行时间
                execution_time = time.time() - start_time
                logger.info(f"监控周期执行时间: {execution_time:.2f}秒")
                
                # 等待下一个周期（1分钟）
                sleep_time = max(60 - execution_time, 10)  # 至少等待10秒
                logger.info(f"等待 {sleep_time:.0f} 秒后开始下一个监控周期")
                time.sleep(sleep_time)
                
            except KeyboardInterrupt:
                logger.info("收到中断信号，正在停止监控系统...")
                break
            except Exception as e:
                logger.error(f"监控系统发生异常: {e}")
                logger.info("等待60秒后重试...")
                time.sleep(60)

def main():
    """主函数"""
    try:
        monitor = CryptoMonitor()
        monitor.run()
    except Exception as e:
        logger.error(f"程序启动失败: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())