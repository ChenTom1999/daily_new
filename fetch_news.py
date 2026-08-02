#!/usr/bin/env python3
"""每天抓 黃金交易相關數據 + 新聞 + AI 摘要，產生 index.html。"""
import datetime
import html
import os
import re

TW = datetime.timezone(datetime.timedelta(hours=8))  # 台北時區


# ── 1. 市場數據（價格） ─────────────────────────────
MARKETS = [
    ("黃金 · GOLD", "GC=F", 2),
    ("美元指數 DXY", "DX-Y.NYB", 2),
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
            if hist is None or hist.empty or "Close" not in hist:
                raise ValueError("no data")
            closes = hist["Close"].dropna()
            if len(closes) < 2:
                raise ValueError("not enough data")
            last = float(closes.iloc[-1])
            prev = float(closes.iloc[-2])
            chg = last - prev
            pct = chg / prev * 100 if prev else 0
            arrow = "\u25B2" if chg >= 0 else "\u25BC"
            cls = "up" if chg >= 0 else "down"
            price = f"{last:,.{digits}f}"
            change = f"{arrow} {abs(chg):.{digits}f} ({pct:+.2f}%)"
            rows.append((label, price, change, cls))
        except Exception as e:
            print(f"[warn] {label} ({sym}) 抓取失敗: {e}")
            rows.append((label, "\u2014", "\u2014", "flat"))
    return rows


# ── 2. 今日經濟數據行事曆（高影響事件） ─────────────
def get_calendar():
    try:
        import requests
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        data = r.json()
    except Exception:
        return None

    now = datetime.datetime.now(TW)
    today = now.date()
    events = []
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
            "passed": dt < now,
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
    ("Hacker News", "https://hnrss.org/frontpage"),
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


# ── 4. AI 重點摘要（GitHub Models，免費） ────────────
def get_summary(news):
    token = os.environ.get("GEMINI_API_KEY")
    if not token:
        print("[warn] 沒有 GEMINI_API_KEY，略過 AI 摘要")
        return None

    titles = []
    for name, entries in news:
        for e in entries:
            t = e.get("title")
            if t:
                titles.append(f"- {t}")
    if not titles:
        return None
    joined = "\n".join(titles[:40])

    prompt = (
        "你是協助黃金(XAU/USD)短波段交易者的助理。以下是今天抓到的新聞標題，"
        "請挑出與黃金、美元、利率、通膨、地緣政治、避險情緒相關的內容，"
        "用繁體中文整理成 3 到 5 條精簡重點，每條一行，聚焦對黃金可能的影響方向。"
        "與金融無關的標題請略過。只輸出條列本身，不要任何開場白或結語。\n\n"
        f"新聞標題：\n{joined}"
    )

    model = pick_gemini_model(token)
    if not model:
        print("[warn] 找不到可用的 Gemini 型號")
        return None

    try:
        from openai import OpenAI
        client = OpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=token,
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )
        print(f"[info] 使用型號: {model}")
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[warn] AI 摘要失敗 (型號 {model}): {e}")
        return None


def pick_gemini_model(token):
    """問 Google 有哪些型號，挑一個支援 generateContent 的 flash 型號。"""
    try:
        import requests
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={token}"
        r = requests.get(url, timeout=20)
        models = r.json().get("models", [])
    except Exception as e:
        print(f"[warn] 無法取得型號清單: {e}")
        return "gemini-flash-latest"  # 退路

    usable = []
    for m in models:
        name = m.get("name", "").replace("models/", "")
        methods = m.get("supportedGenerationMethods", [])
        if "generateContent" in methods and "gemini" in name:
            usable.append(name)

    # 優先順序：latest > flash > 其他，避開已知會擋新用戶的 2.5-flash/2.0-flash
    def score(n):
        s = 0
        if "latest" in n: s += 100
        if "flash" in n: s += 50
        if n in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"): s -= 200
        return s

    usable.sort(key=score, reverse=True)
    print(f"[info] 可用型號候選: {usable[:5]}")
    return usable[0] if usable else None


def summary_to_html(summary):
    if not summary:
        return '<p class="empty">今日暫無 AI 摘要（來源不足或額度用盡）。</p>'
    items = ""
    for line in summary.splitlines():
        line = line.strip()
        if not line:
            continue
        for pref in ("- ", "* ", "\u2022 ", "\u2013 "):
            if line.startswith(pref):
                line = line[len(pref):]
        line = re.sub(r'^\d+[\.\u3001)]\s*', '', line)
        items += f'<li>{html.escape(line)}</li>'
    return f'<ul class="summary">{items}</ul>'


