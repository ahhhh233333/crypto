#!/bin/bash
# 一键部署和运行加密货币监控系统
# 适用于 Ubuntu 22.04

set -e  # 遇到错误立即退出

echo "========================================="
echo "加密货币监控系统 - 一键部署脚本"
echo "========================================="
echo ""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 检查是否以root权限运行
if [ "$EUID" -eq 0 ]; then 
   echo -e "${YELLOW}建议不要以root权限运行此脚本${NC}"
   echo "是否继续？(y/n)"
   read -r response
   if [ "$response" != "y" ]; then
       exit 1
   fi
fi

# 步骤1: 更新系统包
echo -e "${GREEN}[1/7] 更新系统包...${NC}"
sudo apt-get update -qq

# 步骤2: 检查和安装Python3
echo -e "${GREEN}[2/7] 检查Python环境...${NC}"
if ! command -v python3 &> /dev/null; then
    echo "安装Python3..."
    sudo apt-get install -y python3 python3-pip python3-venv
else
    echo "Python3 已安装: $(python3 --version)"
fi

# 步骤3: 创建项目目录
echo -e "${GREEN}[3/7] 创建项目目录...${NC}"
PROJECT_DIR="$HOME/crypto_monitor"
mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"

# 步骤4: 创建虚拟环境
echo -e "${GREEN}[4/7] 创建Python虚拟环境...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "虚拟环境创建成功"
else
    echo "虚拟环境已存在"
fi

# 激活虚拟环境
source venv/bin/activate

# 步骤5: 安装依赖包
echo -e "${GREEN}[5/7] 安装Python依赖包...${NC}"
pip install --upgrade pip -q
pip install ccxt requests pandas numpy -q
echo "依赖包安装完成"

