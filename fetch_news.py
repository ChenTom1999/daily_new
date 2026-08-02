#!/usr/bin/env python3
"""每天抓 RSS 新聞，產生 index.html。"""
import datetime
import html
import feedparser

# 想追的來源，自己改。格式：("顯示名稱", "RSS 網址")
FEEDS = [
    ("BBC World", "http://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Hacker News", "https://hnrss.org/frontpage"),
    ("中央社 國際", "https://feeds.feedburner.com/rsscna/intworld"),
]

PER_FEED = 8  # 每個來源最多顯示幾則


def collect():
    sections = []
    for name, url in FEEDS:
        try:
            parsed = feedparser.parse(url)
            entries = parsed.entries[:PER_FEED]
        except Exception:
            entries = []
        sections.append((name, entries))
    return sections


def render(sections):
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)  # 台北時間
    updated = now.strftime("%Y-%m-%d %H:%M")
    parts = []
    for name, entries in sections:
        items = ""
        for e in entries:
            title = html.escape(e.get("title", "(無標題)"))
            link = html.escape(e.get("link", "#"))
            items += f'<li><a href="{link}" target="_blank" rel="noopener">{title}</a></li>\n'
        if not items:
            items = "<li>（這個來源目前抓不到）</li>"
        parts.append(f'<section><h2>{html.escape(name)}</h2><ul>{items}</ul></section>')
    body = "\n".join(parts)
    return TEMPLATE.format(updated=updated, body=body)


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>我的每日新聞</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, -apple-system, "Noto Sans TC", sans-serif;
         max-width: 720px; margin: 0 auto; padding: 16px 18px 48px; line-height: 1.6; }}
  h1 {{ font-size: 1.4rem; margin: 8px 0 2px; }}
  .updated {{ color: #888; font-size: .85rem; margin-bottom: 24px; }}
  section {{ margin-bottom: 28px; }}
  h2 {{ font-size: 1.05rem; border-bottom: 2px solid currentColor; padding-bottom: 4px; }}
  ul {{ list-style: none; padding: 0; margin: 0; }}
  li {{ margin: 10px 0; }}
  a {{ text-decoration: none; }}
  a:active {{ opacity: .6; }}
</style>
</head>
<body>
  <h1>我的每日新聞</h1>
  <div class="updated">更新時間：{updated}（台北）</div>
  {body}
</body>
</html>
"""

if __name__ == "__main__":
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(render(collect()))
    print("index.html 已產生")
