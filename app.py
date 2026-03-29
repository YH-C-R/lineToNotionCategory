"""
Threads 文分類收藏器
LINE Bot + Notion 自動分類收藏工具
"""

import os
import re
import json
import hashlib
import hmac
import base64
from datetime import datetime, timezone

import requests
from flask import Flask, request, abort

app = Flask(__name__)

# ===== 環境變數 =====
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

# ===== 分類規則（關鍵字比對）=====
# 你可以自由新增/修改分類和關鍵字
CATEGORY_RULES = {
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
}


def classify_content(text: str) -> str:
    """根據關鍵字比對分類內容，找不到就歸為「其他」"""
    text_lower = text.lower()
    scores = {}
    for category, keywords in CATEGORY_RULES.items():
        score = sum(1 for kw in keywords if kw.lower() in text_lower)
        if score > 0:
            scores[category] = score
    if scores:
        return max(scores, key=scores.get)
    return "其他"


def verify_signature(body: str, signature: str) -> bool:
    """驗證 LINE webhook 簽名"""
    hash_value = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(hash_value).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def reply_message(reply_token: str, text: str):
    """回覆 LINE 訊息"""
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}],
    }
    requests.post(url, headers=headers, json=payload)


def extract_threads_url(text: str) -> str | None:
    """從訊息中提取 Threads URL"""
    # 支援 threads.net 的各種格式
    patterns = [
        r"https?://(?:www\.)?threads\.net/[^\s]+",
        r"https?://(?:www\.)?threads\.net/@[^\s]+/post/[^\s]+",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return None


def fetch_threads_content(url: str) -> dict:
    """
    嘗試抓取 Threads 頁面內容
    注意：Threads 沒有公開 API，這裡用簡單的 meta tag 擷取
    如果無法抓取，就用使用者分享的文字本身
    """
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

        # 嘗試從 og:description 取得內容
        og_match = re.search(
            r'<meta\s+(?:property|name)="og:description"\s+content="([^"]*)"',
            html,
        )
        description = og_match.group(1) if og_match else ""

        # 嘗試從 og:title 取得標題
        title_match = re.search(
            r'<meta\s+(?:property|name)="og:title"\s+content="([^"]*)"',
            html,
        )
        title = title_match.group(1) if title_match else ""

        # 嘗試取得作者
        author_match = re.search(r"threads\.net/@([^/]+)", url)
        author = f"@{author_match.group(1)}" if author_match else ""

        return {
            "title": title or author or "Threads 貼文",
            "description": description,
            "author": author,
            "url": url,
        }
    except Exception:
        # 抓取失敗，回傳基本資訊
        author_match = re.search(r"threads\.net/@([^/]+)", url)
        author = f"@{author_match.group(1)}" if author_match else ""
        return {
            "title": author or "Threads 貼文",
            "description": "",
            "author": author,
            "url": url,
        }


def save_to_notion(title: str, category: str, url: str, summary: str) -> bool:
    """儲存到 Notion 資料庫"""
    notion_url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "名稱": {
                "title": [{"text": {"content": title[:100]}}]
            },
            "分類": {
                "select": {"name": category}
            },
            "連結": {
                "url": url
            },
            "內容摘要": {
                "rich_text": [{"text": {"content": summary[:2000]}}]
            },
            "收藏時間": {
                "date": {"start": now}
            },
        },
    }
    resp = requests.post(notion_url, headers=headers, json=payload)
    return resp.status_code == 200


def search_notion(keyword: str) -> list:
    """在 Notion 資料庫中搜尋"""
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    # 判斷是分類搜尋還是關鍵字搜尋
    is_category = keyword in CATEGORY_RULES or keyword == "其他"

    if is_category:
        payload = {
            "filter": {
                "property": "分類",
                "select": {"equals": keyword},
            },
            "sorts": [
                {"property": "收藏時間", "direction": "descending"}
            ],
            "page_size": 10,
        }
    else:
        payload = {
            "filter": {
                "or": [
                    {
                        "property": "名稱",
                        "title": {"contains": keyword},
                    },
                    {
                        "property": "內容摘要",
                        "rich_text": {"contains": keyword},
                    },
                ]
            },
            "sorts": [
                {"property": "收藏時間", "direction": "descending"}
            ],
            "page_size": 10,
        }

    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code != 200:
        return []

    results = []
    for page in resp.json().get("results", []):
        props = page["properties"]
        title_arr = props.get("名稱", {}).get("title", [])
        title = title_arr[0]["text"]["content"] if title_arr else "無標題"
        cat = props.get("分類", {}).get("select", {})
        category = cat.get("name", "未分類") if cat else "未分類"
        link = props.get("連結", {}).get("url", "")
        results.append({"title": title, "category": category, "url": link})

    return results


def handle_message(event: dict):
    """處理收到的訊息"""
    reply_token = event["replyToken"]
    text = event["message"].get("text", "").strip()

    if not text:
        return

    # ===== 指令：查詢 =====
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

    # ===== 指令：列出分類 =====
    if text in ("分類", "分類列表", "列表"):
        categories = list(CATEGORY_RULES.keys()) + ["其他"]
        lines = ["📂 目前的分類：\n"]
        for cat in categories:
            lines.append(f"  • {cat}")
        lines.append("\n輸入「找 分類名」查看該分類的收藏")
        reply_message(reply_token, "\n".join(lines))
        return

    # ===== 指令：使用說明 =====
    if text in ("說明", "幫助", "help", "指令"):
        help_text = (
            "📖 Threads 收藏器使用說明\n\n"
            "【收藏】\n"
            "直接分享 Threads 貼文連結到這裡即可\n\n"
            "【查詢】\n"
            "• 找 科技 → 查看科技類收藏\n"
            "• 找 AI → 用關鍵字搜尋\n\n"
            "【其他】\n"
            "• 分類 → 查看所有分類\n"
            "• 說明 → 顯示此說明"
        )
        reply_message(reply_token, help_text)
        return

    # ===== 收藏 Threads 貼文 =====
    threads_url = extract_threads_url(text)
    if threads_url:
        # 抓取內容
        content = fetch_threads_content(threads_url)
        # 合併文字做分類（用分享時附帶的文字 + 抓到的描述）
        classify_text = f"{text} {content['description']}"
        category = classify_content(classify_text)

        # 存到 Notion
        title = content["title"]
        summary = content["description"] or text
        success = save_to_notion(title, category, threads_url, summary)

        if success:
            reply_message(
                reply_token,
                f"✅ 已收藏！\n\n"
                f"📌 {title}\n"
                f"📂 分類：{category}\n\n"
                f"輸入「找 {category}」查看同類收藏",
            )
        else:
            reply_message(reply_token, "❌ 儲存失敗，請稍後再試")
        return

    # ===== 純文字也可以收藏 =====
    # 如果不是指令也不是 Threads 連結，當作一般筆記收藏
    if len(text) > 10:
        category = classify_content(text)
        success = save_to_notion(
            title=text[:50],
            category=category,
            url="",
            summary=text,
        )
        if success:
            reply_message(
                reply_token,
                f"✅ 已收藏為筆記！\n📂 分類：{category}",
            )
        else:
            reply_message(reply_token, "❌ 儲存失敗，請稍後再試")
        return

    # 不認識的指令
    reply_message(reply_token, "輸入「說明」查看使用方式 📖")


@app.route("/callback", methods=["POST"])
def callback():
    """LINE Webhook 入口"""
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
    """健康檢查"""
    return "OK"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
