#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
加密货币监控系统 - 增强版
支持详细的价格监控、推送条件检查和状态输出
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
from enum import Enum

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class AlertType(Enum):
    """警报类型枚举"""
    PRICE_SPIKE = "price_spike"  # 价格飙升
    PRICE_DROP = "price_drop"   # 价格下跌
    VOLUME_SURGE = "volume_surge" # 交易量激增
    INTERVAL_CHANGE = "interval_change" # 间隔变化

class PushStatus(Enum):
    """推送状态枚举"""
    SUCCESS = "success"         # 推送成功
    FAILED = "failed"           # 推送失败
    SKIPPED = "skipped"         # 跳过推送
    PENDING = "pending"         # 等待推送

@dataclass
class CryptoPrice:
    """加密货币价格数据类"""
    symbol: str
    price: float
    change_24h: float
    volume_24h: float
    timestamp: datetime
    
@dataclass
class AlertInfo:
    """警报信息类"""
    symbol: str
    alert_type: AlertType
    current_price: float
    previous_price: Optional[float]
    change_percent: float
    volume_24h: float
    timestamp: datetime
    push_status: PushStatus
    error_message: Optional[str] = None

class DetailedLogger:
    """详细日志输出类"""
    
    def __init__(self):
        self.console_width = 80
        self.separator = "=" * self.console_width
        self.sub_separator = "-" * self.console_width
    
    def print_header(self, title: str):
        """打印标题头"""
        print(f"\n{self.separator}")
        print(f" {title.center(self.console_width - 2)} ")
        print(f"{self.separator}")
    
    def print_section(self, title: str):
        """打印章节标题"""
        print(f"\n{self.sub_separator}")
        print(f" {title} ")
        print(f"{self.sub_separator}")
    
    def print_price_info(self, symbol: str, price_data: CryptoPrice, meets_criteria: bool):
        """打印价格信息"""
        status_icon = "✅" if meets_criteria else "⚪"
        change_icon = "📈" if price_data.change_24h > 0 else "📉" if price_data.change_24h < 0 else "➡️"
        
        print(f"{status_icon} {symbol:<12} | 价格: ${price_data.price:>12.4f} | "
              f"24h变化: {change_icon} {price_data.change_24h:>6.2f}% | "
              f"交易量: {price_data.volume_24h:>15,.0f}")
    
    def print_alert_details(self, alert: AlertInfo):
        """打印警报详情"""
        alert_icons = {
            AlertType.PRICE_SPIKE: "🚀",
            AlertType.PRICE_DROP: "💥",
            AlertType.VOLUME_SURGE: "📊",
            AlertType.INTERVAL_CHANGE: "⚡"
        }
        
        status_icons = {
            PushStatus.SUCCESS: "✅",
            PushStatus.FAILED: "❌",
            PushStatus.SKIPPED: "⏭️",
            PushStatus.PENDING: "⏳"
        }
        
        icon = alert_icons.get(alert.alert_type, "⚠️")
        status_icon = status_icons.get(alert.push_status, "❓")
        
        print(f"{icon} 【{alert.symbol}】 {alert.alert_type.value.upper()}")
        print(f"   当前价格: ${alert.current_price:.4f}")
        if alert.previous_price:
            print(f"   前次价格: ${alert.previous_price:.4f}")
        print(f"   变化幅度: {alert.change_percent:+.2f}%")
        print(f"   推送状态: {status_icon} {alert.push_status.value.upper()}")
        if alert.error_message:
            print(f"   错误信息: {alert.error_message}")
        print(f"   时间戳: {alert.timestamp.strftime('%H:%M:%S')}")
        print()

