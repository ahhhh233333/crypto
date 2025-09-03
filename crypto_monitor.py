#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
加密货币监控系统
支持多交易所价格监控和异常检测
"""

import os
import sys
import time
import json
import logging
import requests
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

@dataclass
class CryptoPrice:
    """加密货币价格数据类"""
    symbol: str
    price: float
    change_24h: float
    volume_24h: float
    timestamp: datetime
    
class BinanceAPI:
    """Binance API 客户端"""
    
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.binance.com"
        self.fapi_url = "https://fapi.binance.com"  # 期货API
        self.session = requests.Session()
        
        # 设置请求头
        if self.api_key:
            self.session.headers.update({
                'X-MBX-APIKEY': self.api_key
            })
        
        # 备选交易对列表（热门交易对）
        self.fallback_symbols = [
            'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'XRPUSDT',
            'SOLUSDT', 'DOTUSDT', 'DOGEUSDT', 'AVAXUSDT', 'MATICUSDT'
        ]
        
        logger.info("Binance交易所初始化成功")
    
    def get_exchange_info(self) -> Optional[Dict]:
        """获取交易所信息（不需要API密钥）"""
        try:
            response = self.session.get(f"{self.base_url}/api/v3/exchangeInfo", timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"获取交易所信息失败: {e}")
            return None
    
    def get_futures_exchange_info(self) -> Optional[Dict]:
        """获取期货交易所信息（不需要API密钥）"""
        try:
            response = self.session.get(f"{self.fapi_url}/fapi/v1/exchangeInfo", timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"获取期货交易所信息失败: {e}")
            return None
    
    def get_trading_pairs(self, include_futures: bool = True) -> List[str]:
        """获取所有交易对列表"""
        symbols = []
        
        try:
            # 获取现货交易对
            spot_info = self.get_exchange_info()
            if spot_info and 'symbols' in spot_info:
                for symbol_info in spot_info['symbols']:
                    if (symbol_info['status'] == 'TRADING' and 
                        symbol_info['quoteAsset'] == 'USDT'):
                        symbols.append(symbol_info['symbol'])
                logger.info(f"获取到 {len(symbols)} 个现货交易对")
            
            # 获取期货交易对
            if include_futures:
                futures_info = self.get_futures_exchange_info()
                if futures_info and 'symbols' in futures_info:
                    futures_symbols = []
                    for symbol_info in futures_info['symbols']:
                        if (symbol_info['status'] == 'TRADING' and 
                            symbol_info['quoteAsset'] == 'USDT' and
                            symbol_info['contractType'] == 'PERPETUAL'):
                            futures_symbols.append(symbol_info['symbol'])
                    logger.info(f"获取到 {len(futures_symbols)} 个期货交易对")
                    symbols.extend(futures_symbols)
            
            if symbols:
                # 按交易量排序，选择前50个
                return symbols[:50]
            else:
                logger.warning("未能获取交易对，使用备选列表")
                return self.fallback_symbols
                
        except Exception as e:
            logger.error(f"获取交易对失败: {e}")
            logger.info(f"使用备选交易对: {len(self.fallback_symbols)} 个")
            return self.fallback_symbols
    
    def get_24hr_ticker(self, symbols: List[str]) -> Dict[str, CryptoPrice]:
        """获取24小时价格统计（不需要API密钥）"""
        prices = {}
        
        try:
            # 获取所有交易对的24小时统计
            response = self.session.get(f"{self.base_url}/api/v3/ticker/24hr", timeout=15)
            response.raise_for_status()
            tickers = response.json()
            
            for ticker in tickers:
                symbol = ticker['symbol']
                if symbol in symbols:
                    try:
                        price = CryptoPrice(
                            symbol=symbol,
                            price=float(ticker['lastPrice']),
                            change_24h=float(ticker['priceChangePercent']),
                            volume_24h=float(ticker['volume']),
                            timestamp=datetime.now()
                        )
                        prices[symbol] = price
                    except (ValueError, KeyError) as e:
                        logger.warning(f"解析 {symbol} 数据失败: {e}")
                        continue
            
            logger.info(f"成功获取 {len(prices)} 个交易对价格")
            return prices
            
        except Exception as e:
            logger.error(f"获取价格数据失败: {e}")
            return {}

class CryptoMonitor:
    """加密货币监控系统"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.binance = BinanceAPI(
            api_key=config.get('binance_api_key'),
            api_secret=config.get('binance_api_secret')
        )
        self.previous_prices = {}
        self.alert_history = {}
        
        logger.info("加密货币监控系统初始化完成")
    
    def get_monitored_symbols(self) -> List[str]:
        """获取要监控的交易对列表"""
        logger.info("正在获取Binance交易对列表...")
        
        symbols = self.binance.get_trading_pairs(include_futures=True)
        max_symbols = self.config.get('max_symbols', 50)
        
        if len(symbols) > max_symbols:
            symbols = symbols[:max_symbols]
        
        logger.info(f"开始监控 {len(symbols)} 个交易对")
        return symbols
    
    def check_price_alerts(self, current_prices: Dict[str, CryptoPrice]) -> List[Dict]:
        """检查价格异常和生成警报"""
        alerts = []
        threshold = self.config.get('price_change_threshold', 5.0)
        
        for symbol, price_data in current_prices.items():
            # 检查24小时价格变化
            if abs(price_data.change_24h) >= threshold:
                alert = {
                    'symbol': symbol,
                    'price': price_data.price,
                    'change_24h': price_data.change_24h,
                    'type': 'price_change',
                    'timestamp': price_data.timestamp
                }
                alerts.append(alert)
            
            # 检查与上次价格的变化
            if symbol in self.previous_prices:
                prev_price = self.previous_prices[symbol].price
                current_price = price_data.price
                price_change = ((current_price - prev_price) / prev_price) * 100
                
                if abs(price_change) >= threshold:
                    alert = {
                        'symbol': symbol,
                        'price': current_price,
                        'previous_price': prev_price,
                        'change': price_change,
                        'type': 'interval_change',
                        'timestamp': price_data.timestamp
                    }
                    alerts.append(alert)
        
        return alerts
    
    def log_alerts(self, alerts: List[Dict]):
        """记录警报信息"""
        for alert in alerts:
            if alert['type'] == 'price_change':
                logger.warning(
                    f"价格异常: {alert['symbol']} - "
                    f"当前价格: ${alert['price']:.4f}, "
                    f"24h变化: {alert['change_24h']:.2f}%"
                )
            elif alert['type'] == 'interval_change':
                logger.warning(
                    f"价格波动: {alert['symbol']} - "
                    f"从 ${alert['previous_price']:.4f} 到 ${alert['price']:.4f}, "
                    f"变化: {alert['change']:.2f}%"
                )
    
    def run_monitoring_cycle(self) -> int:
        """运行一次监控周期"""
        try:
            # 获取要监控的交易对
            symbols = self.get_monitored_symbols()
            
            if not symbols:
                logger.error("没有可监控的交易对")
                return 0
            
            # 获取当前价格
            current_prices = self.binance.get_24hr_ticker(symbols)
            
            if not current_prices:
                logger.error("未能获取价格数据")
                return 0
            
            # 检查价格警报
            alerts = self.check_price_alerts(current_prices)
            
            # 记录警报
            if alerts:
                self.log_alerts(alerts)
            
            # 更新历史价格
            self.previous_prices.update(current_prices)
            
            return len(current_prices)
            
        except Exception as e:
            logger.error(f"监控周期执行失败: {e}")
            return 0
    
    def start_monitoring(self):
        """启动监控系统"""
        logger.info("加密货币监控系统启动")
        logger.info(f"监控间隔: {self.config.get('monitor_interval', 60)} 秒")
        logger.info(f"价格变化阈值: {self.config.get('price_change_threshold', 5.0)}%")
        logger.info(f"最大监控数量: {self.config.get('max_symbols', 50)} 个")
        
        try:
            while True:
                start_time = time.time()
                
                # 运行监控周期
                updated_count = self.run_monitoring_cycle()
                
                # 计算执行时间
                execution_time = time.time() - start_time
                
                logger.info(
                    f"本轮监控完成，成功更新 {updated_count}/{self.config.get('max_symbols', 50)} 个交易对，"
                    f"等待 {self.config.get('monitor_interval', 60)} 秒后继续..."
                )
                
                # 等待下一个监控周期
                time.sleep(self.config.get('monitor_interval', 60))
                
        except KeyboardInterrupt:
            logger.info("收到停止信号，正在关闭监控系统...")
        except Exception as e:
            logger.error(f"监控系统异常: {e}")
            raise

