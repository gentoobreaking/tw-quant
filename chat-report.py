#!/usr/bin/env python3
"""把管線報告丟給免登入的網頁版 ChatGPT/Grok 分析（透過本機 camofox 反偵測瀏覽器）

用法:
  ./chat-report.py                       # 最新報告 → ChatGPT 網頁分析
  ./chat-report.py 報告.md                # 指定報告
  ./chat-report.py 報告.md "額外指令"     # 指定報告＋附加要求
  SITE=grok ./chat-report.py             # 改用 grok.com
  CHUNK=6000 ./chat-report.py            # 每段字元數（預設 6000）

前置：camofox-browser 服務（未啟動會自動拉起）
輸出：<報告名>_chatgpt_analysis.md（或 _grok_），並印到 stdout
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
BASE = "http://localhost:9377"
USER = "chat-report"
CHUNK = int(os.environ.get("CHUNK", "6000"))
SITES = {
    "chatgpt": {"url": "https://chatgpt.com/",
                "box": "Ask ChatGPT",
                "said": "ChatGPT said:",
                "done": "Copy response"},
    "grok": {"url": "https://grok.com/",
             "box": "Ask Grok anything",
             "said": "Grok said:",
             "done": "Copy"},
}
PROMPT_HEAD = (
    "以下這份台股量化篩選買點報告因長度限制需分多則訊息傳送。\n"
    "請先只回覆「收到」，等我把全文送完、說「報告傳送完畢」後，\n"
    "再以資深台股投資組合經理的角度做第二層分析：\n"
    "1. 整體市場觀察：入選標的集中在哪些產業？反映什麼氛圍？\n"
    "2. 相關性風險：同產業／同題材的重複暴露\n"
    "3. 分級可信度：S/A 級證據是否充分、B 級值不值得觀察\n"
    "4. 執行建議：部位優先序、分批策略、需追蹤的後續事件\n"
    "直接引用報告數字，繁體中文，簡潔有據。\n\n--- 第 1 段 ---\n")


def api(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def ensure_service() -> None:
    try:
        api("GET", "/health")
        return
    except Exception as e:
        print(f"health 檢查失敗（{type(e).__name__}: {e}），嘗試拉起服務…")
    print("camofox 服務未啟動，拉起中…（首次需下載 binary，請耐心）")
    subprocess.Popen(["npx", "-y", "@askjo/camofox-browser"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    for _ in range(60):
        try:
            api("GET", "/health")
            print("服務就緒")
            return
        except Exception:
            time.sleep(3)
    sys.exit("camofox 服務啟動逾時")


def latest_report() -> Path:
    files = sorted(p for p in (PROJECT / "screening_results").glob("pipeline_*.md")
                   if not re.search(r"_(analysis|pi_analysis)$", p.stem))
    if not files:
        sys.exit("找不到 screening_results/pipeline_*.md")
    return files[-1]


def snapshot(tab: str) -> str:
    return api("GET", f"/tabs/{tab}/snapshot?userId={USER}&sessionKey={USER}").get("snapshot", "")


def act(tab: str, action: str, **body) -> dict:
    body.update(userId=USER, sessionKey=USER)
    return api("POST", f"/tabs/{tab}/{action}", body)


def find_textbox(snap: str, site: dict) -> str | None:
    """找主輸入框 ref：優先 placeholder 符合者，否則取第一個有 placeholder 的 textbox"""
    lines = snap.splitlines()
    fallback = None
    for i, l in enumerate(lines):
        if "textbox" not in l:
            continue
        m = re.search(r"\[(e\d+)\]", l)
        if not m:
            continue
        ctx = "\n".join(lines[i:i + 3])   # placeholder 常在下一行
        if site["box"] in ctx:
            return m.group(1)
        if fallback is None and "/placeholder" in ctx:
            fallback = m.group(1)
    return fallback


def fresh_box(tab: str, site: dict) -> str | None:
    """每次動作前重抓 ref：ChatGPT 在互動後會重繪，舊 ref 會漂移到別的元素"""
    return find_textbox(snapshot(tab), site)


def send(tab: str, site: dict, text: str, max_tries: int = 4) -> None:
    """發送一則訊息並驗證真的貼出（對話數增加）；每個動作都用當下新 ref"""
    for attempt in range(1, max_tries + 1):
        try:
            before = snapshot(tab).count("said:")
            box = fresh_box(tab, site)
            if not box:
                time.sleep(4)
                continue
            act(tab, "click", ref=box)
            box = fresh_box(tab, site)          # 點擊後樹狀結構已變，必須重抓
            if not box:
                time.sleep(3)
                continue
            act(tab, "type", ref=box, text=text)
            act(tab, "press", key="Enter")
            deadline = time.time() + 20
            while time.time() < deadline:
                time.sleep(3)
                if snapshot(tab).count("said:") > before:
                    time.sleep(2)
                    return                      # 驗證：訊息已貼出
        except Exception as e:
            print(f"    傳送失敗（{type(e).__name__}: {str(e)[:60]}），"
                  f"重試 {attempt}/{max_tries}")
        else:
            print(f"    未偵測到訊息貼出，重試 {attempt}/{max_tries}")
        time.sleep(5)
    raise RuntimeError("訊息傳送失敗（重試次數用盡）")


def chunks(text: str, size: int):
    lines, buf, out = text.splitlines(keepends=True), "", []
    for ln in lines:
        if len(buf) + len(ln) > size and buf:
            out.append(buf)
            buf = ""
        buf += ln
    if buf:
        out.append(buf)
    return out


def extract_reply(snap: str, site: dict) -> str:
    """抽出最後一個「XXX said:」之後的回覆；須出現 Copy 按鈕（生成完畢）才算"""
    lines = snap.splitlines()
    idx = [i for i, l in enumerate(lines) if site["said"] in l]
    if not idx:
        return ""
    seg = []
    for l in lines[idx[-1] + 1:]:
        if site["done"] in l:
            break
        seg.append(l)
    text = "\n".join(seg)
    text = "\n".join(l.strip().removeprefix("- ").removeprefix("- text:")
                     for l in text.splitlines() if l.strip())
    text = text.replace("- paragraph: ", "").replace("paragraph: ", "")
    return text.strip()


def wait_replied(tab: str, site: dict, timeout: int = 90) -> None:
    """等對方回覆完畢（出現 Copy 按鈕），避免輸入框未就緒就發下一段"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if site["done"] in snapshot(tab):
            time.sleep(2)
            return
        time.sleep(3)


