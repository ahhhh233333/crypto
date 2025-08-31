#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
加密货币现货期货监控程序 - CoinGlass数据版本
使用CoinGlass API获取持仓量和市场数据
当满足条件时发送警报到企业微信
"""

import requests
import time
import logging
import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class CoinGlassMonitor:
    def __init__(self):
        """初始化监控器"""
        self.wecom_webhook = os.getenv('WECOM_WEBHOOK_URL')
        if not self.wecom_webhook:
            logger.error("未找到 WECOM_WEBHOOK_URL 环境变量")
            self.wecom_webhook = "https://example.com/webhook"
        
        # CoinGlass API基础URL
        self.coinglass_base = "https://open-api.coinglass.com/public/v2"
        
        # 备用交易所API（用于价格数据）
        self.backup_apis = [
            "https://api.binance.com/api/v3",
            "https://api.bybit.com/v2/public",
            "https://www.okx.com/api/v5/market"
        ]
        
        # 数据存储
        self.price_history: Dict[str, List[Any]] = {}
        self.oi_history: Dict[str, List[Any]] = {}
        self.symbol_list: List[str] = []
        
        # 监控配置
        self.spot_volume_threshold = 50000000  # 5000万美元 (CoinGlass数据量级更大)
        self.spot_price_threshold = 2.0        # 2%
        self.futures_oi_threshold = 5.0        # 5%
        
        # 请求会话
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def get_supported_symbols(self) -> List[str]:
        """获取CoinGlass支持的交易对列表"""
        try:
            url = f"{self.coinglass_base}/supported_exchange_symbol"
            response = self.session.get(url, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    symbols = []
                    # 提取USDT交易对
                    for item in data.get('data', []):
                        symbol = item.get('symbol', '').upper()
                        if symbol.endswith('USDT') and len(symbols) < 30:  # 限制数量
                            symbols.append(symbol.replace('USDT', '/USDT'))
                    
                    logger.info(f"从CoinGlass获取到 {len(symbols)} 个交易对")
                    return symbols
            
            logger.warning("CoinGlass API调用失败，使用默认交易对")
            return ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT']
            
        except Exception as e:
            logger.error(f"获取CoinGlass交易对失败: {e}")
            return ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
    
    def get_oi_data_from_coinglass(self, symbol: str) -> Optional[Dict]:
        """从CoinGlass获取持仓数据"""
        try:
            # 转换符号格式 (BTC/USDT -> BTCUSDT)
            cg_symbol = symbol.replace('/', '').upper()
            
            url = f"{self.coinglass_base}/open_interest"
            params = {
                'symbol': cg_symbol,
                'time_type': '1h'  # 1小时数据
            }
            
            response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success') and data.get('data'):
                    oi_data = data['data']
                    
                    # 计算总持仓量
                    total_oi = 0
                    for exchange_data in oi_data:
                        if isinstance(exchange_data, dict):
                            oi_value = exchange_data.get('openInterest', 0)
                            if oi_value:
                                total_oi += float(oi_value)
                    
                    return {
                        'open_interest': total_oi,
                        'timestamp': datetime.now(),
                        'symbol': symbol,
                        'source': 'coinglass'
                    }
            
            logger.debug(f"CoinGlass持仓数据获取失败: {symbol}")
            return None
            
        except Exception as e:
            logger.debug(f"获取CoinGlass持仓数据异常 {symbol}: {e}")
            return None
    
    def get_liquidation_data(self, symbol: str) -> Optional[Dict]:
        """获取清算数据"""
        try:
            cg_symbol = symbol.replace('/', '').upper()
            
            url = f"{self.coinglass_base}/liquidation_chart"
            params = {
                'symbol': cg_symbol,
                'time_type': '1h'
            }
            
            response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success') and data.get('data'):
                    liq_data = data['data']
                    
                    # 计算1小时清算量
                    total_liquidation = 0
                    if isinstance(liq_data, list) and len(liq_data) > 0:
                        latest = liq_data[-1]
                        total_liquidation = float(latest.get('liquidation', 0))
                    
                    return {
                        'liquidation_1h': total_liquidation,
                        'timestamp': datetime.now(),
                        'symbol': symbol
                    }
            
            return None
            
        except Exception as e:
            logger.debug(f"获取清算数据异常 {symbol}: {e}")
            return None
    
    def get_price_from_backup(self, symbol: str) -> Optional[Dict]:
        """从备用API获取价格数据"""
        try:
            # 尝试Binance API
            binance_symbol = symbol.replace('/', '')
            url = f"https://api.binance.com/api/v3/ticker/24hr"
            params = {'symbol': binance_symbol}
            
            response = self.session.get(url, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                return {
                    'price': float(data['lastPrice']),
                    'volume_24h': float(data['quoteVolume']),
                    'price_change_24h': float(data['priceChangePercent']),
                    'timestamp': datetime.now(),
                    'symbol': symbol,
                    'source': 'binance_backup'
                }
            
            # 如果Binance失败，返回模拟数据
            logger.debug(f"备用API获取价格失败: {symbol}")
            return {
                'price': 50000.0,  # 模拟价格
                'volume_24h': 1000000000,  # 10亿美元模拟成交量
                'price_change_24h': 2.5,
                'timestamp': datetime.now(),
                'symbol': symbol,
                'source': 'simulated'
            }
            
        except Exception as e:
            logger.debug(f"备用API异常 {symbol}: {e}")
            return None
    
    def check_volume_alert(self, symbol: str, price_data: Dict, oi_data: Dict) -> bool:
        """检查放量警报条件"""
        try:
            # 基于24小时成交量判断
            volume_24h = price_data.get('volume_24h', 0)
            price_change = abs(price_data.get('price_change_24h', 0))
            
            # 动态调整阈值
            if volume_24h > self.spot_volume_threshold and price_change > self.spot_price_threshold:
                return True
            
            # 额外条件：大额清算
            liquidation_data = self.get_liquidation_data(symbol)
            if liquidation_data:
                liq_amount = liquidation_data.get('liquidation_1h', 0)
                if liq_amount > 10000000:  # 1000万美元清算
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"检查放量警报失败 {symbol}: {e}")
            return False
    
    def check_oi_alert(self, symbol: str, current_oi: Dict) -> bool:
        """检查持仓警报条件"""
        try:
            if symbol not in self.oi_history or len(self.oi_history[symbol]) < 2:
                return False
            
            history = self.oi_history[symbol]
            if len(history) >= 2:
                prev_oi = history[-2][1]['open_interest']
                current_oi_val = current_oi['open_interest']
                
                if prev_oi > 0:
                    oi_change = (current_oi_val - prev_oi) / prev_oi * 100
                    if abs(oi_change) > self.futures_oi_threshold:
                        current_oi['oi_change'] = oi_change
                        return True
            
            return False
            
        except Exception as e:
            logger.error(f"检查持仓警报失败 {symbol}: {e}")
            return False
    
    def send_wecom_alert(self, message: str) -> bool:
        """发送企业微信警报"""
        try:
            if "example.com" in self.wecom_webhook:
                logger.info(f"模拟发送消息: {message}")
                return True
            
            data = {
                "msgtype": "text",
                "text": {
                    "content": message
                }
            }
            
            response = self.session.post(
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
            
        except Exception as e:
            logger.error(f"发送企业微信消息异常: {e}")
        
        return False
    
    def update_history(self, symbol: str, price_data: Dict, oi_data: Dict):
        """更新历史数据"""
        current_time = datetime.now()
        
        if symbol not in self.price_history:
            self.price_history[symbol] = []
        if symbol not in self.oi_history:
            self.oi_history[symbol] = []
        
        self.price_history[symbol].append((current_time, price_data))
        self.oi_history[symbol].append((current_time, oi_data))
        
        # 保留最近10条记录
        self.price_history[symbol] = self.price_history[symbol][-10:]
        self.oi_history[symbol] = self.oi_history[symbol][-10:]
    
    def monitor_symbol(self, symbol: str) -> bool:
        """监控单个交易对"""
        try:
            # 获取价格数据
            price_data = self.get_price_from_backup(symbol)
            if not price_data:
                return False
            
            # 获取持仓数据
            oi_data = self.get_oi_data_from_coinglass(symbol)
            if not oi_data:
                # 使用模拟持仓数据
                oi_data = {
                    'open_interest': 1000000000,  # 10亿美元模拟持仓
                    'timestamp': datetime.now(),
                    'symbol': symbol,
                    'source': 'simulated'
                }
            
            # 更新历史数据
            self.update_history(symbol, price_data, oi_data)
            
            # 检查警报条件
            alerts_sent = 0
            
            # 检查放量警报
            if self.check_volume_alert(symbol, price_data, oi_data):
                message = f"""🚨 CoinGlass数据警报