# 步骤6: 保存监控程序
echo -e "${GREEN}[6/7] 创建监控程序...${NC}"
cat > crypto_monitor.py << 'EOFD'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
加密货币现货和期货监控系统
监控Binance期货代币的现货和期货交易数据，实现短线交易提醒
"""

import ccxt
import time
import logging
import os
import requests
import json
from datetime import datetime, timedelta
from collections import defaultdict
import traceback
import numpy as np
import pandas as pd

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('crypto_monitor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class CryptoMonitor:
    def __init__(self):
        """初始化监控系统"""
        # 获取环境变量
        self.wecom_webhook_url = os.getenv('WECOM_WEBHOOK_URL')
        
        if not self.wecom_webhook_url:
            logger.warning("警告: 未设置企业微信Webhook URL (WECOM_WEBHOOK_URL)")
        
        # 初始化交易所实例
        self.exchanges = {
            'binance': ccxt.binance({'enableRateLimit': True}),
            'bybit': ccxt.bybit({'enableRateLimit': True}),
            'okx': ccxt.okx({'enableRateLimit': True}),
            'bitget': ccxt.bitget({'enableRateLimit': True}),
            'mexc': ccxt.mexc({'enableRateLimit': True}),
            'gate': ccxt.gate({'enableRateLimit': True}),
            'kucoin': ccxt.kucoin({'enableRateLimit': True})
        }
        
        # Binance期货专用实例
        self.binance_futures = ccxt.binance({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future'
            }
        })
        
        # 历史数据存储
        self.historical_data = defaultdict(lambda: {
            'prices': [],
            'volumes': [],
            'open_interests': [],
            'timestamps': [],
            'rsi': [],
            'ma_short': [],
            'ma_long': []
        })
        
        # 期货代币列表
        self.futures_symbols = []
        
        # 最大历史记录数
        self.max_history = 60  # 保存60分钟的数据
        
        # 上次警报时间记录（避免重复警报）
        self.last_alert_time = defaultdict(lambda: datetime.min)
        self.alert_cooldown = 300  # 5分钟冷却时间
        
    def initialize_futures_symbols(self):
        """获取Binance期货所有USDT交易对"""
        try:
            logger.info("正在获取Binance期货代币列表...")
            markets = self.binance_futures.load_markets()
            
            self.futures_symbols = [
                symbol for symbol in markets
                if symbol.endswith('/USDT') and 
                markets[symbol].get('type') == 'future' and
                markets[symbol].get('active', True)
            ]
            
            logger.info(f"找到 {len(self.futures_symbols)} 个期货交易对")
            return True
            
        except Exception as e:
            logger.error(f"获取期货代币列表失败: {e}")
            return False
    
    def find_best_spot_exchange(self, base_symbol):
        """找出指定代币成交额最大的现货交易所"""
        best_exchange = None
        max_volume = 0
        symbol = f"{base_symbol}/USDT"
        
        for name, exchange in self.exchanges.items():
            try:
                # 检查交易所是否支持该交易对
                if not hasattr(exchange, 'has') or not exchange.has.get('fetchTicker', True):
                    continue
                
                # 加载市场信息
                if not exchange.markets:
                    exchange.load_markets()
                
                if symbol not in exchange.markets:
                    continue
                
                # 获取24小时成交数据
                ticker = exchange.fetch_ticker(symbol)
                quote_volume = ticker.get('quoteVolume', 0) or 0
                
                if quote_volume > max_volume:
                    max_volume = quote_volume
                    best_exchange = name
                    
                time.sleep(0.1)  # 避免请求过快
                
            except Exception as e:
                logger.debug(f"获取 {name} 的 {symbol} 数据失败: {e}")
                continue
        
        return best_exchange, max_volume
    
    def calculate_rsi(self, prices, period=14):
        """计算RSI指标"""
        if len(prices) < period + 1:
            return None
        
        prices_array = np.array(prices[-period-1:])
        deltas = np.diff(prices_array)
        seed = deltas[:period]
        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period
        
        if down == 0:
            return 100
        
        rs = up / down
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def calculate_ma(self, prices, period):
        """计算移动平均线"""
        if len(prices) < period:
            return None
        return np.mean(prices[-period:])
    
    def get_trading_signal(self, symbol_data):
        """根据技术指标生成交易信号"""
        signals = []
        strength = 0  # 信号强度 -100 到 100
        
        # 获取最新数据
        if len(symbol_data['prices']) < 20:
            return "数据不足", 0, "历史数据不足"
        
        current_price = symbol_data['prices'][-1]
        rsi = symbol_data['rsi'][-1] if symbol_data['rsi'] else None
        ma_short = symbol_data['ma_short'][-1] if symbol_data['ma_short'] else None
        ma_long = symbol_data['ma_long'][-1] if symbol_data['ma_long'] else None
        
        # RSI信号
        if rsi:
            if rsi < 30:
                signals.append("RSI超卖")
                strength += 30
            elif rsi > 70:
                signals.append("RSI超买")
                strength -= 30
            elif 30 <= rsi <= 40:
                signals.append("RSI偏低")
                strength += 15
            elif 60 <= rsi <= 70:
                signals.append("RSI偏高")
                strength -= 15
        
        # 均线信号
        if ma_short and ma_long:
            if ma_short > ma_long and current_price > ma_short:
                signals.append("均线多头")
                strength += 25
            elif ma_short < ma_long and current_price < ma_short:
                signals.append("均线空头")
                strength -= 25
        
        # 价格动量
        if len(symbol_data['prices']) >= 5:
            price_5min_ago = symbol_data['prices'][-5]
            price_change = (current_price - price_5min_ago) / price_5min_ago * 100
            
            if price_change > 3:
                signals.append("强势上涨")
                strength += 20
            elif price_change < -3:
                signals.append("强势下跌")
                strength -= 20
        
        # 持仓量变化
        if len(symbol_data['open_interests']) >= 5:
            oi_current = symbol_data['open_interests'][-1]
            oi_5min_ago = symbol_data['open_interests'][-5]
            
            if oi_5min_ago > 0:
                oi_change = (oi_current - oi_5min_ago) / oi_5min_ago * 100
                
                if oi_change > 5:
                    if strength > 0:
                        signals.append("持仓增加-看多")
                        strength += 15
                    else:
                        signals.append("持仓增加-逼空")
                        strength -= 10
                elif oi_change < -5:
                    signals.append("持仓减少")
                    strength = strength * 0.7  # 减弱信号
        
        # 生成建议
        if strength >= 40:
            action = "强烈买入"
        elif strength >= 20:
            action = "买入"
        elif strength >= 10:
            action = "轻仓买入"
        elif strength <= -40:
            action = "强烈卖出"
        elif strength <= -20:
            action = "卖出"
        elif strength <= -10:
            action = "轻仓卖出"
        else:
            action = "观望"
        
        reason = f"信号强度:{strength}, 指标:{', '.join(signals) if signals else '无明显信号'}"
        
        return action, strength, reason
    
    def monitor_symbol(self, symbol):
        """监控单个交易对"""
        try:
            base = symbol.split('/')[0]
            
            # 找出最佳现货交易所
            best_exchange, daily_volume = self.find_best_spot_exchange(base)
            
            if not best_exchange:
                logger.debug(f"{symbol} 未找到可用的现货交易所")
                return
            
            spot_exchange = self.exchanges[best_exchange]
            
            # 获取现货数据
            spot_ticker = spot_exchange.fetch_ticker(symbol)
            current_price = spot_ticker['last']
            
            # 获取1分钟K线数据计算成交额
            try:
                ohlcv = spot_exchange.fetch_ohlcv(symbol, '1m', limit=2)
                if len(ohlcv) >= 1:
                    # 最新的1分钟成交额
                    minute_volume = ohlcv[-1][5] * ohlcv[-1][4]  # volume * close
                else:
                    minute_volume = 0
            except:
                minute_volume = 0
            
            # 获取期货持仓量
            try:
                open_interest_data = self.binance_futures.fetch_open_interest(symbol)
                open_interest = open_interest_data.get('openInterestAmount', 0)
            except:
                open_interest = 0
            
            # 更新历史数据
            data = self.historical_data[symbol]
            data['prices'].append(current_price)
            data['volumes'].append(minute_volume)
            data['open_interests'].append(open_interest)
            data['timestamps'].append(datetime.now())
            
            # 计算技术指标
            if len(data['prices']) >= 14:
                rsi = self.calculate_rsi(data['prices'])
                data['rsi'].append(rsi)
            else:
                data['rsi'].append(None)
            
            ma_short = self.calculate_ma(data['prices'], 7)
            ma_long = self.calculate_ma(data['prices'], 21)
            data['ma_short'].append(ma_short)
            data['ma_long'].append(ma_long)
            
            # 限制历史数据长度
            for key in data:
                if len(data[key]) > self.max_history:
                    data[key] = data[key][-self.max_history:]
            
            # 检查是否需要发送警报
            alerts = []
            
            # 现货放量检查
            if minute_volume > 50000 and len(data['prices']) >= 2:
                price_change = (current_price - data['prices'][-2]) / data['prices'][-2] * 100
                
                if abs(price_change) > 2:
                    action, strength, reason = self.get_trading_signal(data)
                    
                    alert_msg = f"""🔔 现货放量警报
