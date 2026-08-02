#!/usr/bin/env python3
"""每天抓 黃金交易數據 + 新聞 + AI 摘要 + AI 技術雷達，產生 index.html。"""
import datetime
import html
import json
import os
import re

TW = datetime.timezone(datetime.timedelta(hours=8))


# ── 1. 市場數據 ─────────────────────────────
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
            last = float(closes.iloc[-1]); prev = float(closes.iloc[-2])
            chg = last - prev; pct = chg / prev * 100 if prev else 0
            arrow = "\u25B2" if chg >= 0 else "\u25BC"
            cls = "up" if chg >= 0 else "down"
            price = f"{last:,.{digits}f}"
            change = f"{arrow} {abs(chg):.{digits}f} ({pct:+.2f}%)"
            rows.append((label, price, change, cls))
        except Exception as e:
            print(f"[warn] {label} ({sym}) 抓取失敗: {e}")
            rows.append((label, "\u2014", "\u2014", "flat"))
    return rows


# ── 2. 經濟數據行事曆 ─────────────
def get_calendar():
    try:
        import requests
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        data = r.json()
    except Exception:
        return None
    now = datetime.datetime.now(TW); today = now.date()
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
            "time": dt.strftime("%H:%M"), "passed": dt < now,
            "country": ev.get("country", ""), "title": ev.get("title", ""),
            "forecast": ev.get("forecast", "") or "-", "previous": ev.get("previous", "") or "-",
        })
    events.sort(key=lambda x: x["time"])
    return events


# ── 3. 市場新聞（RSS） ─────────────
FEEDS = [
    ("BBC World", "http://feeds.bbci.co.uk/news/world/rss.xml"),
    ("中央社 國際", "https://feeds.feedburner.com/rsscna/intworld"),
]
PER_FEED = 6


def get_news():
    try:
        import feedparser
    except Exception:
        return []
    out = []
    for name, url in FEEDS:
        try:
            entries = feedparser.parse(url).entries[:PER_FEED]
        except Exception:
            entries = []
        out.append((name, entries))
    return out


# ── 4. Hacker News 熱門技術討論 ─────────────
def get_hackernews(limit=12):
    try:
        import requests
        url = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=30"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        hits = r.json().get("hits", [])
    except Exception as e:
        print(f"[warn] Hacker News 抓取失敗: {e}")
        return []
    stories = []
    for h in hits:
        title = h.get("title")
        if not title:
            continue
        oid = h.get("objectID")
        stories.append({
            "title": title,
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={oid}",
            "points": h.get("points") or 0,
            "comments": h.get("num_comments") or 0,
            "hn_url": f"https://news.ycombinator.com/item?id={oid}",
        })
    stories.sort(key=lambda s: s["points"], reverse=True)
    return stories[:limit]


# ── 5. Gemini（AI 摘要 + 技術雷達共用） ─────────────
def pick_gemini_model(token):
    try:
        import requests
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={token}"
        models = requests.get(url, timeout=20).json().get("models", [])
    except Exception as e:
        print(f"[warn] 無法取得型號清單: {e}")
        return "gemini-flash-latest"
    usable = []
    for m in models:
        name = m.get("name", "").replace("models/", "")
        if "generateContent" in m.get("supportedGenerationMethods", []) and "gemini" in name:
            usable.append(name)

    def score(n):
        s = 0
        if "latest" in n: s += 100
        if "flash" in n: s += 50
        if n in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"): s -= 200
        return s

    usable.sort(key=score, reverse=True)
    print(f"[info] 可用型號候選: {usable[:5]}")
    return usable[0] if usable else None


def gemini_chat(token, model, prompt):
    if not token or not model:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(base_url="https://generativelanguage.googleapis.com/v1beta/openai/", api_key=token)
        resp = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}], temperature=0.4)
        print(f"[info] Gemini 使用型號: {model}")
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[warn] Gemini 呼叫失敗 (型號 {model}): {e}")
        return None


