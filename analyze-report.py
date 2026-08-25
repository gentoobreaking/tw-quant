#!/Users/david/Projects/tw-quant/venv/bin/python
"""管線報告 AI 分析：把 screening_results 的 markdown 報告整份餵給 LLM 做第二層解讀

用法:
  ./analyze-report.py                          # 自動抓最新一份 pipeline_*.md，模型用 config
  ./analyze-report.py hy3-free 16384           # 指定模型與 max_tokens
  ./analyze-report.py 報告.md                   # 指定報告
  ./analyze-report.py 報告.md hy3-free 16384    # 指定報告＋模型

輸出: 報告同目錄下 <報告名>_analysis.md，並印到 stdout
"""
import json
import sys
import types
from pathlib import Path

PROJECT = Path(__file__).resolve().parent   # 腳本位於專案根目錄
sys.path.insert(0, str(PROJECT))

# 測試腳本輕量化：跳過 common/__init__.py 全量載入（避免 yfinance 重依賴）
_pkg = types.ModuleType("common")
_pkg.__path__ = [str(PROJECT / "common")]
sys.modules.setdefault("common", _pkg)

from common.ai_review import OUTPUT_CONSTRAINTS, _call_llm, resolve_ai_config

ANALYST_PROMPT = (
    "你是一名資深台股投資組合經理。以下是量化篩選管線產生的買點報告，"
    "請從「組合層面」進行第二層分析，包含：\n"
    "1. 整體市場觀察：入選標的集中在哪些產業？反映什麼市場氛圍？\n"
    "2. 標的間的相关性風險：同產業或同一題材的重複暴露\n"
    "3. 分級可信度：S/A 級標的的證據是否充分、B 級是否值得觀察\n"
    "4. 執行建議：部位分配優先序、分批策略、需追蹤的後續事件\n"
    "以繁體中文輸出，簡潔有據，直接引用報告數字，不要客套。"
)


def latest_report() -> Path:
    # 只抓管線產生的報告（pipeline_日期.md），排除 *_analysis.md
    files = sorted(p for p in (PROJECT / "screening_results").glob("pipeline_*.md")
                   if not p.stem.endswith("_analysis"))
    if not files:
        sys.exit("找不到 screening_results/pipeline_*.md")
    return files[-1]


def main() -> int:
    args = sys.argv[1:]
    # 第一個參數若不是存在的檔案／.md 路徑，視為模型名（可省略報告路徑）
    report = None
    if args:
        cand = Path(args[0])
        if not cand.exists() and not cand.suffix == ".md":
            pass                      # 不是路徑 → 當模型名
        elif cand.exists():
            report = cand
            args = args[1:]
        else:
            report = PROJECT / "screening_results" / cand.name
            args = args[1:] if report.exists() else args
    if report is None or not report.exists():
        report = latest_report()

    model = args[0] if args else None
    max_tokens = int(args[1]) if len(args) > 1 else None

    cfg = json.load(open(PROJECT / "config_pipeline.json"))
    ai_cfg = resolve_ai_config(cfg.get("ai_review", {}))
    if model:
        ai_cfg["model"] = model
        ai_cfg["fallback_models"] = []          # 手動指定時不備援，如實失敗
    if max_tokens:
        ai_cfg["max_tokens"] = max_tokens

    content = report.read_text(encoding="utf-8")
    print(f"報告: {report}（{len(content)} 字元）｜模型: {ai_cfg['model']}"
          f"｜max_tokens: {ai_cfg['max_tokens']}")

    messages = [
        {"role": "system",
         "content": f"{ANALYST_PROMPT}\n{OUTPUT_CONSTRAINTS}"},
        {"role": "user", "content": content},
    ]
    try:
        text = _call_llm(messages, ai_cfg)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: {e}")
        return 1

    out = report.with_name(report.stem + "_analysis.md")
    header = (f"# 報告 AI 分析｜{report.name}\n"
              f"> 模型: {ai_cfg['model']}｜產生時間: "
              f"{__import__('datetime').datetime.now():%Y-%m-%d %H:%M}\n\n")
    out.write_text(header + text + "\n", encoding="utf-8")
    print(f"已儲存: {out}\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