代币: {symbol}
交易所: {best_exchange}
当前价格: ${current_price:.4f}
1分钟成交额: ${minute_volume:,.0f}
价格波动: {price_change:+.2f}%
RSI: {data['rsi'][-1]:.1f if data['rsi'][-1] else 'N/A'}
建议: {action}
原因: {reason}"""
                    alerts.append(alert_msg)
            
            # 期货持仓检查
            if len(data['open_interests']) >= 5 and data['open_interests'][-5] > 0:
                oi_change = (open_interest - data['open_interests'][-5]) / data['open_interests'][-5] * 100
                
                if oi_change > 5:
                    action, strength, reason = self.get_trading_signal(data)
                    
                    alert_msg = f"""📈 期货加仓警报
代币: {symbol}
当前价格: ${current_price:.4f}
持仓增加: {oi_change:+.2f}%
当前持仓: ${open_interest:,.0f}
RSI: {data['rsi'][-1]:.1f if data['rsi'][-1] else 'N/A'}
建议: {action}
原因: {reason}"""
                    alerts.append(alert_msg)
            
            # 发送警报
            for alert in alerts:
                if self.check_alert_cooldown(symbol):
                    self.send_alert(alert)
                    logger.info(f"发送警报: {symbol}")
            
        except Exception as e:
            logger.error(f"监控 {symbol} 失败: {e}")
            logger.debug(traceback.format_exc())
    
    def check_alert_cooldown(self, symbol):
        """检查是否在冷却时间内"""
        now = datetime.now()
        last_alert = self.last_alert_time[symbol]
        
        if (now - last_alert).total_seconds() > self.alert_cooldown:
            self.last_alert_time[symbol] = now
            return True
        return False
    
    def send_alert(self, message):
        """发送警报到企业微信"""
        try:
            # 发送到企业微信
            if self.wecom_webhook_url:
                self.send_to_wecom(message)
            else:
                logger.warning("未配置企业微信，警报仅记录到日志")
                logger.info(f"警报内容:\n{message}")
                
        except Exception as e:
            logger.error(f"发送警报失败: {e}")
    
    def send_to_wecom(self, message):
        """发送消息到企业微信"""
        try:
            data = {
                "msgtype": "text",
                "text": {
                    "content": message
                }
            }
            
            response = requests.post(
                self.wecom_webhook_url,
                json=data,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('errcode') == 0:
                    logger.info("企业微信消息发送成功")
                else:
                    logger.error(f"企业微信消息发送失败: {result}")
            else:
                logger.error(f"企业微信请求失败: {response.status_code}")
                
        except Exception as e:
            logger.error(f"发送企业微信消息异常: {e}")
    
    def run(self):
        """主运行循环"""
        logger.info("加密货币监控系统启动")
        
        # 初始化期货代币列表
        if not self.initialize_futures_symbols():
            logger.error("无法获取期货代币列表，程序退出")
            return
        
        # 限制监控数量，避免API超限
        monitor_symbols = self.futures_symbols[:20]  # 只监控前20个交易对
        logger.info(f"开始监控 {len(monitor_symbols)} 个交易对")
        
        while True:
            try:
                start_time = time.time()
                
                for symbol in monitor_symbols:
                    try:
                        self.monitor_symbol(symbol)
                        time.sleep(1)  # 每个交易对间隔1秒
                    except Exception as e:
                        logger.error(f"监控 {symbol} 时出错: {e}")
                        continue
                
                # 计算剩余等待时间
                elapsed_time = time.time() - start_time
                wait_time = max(60 - elapsed_time, 1)  # 确保至少等待1秒
                
                logger.info(f"本轮监控完成，等待 {wait_time:.1f} 秒后继续...")
                time.sleep(wait_time)
                
            except KeyboardInterrupt:
                logger.info("收到中断信号，程序退出")
                break
            except Exception as e:
                logger.error(f"主循环错误: {e}")
                logger.debug(traceback.format_exc())
                time.sleep(60)

def main():
    """主函数"""
    monitor = CryptoMonitor()
    monitor.run()

if __name__ == "__main__":
    main()
EOFD

echo "监控程序创建成功"

# 步骤7: 创建配置文件
echo -e "${GREEN}[7/7] 创建配置文件...${NC}"
cat > config.env << 'EOF'
# 企业微信 Webhook URL
# 请将下面的URL替换为你的企业微信机器人Webhook地址
# 获取方法：在企业微信群中添加机器人，获取Webhook地址
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY_HERE

# 其他配置（可选）
# TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
# TELEGRAM_CHAT_ID=your_telegram_chat_id_here
EOF

# 创建启动脚本
cat > start.sh << 'EOF'
#!/bin/bash
# 启动监控程序

# 加载配置
if [ -f config.env ]; then
    export $(cat config.env | grep -v '^#' | xargs)
fi

# 激活虚拟环境
source venv/bin/activate

# 启动程序
echo "启动加密货币监控系统..."
python3 crypto_monitor.py
EOF

chmod +x start.sh

# 创建后台运行脚本
cat > run_background.sh << 'EOF'
#!/bin/bash
# 后台运行监控程序

# 加载配置
if [ -f config.env ]; then
    export $(cat config.env | grep -v '^#' | xargs)
fi

# 检查是否已经在运行
if pgrep -f "crypto_monitor.py" > /dev/null; then
    echo "监控程序已经在运行中"
    echo "如需重启，请先执行: ./stop.sh"
    exit 1
fi

# 激活虚拟环境并后台运行
source venv/bin/activate
nohup python3 crypto_monitor.py > output.log 2>&1 &
echo $! > monitor.pid

echo "监控程序已在后台启动"
echo "PID: $(cat monitor.pid)"
echo "查看日志: tail -f output.log"
echo "查看详细日志: tail -f crypto_monitor.log"
EOF

chmod +x run_background.sh

# 创建停止脚本
cat > stop.sh << 'EOF'
#!/bin/bash
# 停止监控程序

if [ -f monitor.pid ]; then
    PID=$(cat monitor.pid)
    if ps -p $PID > /dev/null; then
        kill $PID
        echo "监控程序已停止 (PID: $PID)"
        rm monitor.pid
    else
        echo "进程不存在"
        rm monitor.pid
    fi
else
    # 尝试通过进程名查找
    PID=$(pgrep -f "crypto_monitor.py")
    if [ ! -z "$PID" ]; then
        kill $PID
        echo "监控程序已停止 (PID: $PID)"
    else
        echo "监控程序未在运行"
    fi
fi
EOF

chmod +x stop.sh

# 创建查看状态脚本
cat > status.sh << 'EOF'
#!/bin/bash
# 查看监控程序状态

echo "========================================="
echo "监控程序状态"
echo "========================================="

# 检查进程
PID=$(pgrep -f "crypto_monitor.py")
if [ ! -z "$PID" ]; then
    echo -e "状态: \033[0;32m运行中\033[0m"
    echo "PID: $PID"
    echo ""
    echo "内存使用:"
    ps aux | grep -E "PID|crypto_monitor.py" | grep -v grep
else
    echo -e "状态: \033[0;31m未运行\033[0m"
fi

echo ""
echo "========================================="
echo "最近的日志 (最后10行):"
echo "========================================="

if [ -f crypto_monitor.log ]; then
    tail -n 10 crypto_monitor.log
else
    echo "日志文件不存在"
fi

echo ""
echo "========================================="
echo "可用命令:"
echo "========================================="
echo "./start.sh          - 前台运行（可看到实时输出）"
echo "./run_background.sh - 后台运行"
echo "./stop.sh           - 停止程序"
echo "./status.sh         - 查看状态"
echo "========================================="
EOF

chmod +x status.sh

# 完成提示
echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}安装完成！${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo -e "${YELLOW}重要提示：${NC}"
echo -e "${YELLOW}1. 请编辑 config.env 文件，设置你的企业微信 Webhook URL${NC}"
echo -e "${YELLOW}   命令: nano config.env${NC}"
echo ""
echo -e "${GREEN}可用命令：${NC}"
echo "  ./start.sh          - 前台运行（可看到实时输出，用于测试）"
echo "  ./run_background.sh - 后台运行（推荐用于长期运行）"
echo "  ./stop.sh           - 停止程序"
echo "  ./status.sh         - 查看运行状态"
echo ""
echo -e "${GREEN}快速开始：${NC}"
echo "  1. 编辑配置: nano config.env"
echo "  2. 测试运行: ./start.sh"
echo "  3. 后台运行: ./run_background.sh"
echo ""
echo -e "${GREEN}查看日志：${NC}"
echo "  tail -f crypto_monitor.log  # 查看详细日志"
echo "  tail -f output.log          # 查看输出日志（后台运行时）"
echo ""
echo -e "${GREEN}项目位置: $PROJECT_DIR${NC}"
echo -e "${GREEN}=========================================${NC}"
