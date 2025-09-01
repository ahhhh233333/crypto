#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置测试脚本
用于验证系统环境和配置是否正确
"""

import os
import sys
import requests
from datetime import datetime

def test_python_version():
    """测试Python版本"""
    print("🐍 Python版本检查:")
    version = sys.version_info
    print(f"   当前版本: {version.major}.{version.minor}.{version.micro}")
    
    if version.major == 3 and version.minor >= 8:
        print("   ✅ Python版本满足要求 (3.8+)")
        return True
    else:
        print("   ❌ Python版本过低，需要3.8或更高版本")
        return False

def test_dependencies():
    """测试依赖包"""
    print("\n📦 依赖包检查:")
    
    required_packages = {
        'ccxt': 'CCXT交易所库',
        'requests': 'HTTP请求库'
    }
    
    all_ok = True
    
    for package, description in required_packages.items():
        try:
            __import__(package)
            print(f"   ✅ {package} - {description}")
        except ImportError:
            print(f"   ❌ {package} - {description} (未安装)")
            all_ok = False
    
    return all_ok

def test_ccxt_exchanges():
    """测试CCXT交易所连接"""
    print("\n🏦 交易所连接测试:")
    
    try:
        import ccxt
        
        # 测试Binance连接
        binance = ccxt.binance()
        binance.enableRateLimit = True
        
        # 获取BTC/USDT ticker作为连接测试
        ticker = binance.fetch_ticker('BTC/USDT')
        print(f"   ✅ Binance连接正常 - BTC/USDT价格: ${ticker['last']:,.2f}")
        
        # 测试期货市场
        binance_futures = ccxt.binance({'options': {'defaultType': 'future'}})
        markets = binance_futures.load_markets()
        futures_count = len([s for s in markets if s.endswith('/USDT') and markets[s]['type'] == 'future'])
        print(f"   ✅ Binance期货市场 - 找到{futures_count}个USDT交易对")
        
        return True
        
    except Exception as e:
        print(f"   ❌ 交易所连接失败: {e}")
        return False

def test_environment_variables():
    """测试环境变量配置"""
    print("\n🔧 环境变量检查:")
    
    # 检查必需的环境变量
    wecom_url = os.getenv('WECOM_WEBHOOK_URL')
    
    if wecom_url:
        if wecom_url.startswith('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key='):
            print("   ✅ WECOM_WEBHOOK_URL 格式正确")
        else:
            print("   ⚠️  WECOM_WEBHOOK_URL 格式可能不正确")
    else:
        print("   ❌ WECOM_WEBHOOK_URL 未设置")
        return False
    
    # 检查可选的环境变量
    telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
    telegram_chat = os.getenv('TELEGRAM_CHAT_ID')
    
    if telegram_token and telegram_chat:
        print("   ✅ Telegram配置已设置")
    else:
        print("   ℹ️  Telegram配置未设置（可选）")
    
    return True

def test_wecom_webhook():
    """测试企业微信Webhook"""
    print("\n📱 企业微信Webhook测试:")
    
    wecom_url = os.getenv('WECOM_WEBHOOK_URL')
    if not wecom_url:
        print("   ❌ WECOM_WEBHOOK_URL未设置，跳过测试")
        return False
    
    try:
        test_message = {
            "msgtype": "text",
            "text": {
                "content": f"🧪 配置测试消息\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n状态: 企业微信Webhook连接正常"
            }
        }
        
        response = requests.post(wecom_url, json=test_message, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            if result.get('errcode') == 0:
                print("   ✅ 企业微信Webhook测试成功")
                print("   📨 测试消息已发送到企业微信群")
                return True
            else:
                print(f"   ❌ 企业微信API返回错误: {result}")
        else:
            print(f"   ❌ HTTP请求失败: {response.status_code}")
            
    except Exception as e:
        print(f"   ❌ 企业微信Webhook测试失败: {e}")
    
    return False

def test_file_permissions():
    """测试文件权限"""
    print("\n📁 文件权限检查:")
    
    # 检查日志文件写入权限
    try:
        with open('test_log.tmp', 'w') as f:
            f.write('test')
        os.remove('test_log.tmp')
        print("   ✅ 日志文件写入权限正常")
        return True
    except Exception as e:
        print(f"   ❌ 文件写入权限异常: {e}")
        return False

def main():
    """主测试函数"""
    print("🚀 加密货币监控系统配置测试")
    print("=" * 50)
    
    tests = [
        test_python_version,
        test_dependencies,
        test_environment_variables,
        test_file_permissions,
        test_ccxt_exchanges,
        test_wecom_webhook
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"   ❌ 测试异常: {e}")
    
    print("\n" + "=" * 50)
    print(f"📊 测试结果: {passed}/{total} 项通过")
    
    if passed == total:
        print("🎉 所有测试通过！系统配置正确，可以启动监控程序")
        print("\n🚀 启动命令:")
        print("   Linux: ./start.sh run")
        print("   或直接: python crypto_monitor.py")
        return 0
    else:
        print("⚠️  部分测试失败，请检查配置后重试")
        print("\n🔧 常见问题解决:")
        print("   1. 安装依赖: pip install -r requirements.txt")
        print("   2. 配置环境变量: 复制.env.example为.env并填写配置")
        print("   3. 检查网络连接和防火墙设置")
        return 1

if __name__ == "__main__":
    exit(main())