class BinanceAPI:
    """Binance API 客户端 - 增强版"""
    
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.binance.com"
        self.fapi_url = "https://fapi.binance.com"
        self.session = requests.Session()
        
        if self.api_key:
            self.session.headers.update({
                'X-MBX-APIKEY': self.api_key
            })
        
        self.fallback_symbols = [
            'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'XRPUSDT',
            'SOLUSDT', 'DOTUSDT', 'DOGEUSDT', 'AVAXUSDT', 'MATICUSDT',
            'LINKUSDT', 'LTCUSDT', 'UNIUSDT', 'ATOMUSDT', 'FILUSDT'
        ]
        
        logger.info("Binance交易所初始化成功")
    
    def get_exchange_info(self) -> Optional[Dict]:
        """获取交易所信息"""
        try:
            response = self.session.get(f"{self.base_url}/api/v3/exchangeInfo", timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"获取交易所信息失败: {e}")
            return None
    
    def get_futures_exchange_info(self) -> Optional[Dict]:
        """获取期货交易所信息"""
        try:
            response = self.session.get(f"{self.fapi_url}/fapi/v1/exchangeInfo", timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"获取期货交易所信息失败: {e}")
            return None
    
    def get_trading_pairs(self, include_futures: bool = True) -> List[str]:
        """获取交易对列表"""
        symbols = []
        
        try:
            # 获取现货交易对
            spot_info = self.get_exchange_info()
            if spot_info and 'symbols' in spot_info:
                spot_symbols = []
                for symbol_info in spot_info['symbols']:
                    if (symbol_info['status'] == 'TRADING' and 
                        symbol_info['quoteAsset'] == 'USDT'):
                        spot_symbols.append(symbol_info['symbol'])
                symbols.extend(spot_symbols[:25])  # 限制现货交易对数量
                logger.info(f"获取到 {len(spot_symbols)} 个现货交易对，选择前 25 个")
            
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
                    symbols.extend(futures_symbols[:25])  # 限制期货交易对数量
                    logger.info(f"获取到 {len(futures_symbols)} 个期货交易对，选择前 25 个")
            
            if symbols:
                return symbols
            else:
                logger.warning("未能获取交易对，使用备选列表")
                return self.fallback_symbols
                
        except Exception as e:
            logger.error(f"获取交易对失败: {e}")
            return self.fallback_symbols
    
    def get_24hr_ticker(self, symbols: List[str]) -> Dict[str, CryptoPrice]:
        """获取24小时价格统计"""
        prices = {}
        
        try:
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
            
            return prices
            
        except Exception as e:
            logger.error(f"获取价格数据失败: {e}")
            return {}