def get_summary(news, token, model):
    titles = [f"- {e.get('title')}" for _, ents in news for e in ents if e.get("title")]
    if not titles:
        return None
    prompt = (
        "你是協助黃金(XAU/USD)短波段交易者的助理。以下是今天的新聞標題，"
        "請挑出與黃金、美元、利率、通膨、地緣政治、避險情緒相關的內容，"
        "用繁體中文整理成 3 到 5 條精簡重點，每條一行，聚焦對黃金可能的影響方向。"
        "與金融無關的請略過。只輸出條列本身，不要開場白。\n\n新聞標題：\n"
        + "\n".join(titles[:40])
    )
    return gemini_chat(token, model, prompt)


def get_tech_summary(hn, token, model):
    if not hn:
        return None
    titles = "\n".join(f"- {s['title']} ({s['points']} pts)" for s in hn)
    prompt = (
        "以下是 Hacker News 目前最熱門的技術討論標題。請挑出與 AI、LLM、機器學習、"
        "軟體工程、開發工具、重大新技術或新產品發布相關、且值得工程師注意的項目，"
        "用繁體中文整理成 3 到 6 條。每條用一句話說明：這是什麼、以及為什麼值得注意。"
        "與技術無關的（政治、生活等）請略過。只輸出條列，不要開場白。\n\n標題清單：\n"
        + titles
    )
    return gemini_chat(token, model, prompt)


def summary_to_html(summary, empty_msg):
    if not summary:
        return f'<p class="empty">{html.escape(empty_msg)}</p>'
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