def load_config() -> Dict:
    """加载配置文件"""
    config_file = 'config.json'
    default_config = {
        'binance_api_key': '',
        'binance_api_secret': '',
        'monitor_interval': 60,
        'price_change_threshold': 5.0,
        'max_symbols': 50,
        'log_level': 'INFO'
    }
    
    # 尝试从文件加载配置
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                file_config = json.load(f)
                default_config.update(file_config)
                logger.info(f"已加载配置文件: {config_file}")
        except Exception as e:
            logger.warning(f"加载配置文件失败: {e}，使用默认配置")
    
    # 从环境变量加载配置
    env_config = {
        'binance_api_key': os.getenv('BINANCE_API_KEY', ''),
        'binance_api_secret': os.getenv('BINANCE_API_SECRET', ''),
        'monitor_interval': float(os.getenv('MONITOR_INTERVAL', 60)),
        'price_change_threshold': float(os.getenv('PRICE_CHANGE_THRESHOLD', 5.0)),
        'max_symbols': int(os.getenv('MAX_SYMBOLS', 50))
    }
    
    # 环境变量覆盖文件配置
    for key, value in env_config.items():
        if value:  # 只有非空值才覆盖
            default_config[key] = value
    
    return default_config

def create_default_config():
    """创建默认配置文件"""
    config_file = 'config.json'
    if not os.path.exists(config_file):
        default_config = {
            "binance_api_key": "",
            "binance_api_secret": "",
            "monitor_interval": 60,
            "price_change_threshold": 5.0,
            "max_symbols": 50,
            "log_level": "INFO"
        }
        
        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=2, ensure_ascii=False)
            logger.info(f"已创建默认配置文件: {config_file}")
        except Exception as e:
            logger.error(f"创建配置文件失败: {e}")

def main():
    """主函数"""
    try:
        # 创建默认配置文件
        create_default_config()
        
        # 加载配置
        config = load_config()
        
        # 设置日志级别
        log_level = getattr(logging, config.get('log_level', 'INFO').upper())
        logging.getLogger().setLevel(log_level)
        
        # 创建并启动监控系统
        monitor = CryptoMonitor(config)
        monitor.start_monitoring()
        
        return 0
        
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
        return 0
    except Exception as e:
        logger.error(f"程序执行失败: {e}")
        return 1

if __name__ == "__main__":
    exit(main())
