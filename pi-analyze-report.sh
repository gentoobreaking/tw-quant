#!/bin/bash
# 讓本機 pi agent 分析管線報告（screening_results/pipeline_日期.md）
# 用法:
#   ./pi-analyze-report.sh                    # 自動抓最新報告
#   ./pi-analyze-report.sh 報告.md             # 指定報告
#   ./pi-analyze-report.sh 報告.md "額外指令"  # 指定報告＋附加分析要求
set -euo pipefail
cd "$(dirname "$0")"

REPORT="${1:-}"
EXTRA="${2:-}"

# 未指定時：抓最新的管線報告（排除 *_analysis.md / *_pi_analysis.md）
if [ -z "$REPORT" ]; then
  REPORT=$(ls -t screening_results/pipeline_*.md 2>/dev/null \
           | grep -v '_analysis' | head -1 || true)
  [ -n "$REPORT" ] || { echo "找不到 screening_results/pipeline_*.md"; exit 1; }
elif [ ! -f "$REPORT" ]; then
  echo "報告不存在: $REPORT"; exit 1
fi

OUT="${REPORT%.md}_pi_analysis.md"

PROMPT="上面附上的是量化篩選管線產生的台股買點報告。請以資深台股投資組合經理的角度做第二層分析：
1. 整體市場觀察：入選標的集中哪些產業？反映什麼氛圍？
2. 相關性風險：同產業／同題材的重複暴露
3. 分級可信度：S/A 級證據是否充分、B 級值不值得觀察
4. 執行建議：部位優先序、分批策略、需追蹤的後續事件
直接引用報告數字，繁體中文，簡潔有據。${EXTRA:+
附加要求：${EXTRA}}"

echo "報告: ${REPORT}｜pi agent 分析中…"
# --no-session：一次性分析不存 session；stdin 管線附上報告全文（官方支援模式）
cat "${REPORT}" | pi --no-session -p "${PROMPT}" | tee "${OUT}"
echo ""
echo "已儲存: ${OUT}"
