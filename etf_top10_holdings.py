import requests
from bs4 import BeautifulSoup
import re
import json
import pandas as pd

stock_code = input("stock code: ")
headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
url = f"https://tw.stock.yahoo.com/quote/{stock_code}.TW/holding"
resp = requests.get(url, headers=headers)
soup = BeautifulSoup(resp.content, "html.parser")

script = soup.find("script", string=re.compile(r"root.App.main")).string
match = re.search(r"root\.App\.main\s+=\s+\{", script)
start = match.end() - 1
depth = 0
for i, c in enumerate(script[start:]):
    if c == "{":
        depth += 1
    elif c == "}":
        depth -= 1
    if depth == 0:
        end = start + i + 1
        break

data = script[start:end]
cleaned = re.sub(r"\bundefined\b", "null", data)
cleaned = re.sub(r"\bNaN\b", "null", cleaned)
cleaned = re.sub(r"\bInfinity\b", "null", cleaned)

parsed = json.loads(cleaned)
holdings = parsed["context"]["dispatcher"]["stores"]["QuoteETFStore"]["etfInfo"]["data"]["portfolio"]["top10Holdings"]["holdingDetail"]

top10 = []
for h in holdings[:10]:
    top10.append({"ticker": h["ticker"], "name": h["name"], "weighting": h["weighting"]})

df = pd.DataFrame(top10)
print(f"{stock_code} top 10 holdings:")
print(df.to_string(index=False))