def main() -> int:
    args = sys.argv[1:]
    report = Path(args[0]) if args and Path(args[0]).exists() else None
    extra = ""
    if report and len(args) > 1:
        extra = args[1]
    elif not report and args:
        extra = args[0]
    if report is None:
        report = latest_report()

    site = SITES[os.environ.get("SITE", "chatgpt")]
    ensure_service()
    tab = api("POST", "/tabs", {"url": site["url"], "userId": USER,
                                "sessionKey": USER})["tabId"]
    print(f"報告: {report.name}（{report.stat().st_size} bytes）｜站點: {site['said'].split()[0]}")
    time.sleep(6)

    snap = snapshot(tab)
    box = find_textbox(snap, site)
    if not box:
        api("DELETE", f"/tabs/{tab}?userId={USER}&sessionKey={USER}")
        sys.exit("找不到輸入框，請確認頁面狀態")

    content = report.read_text(encoding="utf-8")
    parts = [PROMPT_HEAD] + chunks(content, CHUNK)
    for n, part in enumerate(parts, 1):
        print(f"  傳送 {n}/{len(parts)} 段…")
        try:
            send(tab, site, part)
        except Exception as e:                    # 偶發 422：重試一次
            print(f"    （{type(e).__name__}，重試…）")
            time.sleep(6)
            send(tab, site, part)
        if n < len(parts):
            wait_replied(tab, site)               # 等回覆完再發下一段
    time.sleep(5)
    send(tab, site, "報告傳送完畢。請開始你的第二層分析。"
         + (f"\n附加要求：{extra}" if extra else ""))
    print("已全部送出，等待分析…")

    deadline = time.time() + 480          # 最長等 8 分鐘
    reply, stable = "", 0
    while time.time() < deadline:
        time.sleep(12)
        new = extract_reply(snapshot(tab), site)
        if new and new == reply:          # 內容連續兩次相同＝生成完畢
            stable += 1
            if stable >= 1:
                break
        else:
            stable = 0
        reply = new
        print(f"  …生成中（{len(reply)} 字元）")

    try:
        api("DELETE", f"/tabs/{tab}?userId={USER}&sessionKey={USER}")
    except Exception:
        pass

    if not reply:
        sys.exit("等待逾時，未取得完整回覆")

    tag = os.environ.get("SITE", "chatgpt")
    out = report.with_name(report.stem + f"_{tag}_analysis.md")
    header = (f"# {tag} 網頁分析｜{report.name}\n"
              f"> 產生時間: {time.strftime('%Y-%m-%d %H:%M')}\n\n")
    out.write_text(header + reply + "\n", encoding="utf-8")
    print(f"已儲存: {out}\n")
    print(reply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