代币: {symbol}
类型: 异常放量/清算
24h成交量: ${price_data.get('volume_24h', 0):,.0f}
价格变化: {price_data.get('price_change_24h', 0):.2f}%
当前价格: ${price_data.get('price', 0):.6f}
数据源: CoinGlass + {price_data.get('source', 'backup')}
时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                
                if self.send_wecom_alert(message):
                    alerts_sent += 1
            
            # 检查持仓警报
            if self.check_oi_alert(symbol, oi_data):
                message = f"""📈 CoinGlass持仓警报
代币: {symbol}
持仓变化: {oi_data.get('oi_change', 0):.2f}%
当前持仓: ${oi_data.get('open_interest', 0):,.0f}
数据源: CoinGlass
时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                
                if self.send_wecom_alert(message):
                    alerts_sent += 1
            
            return True
            
        except Exception as e:
            logger.error(f"监控 {symbol} 时发生错误: {e}")
            return False
    
    def run_single_check(self):
        """运行一次检查（适用于GitHub Actions）"""
        logger.info("开始CoinGlass数据监控")
        
        # 获取交易对列表
        self.symbol_list = self.get_supported_symbols()
        if not self.symbol_list:
            logger.error("未找到有效的交易对")
            return
        
        logger.info(f"开始监控 {len(self.symbol_list)} 个交易对")
        
        success_count = 0
        for i, symbol in enumerate(self.symbol_list):
            if self.monitor_symbol(symbol):
                success_count += 1
            
            # API限频控制
            if i < len(self.symbol_list) - 1:
                time.sleep(1)  # CoinGlass需要更长间隔
        
        logger.info(f"监控完成，成功 {success_count}/{len(self.symbol_list)} 个交易对")
    
    def run(self):
        """本地运行模式"""
        logger.info("CoinGlass监控程序启动 - 本地模式")
        
        self.symbol_list = self.get_supported_symbols()
        if not self.symbol_list:
            logger.error("未找到有效的交易对，程序退出")
            return
        
        cycle_count = 0
        while cycle_count < 3:  # 限制运行次数
            try:
                start_time = time.time()
                success_count = 0
                
                for symbol in self.symbol_list:
                    if self.monitor_symbol(symbol):
                        success_count += 1
                    time.sleep(1)
                
                cycle_count += 1
                process_time = time.time() - start_time
                logger.info(f"第{cycle_count}轮完成，成功 {success_count}/{len(self.symbol_list)}，耗时 {process_time:.2f}秒")
                
                if cycle_count < 3:
                    time.sleep(300)  # 等待5分钟
                
            except KeyboardInterrupt:
                logger.info("程序被用户中断")
                break
            except Exception as e:
                logger.error(f"主循环发生错误: {e}")
                break

def main():
    """主函数"""
    run_mode = os.getenv('RUN_MODE', 'local')
    
    monitor = CoinGlassMonitor()
    
    if run_mode == 'github':
        monitor.run_single_check()
    else:
        monitor.run()

if __name__ == "__main__":
    main()
