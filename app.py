"""
Threads 文分類收藏器 v2
Cloud Run + Notion（單一資料庫）

- 貼連結 → 自動分類直接存入
- #新分類 → Notion Select 自動新增
- 2 分鐘內回覆 #分類 或文字 → 改最新一筆的分類或標題
- 不需要額外資料庫，不需要 pending 機制
"""

import os
import re
import json
import hashlib
import hmac
import html as html_mod
import base64
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from flask import Flask, request, abort

app = Flask(__name__)

# ===== 環境變數（只要 4 個）=====
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

CONFIRM_TIMEOUT = 120  # 2 分鐘

# ===== 內建關鍵字分類規則 =====
DEFAULT_KEYWORDS = {
    "科技": ["AI", "人工智慧", "程式", "coding", "app", "軟體", "科技", "tech",
             "iPhone", "Android", "API", "開發", "工程師", "Python", "JavaScript",
             "機器學習", "deep learning", "GPT", "Claude", "LLM", "blockchain",
             "區塊鏈", "加密", "crypto", "web3"],
    "投資理財": ["股票", "投資", "ETF", "基金", "理財", "房地產", "股市", "美股",
                "台股", "加密貨幣", "bitcoin", "被動收入", "財務自由", "存錢",
                "信用卡", "保險", "貸款", "報酬率"],
    "生活": ["美食", "旅遊", "咖啡", "餐廳", "料理", "食譜", "景點", "飯店",
            "穿搭", "OOTD", "居家", "佈置", "生活", "日常", "開箱", "好物推薦"],
    "健康": ["健身", "運動", "減脂", "增肌", "飲食", "營養", "跑步", "瑜珈",
            "睡眠", "心理", "冥想", "壓力", "健康", "醫療", "保健"],
    "職涯": ["面試", "履歷", "求職", "職涯", "工作", "轉職", "副業", "自由工作",
            "遠端", "remote", "薪水", "談薪", "管理", "領導", "創業", "startup"],
    "學習": ["學習", "讀書", "筆記", "課程", "英文", "日文", "考試", "自學",
            "知識", "書單", "閱讀", "生產力", "效率", "時間管理"],
    "育兒": ["育兒", "小孩", "寶寶", "嬰兒", "幼兒", "親子", "教養", "副食品",
            "奶粉", "尿布", "托嬰", "幼稚園", "幼兒園", "懷孕", "孕期",
            "產後", "母乳", "哺乳", "學齡前", "兒童", "媽媽", "爸爸",
            "親職", "胎教", "坐月子", "月子"],
}


# =====================================================
# Notion 操作
# =====================================================

def get_latest_bookmark() -> dict | None:
    """取得最新一筆收藏，如果在 2 分鐘內就回傳"""
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "sorts": [{"property": "收藏時間", "direction": "descending"}],
        "page_size": 1,
    }
    resp = requests.post(url, headers=NOTION_HEADERS, json=payload)
    if resp.status_code != 200:
        return None
    results = resp.json().get("results", [])
    if not results:
        return None

    page = results[0]
    props = page["properties"]

    # 檢查是否在 2 分鐘內
    date_prop = props.get("收藏時間", {}).get("date", {})
    if not date_prop or not date_prop.get("start"):
        return None
    created = datetime.fromisoformat(date_prop["start"].replace("Z", "+00:00"))
    if (datetime.now(timezone.utc) - created).total_seconds() > CONFIRM_TIMEOUT:
        return None

    cat = props.get("分類", {}).get("select", {})
    t = props.get("名稱", {}).get("title", [])
    return {
        "page_id": page["id"],
        "title": t[0]["text"]["content"] if t else "無",
        "category": cat.get("name", "未分類") if cat else "未分類",
    }


def classify_content(text: str) -> str:
    text_lower = text.lower()
    scores = {}
    for category, keywords in DEFAULT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in text_lower)
        if score > 0:
            scores[category] = score
    if scores:
        return max(scores, key=scores.get)
    return "其他"


def extract_manual_tag(text: str) -> str | None:
    """提取 #標籤，任何標籤都接受，新的會由 Notion 自動建立"""
    match = re.search(r"[#＃]\s*(\S+)", text)
    if match:
        tag = match.group(1)
        if len(tag) >= 1 and not tag.isdigit():
            return tag
    return None


def extract_url(text: str) -> str | None:
    pattern = r"https?://[^\s]+"
    match = re.search(pattern, text)
    if match:
        return re.split(r"[?]", match.group(0))[0]
    return None