# ── 組合 HTML ─────────────
def render(markets, calendar, news, summary, hn, tech):
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

    summ = summary_to_html(summary, "今日暫無 AI 摘要（來源不足或額度用盡）。")
    techsum = summary_to_html(tech, "今日暫無技術重點。")

    if calendar is None:
        cal = '<p class="empty">\u4e8b\u4ef6\u62d3\u4e0d\u5230\uff0c\u665a\u9ede\u518d\u770b\u3002</p>'
    elif len(calendar) == 0:
        cal = '<p class="empty">\u4eca\u5929\u6c92\u6709\u9ad8\u5f71\u97ff\u6578\u64da \u2014 \u5834\u5b50\u53ef\u80fd\u504f\u5b89\u975c\u3002</p>'
    else:
        assigned = False; items = ""
        for e in calendar:
            klass = "passed" if e["passed"] else ""
            if not e["passed"] and not assigned:
                klass = "next"; assigned = True
            items += (f'<li class="ev {klass}"><span class="ev-time">{html.escape(e["time"])}</span>'
                      f'<span class="ev-ctry">{html.escape(e["country"])}</span>'
                      f'<span class="ev-title">{html.escape(e["title"])}</span>'
                      f'<span class="ev-data">\u9810\u6e2c {html.escape(str(e["forecast"]))} '
                      f'\u00b7 \u524d\u503c {html.escape(str(e["previous"]))}</span></li>')
        cal = f'<ul class="events">{items}</ul>'

    # 技術雷達熱門清單
    if hn:
        ri = ""
        for s in hn:
            ri += (f'<li><a href="{html.escape(s["url"])}" target="_blank" rel="noopener">{html.escape(s["title"])}</a>'
                   f'<span class="radar-meta">\u25B2 {s["points"]} \u00b7 '
                   f'<a href="{html.escape(s["hn_url"])}" target="_blank" rel="noopener">{s["comments"]} \u5247\u8a0e\u8ad6</a></span></li>')
        radar = f'<ul class="radar">{ri}</ul>'
    else:
        radar = '<p class="empty">\uff08\u6280\u8853\u52d5\u614b\u62d3\u4e0d\u5230\uff09</p>'

    nn = ""
    for name, entries in news:
        li = ""
        for e in entries:
            li += (f'<li><a href="{html.escape(e.get("link", "#"))}" target="_blank" rel="noopener">'
                   f'{html.escape(e.get("title", "(\u7121\u6a19\u984c)"))}</a></li>')
        if not li:
            li = '<li class="empty">\uff08\u9019\u500b\u4f86\u6e90\u76ee\u524d\u62d3\u4e0d\u5230\uff09</li>'
        nn += f'<div class="src"><div class="src-name">{html.escape(name)}</div><ul>{li}</ul></div>'

    out = TEMPLATE
    for k, v in [("%%UPDATED%%", html.escape(updated)), ("%%HERO%%", hero), ("%%STRIP%%", strip),
                 ("%%SUMMARY%%", summ), ("%%CALENDAR%%", cal), ("%%TECHSUM%%", techsum),
                 ("%%RADAR%%", radar), ("%%NEWS%%", nn)]:
        out = out.replace(k, v)
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
    --gold:#D4AF57; --gold-hi:#F0D888; --up:#63BC93; --down:#DB7A7A;
    --sans:'Space Grotesk',"PingFang TC","Noto Sans TC",system-ui,sans-serif;
    --mono:'JetBrains Mono',ui-monospace,monospace;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--ink); color:var(--text); font-family:var(--sans);
         line-height:1.5; -webkit-font-smoothing:antialiased; }
  .wrap { max-width:680px; margin:0 auto; padding:22px 20px 64px; }
  .topbar { display:flex; justify-content:space-between; align-items:baseline;
            padding-bottom:20px; border-bottom:1px solid var(--line); }
  .brand { font-size:.78rem; letter-spacing:.14em; text-transform:uppercase; color:var(--gold); font-weight:600; }
  .stamp { font-family:var(--mono); font-size:.72rem; color:var(--faint); }
  .hero { padding:34px 0 26px; }
  .hero-label { font-size:.8rem; letter-spacing:.1em; color:var(--muted); text-transform:uppercase; margin-bottom:6px; }
  .hero-price { font-family:var(--mono); font-weight:700; font-size:4rem; line-height:1; letter-spacing:-.02em;
                background:linear-gradient(180deg,var(--gold-hi),var(--gold));
                -webkit-background-clip:text; background-clip:text; color:transparent; }
  .hero-chg { font-family:var(--mono); font-size:1rem; margin-top:10px; }
  .hero-chg.up { color:var(--up); } .hero-chg.down { color:var(--down); } .hero-chg.flat { color:var(--muted); }
  .hero-rule { height:1px; background:linear-gradient(90deg,var(--gold),transparent); }
  .strip { display:grid; grid-template-columns:repeat(3,1fr); gap:1px; background:var(--line);
           border:1px solid var(--line); border-radius:10px; overflow:hidden; margin-top:26px; }
  .cell { background:var(--card); padding:14px; }
  .c-label { font-size:.7rem; color:var(--muted); margin-bottom:6px; letter-spacing:.04em; }
  .c-val { font-family:var(--mono); font-size:1.25rem; font-weight:500; }
  .c-chg { font-family:var(--mono); font-size:.72rem; margin-top:3px; color:var(--muted); }
  .c-chg.up { color:var(--up); } .c-chg.down { color:var(--down); }
  h2 { font-size:.82rem; letter-spacing:.12em; text-transform:uppercase; color:var(--muted);
       font-weight:600; margin:42px 0 14px; }
  .summary { list-style:none; margin:0; padding:6px 16px; background:var(--card);
             border:1px solid var(--line); border-radius:10px; }
  .summary li { position:relative; padding:11px 0 11px 20px; font-size:.95rem; border-bottom:1px solid rgba(255,255,255,.05); }
  .summary li:last-child { border-bottom:none; }
  .summary li::before { content:"\\2014"; position:absolute; left:0; color:var(--gold); }
  .events { list-style:none; margin:0; padding:0; }
  .ev { display:grid; grid-template-columns:52px 42px 1fr; gap:4px 12px; align-items:baseline;
        padding:13px 14px; border-radius:8px; border:1px solid transparent; }
  .ev + .ev { margin-top:2px; }
  .ev-time { font-family:var(--mono); font-size:.9rem; }
  .ev-ctry { font-family:var(--mono); font-size:.66rem; color:var(--ink); background:var(--muted);
             border-radius:4px; padding:2px 5px; text-align:center; justify-self:start; }
  .ev-title { font-size:.92rem; }
  .ev-data { grid-column:3; font-family:var(--mono); font-size:.72rem; color:var(--faint); margin-top:2px; }
  .ev.next { border-color:var(--line); background:var(--card); }
  .ev.next .ev-time { color:var(--gold); } .ev.next .ev-ctry { background:var(--gold); }
  .ev.passed { opacity:.4; }
  .empty { color:var(--muted); font-size:.9rem; padding:6px 0; }
  .radar-sub { font-family:var(--mono); font-size:.72rem; color:var(--gold); letter-spacing:.06em; margin:20px 0 8px; }
  .radar { list-style:none; margin:0; padding:0; }
  .radar li { padding:9px 0; border-bottom:1px solid rgba(255,255,255,.04); }
  .radar a { color:var(--text); text-decoration:none; font-size:.94rem; border-bottom:1px solid transparent;
             transition:border-color .15s,color .15s; }
  .radar a:hover { color:var(--gold-hi); border-color:var(--gold); }
  .radar-meta { display:block; font-family:var(--mono); font-size:.7rem; color:var(--faint); margin-top:3px; }
  .radar-meta a { color:var(--faint); border:none; } .radar-meta a:hover { color:var(--gold); }
  .src { margin-bottom:22px; }
  .src-name { font-family:var(--mono); font-size:.72rem; color:var(--gold); letter-spacing:.06em; margin-bottom:8px; }
  .src ul { list-style:none; margin:0; padding:0; }
  .src li { padding:7px 0; border-bottom:1px solid rgba(255,255,255,.04); }
  .src a { color:var(--text); text-decoration:none; font-size:.94rem; border-bottom:1px solid transparent;
           transition:border-color .15s,color .15s; }
  .src a:hover { color:var(--gold-hi); border-color:var(--gold); }
  .foot { margin-top:48px; padding-top:18px; border-top:1px solid var(--line);
          font-size:.74rem; color:var(--faint); line-height:1.7; }
  .foot a { color:var(--muted); }
  .wrap > * { animation:rise .5s ease both; }
  @keyframes rise { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:none; } }
  @media (prefers-reduced-motion:reduce) { .wrap > * { animation:none; } }
  @media (max-width:420px) { .hero-price { font-size:3rem; } .strip { grid-template-columns:1fr; } }
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
  <div class="strip">%%STRIP%%</div>

  <h2>AI \u91cd\u9ede\u6458\u8981\uff08\u91d1\u878d\uff09</h2>
  %%SUMMARY%%

  <h2>\u4eca\u65e5\u9ad8\u5f71\u97ff\u6578\u64da</h2>
  %%CALENDAR%%

  <h2>AI \u6280\u8853\u96f7\u9054</h2>
  %%TECHSUM%%
  <div class="radar-sub">\u71b1\u9580\u8a0e\u8ad6 \u00b7 HACKER NEWS</div>
  %%RADAR%%

  <h2>\u5e02\u5834\u65b0\u805e</h2>
  %%NEWS%%

  <div class="foot">
    <a href="history.html">\u2192 \u6b77\u53f2\u7d00\u9304</a><br>
    Fed \u5347\u964d\u606f\u6a5f\u7387\uff1a<a href="https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html" target="_blank" rel="noopener">CME FedWatch</a><br>
    \u8cc7\u6599\u81ea\u52d5\u62d3\u53d6\uff0cAI \u6458\u8981\u50c5\u4f9b\u53c3\u8003\uff0c\u4e0d\u69cb\u6210\u4ea4\u6613\u5efa\u8b70\u3002
  </div>
