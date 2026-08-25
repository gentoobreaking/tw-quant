
 修正內容

 ┌────────────────┬───────────────────────────────────────────────┬────────────────────────────────────────────────────┐
 │ 檔案           │ 原問題                                        │ 修正                                               │
 ├────────────────┼───────────────────────────────────────────────┼────────────────────────────────────────────────────┤
 │ simple-test.sh │ 續行符 \ 後多一個空格（斷行失效）；model 寫死 │ 改為參數化 ./simple-test.sh [model] [max_tokens]， │
 │                │                                               │ 預設吃 $OPENAI_MODEL                               │
 ├────────────────┼───────────────────────────────────────────────┼────────────────────────────────────────────────────┤
 │ ai-test.py     │ 每行開頭多一個空白（IndentationError）；相對  │ 清掉縮排；路徑改用 Path(__file__).parents[1] 自動  │
 │                │ 路徑只能在專案根目錄跑                        │ 定位專案根；參數化 [model] [max_tokens]            │
 ├────────────────┼───────────────────────────────────────────────┼────────────────────────────────────────────────────┤
 │ ai-test2.py    │ 同上縮排問題；config 用相對路徑；model 寫死   │ 同上；參數化 [model]                               │
 └────────────────┴───────────────────────────────────────────────┴────────────────────────────────────────────────────┘

 驗證結果（剛跑過）

 ```
   $ ./ai-test.py nemotron-3-ultra-free 1200
   OK: --- ### 3037 欣興 - 風控覆核報告與量化投資建議 ...   ✅ 正常回覆

   $ ./ai-test2.py nemotron-3-ultra-free
   A. 簡短問題   max_tokens=300 : {'finish_reason': 'stop', 'content_len': 73, 'reasoning_len': 0}
   B. 管線payload max_tokens=1200: {'finish_reason': 'length', 'content_len': 1328, 'reasoning_len': 0}  ⚠️
 ```

 做實驗時的提示

 - nemotron 是非推理模型（reasoning_len: 0），所以 B 測試雖然也頂到 finish_reason: length（1200 tokens 不夠它寫完整篇報
   告），但 content 有 1328 字——它邊寫邊出內容，不像 x-preview-f-free 全燒在 reasoning 上。若要它完整收尾，可把
   max_tokens 調大試試。
 - 實驗指令範例：
   ```bash
     cd ~/Projects/tw-quant/test-case
     ./simple-test.sh deepseek-v4-flash-free 300          # 快速连通测试
     venv/bin/python ai-test.py mimo-v2.5-free 1200        # 走管線完整重試邏輯
     venv/bin/python ai-test2.py x-preview-f-free          # 對照 reasoning 吃 token 的行為
   ```
 - 免費端點偶發 503，兩支 Python 都已內建重試，遇到會自動等 5 秒重打。


./ai-test.py x-preview-f-free 131072

python3 ai-test.py [model] [max_tokens] [retries] [retry_backoff] [retry_delay]

python3 ai-test.py x-preview-f-free 100000 3 0 1

python3 ai-test.py hy3-free 100000 3 0 1
OK: 價已越進場區上緣，數據顯落後矛盾；ABF循環及財報時程缺覆蓋；流動性足但波動偏高，勿追高。

python3 ai-test.py nemotron-3-ultra-free 100000 3 0 1
OK: 現價逾入場區間8%，追高回檔風險大；2027年EPS增長87%仰賴度高，PCB產業週期轉折需警惕。

python3 ai-test.py mimo-v2.5-free 100000 3 0 1
FAIL: HTTP 錯誤（已重試 3 次）: HTTP 429: {"type":"error","error":{"type":"FreeUsageLimitError","message":"Error from provider (Console): Rate limit exceeded. Please try again later."}}

python3 ai-test.py hy3-free 100000 3 0 1
OK: 數據無矛盾，但收盤價已逾進場高點，法人連買慎防調節。未含載板景氣循環及財報時程風險，流動性佳惟停損寬須控倉。

=> default: hy3-free , fallback: nemotron-3-ultra-free , mimo-v2.5-free

