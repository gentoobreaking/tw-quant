import twstock

# 更新最新的上市、上櫃股票清單
twstock.codes.update()

# 取得所有股票代碼
all_stocks = twstock.codes
for code, info in all_stocks.items():
    print(f"代碼: {code}, 名稱: {info.name}, 市場: {info.market}")