</div>
</body>
</html>
"""


# ── 歷史快照 + 歷史頁 ─────────────
def save_snapshot(markets, calendar, news, summary, hn, tech):
    os.makedirs("data", exist_ok=True)
    date = datetime.datetime.now(TW).strftime("%Y-%m-%d")

    def parse_val(p):
        try:
            return float(str(p).replace(",", ""))
        except Exception:
            return None

    snap = {
        "date": date,
        "updated": datetime.datetime.now(TW).strftime("%Y-%m-%d %H:%M"),
        "markets": [{"label": l, "price": pr, "change": ch, "cls": cl, "value": parse_val(pr)}
                    for (l, pr, ch, cl) in markets],
        "calendar": calendar,
        "summary": summary,
        "tech": tech,
        "hackernews": [{"title": s.get("title"), "url": s.get("url"),
                        "points": s.get("points"), "comments": s.get("comments")} for s in hn],
    }
    with open(f"data/{date}.json", "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    print(f"快照已存: data/{date}.json")


def _bullets(text):
    if not text:
        return '<p class="empty">\u7121</p>'
    items = ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for p in ("- ", "* ", "\u2022 ", "\u2013 "):
            if line.startswith(p):
                line = line[len(p):]
        line = re.sub(r'^\d+[\.\u3001)]\s*', '', line)
        items += f'<li class="bullet">{html.escape(line)}</li>'
    return f'<ul>{items}</ul>'


def _render_day(d):
    date = d.get("date", "")
    markets = d.get("markets", [])
    gold = markets[0] if markets else {}
    gprice = gold.get("price", "\u2014")
    gcls = gold.get("cls", "flat")
    gchg = gold.get("change", "")

    mrows = ""
    for m in markets:
        mrows += (f'<div class="mrow"><span>{html.escape(m.get("label",""))}</span>'
                  f'<span class="{m.get("cls","")}">{html.escape(m.get("price","\u2014"))}\u3000'
                  f'{html.escape(m.get("change",""))}</span></div>')

    fin = _bullets(d.get("summary"))
    tech = _bullets(d.get("tech"))

    hn = d.get("hackernews", [])
    hnli = "".join(
        f'<li><a href="{html.escape(s.get("url") or "#")}" target="_blank" rel="noopener">'
        f'{html.escape(s.get("title") or "")}</a></li>' for s in hn)
    hnli = hnli or '<li class="empty">\u7121</li>'

    ev = d.get("calendar")
    if ev:
        evli = "".join(
            f'<li>{html.escape(e.get("time",""))} {html.escape(e.get("country",""))} \u00b7 '
            f'{html.escape(e.get("title",""))}</li>' for e in ev)
    else:
        evli = '<li class="empty">\u7576\u5929\u7121\u9ad8\u5f71\u97ff\u4e8b\u4ef6</li>'

    return (
        f'<details><summary><span class="d-date">{html.escape(date)}</span>'
        f'<span class="d-gold {gcls}">{html.escape(gprice)} {html.escape(gchg)}</span></summary>'
        f'<div class="day">'
        f'<h3>\u5e02\u5834\u6578\u64da</h3>{mrows}'
        f'<h3>AI \u91d1\u878d\u6458\u8981</h3>{fin}'
        f'<h3>AI \u6280\u8853\u96f7\u9054</h3>{tech}<ul>{hnli}</ul>'
        f'<h3>\u7d93\u6fdf\u4e8b\u4ef6</h3><ul>{evli}</ul>'
        f'</div></details>'
    )


def build_history():
    import glob
    files = sorted(glob.glob("data/*.json"), reverse=True)
    blocks = ""
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                blocks += _render_day(json.load(f))
        except Exception as e:
            print(f"[warn] 讀取 {fp} 失敗: {e}")
    if not blocks:
        blocks = '<p class="empty">\u9084\u6c92\u6709\u4efb\u4f55\u6b77\u53f2\u7d00\u9304\u3002</p>'
    out = HISTORY_TEMPLATE.replace("%%DAYS%%", blocks)
    with open("history.html", "w", encoding="utf-8") as f:
        f.write(out)
    print(f"history.html 已產生（{len(files)} 天）")


HISTORY_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>\u6b77\u53f2\u7d00\u9304 \u00b7 XAU/USD</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{--ink:#141210;--card:#1C1915;--line:rgba(212,175,87,.16);--text:#EDE6D6;--muted:#9A9080;--faint:#6B6355;--gold:#D4AF57;--gold-hi:#F0D888;--up:#63BC93;--down:#DB7A7A;--sans:'Space Grotesk',"PingFang TC","Noto Sans TC",system-ui,sans-serif;--mono:'JetBrains Mono',ui-monospace,monospace;}
  *{box-sizing:border-box;} body{margin:0;background:var(--ink);color:var(--text);font-family:var(--sans);line-height:1.5;}
  .wrap{max-width:680px;margin:0 auto;padding:22px 20px 64px;}
  .topbar{display:flex;justify-content:space-between;align-items:baseline;padding-bottom:20px;border-bottom:1px solid var(--line);}
  .brand{font-size:.78rem;letter-spacing:.14em;text-transform:uppercase;color:var(--gold);font-weight:600;}
  .back{font-family:var(--mono);font-size:.72rem;color:var(--muted);text-decoration:none;} .back:hover{color:var(--gold);}
  h1{font-size:1.1rem;margin:26px 0 6px;} .hint{color:var(--faint);font-size:.78rem;margin-bottom:20px;}
  details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin-bottom:10px;overflow:hidden;}
  summary{cursor:pointer;padding:14px 16px;font-family:var(--mono);font-size:.9rem;list-style:none;display:flex;justify-content:space-between;align-items:baseline;gap:12px;}
  summary::-webkit-details-marker{display:none;}
  .d-date{color:var(--text);} .d-gold{color:var(--gold);} .up{color:var(--up);} .down{color:var(--down);} .flat{color:var(--muted);}
  .day{padding:4px 16px 18px;border-top:1px solid var(--line);}
  .day h3{font-size:.72rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin:16px 0 8px;}
  .mrow{display:flex;justify-content:space-between;font-family:var(--mono);font-size:.82rem;padding:5px 0;border-bottom:1px solid rgba(255,255,255,.04);}
  .day ul{list-style:none;margin:0;padding:0;} .day li{padding:5px 0;font-size:.88rem;}
  .day a{color:var(--text);text-decoration:none;border-bottom:1px solid transparent;} .day a:hover{color:var(--gold-hi);border-color:var(--gold);}
  .bullet{position:relative;padding-left:16px;} .bullet::before{content:"\\2014";position:absolute;left:0;color:var(--gold);}
  .empty{color:var(--muted);font-size:.85rem;}
</style></head>
<body><div class="wrap">
  <div class="topbar"><span class="brand">XAU/USD \u00b7 \u6b77\u53f2\u7d00\u9304</span><a class="back" href="index.html">\u2190 \u56de\u4eca\u65e5</a></div>
  <h1>\u6bcf\u65e5\u5feb\u7167</h1>
  <div class="hint">\u9ede\u65e5\u671f\u5c55\u958b\u7576\u5929\u7684\u5b8c\u6574\u7d00\u9304\u3002\u8cc7\u6599\u5f9e\u5efa\u7acb\u6b64\u529f\u80fd\u7576\u5929\u8d77\u7d2f\u7a4d\u3002</div>
  %%DAYS%%
</div></body></html>
"""


def safe(fn, fallback, *args):
    try:
        return fn(*args)
    except Exception as e:
        print(f"[warn] {fn.__name__} \u5931\u6557: {e}")
        return fallback


if __name__ == "__main__":
    token = os.environ.get("GEMINI_API_KEY")
    model = pick_gemini_model(token) if token else None
    if not token:
        print("[warn] 沒有 GEMINI_API_KEY，AI 區塊將略過")
    markets = safe(get_markets, [])
    calendar = safe(get_calendar, None)
    news = safe(get_news, [])
    hn = safe(get_hackernews, [])
    summary = safe(get_summary, None, news, token, model)
    tech = safe(get_tech_summary, None, hn, token, model)
    out = render(markets, calendar, news, summary, hn, tech)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(out)
    print("index.html \u5df2\u7522\u751f")
    safe(save_snapshot, None, markets, calendar, news, summary, hn, tech)
    safe(build_history, None)