# ── 組合成 HTML ────────────────────────────────────
def render(markets, calendar, news, summary):
    updated = datetime.datetime.now(TW).strftime("%Y-%m-%d %H:%M")

    if markets:
        hlabel, hprice, hchg, hcls = markets[0]
        hero = (f'<div class="hero-label">{html.escape(hlabel)}</div>'
                f'<div class="hero-price">{html.escape(hprice)}</div>'
                f'<div class="hero-chg {hcls}">{html.escape(hchg)}</div>')
        strip = ""
        for label, price, change, cls in markets[1:]:
            strip += (f'<div class="cell"><div class="c-label">{html.escape(label)}</div>'
                      f'<div class="c-val">{html.escape(price)}</div>'
                      f'<div class="c-chg {cls}">{html.escape(change)}</div></div>')
    else:
        hero = ('<div class="hero-label">\u9ec3\u91d1 \u00b7 GOLD</div>'
                '<div class="hero-price">\u2014</div>'
                '<div class="hero-chg flat">\u5e02\u5834\u6578\u64da\u6291\u4e0d\u5230</div>')
        strip = ""

    summ = summary_to_html(summary)

    if calendar is None:
        cal = '<p class="empty">\u4e8b\u4ef6\u62d3\u4e0d\u5230\uff0c\u665a\u9ede\u518d\u770b\u3002</p>'
    elif len(calendar) == 0:
        cal = '<p class="empty">\u4eca\u5929\u6c92\u6709\u9ad8\u5f71\u97ff\u6578\u64da \u2014 \u5834\u5b50\u53ef\u80fd\u504f\u5b89\u975c\u3002</p>'
    else:
        assigned_next = False
        items = ""
        for e in calendar:
            klass = "passed" if e["passed"] else ""
            if not e["passed"] and not assigned_next:
                klass = "next"
                assigned_next = True
            items += (
                f'<li class="ev {klass}">'
                f'<span class="ev-time">{html.escape(e["time"])}</span>'
                f'<span class="ev-ctry">{html.escape(e["country"])}</span>'
                f'<span class="ev-title">{html.escape(e["title"])}</span>'
                f'<span class="ev-data">\u9810\u6e2c {html.escape(str(e["forecast"]))} '
                f'\u00b7 \u524d\u503c {html.escape(str(e["previous"]))}</span>'
                f'</li>'
            )
        cal = f'<ul class="events">{items}</ul>'

    nn = ""
    for name, entries in news:
        li = ""
        for e in entries:
            title = html.escape(e.get("title", "(\u7121\u6a19\u984c)"))
            link = html.escape(e.get("link", "#"))
            li += f'<li><a href="{link}" target="_blank" rel="noopener">{title}</a></li>'
        if not li:
            li = '<li class="empty">\uff08\u9019\u500b\u4f86\u6e90\u76ee\u524d\u62d3\u4e0d\u5230\uff09</li>'
        nn += f'<div class="src"><div class="src-name">{html.escape(name)}</div><ul>{li}</ul></div>'

    out = TEMPLATE
    out = out.replace("%%UPDATED%%", html.escape(updated))
    out = out.replace("%%HERO%%", hero)
    out = out.replace("%%STRIP%%", strip)
    out = out.replace("%%SUMMARY%%", summ)
    out = out.replace("%%CALENDAR%%", cal)
    out = out.replace("%%NEWS%%", nn)
    return out


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>XAU/USD 每日儀表板</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  :root {
    --ink:#141210; --card:#1C1915; --line:rgba(212,175,87,.16);
    --text:#EDE6D6; --muted:#9A9080; --faint:#6B6355;
    --gold:#D4AF57; --gold-hi:#F0D888;
    --up:#63BC93; --down:#DB7A7A;
    --sans:'Space Grotesk',"PingFang TC","Noto Sans TC",system-ui,sans-serif;
    --mono:'JetBrains Mono',ui-monospace,monospace;
  }
  * { box-sizing:border-box; }
  body {
    margin:0; background:var(--ink); color:var(--text);
    font-family:var(--sans); line-height:1.5;
    -webkit-font-smoothing:antialiased;
  }
  .wrap { max-width:680px; margin:0 auto; padding:22px 20px 64px; }

  .topbar { display:flex; justify-content:space-between; align-items:baseline;
            padding-bottom:20px; border-bottom:1px solid var(--line); }
  .brand { font-size:.78rem; letter-spacing:.14em; text-transform:uppercase;
           color:var(--gold); font-weight:600; }
  .stamp { font-family:var(--mono); font-size:.72rem; color:var(--faint); }

  .hero { padding:34px 0 26px; text-align:left; }
  .hero-label { font-size:.8rem; letter-spacing:.1em; color:var(--muted);
                text-transform:uppercase; margin-bottom:6px; }
  .hero-price { font-family:var(--mono); font-weight:700; font-size:4rem;
                line-height:1; letter-spacing:-.02em;
                background:linear-gradient(180deg,var(--gold-hi),var(--gold));
                -webkit-background-clip:text; background-clip:text; color:transparent; }
  .hero-chg { font-family:var(--mono); font-size:1rem; margin-top:10px; }
  .hero-chg.up { color:var(--up); } .hero-chg.down { color:var(--down); }
  .hero-chg.flat { color:var(--muted); }
  .hero-rule { height:1px; margin:4px 0 0;
               background:linear-gradient(90deg,var(--gold),transparent); }

  .strip { display:grid; grid-template-columns:repeat(3,1fr); gap:1px;
           background:var(--line); border:1px solid var(--line);
           border-radius:10px; overflow:hidden; }
  .cell { background:var(--card); padding:14px 14px; }
  .c-label { font-size:.7rem; color:var(--muted); margin-bottom:6px; letter-spacing:.04em; }
  .c-val { font-family:var(--mono); font-size:1.25rem; font-weight:500; }
  .c-chg { font-family:var(--mono); font-size:.72rem; margin-top:3px; color:var(--muted); }
  .c-chg.up { color:var(--up); } .c-chg.down { color:var(--down); }

  h2 { font-size:.82rem; letter-spacing:.12em; text-transform:uppercase;
       color:var(--muted); font-weight:600; margin:42px 0 14px; }

  /* AI 摘要 */
  .summary { list-style:none; margin:0; padding:0;
             background:var(--card); border:1px solid var(--line);
             border-radius:10px; padding:6px 16px; }
  .summary li { position:relative; padding:11px 0 11px 20px; font-size:.95rem;
                border-bottom:1px solid rgba(255,255,255,.05); }
  .summary li:last-child { border-bottom:none; }
  .summary li::before { content:"\\2014"; position:absolute; left:0; color:var(--gold); }

  .events { list-style:none; margin:0; padding:0; }
  .ev { display:grid; grid-template-columns:52px 42px 1fr; gap:4px 12px;
        align-items:baseline; padding:13px 14px; border-radius:8px;
        border:1px solid transparent; }
  .ev + .ev { margin-top:2px; }
  .ev-time { font-family:var(--mono); font-size:.9rem; color:var(--text); }
  .ev-ctry { font-family:var(--mono); font-size:.66rem; color:var(--ink);
             background:var(--muted); border-radius:4px; padding:2px 5px;
             text-align:center; justify-self:start; }
  .ev-title { font-size:.92rem; }
  .ev-data { grid-column:3; font-family:var(--mono); font-size:.72rem;
             color:var(--faint); margin-top:2px; }
  .ev.next { border-color:var(--line); background:var(--card); }
  .ev.next .ev-time { color:var(--gold); }
  .ev.next .ev-ctry { background:var(--gold); }
  .ev.passed { opacity:.4; }
  .empty { color:var(--muted); font-size:.9rem; padding:6px 0; }

  .src { margin-bottom:22px; }
  .src-name { font-family:var(--mono); font-size:.72rem; color:var(--gold);
              letter-spacing:.06em; margin-bottom:8px; }
  .src ul { list-style:none; margin:0; padding:0; }
  .src li { padding:7px 0; border-bottom:1px solid rgba(255,255,255,.04); }
  .src a { color:var(--text); text-decoration:none; font-size:.94rem;
           border-bottom:1px solid transparent; transition:border-color .15s,color .15s; }
  .src a:hover { color:var(--gold-hi); border-color:var(--gold); }
  .src a:focus-visible { outline:2px solid var(--gold); outline-offset:3px; }

  .foot { margin-top:48px; padding-top:18px; border-top:1px solid var(--line);
          font-size:.74rem; color:var(--faint); line-height:1.7; }
  .foot a { color:var(--muted); }

  .wrap > * { animation:rise .5s ease both; }
  .hero { animation-delay:.05s; } .strip { animation-delay:.1s; }
  @keyframes rise { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:none; } }
  @media (prefers-reduced-motion:reduce) { .wrap > * { animation:none; } }

  @media (max-width:420px) {
    .hero-price { font-size:3rem; }
    .strip { grid-template-columns:1fr; }
  }
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <span class="brand">XAU/USD \u00b7 \u6bcf\u65e5\u5100\u8868\u677f</span>
    <span class="stamp">%%UPDATED%% TPE</span>
  </div>

  <div class="hero">%%HERO%%</div>
  <div class="hero-rule"></div>

  <div class="strip" style="margin-top:26px;">%%STRIP%%</div>

  <h2>AI \u91cd\u9ede\u6458\u8981</h2>
  %%SUMMARY%%

  <h2>\u4eca\u65e5\u9ad8\u5f71\u97ff\u6578\u64da</h2>
  %%CALENDAR%%

  <h2>\u5e02\u5834\u65b0\u805e</h2>
  %%NEWS%%

  <div class="foot">
    Fed \u5347\u964d\u606f\u6a5f\u7387\uff1a<a href="https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html" target="_blank" rel="noopener">CME FedWatch</a><br>
    \u8cc7\u6599\u81ea\u52d5\u62d3\u53d6\uff0cAI \u6458\u8981\u50c5\u4f9b\u53c3\u8003\uff0c\u4e0d\u69cb\u6210\u4ea4\u6613\u5efa\u8b70\u3002
  </div>
</div>
</body>
</html>
"""


def safe(fn, fallback, *args):
    try:
        return fn(*args)
    except Exception as e:
        print(f"[warn] {fn.__name__} \u5931\u6557: {e}")
        return fallback


if __name__ == "__main__":
    markets = safe(get_markets, [])
    calendar = safe(get_calendar, None)
    news = safe(get_news, [])
    summary = safe(get_summary, None, news)
    out = render(markets, calendar, news, summary)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(out)
    print("index.html \u5df2\u7522\u751f")
