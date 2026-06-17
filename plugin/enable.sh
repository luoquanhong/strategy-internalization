#!/bin/bash
# 策略注入插件 — 启用脚本
# 用法: bash enable.sh
# 已在启用状态时幂等不报错

set -e

CONFIG="$HOME/.hermes/config.yaml"
GATEWAY_SVC="hermes-gateway"
PLUGIN_DIR="$HOME/.hermes/plugins/strategy-injection"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }

echo ""
echo "========================================"
echo "  策略注入插件 — 启用"
echo "========================================"

# ── 1. 检查插件文件是否存在 ─────────────────
if [ ! -d "$PLUGIN_DIR" ] || [ ! -f "$PLUGIN_DIR/plugin.yaml" ] || [ ! -f "$PLUGIN_DIR/__init__.py" ]; then
    echo "[✗] 插件目录结构不完整，请确认 $PLUGIN_DIR 存在"
    exit 1
fi

# ── 2. 检查是否已启用 ───────────────────────
if grep -q "strategy-injection" "$CONFIG" 2>/dev/null; then
    warn "config.yaml 中已有 strategy-injection 行（可能已启用）"
    warn "如需重新加载，请重启 gateway：systemctl --user restart $GATEWAY_SVC"
    exit 0
fi

# ── 3. 备份 config ──────────────────────────
BACKUP="${CONFIG}.bak.$(date +%Y%m%d_%H%M%S)"
cp "$CONFIG" "$BACKUP"
info "config.yaml 已备份到: $(basename "$BACKUP")"

# ── 4. 添加 plugins.enabled ─────────────────
# 找到文件末尾，追加 plugins 段
# 先检查是否已有 plugins: 顶部键（key），如果没有就追加
if grep -q "^plugins:" "$CONFIG" 2>/dev/null; then
    # 已有 plugins: 段，在 enabled: 下追加
    if grep -q "^  enabled:" "$CONFIG" 2>/dev/null; then
        # 已有 enabled: 列表，在列表末尾追加
        sed -i '/^  enabled:/a\  - strategy-injection' "$CONFIG"
    else
        # 有 plugins: 但没有 enabled:，在 plugins: 下插入
        sed -i '/^plugins:/a\  enabled:\n  - strategy-injection' "$CONFIG"
    fi
else
    # 没有 plugins: 段，追加到文件末尾
    echo "" >> "$CONFIG"
    echo "plugins:" >> "$CONFIG"
    echo "  enabled:" >> "$CONFIG"
    echo "  - strategy-injection" >> "$CONFIG"
fi
info "已添加 strategy-injection 到 config.yaml plugins.enabled"

# ── 5. 重启 gateway ────────────────────────
echo ""
echo "--- 重启 $GATEWAY_SVC ---"
if systemctl --user daemon-reload 2>/dev/null; then
    systemctl --user restart "$GATEWAY_SVC"
    info "gateway 已重启"
else
    warn "无法重启 gateway（可能是 CLI 模式或没有 systemd）"
    warn "请手动重启 Hermes 客户端以生效"
fi

# ── 6. 验证 ────────────────────────────────
echo ""
echo "--- 验证 ---"
sleep 2
if command -v hermes &>/dev/null; then
    STATUS=$(hermes plugins list 2>/dev/null | grep "strategy-injection" | head -1)
    if echo "$STATUS" | grep -q "enabled"; then
        info "✅ 插件已成功启用！"
        hermes plugins list 2>/dev/null | head -1
        echo "$STATUS"
    else
        warn "插件状态: $STATUS"
    fi
fi

echo ""
echo "========================================"
info "启用完成！"
echo "  回滚指令：bash $PLUGIN_DIR/rollback.sh"
echo "  备份文件: $BACKUP"
echo "========================================"
