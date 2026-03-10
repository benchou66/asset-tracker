"""
每日個股現值更新腳本
- 從台灣證交所抓當日收盤價
- 計算現值、損益、報酬率
- 寫入 Firebase Realtime Database
"""

import os
import json
import time
import requests
import firebase_admin
from firebase_admin import credentials, db
from datetime import datetime, timezone, timedelta

# ── 設定 ──────────────────────────────────────────────
FIREBASE_DATABASE_URL = os.environ["FIREBASE_DATABASE_URL"]
SERVICE_ACCOUNT_JSON  = os.environ["FIREBASE_SERVICE_ACCOUNT"]

# 台灣時區
TW_TZ   = timezone(timedelta(hours=8))
TODAY   = datetime.now(TW_TZ).strftime("%Y-%m-%d")

# ── 初始化 Firebase ────────────────────────────────────
cred = credentials.Certificate(json.loads(SERVICE_ACCOUNT_JSON))
firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DATABASE_URL})


def get_twse_close_price(stock_code: str) -> float | None:
    """
    從證交所抓個股當日收盤價
    API: https://www.twse.com.tw/exchangeReport/STOCK_DAY
    """
    url = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
    params = {
        "response": "json",
        "date": TODAY.replace("-", ""),
        "stockNo": stock_code,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("stat") != "OK" or not data.get("data"):
            print(f"  ⚠️  {stock_code}: 無資料 (可能今日未開市或代號錯誤)")
            return None
        # data[-1] 是當月最後一筆（今日）
        last_row = data["data"][-1]
        # 欄位: 日期, 成交股數, 成交金額, 開盤, 最高, 最低, 收盤, 漲跌, 成交筆數
        close_str = last_row[6].replace(",", "")
        return float(close_str)
    except Exception as e:
        print(f"  ❌ {stock_code} 抓取失敗: {e}")
        return None


def get_otc_close_price(stock_code: str) -> float | None:
    """
    從櫃買中心抓個股當日收盤價（上櫃股票）
    API: https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes
    """
    url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
    params = {"date": TODAY.replace("-", "/")[2:]}  # YY/MM/DD
    try:
        resp = requests.get(url, params=params, timeout=10)
        rows = resp.json()
        for row in rows:
            if row.get("SecuritiesCompanyCode") == stock_code:
                return float(row["Close"].replace(",", ""))
        print(f"  ⚠️  {stock_code}: 未在上櫃清單中找到")
        return None
    except Exception as e:
        print(f"  ❌ {stock_code} OTC 抓取失敗: {e}")
        return None


def fetch_close_price(stock_code: str) -> float | None:
    """先試上市，失敗再試上櫃"""
    price = get_twse_close_price(stock_code)
    if price is None:
        print(f"  🔄 {stock_code}: 嘗試上櫃...")
        price = get_otc_close_price(stock_code)
    return price


def main():
    print(f"📅 {TODAY} 開始更新個股現值...")

    # ── 讀取 Firebase 現有 holdings ──────────────────────
    ref_path = f"holdings"
    holdings_ref = db.reference(ref_path)
    holdings = holdings_ref.get()

    if not holdings or not holdings.get("stocks"):
        print("⚠️  Firebase 中無持股資料，結束")
        return

    stocks = holdings["stocks"]
    print(f"📊 找到 {len(stocks)} 檔持股")

    updated = []
    for stock in stocks:
        code   = stock.get("code", "").strip()
        name   = stock.get("name", code)
        shares = float(str(stock.get("shares", 0)).replace(",", ""))
        cost   = float(str(stock.get("cost", 0)).replace(",", "") or 0)

        if not code or shares == 0:
            updated.append(stock)
            continue

        print(f"  📈 {code} {name} ({shares:,.0f} 股)...")
        time.sleep(0.5)  # 避免打太快被擋

        price = fetch_close_price(code)
        if price is None:
            print(f"  ⏭️  {code}: 無法取得收盤價，保留舊值")
            updated.append(stock)
            continue

        value = round(price * shares)
        pnl   = round(value - cost) if cost else None
        pct   = round((value - cost) / cost * 100, 2) if cost else None

        pnl_str = f"+{pnl:,}" if pnl and pnl >= 0 else f"{pnl:,}" if pnl else ""
        pct_str = f"+{pct}%" if pct and pct >= 0 else f"{pct}%" if pct else ""

        print(f"     收盤價 {price:,.2f} → 現值 {value:,} | 損益 {pnl_str} ({pct_str})")

        updated.append({
            **stock,
            "value": value,
            "pnl":   pnl_str,
            "pct":   pct_str,
        })

    # ── 寫回 Firebase ──────────────────────────────────
    holdings_ref.update({
        "stocks": updated,
        "date":   TODAY,
        "updatedAt": datetime.now(TW_TZ).isoformat(),
    })

    total_value = sum(s.get("value", 0) for s in updated if isinstance(s.get("value"), (int, float)))
    print(f"\n✅ 更新完成！總現值：NT$ {total_value:,}")


if __name__ == "__main__":
    main()