class PushService:
    """推送服务类"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.webhook_url = config.get('webhook_url', '')
        self.enable_push = config.get('enable_push', False)
        self.push_cooldown = config.get('push_cooldown', 300)  # 5分钟冷却
        self.last_push_time = {}
    
    def should_push(self, symbol: str) -> bool:
        """检查是否应该推送"""
        if not self.enable_push:
            return False
        
        current_time = time.time()
        last_time = self.last_push_time.get(symbol, 0)
        
        return (current_time - last_time) >= self.push_cooldown
    
    def send_alert(self, alert: AlertInfo) -> PushStatus:
        """发送警报推送"""
        if not self.should_push(alert.symbol):
            alert.push_status = PushStatus.SKIPPED
            return PushStatus.SKIPPED
        
        try:
            if self.webhook_url:
                # 模拟推送到webhook
                message = {
                    "symbol": alert.symbol,
                    "type": alert.alert_type.value,
                    "price": alert.current_price,
                    "change": alert.change_percent,
                    "timestamp": alert.timestamp.isoformat()
                }
                
                # 这里可以添加实际的HTTP请求
                # response = requests.post(self.webhook_url, json=message, timeout=5)
                # response.raise_for_status()
                
                # 模拟成功
                self.last_push_time[alert.symbol] = time.time()
                alert.push_status = PushStatus.SUCCESS
                return PushStatus.SUCCESS
            else:
                alert.push_status = PushStatus.SKIPPED
                alert.error_message = "未配置推送URL"
                return PushStatus.SKIPPED
                
        except Exception as e:
            alert.push_status = PushStatus.FAILED
            alert.error_message = str(e)
            return PushStatus.FAILED

class EnhancedCryptoMonitor:
    """增强版加密货币监控系统"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.binance = BinanceAPI(
            api_key=config.get('binance_api_key'),
            api_secret=config.get('binance_api_secret')
        )
        self.push_service = PushService(config)
        self.detailed_logger = DetailedLogger()
        self.previous_prices = {}
        self.alert_history = []
        self.cycle_count = 0
        
        logger.info("增强版加密货币监控系统初始化完成")
    
    def get_monitored_symbols(self) -> List[str]:
        """获取监控交易对列表"""
        symbols = self.binance.get_trading_pairs(include_futures=True)
        max_symbols = self.config.get('max_symbols', 30)
        
        if len(symbols) > max_symbols:
            symbols = symbols[:max_symbols]
        
        return symbols
    
    def check_alert_conditions(self, symbol: str, current_price: CryptoPrice) -> List[AlertInfo]:
        """检查警报条件"""
        alerts = []
        threshold = self.config.get('price_change_threshold', 5.0)
        volume_threshold = self.config.get('volume_threshold', 1000000)
        
        # 检查24小时价格变化
        if abs(current_price.change_24h) >= threshold:
            alert_type = AlertType.PRICE_SPIKE if current_price.change_24h > 0 else AlertType.PRICE_DROP
            alert = AlertInfo(
                symbol=symbol,
                alert_type=alert_type,
                current_price=current_price.price,
                previous_price=None,
                change_percent=current_price.change_24h,
                volume_24h=current_price.volume_24h,
                timestamp=current_price.timestamp,
                push_status=PushStatus.PENDING
            )
            alerts.append(alert)
        
        # 检查交易量激增
        if current_price.volume_24h >= volume_threshold:
            alert = AlertInfo(
                symbol=symbol,
                alert_type=AlertType.VOLUME_SURGE,
                current_price=current_price.price,
                previous_price=None,
                change_percent=current_price.change_24h,
                volume_24h=current_price.volume_24h,
                timestamp=current_price.timestamp,
                push_status=PushStatus.PENDING
            )
            alerts.append(alert)
        
        # 检查间隔价格变化
        if symbol in self.previous_prices:
            prev_price = self.previous_prices[symbol].price
            price_change = ((current_price.price - prev_price) / prev_price) * 100
            
            if abs(price_change) >= threshold:
                alert = AlertInfo(
                    symbol=symbol,
                    alert_type=AlertType.INTERVAL_CHANGE,
                    current_price=current_price.price,
                    previous_price=prev_price,
                    change_percent=price_change,
                    volume_24h=current_price.volume_24h,
                    timestamp=current_price.timestamp,
                    push_status=PushStatus.PENDING
                )
                alerts.append(alert)
        
        return alerts
    
    def process_alerts(self, alerts: List[AlertInfo]):
        """处理警报推送"""
        for alert in alerts:
            push_status = self.push_service.send_alert(alert)
            alert.push_status = push_status
            self.alert_history.append(alert)
    
    def display_monitoring_results(self, symbols: List[str], current_prices: Dict[str, CryptoPrice], alerts: List[AlertInfo]):
        """显示监控结果"""
        self.cycle_count += 1
        
        # 显示监控周期头部信息
        self.detailed_logger.print_header(f"监控周期 #{self.cycle_count} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 显示基本统计信息
        print(f"📊 监控统计: {len(current_prices)}/{len(symbols)} 个交易对获取成功")
        print(f"⚠️  触发警报: {len(alerts)} 个")
        print(f"🔄 监控间隔: {self.config.get('monitor_interval', 60)} 秒")
        print(f"📈 价格阈值: {self.config.get('price_change_threshold', 5.0)}%")
        
        # 显示所有交易对价格信息
        self.detailed_logger.print_section("交易对价格监控")
        
        for symbol in symbols:
            if symbol in current_prices:
                price_data = current_prices[symbol]
                # 检查是否符合推送条件
                symbol_alerts = [a for a in alerts if a.symbol == symbol]
                meets_criteria = len(symbol_alerts) > 0
                self.detailed_logger.print_price_info(symbol, price_data, meets_criteria)
            else:
                print(f"❌ {symbol:<12} | 获取价格失败")
        
        # 显示警报详情
        if alerts:
            self.detailed_logger.print_section("警报详情与推送状态")
            for alert in alerts:
                self.detailed_logger.print_alert_details(alert)
        else:
            self.detailed_logger.print_section("警报状态")
            print("✅ 本轮监控未触发任何警报")
        
        # 显示推送统计
        if alerts:
            success_count = len([a for a in alerts if a.push_status == PushStatus.SUCCESS])
            failed_count = len([a for a in alerts if a.push_status == PushStatus.FAILED])
            skipped_count = len([a for a in alerts if a.push_status == PushStatus.SKIPPED])
            
            print(f"\n📤 推送统计:")
            print(f"   ✅ 成功: {success_count}")
            print(f"   ❌ 失败: {failed_count}")
            print(f"   ⏭️  跳过: {skipped_count}")
        
        print(f"\n⏰ 等待 {self.config.get('monitor_interval', 60)} 秒后继续下一轮监控...")
        print("\n" + "=" * 80)
    
    def run_monitoring_cycle(self) -> Tuple[int, List[AlertInfo]]:
        """运行监控周期"""
        try:
            # 获取监控交易对
            symbols = self.get_monitored_symbols()
            
            if not symbols:
                logger.error("没有可监控的交易对")
                return 0, []
            
            # 获取当前价格
            current_prices = self.binance.get_24hr_ticker(symbols)
            
            if not current_prices:
                logger.error("未能获取价格数据")
                return 0, []
            
            # 检查警报条件
            all_alerts = []
            for symbol, price_data in current_prices.items():
                alerts = self.check_alert_conditions(symbol, price_data)
                all_alerts.extend(alerts)
            
            # 处理警报推送
            self.process_alerts(all_alerts)
            
            # 显示详细监控结果
            self.display_monitoring_results(symbols, current_prices, all_alerts)
            
            # 更新历史价格
            self.previous_prices.update(current_prices)
            
            return len(current_prices), all_alerts
            
        except Exception as e:
            logger.error(f"监控周期执行失败: {e}")
            return 0, []
    
    def start_monitoring(self):
        """启动监控系统"""
        self.detailed_logger.print_header("加密货币监控系统启动")
        
        print(f"🚀 系统配置:")
        print(f"   监控间隔: {self.config.get('monitor_interval', 60)} 秒")
        print(f"   价格阈值: {self.config.get('price_change_threshold', 5.0)}%")
        print(f"   交易量阈值: {self.config.get('volume_threshold', 1000000):,}")
        print(f"   最大监控数: {self.config.get('max_symbols', 30)} 个")
        print(f"   推送功能: {'启用' if self.config.get('enable_push', False) else '禁用'}")
        
        try:
            while True:
                start_time = time.time()
                
                # 运行监控周期
                updated_count, alerts = self.run_monitoring_cycle()
                
                # 等待下一个监控周期
                time.sleep(self.config.get('monitor_interval', 60))
                
        except KeyboardInterrupt:
            self.detailed_logger.print_header("监控系统正在关闭")
            print("👋 收到停止信号，正在安全关闭监控系统...")
            print(f"📊 总监控周期: {self.cycle_count}")
            print(f"📈 总警报数量: {len(self.alert_history)}")
        except Exception as e:
            logger.error(f"监控系统异常: {e}")
            raise

def load_config() -> Dict:
    """加载配置"""
    config_file = 'config.json'
    default_config = {
        'binance_api_key': '',
        'binance_api_secret': '',
        'monitor_interval': 60,
        'price_change_threshold': 5.0,
        'volume_threshold': 1000000,
        'max_symbols': 30,
        'enable_push': False,
        'webhook_url': '',
        'push_cooldown': 300,
        'log_level': 'INFO'
    }
    
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                file_config = json.load(f)
                default_config.update(file_config)
        except Exception as e:
            logger.warning(f"加载配置文件失败: {e}，使用默认配置")
    
    # 环境变量覆盖
    env_config = {
        'binance_api_key': os.getenv('BINANCE_API_KEY', ''),
        'binance_api_secret': os.getenv('BINANCE_API_SECRET', ''),
        'monitor_interval': float(os.getenv('MONITOR_INTERVAL', 60)),
        'price_change_threshold': float(os.getenv('PRICE_CHANGE_THRESHOLD', 5.0)),
        'volume_threshold': float(os.getenv('VOLUME_THRESHOLD', 1000000)),
        'max_symbols': int(os.getenv('MAX_SYMBOLS', 30)),
        'enable_push': os.getenv('ENABLE_PUSH', 'false').lower() == 'true',
        'webhook_url': os.getenv('WEBHOOK_URL', ''),
        'push_cooldown': int(os.getenv('PUSH_COOLDOWN', 300))
    }
    
    for key, value in env_config.items():
        if value or isinstance(value, (int, float, bool)):
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
            "volume_threshold": 1000000,
            "max_symbols": 30,
            "enable_push": false,
            "webhook_url": "",
            "push_cooldown": 300,
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
        create_default_config()
        config = load_config()
        
        log_level = getattr(logging, config.get('log_level', 'INFO').upper())
        logging.getLogger().setLevel(log_level)
        
        monitor = EnhancedCryptoMonitor(config)
        monitor.start_monitoring()
        
        return 0
        
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        logger.error(f"程序执行失败: {e}")
        return 1

if __name__ == "__main__":
    exit(main())