def fetch_page_content(url: str) -> dict:
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            )
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        html = resp.text

        og_desc = re.search(
            r'<meta\s+(?:property|name)="og:description"\s+content="([^"]*)"', html
        )
        description = html_mod.unescape(og_desc.group(1)) if og_desc else ""

        og_title = re.search(
            r'<meta\s+(?:property|name)="og:title"\s+content="([^"]*)"', html
        )
        html_title = re.search(r"<title>([^<]*)</title>", html)
        title = ""
        if og_title:
            title = og_title.group(1)
        elif html_title:
            title = html_title.group(1)
        title = html_mod.unescape(title)

        author = ""
        m = re.search(r"threads\.(?:net|com)/@([^/]+)", url)
        if m:
            author = f"@{m.group(1)}"

        domain = urlparse(url).netloc.replace("www.", "")
        return {"title": title or author or domain, "description": description,
                "domain": domain, "url": url}
    except Exception:
        domain = urlparse(url).netloc.replace("www.", "")
        m = re.search(r"threads\.(?:net|com)/@([^/]+)", url)
        author = f"@{m.group(1)}" if m else ""
        return {"title": author or domain, "description": "",
                "domain": domain, "url": url}


def save_to_notion(title: str, category: str, url: str, summary: str) -> dict:
    notion_url = "https://api.notion.com/v1/pages"
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "名稱": {"title": [{"text": {"content": title[:100]}}]},
            "分類": {"select": {"name": category}},
            "連結": {"url": url} if url else {"url": None},
            "內容摘要": {"rich_text": [{"text": {"content": summary[:2000]}}]},
            "收藏時間": {"date": {"start": now}},
        },
    }
    resp = requests.post(notion_url, headers=NOTION_HEADERS, json=payload)
    if resp.status_code == 200:
        return {"ok": True, "page_id": resp.json().get("id", "")}
    return {"ok": False, "status": resp.status_code, "error": resp.text[:200]}


def update_notion_page(page_id: str, updates: dict) -> bool:
    url = f"https://api.notion.com/v1/pages/{page_id}"
    properties = {}
    if "title" in updates:
        properties["名稱"] = {"title": [{"text": {"content": updates["title"][:100]}}]}
    if "category" in updates:
        properties["分類"] = {"select": {"name": updates["category"]}}
    resp = requests.patch(url, headers=NOTION_HEADERS, json={"properties": properties})
    return resp.status_code == 200


def search_notion(keyword: str) -> list:
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"

    # 先嘗試分類搜尋
    payload = {
        "filter": {"property": "分類", "select": {"equals": keyword}},
        "sorts": [{"property": "收藏時間", "direction": "descending"}],
        "page_size": 10,
    }
    resp = requests.post(url, headers=NOTION_HEADERS, json=payload)
    data = resp.json().get("results", []) if resp.status_code == 200 else []

    # 沒結果就用關鍵字搜尋
    if not data:
        payload = {
            "filter": {
                "or": [
                    {"property": "名稱", "title": {"contains": keyword}},
                    {"property": "內容摘要", "rich_text": {"contains": keyword}},
                ]
            },
            "sorts": [{"property": "收藏時間", "direction": "descending"}],
            "page_size": 10,
        }
        resp = requests.post(url, headers=NOTION_HEADERS, json=payload)
        data = resp.json().get("results", []) if resp.status_code == 200 else []

    results = []
    for page in data:
        props = page["properties"]
        t = props.get("名稱", {}).get("title", [])
        title = t[0]["text"]["content"] if t else "無標題"
        c = props.get("分類", {}).get("select", {})
        category = c.get("name", "未分類") if c else "未分類"
        link = props.get("連結", {}).get("url", "")
        results.append({"title": title, "category": category, "url": link})
    return results


def get_all_category_names() -> list:
    """從收藏資料庫的 Select 欄位讀取所有分類"""
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}"
    resp = requests.get(url, headers=NOTION_HEADERS)
    if resp.status_code != 200:
        return sorted(list(DEFAULT_KEYWORDS.keys()) + ["其他"])
    props = resp.json().get("properties", {})
    options = props.get("分類", {}).get("select", {}).get("options", [])
    names = [o["name"] for o in options]
    for k in DEFAULT_KEYWORDS:
        if k not in names:
            names.append(k)
    if "其他" not in names:
        names.append("其他")
    return sorted(names)


# =====================================================
# LINE 訊息處理
# =====================================================

def verify_signature(body: str, signature: str) -> bool:
    h = hmac.new(LINE_CHANNEL_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(h).decode(), signature)


def reply_message(reply_token: str, text: str):
    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"},
        json={"replyToken": reply_token,
              "messages": [{"type": "text", "text": text}]},
    )


