#!/bin/bash

# 加密货币监控系统启动脚本
# 适用于 Ubuntu 22.04 及其他 Linux 系统

set -e  # 遇到错误时退出

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查Python版本
check_python() {
    print_info "检查Python版本..."
    
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
        print_success "找到Python版本: $PYTHON_VERSION"
        
        # 检查版本是否满足要求（3.8+）
        PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
        PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)
        
        if [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -ge 8 ]; then
            print_success "Python版本满足要求 (3.8+)"
        else
            print_error "Python版本过低，需要3.8或更高版本"
            exit 1
        fi
    else
        print_error "未找到Python3，请先安装Python3"
        exit 1
    fi
}

# 检查pip
check_pip() {
    print_info "检查pip..."
    
    if command -v pip3 &> /dev/null; then
        print_success "找到pip3"
    else
        print_warning "未找到pip3，尝试安装..."
        sudo apt update
        sudo apt install -y python3-pip
    fi
}

# 创建虚拟环境
setup_venv() {
    print_info "设置Python虚拟环境..."
    
    if [ ! -d "venv" ]; then
        print_info "创建虚拟环境..."
        python3 -m venv venv
        print_success "虚拟环境创建完成"
    else
        print_info "虚拟环境已存在"
    fi
    
    # 激活虚拟环境
    source venv/bin/activate
    print_success "虚拟环境已激活"
}

# 安装依赖
install_dependencies() {
    print_info "安装Python依赖包..."
    
    if [ -f "requirements.txt" ]; then
        pip install --upgrade pip
        pip install -r requirements.txt
        print_success "依赖包安装完成"
    else
        print_error "未找到requirements.txt文件"
        exit 1
    fi
}

# 检查环境变量配置
check_env_config() {
    print_info "检查环境变量配置..."
    
    if [ ! -f ".env" ]; then
        if [ -f ".env.example" ]; then
            print_warning "未找到.env文件，复制.env.example为模板"
            cp .env.example .env
            print_warning "请编辑.env文件，配置必要的环境变量"
            print_warning "特别是WECOM_WEBHOOK_URL必须配置"
            return 1
        else
            print_error "未找到环境配置文件"
            return 1
        fi
    fi
    
    # 加载环境变量
    if [ -f ".env" ]; then
        export $(cat .env | grep -v '^#' | xargs)
    fi
    
    # 检查必需的环境变量
    if [ -z "$WECOM_WEBHOOK_URL" ]; then
        print_error "WECOM_WEBHOOK_URL环境变量未设置"
        print_error "请在.env文件中配置企业微信Webhook URL"
        return 1
    fi
    
    print_success "环境变量配置检查通过"
    return 0
}

# 创建systemd服务文件
create_systemd_service() {
    print_info "创建systemd服务文件..."
    
    SERVICE_FILE="/etc/systemd/system/crypto-monitor.service"
    CURRENT_DIR=$(pwd)
    CURRENT_USER=$(whoami)
    
    sudo tee $SERVICE_FILE > /dev/null <<EOF
[Unit]
Description=Crypto Currency Monitor
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$CURRENT_DIR
Environment=PATH=$CURRENT_DIR/venv/bin
EnvironmentFile=$CURRENT_DIR/.env
ExecStart=$CURRENT_DIR/venv/bin/python $CURRENT_DIR/crypto_monitor.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    print_success "systemd服务文件创建完成"
    
    # 重新加载systemd
    sudo systemctl daemon-reload
    print_success "systemd配置已重新加载"
}

# 启动服务
start_service() {
    print_info "启动加密货币监控服务..."
    
    # 启用服务（开机自启）
    sudo systemctl enable crypto-monitor.service
    
    # 启动服务
    sudo systemctl start crypto-monitor.service
    
    # 检查服务状态
    if sudo systemctl is-active --quiet crypto-monitor.service; then
        print_success "服务启动成功！"
        print_info "使用以下命令管理服务："
        echo "  查看状态: sudo systemctl status crypto-monitor"
        echo "  查看日志: sudo journalctl -u crypto-monitor -f"
        echo "  停止服务: sudo systemctl stop crypto-monitor"
        echo "  重启服务: sudo systemctl restart crypto-monitor"
    else
        print_error "服务启动失败"
        print_info "查看错误日志: sudo journalctl -u crypto-monitor -n 50"
        exit 1
    fi
}

# 直接运行（前台模式）
run_foreground() {
    print_info "在前台模式运行监控程序..."
    print_info "按Ctrl+C停止程序"
    
    # 激活虚拟环境
    source venv/bin/activate
    
    # 加载环境变量
    if [ -f ".env" ]; then
        export $(cat .env | grep -v '^#' | xargs)
    fi
    
    # 运行程序
    python crypto_monitor.py
}

# 显示帮助信息
show_help() {
    echo "加密货币监控系统启动脚本"
    echo ""
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  setup     - 设置环境（安装依赖、创建虚拟环境等）"
    echo "  service   - 创建并启动systemd服务（后台运行）"
    echo "  run       - 直接运行程序（前台模式）"
    echo "  status    - 查看服务状态"
    echo "  logs      - 查看服务日志"
    echo "  stop      - 停止服务"
    echo "  restart   - 重启服务"
    echo "  help      - 显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 setup     # 首次使用，设置环境"
    echo "  $0 service   # 创建系统服务并启动"
    echo "  $0 run       # 直接运行程序"
}

# 主函数
main() {
    case "${1:-help}" in
        setup)
            print_info "开始设置加密货币监控系统环境..."
            check_python
            check_pip
            setup_venv
            install_dependencies
            
            if ! check_env_config; then
                print_warning "请配置.env文件后再运行程序"
                exit 1
            fi
            
            print_success "环境设置完成！"
            print_info "下一步: 运行 '$0 service' 创建系统服务，或运行 '$0 run' 直接启动"
            ;;
        
        service)
            if ! check_env_config; then
                print_error "请先配置环境变量"
                exit 1
            fi
            
            create_systemd_service
            start_service
            ;;
        
        run)
            if ! check_env_config; then
                print_error "请先配置环境变量"
                exit 1
            fi
            
            run_foreground
            ;;
        
        status)
            sudo systemctl status crypto-monitor.service
            ;;
        
        logs)
            sudo journalctl -u crypto-monitor -f
            ;;
        
        stop)
            sudo systemctl stop crypto-monitor.service
            print_success "服务已停止"
            ;;
        
        restart)
            sudo systemctl restart crypto-monitor.service
            print_success "服务已重启"
            ;;
        
        help|--help|-h)
            show_help
            ;;
        
        *)
            print_error "未知选项: $1"
            show_help
            exit 1
            ;;
    esac
}

# 检查是否为root用户运行
if [ "$EUID" -eq 0 ]; then
    print_error "请不要使用root用户运行此脚本"
    exit 1
fi

# 运行主函数
main "$@"