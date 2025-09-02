#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
加密货币监控系统
监控主要加密货币的价格变化，并记录显著波动
"""

import ccxt
import time
import logging
import signal
import sys
import os
from datetime import datetime
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
import json

# 加载环境变量
load_dotenv()

class CryptoMonitor:
    """加密货币监控类"""
    
    def __init__(self):
        """初始化监控系统"""
        # 设置日志
        self._setup_logging()
        
        # 初始化配置
        self.config = self._load_config()
        
        # 初始化交易所
        self.exchanges = self._initialize_exchanges()
        
        # 监控状态
        self.running = True
        self.last_prices = {}
        
        # 注册信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger.info("加密货币监控系统初始化完成")
    
    def _setup_logging(self):
        """设置日志系统"""
        log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        log_file = os.getenv('LOG_FILE', 'crypto_monitor.log')
        
        # 创建日志格式
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # 设置根日志器
        self.logger = logging.getLogger('CryptoMonitor')
        self.logger.setLevel(getattr(logging, log_level, logging.INFO))
        
        # 清除现有处理器
        self.logger.handlers.clear()
        
        # 文件处理器
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
        
        # 控制台处理器
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        
        # 防止日志重复
        self.logger.propagate = False
    
    def _load_config(self) -> Dict[str, Any]:
        """加载配置"""
        return {
            'monitor_interval': float(os.getenv('MONITOR_INTERVAL', '60')),
            'price_change_threshold': float(os.getenv('PRICE_CHANGE_THRESHOLD', '5.0')),
            'max_symbols': int(os.getenv('MAX_SYMBOLS', '50')),
            'binance_api_key': os.getenv('BINANCE_API_KEY', ''),
            'binance_secret': os.getenv('BINANCE_SECRET', ''),
        }
    
    def _initialize_exchanges(self) -> Dict[str, ccxt.Exchange]:
        """初始化交易所连接"""
        exchanges = {}
        
        try:
            # 初始化Binance交易所
            binance_config = {
                'apiKey': self.config['binance_api_key'],
                'secret': self.config['binance_secret'],
                'sandbox': False,
                'enableRateLimit': True,
                'timeout': 30000,
                'options': {
                    'adjustForTimeDifference': True,
                }
            }
            
            # 如果没有API密钥，移除认证信息
            if not self.config['binance_api_key']:
                binance_config.pop('apiKey', None)
                binance_config.pop('secret', None)
            
            binance = ccxt.binance(binance_config)
            exchanges['binance'] = binance
            
            self.logger.info("Binance交易所初始化成功")
            
        except Exception as e:
            self.logger.error(f"初始化Binance交易所失败: {e}")
            
        return exchanges
    
    def get_trading_symbols(self, exchange_name: str = 'binance') -> List[str]:
        """获取交易对列表"""
        if exchange_name not in self.exchanges:
            self.logger.error(f"交易所 {exchange_name} 未初始化")
            return []
        
        exchange = self.exchanges[exchange_name]
        
        try:
            self.logger.info(f"正在获取{exchange_name.title()}交易对列表...")
            
            # 加载市场数据
            markets = exchange.load_markets()
            
            # 获取所有USDT交易对
            usdt_symbols = []
            for symbol, market in markets.items():
                # 过滤条件：活跃的USDT交易对
                if (market.get('active', True) and 
                    symbol.endswith('/USDT') and
                    market.get('type') in ['spot', 'future', None]):
                    usdt_symbols.append(symbol)
            
            # 如果仍然没有找到交易对，使用备选方案
            if not usdt_symbols:
                self.logger.warning("未找到USDT交易对，使用备选交易对列表")
                usdt_symbols = [
                    'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'ADA/USDT', 'XRP/USDT',
                    'SOL/USDT', 'DOT/USDT', 'DOGE/USDT', 'AVAX/USDT', 'MATIC/USDT',
                    'LINK/USDT', 'UNI/USDT', 'LTC/USDT', 'BCH/USDT', 'ATOM/USDT'
                ]
            
            # 按交易量排序并限制数量
            if len(usdt_symbols) > self.config['max_symbols']:
                try:
                    # 获取24小时统计数据
                    tickers = exchange.fetch_tickers(usdt_symbols[:100])
                    
                    # 按交易量排序
                    sorted_symbols = sorted(
                        tickers.keys(),
                        key=lambda x: float(tickers[x].get('quoteVolume') or 0),
                        reverse=True
                    )[:self.config['max_symbols']]
                    
                    usdt_symbols = sorted_symbols
                    self.logger.info(f"按交易量排序，选择前{self.config['max_symbols']}个交易对")
                    
                except Exception as e:
                    self.logger.warning(f"无法获取交易量数据进行排序: {e}")
                    usdt_symbols = usdt_symbols[:self.config['max_symbols']]
            
            self.logger.info(f"找到 {len(usdt_symbols)} 个交易对")
            
            # 显示前10个交易对作为示例
            if usdt_symbols:
                sample_symbols = usdt_symbols[:10]
                self.logger.info(f"示例交易对: {', '.join(sample_symbols)}")
            
            return usdt_symbols
            
        except Exception as e:
            self.logger.error(f"获取交易对失败: {e}")
            # 返回备选交易对
            fallback_symbols = [
                'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'ADA/USDT', 'XRP/USDT',
                'SOL/USDT', 'DOT/USDT', 'DOGE/USDT', 'AVAX/USDT', 'MATIC/USDT'
            ]
            self.logger.info(f"使用备选交易对: {len(fallback_symbols)} 个")
            return fallback_symbols
    
    def fetch_ticker_data(self, symbol: str, exchange_name: str = 'binance') -> Optional[Dict[str, Any]]:
        """获取单个交易对的行情数据"""
        if exchange_name not in self.exchanges:
            return None
        
        exchange = self.exchanges[exchange_name]
        
        try:
            ticker = exchange.fetch_ticker(symbol)
            
            return {
                'symbol': symbol,
                'price': float(ticker.get('last', 0)),
                'change': float(ticker.get('percentage', 0)),
                'volume': float(ticker.get('quoteVolume', 0)),
                'high': float(ticker.get('high', 0)),
                'low': float(ticker.get('low', 0)),
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            self.logger.debug(f"获取 {symbol} 行情数据失败: {e}")
            return None
    
    def analyze_price_change(self, symbol: str, current_data: Dict[str, Any]) -> Dict[str, Any]:
        """分析价格变化"""
        analysis = {
            'symbol': symbol,
            'current_price': current_data['price'],
            'change_24h': current_data['change'],
            'is_significant': False,
            'trend': 'stable'
        }
        
        # 检查是否为显著变化
        if abs(current_data['change']) >= self.config['price_change_threshold']:
            analysis['is_significant'] = True
            analysis['trend'] = 'up' if current_data['change'] > 0 else 'down'
        
        # 与上次价格比较（如果有的话）
        if symbol in self.last_prices:
            last_price = self.last_prices[symbol]
            price_diff = ((current_data['price'] - last_price) / last_price) * 100
            analysis['price_diff_from_last'] = price_diff
        
        # 更新最后价格
        self.last_prices[symbol] = current_data['price']
        
        return analysis
    
    def log_price_update(self, analysis: Dict[str, Any]):
        """记录价格更新"""
        symbol = analysis['symbol']
        price = analysis['current_price']
        change_24h = analysis['change_24h']
        
        # 格式化价格显示
        if price >= 1:
            price_str = f"${price:.4f}"
        else:
            price_str = f"${price:.8f}"
        
        # 根据变化幅度选择日志级别和图标
        if analysis['is_significant']:
            if analysis['trend'] == 'up':
                icon = "🚀"
                level = logging.INFO
            else:
                icon = "📉"
                level = logging.INFO
        else:
            icon = "📊"
            level = logging.DEBUG
        
        # 构建日志消息
        message = f"{icon} {symbol}: {price_str} ({change_24h:+.2f}%)"
        
        # 添加额外信息
        if 'price_diff_from_last' in analysis:
            diff = analysis['price_diff_from_last']
            if abs(diff) > 0.1:  # 只显示显著的短期变化
                message += f" [短期: {diff:+.2f}%]"
        
        self.logger.log(level, message)
    
    def monitor_prices(self, symbols: List[str]):
        """监控价格变化"""
        if not symbols:
            self.logger.error("没有交易对需要监控")
            return
        
        self.logger.info(f"开始监控 {len(symbols)} 个交易对")
        
        while self.running:
            try:
                successful_updates = 0
                
                for symbol in symbols:
                    if not self.running:
                        break
                    
                    # 获取行情数据
                    ticker_data = self.fetch_ticker_data(symbol)
                    
                    if ticker_data:
                        # 分析价格变化
                        analysis = self.analyze_price_change(symbol, ticker_data)
                        
                        # 记录价格更新
                        self.log_price_update(analysis)
                        
                        successful_updates += 1
                    
                    # 短暂延迟避免请求过于频繁
                    time.sleep(0.1)
                
                if self.running:
                    self.logger.info(
                        f"本轮监控完成，成功更新 {successful_updates}/{len(symbols)} 个交易对，"
                        f"等待 {self.config['monitor_interval']} 秒后继续..."
                    )
                    time.sleep(self.config['monitor_interval'])
                    
            except KeyboardInterrupt:
                self.logger.info("收到键盘中断信号")
                break
            except Exception as e:
                self.logger.error(f"监控过程中发生错误: {e}")
                time.sleep(10)  # 错误后等待更长时间
    
    def _signal_handler(self, signum, frame):
        """信号处理器"""
        self.logger.info("收到中断信号，程序退出")
        self.running = False
        sys.exit(0)
    
    def start(self):
        """启动监控系统"""
        self.logger.info("加密货币监控系统启动")
        
        # 显示配置信息
        self.logger.info(f"监控间隔: {self.config['monitor_interval']} 秒")
        self.logger.info(f"价格变化阈值: {self.config['price_change_threshold']}%")
        self.logger.info(f"最大监控数量: {self.config['max_symbols']} 个")
        
        # 获取交易对列表
        symbols = self.get_trading_symbols()
        
        if not symbols:
            self.logger.error("无法获取任何交易对，程序退出")
            return 1
        
        # 开始监控
        try:
            self.monitor_prices(symbols)
        except Exception as e:
            self.logger.error(f"监控系统发生致命错误: {e}")
            return 1
        
        return 0

def main():
    """主函数"""
    try:
        monitor = CryptoMonitor()
        return monitor.start()
    except Exception as e:
        print(f"程序启动失败: {e}")
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