def handle_message(event: dict):
    reply_token = event["replyToken"]
    text = event["message"].get("text", "").strip()
    if not text:
        return

    # ===== 查詢 =====
    if text.startswith("找 ") or text.startswith("找"):
        keyword = text.replace("找 ", "").replace("找", "").strip()
        if not keyword:
            reply_message(reply_token, "請輸入要搜尋的分類或關鍵字\n例如：找 科技")
            return
        results = search_notion(keyword)
        if not results:
            reply_message(reply_token, f"找不到「{keyword}」相關的收藏 🔍")
            return
        lines = [f"🔍 「{keyword}」的搜尋結果：\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. [{r['category']}] {r['title']}")
            if r["url"]:
                lines.append(f"   {r['url']}")
        reply_message(reply_token, "\n".join(lines))
        return

    # ===== 列出分類 =====
    if text in ("分類", "分類列表", "列表"):
        names = get_all_category_names()
        lines = ["📂 目前的分類：\n"]
        for cat in names:
            lines.append(f"  • {cat}")
        lines.append("\n用 #新名稱 收藏時會自動新增分類")
        reply_message(reply_token, "\n".join(lines))
        return

    # ===== 使用說明 =====
    if text in ("說明", "幫助", "help", "指令"):
        reply_message(reply_token,
            "📖 收藏器使用說明\n\n"
            "【收藏】\n"
            "• 貼連結 → 自動分類存入\n"
            "• #科技 + 連結 → 指定分類\n"
            "• #新分類 + 連結 → 自動建立新分類\n\n"
            "【存入後修改（2 分鐘內）】\n"
            "• #生活 → 改最新一筆的分類\n"
            "• 輸入文字 → 改最新一筆的標題\n\n"
            "【查詢】\n"
            "• 找 科技 → 按分類或關鍵字搜尋\n"
            "• 分類 → 查看所有分類\n"
            "• 說明 → 顯示此說明"
        )
        return

    # ===== 收藏網頁連結 =====
    page_url = extract_url(text)
    if page_url:
        content = fetch_page_content(page_url)
        classify_text = f"{text} {content['description']}"
        manual_tag = extract_manual_tag(text)
        category = manual_tag or classify_content(classify_text)
        summary = content["description"] or text

        result = save_to_notion("無", category, page_url, summary)
        if result["ok"]:
            reply_message(
                reply_token,
                f"✅ 已收藏！\n\n"
                f"📂 分類：{category}\n"
                f"🔗 {page_url}\n\n"
                f"改分類 → #分類名\n"
                f"改標題 → 輸入文字",
            )
        else:
            reply_message(reply_token, f"❌ 儲存失敗\n{result.get('error', 'unknown')}")
        return

    # ===== 2 分鐘內修改最新一筆 =====

    # #分類名（沒有連結）→ 改最新一筆的分類
    tag = extract_manual_tag(text)
    if tag and not extract_url(text):
        latest = get_latest_bookmark()
        if latest:
            if update_notion_page(latest["page_id"], {"category": tag}):
                reply_message(reply_token, f"✅ 最新收藏的分類已改為：{tag}")
            else:
                reply_message(reply_token, "❌ 修改失敗")
        else:
            reply_message(reply_token, "⏰ 沒有 2 分鐘內的收藏可以修改")
        return

    # ===== 純文字筆記收藏（>10 字）=====
    if len(text) > 10:
        category = classify_content(text)
        result = save_to_notion("無", category, "", text)
        if result["ok"]:
            reply_message(reply_token, f"✅ 已收藏為筆記！\n📂 分類：{category}")
        else:
            reply_message(reply_token, f"❌ 儲存失敗\n{result.get('error', 'unknown')}")
        return

    # ===== 短文字 → 改最新一筆的標題 =====
    reserved = {"ok", "好", "是", "y", "yes", "對", "確認",
                "取消", "不要", "算了", "cancel"}
    if text.lower() not in reserved and len(text) <= 10:
        latest = get_latest_bookmark()
        if latest:
            if update_notion_page(latest["page_id"], {"title": text[:100]}):
                reply_message(reply_token, f"✅ 最新收藏的標題已改為：{text}")
            else:
                reply_message(reply_token, "❌ 修改失敗")
        else:
            reply_message(reply_token, "輸入「說明」查看使用方式 📖")
        return

    reply_message(reply_token, "輸入「說明」查看使用方式 📖")


# =====================================================
# Flask 路由
# =====================================================

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    if not verify_signature(body, signature):
        abort(400)
    data = json.loads(body)
    for event in data.get("events", []):
        if event["type"] == "message" and event["message"]["type"] == "text":
            handle_message(event)
    return "OK"


@app.route("/health", methods=["GET"])
def health():
    return "OK"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
