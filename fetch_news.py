#!/usr/bin/env python3
"""每天抓 黃金交易相關數據 + 新聞，產生 index.html。"""
import datetime
import html

TW = datetime.timezone(datetime.timedelta(hours=8))  # 台北時區


# ── 1. 市場數據（價格） ─────────────────────────────
# 用 yfinance 抓。symbol 對照：
#   GC=F        黃金期貨（近似 XAUUSD）
#   DX-Y.NYB    美元指數 DXY
#   ^TNX        美債 10 年殖利率
#   ^VIX        VIX 恐慌指數
MARKETS = [
    ("黃金 (Gold)", "GC=F", 2),
    ("美元指數 (DXY)", "DX-Y.NYB", 2),
    ("美債10年殖利率", "^TNX", 3),
    ("VIX 恐慌指數", "^VIX", 2),
]


def get_markets():
    rows = []
    try:
        import yfinance as yf
    except Exception:
        return rows
    for label, sym, digits in MARKETS:
        try:
            hist = yf.Ticker(sym).history(period="7d")
            closes = hist["Close"].dropna()
            last = float(closes.iloc[-1])
            prev = float(closes.iloc[-2])
            chg = last - prev
            pct = chg / prev * 100 if prev else 0
            arrow = "▲" if chg >= 0 else "▼"
            cls = "up" if chg >= 0 else "down"
            price = f"{last:,.{digits}f}"
            change = f"{arrow} {abs(chg):.{digits}f} ({pct:+.2f}%)"
            rows.append((label, price, change, cls))
        except Exception:
            rows.append((label, "—", "抓不到", "flat"))
    return rows


# ── 2. 今日經濟數據行事曆（高影響事件） ─────────────
def get_calendar():
    events = []
    try:
        import requests
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        data = r.json()
    except Exception:
        return None  # None = 抓取失敗

    today = datetime.datetime.now(TW).date()
    for ev in data:
        if ev.get("impact") != "High":
            continue
        try:
            dt = datetime.datetime.fromisoformat(ev["date"]).astimezone(TW)
        except Exception:
            continue
        if dt.date() != today:
            continue
        events.append({
            "time": dt.strftime("%H:%M"),
            "country": ev.get("country", ""),
            "title": ev.get("title", ""),
            "forecast": ev.get("forecast", "") or "-",
            "previous": ev.get("previous", "") or "-",
        })
    events.sort(key=lambda x: x["time"])
    return events


# ── 3. 新聞（RSS） ─────────────────────────────────
FEEDS = [
    ("BBC World", "http://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Reuters 商品", "https://www.investing.com/rss/news_285.rss"),
    ("中央社 國際", "https://feeds.feedburner.com/rsscna/intworld"),
]
PER_FEED = 6


def get_news():
    try:
        import feedparser
    except Exception:
        return []
    sections = []
    for name, url in FEEDS:
        try:
            entries = feedparser.parse(url).entries[:PER_FEED]
        except Exception:
            entries = []
        sections.append((name, entries))
    return sections


# ── 組合成 HTML ────────────────────────────────────
def render(markets, calendar, news):
    updated = datetime.datetime.now(TW).strftime("%Y-%m-%d %H:%M")

    # 市場數據
    m = ""
    for label, price, change, cls in markets:
        m += (f'<div class="mkt"><span class="mlabel">{html.escape(label)}</span>'
              f'<span class="mprice">{html.escape(price)}</span>'
              f'<span class="mchg {cls}">{html.escape(change)}</span></div>\n')
    if not markets:
        m = '<div class="mkt">（市場數據抓不到）</div>'

    # 行事曆
    if calendar is None:
        c = "<p>（行事曆抓不到，稍後重整或改天再看）</p>"
    elif len(calendar) == 0:
        c = "<p>今天沒有高影響事件 🎉（波動可能較平靜）</p>"
    else:
        rows = ""
        for e in calendar:
            rows += (f'<tr><td>{html.escape(e["time"])}</td>'
                     f'<td>{html.escape(e["country"])}</td>'
                     f'<td>{html.escape(e["title"])}</td>'
                     f'<td>{html.escape(str(e["forecast"]))}</td>'
                     f'<td>{html.escape(str(e["previous"]))}</td></tr>\n')
        c = ('<table><tr><th>時間</th><th>國</th><th>事件</th>'
             '<th>預測</th><th>前值</th></tr>' + rows + '</table>')

    # 新聞
    n = ""
    for name, entries in news:
        items = ""
        for e in entries:
            title = html.escape(e.get("title", "(無標題)"))
            link = html.escape(e.get("link", "#"))
            items += f'<li><a href="{link}" target="_blank" rel="noopener">{title}</a></li>\n'
        if not items:
            items = "<li>（這個來源目前抓不到）</li>"
        n += f'<section><h3>{html.escape(name)}</h3><ul>{items}</ul></section>\n'

    return TEMPLATE.format(updated=updated, markets=m, calendar=c, news=n)


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>黃金交易儀表板</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, -apple-system, "Noto Sans TC", sans-serif;
         max-width: 760px; margin: 0 auto; padding: 16px 18px 60px; line-height: 1.55; }}
  h1 {{ font-size: 1.35rem; margin: 6px 0 2px; }}
  h2 {{ font-size: 1.1rem; margin-top: 30px; border-bottom: 2px solid currentColor; padding-bottom: 4px; }}
  h3 {{ font-size: 1rem; margin: 18px 0 6px; color: #888; }}
  .updated {{ color: #888; font-size: .8rem; margin-bottom: 18px; }}
  .mkt {{ display: flex; justify-content: space-between; align-items: baseline;
         padding: 10px 4px; border-bottom: 1px solid rgba(128,128,128,.25); gap: 10px; }}
  .mlabel {{ font-weight: 600; flex: 1; }}
  .mprice {{ font-size: 1.15rem; font-variant-numeric: tabular-nums; }}
  .mchg {{ font-size: .85rem; min-width: 130px; text-align: right; font-variant-numeric: tabular-nums; }}
  .up {{ color: #16a34a; }}
  .down {{ color: #dc2626; }}
  .flat {{ color: #888; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .85rem; }}
  th, td {{ text-align: left; padding: 6px 6px; border-bottom: 1px solid rgba(128,128,128,.2); }}
  th {{ color: #888; font-weight: 600; }}
  ul {{ list-style: none; padding: 0; margin: 0; }}
  li {{ margin: 8px 0; }}
  a {{ text-decoration: none; }}
  .foot {{ margin-top: 40px; font-size: .75rem; color: #999; }}
  .foot a {{ color: #999; text-decoration: underline; }}
</style>
</head>
<body>
  <h1>黃金交易儀表板</h1>
  <div class="updated">更新：{updated}（台北）</div>

  <h2>市場數據</h2>
  {markets}

  <h2>今日經濟數據（高影響）</h2>
  {calendar}

  <h2>新聞</h2>
  {news}

  <div class="foot">
    Fed 升降息機率請看 <a href="https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html" target="_blank" rel="noopener">CME FedWatch</a>。
    本頁資料自動抓取，僅供個人參考，不構成任何交易建議。
  </div>
</body>
</html>
"""

if __name__ == "__main__":
    out = render(get_markets(), get_calendar(), get_news())
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(out)
    print("index.html 已產生")
