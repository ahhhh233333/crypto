#!/bin/bash

# 加密货币监控系统安装脚本

echo "=== 加密货币监控系统安装脚本 ==="

# 检查操作系统
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "检测到Linux系统"
    PYTHON_CMD="python3"
    PIP_CMD="pip3"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    echo "检测到macOS系统"
    PYTHON_CMD="python3"
    PIP_CMD="pip3"
elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
    echo "检测到Windows系统"
    PYTHON_CMD="python"
    PIP_CMD="pip"
else
    echo "未知操作系统，使用默认设置"
    PYTHON_CMD="python3"
    PIP_CMD="pip3"
fi

# 检查Python版本
echo "检查Python版本..."
if ! command -v $PYTHON_CMD &> /dev/null; then
    echo "错误: 未找到Python，请先安装Python 3.7+"
    exit 1
fi

PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
echo "当前Python版本: $PYTHON_VERSION"

# 检查Python版本是否满足要求
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 7 ]); then
    echo "错误: 需要Python 3.7或更高版本，当前版本: $PYTHON_VERSION"
    exit 1
fi

# 创建虚拟环境
echo "创建虚拟环境..."
if [ ! -d "venv" ]; then
    $PYTHON_CMD -m venv venv
    echo "虚拟环境创建成功"
else
    echo "虚拟环境已存在"
fi

# 激活虚拟环境
echo "激活虚拟环境..."
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
    source venv/Scripts/activate
else
    source venv/bin/activate
fi

# 升级pip
echo "升级pip..."
pip install --upgrade pip

# 创建requirements.txt（如果不存在）
if [ ! -f "requirements.txt" ]; then
    echo "创建requirements.txt文件..."
    cat > requirements.txt << EOL
ccxt>=4.0.0
requests>=2.25.0
numpy>=1.20.0
pandas>=1.3.0
python-dotenv>=0.19.0
colorama>=0.4.4
EOL
fi

# 安装依赖
echo "安装Python依赖包..."
pip install -r requirements.txt

# 创建.env示例文件
if [ ! -f ".env" ]; then
    echo "创建.env配置文件..."
    cat > .env << EOL
# Binance API配置（可选，用于获取更多数据）
BINANCE_API_KEY=your_api_key_here
BINANCE_SECRET=your_secret_here

# 监控配置
MONITOR_INTERVAL=60
PRICE_CHANGE_THRESHOLD=5.0
MAX_SYMBOLS=50

# 日志配置
LOG_LEVEL=INFO
LOG_FILE=crypto_monitor.log
EOL
fi

# 创建启动脚本
echo "创建启动脚本..."
cat > start.sh << EOL
#!/bin/bash
echo "启动加密货币监控系统..."

# 激活虚拟环境
if [[ "\$OSTYPE" == "msys" ]] || [[ "\$OSTYPE" == "cygwin" ]]; then
    source venv/Scripts/activate
else
    source venv/bin/activate
fi

# 启动监控程序
$PYTHON_CMD crypto_monitor.py
EOL

# 创建停止脚本
echo "创建停止脚本..."
cat > stop.sh << EOL
#!/bin/bash
echo "停止加密货币监控系统..."

# 查找并终止进程
PID=\$(ps aux | grep '[p]ython.*crypto_monitor.py' | awk '{print \$2}')
if [ -n "\$PID" ]; then
    kill \$PID
    echo "监控程序已停止 (PID: \$PID)"
else
    echo "监控程序未在运行"
fi
EOL

# 设置脚本执行权限
chmod +x start.sh
chmod +x stop.sh

echo "=== 安装完成 ==="
echo "使用方法:"
echo "  启动监控: ./start.sh"
echo "  停止监控: ./stop.sh"
echo "  直接运行: $PYTHON_CMD crypto_monitor.py"
echo ""
echo "配置文件: .env"
echo "日志文件: crypto_monitor.log"
echo ""
echo "注意: 如需使用Binance API，请在.env文件中配置API密钥"
