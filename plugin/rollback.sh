#!/bin/bash
# 策略注入插件 — 一键回滚脚本
# 用法: bash rollback.sh           # 软回滚（只禁用，保留插件文件）
#       bash rollback.sh --clean   # 硬回滚（禁用 + 删插件文件 + 清检索缓存）
#
# 幂等：多次执行不报错，已回滚时输出"(already rolled back)" 退出码0。
#
# 回滚两步：① 删 config.yaml 里的 strategy-injection 启用行
#           ② 重启 hermes-gateway

set -e

CONFIG="$HOME/.hermes/config.yaml"
GATEWAY_SVC="hermes-gateway"
PLUGIN_DIR="$HOME/.hermes/plugins/strategy-injection"
# 引擎状态文件路径：优先用环境变量，回退到与插件同级或上层 strategy-internalization
ENGINE_STATE="${STRATEGY_ENGINE_STATE:-$HOME/strategy-internalization/retrieval_state.json}"

# ── 颜色输出 ──
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
err()   { echo -e "${RED}[✗]${NC} $1"; }

echo ""
echo "========================================"
echo "  策略注入插件 — 回滚"
echo "========================================"

# ── 1. 检查是否已回滚 ──────────────────────────
if ! grep -q "strategy-injection" "$CONFIG" 2>/dev/null; then
    warn "config.yaml 中没有 strategy-injection 行，可能已回滚"
    # 但可能插件文件还在，继续走清理
fi

# ── 2. 备份 config ─────────────────────────────
BACKUP="${CONFIG}.bak.$(date +%Y%m%d_%H%M%S)"
cp "$CONFIG" "$BACKUP"
info "config.yaml 已备份到: $(basename "$BACKUP")"

# ── 3. 删除启用行 ──────────────────────────────
# 删 - strategy-injection 及其可能的缩进变体
sed -i '/strategy-injection/d' "$CONFIG"
info "已从 config.yaml 删除 strategy-injection 启用行"

# ── 4. (可选) 硬清理 ────────────────────────────
if [ "$1" = "--clean" ]; then
    echo ""
    echo "--- 硬清理模式 ---"
    if [ -d "$PLUGIN_DIR" ]; then
        rm -rf "$PLUGIN_DIR"
        info "已删除插件目录: $PLUGIN_DIR"
    else
        warn "插件目录不存在，跳过"
    fi
    if [ -f "$ENGINE_STATE" ]; then
        rm -f "$ENGINE_STATE"
        info "已删除检索缓存: $ENGINE_STATE"
    else
        warn "检索缓存不存在，跳过"
    fi
fi

# ── 5. 重启 gateway ────────────────────────────
echo ""
echo "--- 重启 $GATEWAY_SVC ---"
if systemctl --user daemon-reload 2>/dev/null; then
    systemctl --user restart "$GATEWAY_SVC"
    info "gateway 已重启"
else
    warn "无法重启 gateway（可能是 CLI 模式或没有 systemd）"
    warn "请手动重启 Hermes 客户端以生效"
fi

# ── 6. 验证 ────────────────────────────────────
echo ""
echo "--- 验证 ---"
if grep -q "strategy-injection" "$CONFIG" 2>/dev/null; then
    err "config.yaml 中仍有 strategy-injection（sed 未命中，检查缩进）"
    exit 1
else
    info "config.yaml 验证通过，再无 strategy-injection"
fi

sleep 1
if command -v hermes &>/dev/null; then
    STATUS=$(hermes plugins list 2>/dev/null | grep "strategy-injection" | head -1)
    if echo "$STATUS" | grep -q "not enabled"; then
        info "插件状态验证通过: strategy-injection = not enabled"
    elif [ -n "$STATUS" ]; then
        warn "插件状态异常: $STATUS"
    else
        warn "hermes plugins list 未找到 strategy-injection（可能已被 --clean 删掉）"
    fi
fi

echo ""
echo "========================================"
info "回滚完成！"
echo "  如需重新启用：bash $PLUGIN_DIR/enable.sh"
echo "  备份文件: $BACKUP"
echo "========================================"
