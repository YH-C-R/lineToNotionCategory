"""
Threads 文分類收藏器
LINE Bot + Notion 自動分類收藏工具
支援：手動標籤、自動分類確認、更改分類
"""

import os
import re
import json
import hashlib
import hmac
import base64
import time
from datetime import datetime, timezone

import requests
from flask import Flask, request, abort

app = Flask(__name__)

# ===== 環境變數 =====
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

# ===== 暫存等待確認的收藏（user_id -> 收藏資料）=====
# 用簡單的 dict 暫存，Render 免費方案單進程夠用
pending_saves = {}

# 確認等待時間（秒），超過就自動存入
CONFIRM_TIMEOUT = 120  # 2 分鐘

# ===== 分類規則（關鍵字比對）=====
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

# 所有合法分類名（含「其他」）
ALL_CATEGORIES = set(CATEGORY_RULES.keys()) | {"其他"}


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


def extract_manual_tag(text: str) -> str | None:
    """
    從訊息中提取手動標籤
    支援格式：#科技、# 科技、＃科技（全形）
    """
    match = re.search(r"[#＃]\s*(\S+)", text)
    if match:
        tag = match.group(1)
        if tag in ALL_CATEGORIES:
            return tag
    return None


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


def push_message(user_id: str, text: str):
    """主動推送訊息給使用者（用於超時自動存入通知）"""
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {
        "to": user_id,
        "messages": [{"type": "text", "text": text}],
    }
    requests.post(url, headers=headers, json=payload)


def extract_url(text: str) -> str | None:
    """從訊息中提取任意 URL"""
    pattern = r"https?://[^\s]+"
    match = re.search(pattern, text)
    if match:
        url = match.group(0)
        # 移除追蹤參數，只保留乾淨的連結
        url = re.split(r"[?]", url)[0]
        return url
    return None


def is_threads_url(url: str) -> bool:
    """判斷是否為 Threads 連結"""
    return bool(re.search(r"threads\.(?:net|com)/", url))


def fetch_page_content(url: str) -> dict:
    """抓取任意網頁的 og:title 和 og:description"""
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
            r'<meta\s+(?:property|name)="og:description"\s+content="([^"]*)"',
            html,
        )
        description = og_desc.group(1) if og_desc else ""

        og_title = re.search(
            r'<meta\s+(?:property|name)="og:title"\s+content="([^"]*)"',
            html,
        )
        # 也嘗試一般的 <title> 標籤
        html_title = re.search(r"<title>([^<]*)</title>", html)
        title = ""
        if og_title:
            title = og_title.group(1)
        elif html_title:
            title = html_title.group(1)

        # 如果是 Threads，嘗試抓作者
        author = ""
        author_match = re.search(r"threads\.(?:net|com)/@([^/]+)", url)
        if author_match:
            author = f"@{author_match.group(1)}"

        # 取得網站名稱
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.replace("www.", "")

        return {
            "title": title or author or domain,
            "description": description,
            "author": author,
            "domain": domain,
            "url": url,
        }
    except Exception:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.replace("www.", "")
        author_match = re.search(r"threads\.(?:net|com)/@([^/]+)", url)
        author = f"@{author_match.group(1)}" if author_match else ""
        return {
            "title": author or domain,
            "description": "",
            "author": author,
            "domain": domain,
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
    if resp.status_code == 200:
        return {"ok": True}
    else:
        return {"ok": False, "status": resp.status_code, "error": resp.text[:200]}


def search_notion(keyword: str) -> list:
    """在 Notion 資料庫中搜尋"""
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    is_category = keyword in ALL_CATEGORIES

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


def check_and_save_expired(user_id: str):
    """檢查是否有超時未確認的收藏，如果有就自動存入"""
    if user_id not in pending_saves:
        return
    pending = pending_saves[user_id]
    if time.time() - pending["timestamp"] > CONFIRM_TIMEOUT:
        data = pending["data"]
        result = save_to_notion(
            data["title"], data["category"], data["url"], data["summary"]
        )
        del pending_saves[user_id]
        if result["ok"]:
            push_message(
                user_id,
                f"⏰ 已自動存入！\n📌 {data['title']}\n📂 分類：{data['category']}",
            )


def handle_message(event: dict):
    """處理收到的訊息"""
    reply_token = event["replyToken"]
    user_id = event["source"].get("userId", "")
    text = event["message"].get("text", "").strip()

    if not text:
        return

    # ===== 先檢查是否有超時的暫存 =====
    check_and_save_expired(user_id)

    # ===== 處理確認/更改分類的回覆 =====
    if user_id in pending_saves:
        pending = pending_saves[user_id]
        data = pending["data"]

        # 回覆 OK → 用建議分類存入
        if text.lower() in ("ok", "好", "是", "y", "yes", "對", "確認"):
            result = save_to_notion(
                data["title"], data["category"], data["url"], data["summary"]
            )
            del pending_saves[user_id]
            if result["ok"]:
                reply_message(
                    reply_token,
                    f"✅ 已收藏！\n📌 {data['title']}\n📂 分類：{data['category']}",
                )
            else:
                reply_message(reply_token, f"❌ 儲存失敗\n{result.get('error', 'unknown')}")
            return

        # 回覆 #新分類 → 用新分類存入
        new_tag = extract_manual_tag(text)
        if new_tag:
            data["category"] = new_tag
            result = save_to_notion(
                data["title"], data["category"], data["url"], data["summary"]
            )
            del pending_saves[user_id]
            if result["ok"]:
                reply_message(
                    reply_token,
                    f"✅ 已收藏！\n📌 {data['title']}\n📂 分類：{new_tag}（已更改）",
                )
            else:
                reply_message(reply_token, f"❌ 儲存失敗\n{result.get('error', 'unknown')}")
            return

        # 直接輸入分類名（不加 #）也行
        if text in ALL_CATEGORIES:
            data["category"] = text
            result = save_to_notion(
                data["title"], data["category"], data["url"], data["summary"]
            )
            del pending_saves[user_id]
            if result["ok"]:
                reply_message(
                    reply_token,
                    f"✅ 已收藏！\n📌 {data['title']}\n📂 分類：{text}（已更改）",
                )
            else:
                reply_message(reply_token, f"❌ 儲存失敗\n{result.get('error', 'unknown')}")
            return

        # 回覆「取消」→ 丟棄
        if text in ("取消", "不要", "算了", "cancel"):
            del pending_saves[user_id]
            reply_message(reply_token, "🗑️ 已取消，不存入")
            return

        # 其他回覆 → 提示正確格式
        cats = "、".join(ALL_CATEGORIES)
        reply_message(
            reply_token,
            f"請回覆：\n"
            f"• OK → 確認存入\n"
            f"• #分類名 → 改分類（如 #生活）\n"
            f"• 取消 → 不存\n\n"
            f"可用分類：{cats}",
        )
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
            "• 直接貼連結 → 自動分類，問你確認\n"
            "• #科技 + 連結 → 直接用指定分類存入\n\n"
            "【確認收藏】\n"
            "• OK → 同意建議分類\n"
            "• #生活 → 改成其他分類\n"
            "• 取消 → 不存\n\n"
            "【查詢】\n"
            "• 找 科技 → 查看科技類收藏\n"
            "• 找 AI → 用關鍵字搜尋\n\n"
            "【其他】\n"
            "• 分類 → 查看所有分類\n"
            "• 說明 → 顯示此說明"
        )
        reply_message(reply_token, help_text)
        return

    # ===== 收藏網頁連結 =====
    page_url = extract_url(text)
    if page_url:
        content = fetch_page_content(page_url)
        classify_text = f"{text} {content['description']}"
        manual_tag = extract_manual_tag(text)

        if manual_tag:
            # ====== 方式 A：有手動標籤 → 直接存入 ======
            title = content["title"]
            summary = content["description"] or text
            result = save_to_notion(title, manual_tag, page_url, summary)
            if result["ok"]:
                reply_message(
                    reply_token,
                    f"✅ 已收藏！\n\n"
                    f"📌 {title}\n"
                    f"📂 分類：{manual_tag}\n\n"
                    f"輸入「找 {manual_tag}」查看同類收藏",
                )
            else:
                reply_message(reply_token, f"❌ 儲存失敗\n{result.get('error', 'unknown')}")
        else:
            # ====== 方式 B：自動分類 → 暫存等確認 ======
            category = classify_content(classify_text)
            title = content["title"]
            summary = content["description"] or text

            pending_saves[user_id] = {
                "timestamp": time.time(),
                "data": {
                    "title": title,
                    "category": category,
                    "url": page_url,
                    "summary": summary,
                },
            }

            reply_message(
                reply_token,
                f"📌 {title}\n"
                f"📂 建議分類：{category}\n\n"
                f"回覆：\n"
                f"• OK → 確認存入\n"
                f"• #分類名 → 改分類（如 #生活）\n"
                f"• 取消 → 不存\n\n"
                f"2 分鐘內沒回覆會自動用「{category}」存入",
            )
        return

    # ===== 純文字筆記收藏 =====
    if len(text) > 10:
        manual_tag = extract_manual_tag(text)
        if manual_tag:
            clean_text = re.sub(r"[#＃]\s*\S+", "", text).strip()
            result = save_to_notion(
                title=clean_text[:50],
                category=manual_tag,
                url="",
                summary=clean_text,
            )
            if result["ok"]:
                reply_message(
                    reply_token,
                    f"✅ 已收藏為筆記！\n📂 分類：{manual_tag}",
                )
            else:
                reply_message(reply_token, f"❌ 儲存失敗\n{result.get('error', 'unknown')}")
        else:
            category = classify_content(text)
            pending_saves[user_id] = {
                "timestamp": time.time(),
                "data": {
                    "title": text[:50],
                    "category": category,
                    "url": "",
                    "summary": text,
                },
            }
            reply_message(
                reply_token,
                f"📝 筆記收藏\n"
                f"📂 建議分類：{category}\n\n"
                f"回覆：\n"
                f"• OK → 確認存入\n"
                f"• #分類名 → 改分類\n"
                f"• 取消 → 不存",
            )
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


@app.route("/debug-notion", methods=["GET"])
def debug_notion():
    """除錯用：測試 Notion 連線，部署成功後可移除"""
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
            "名稱": {"title": [{"text": {"content": "測試連線"}}]},
            "分類": {"select": {"name": "其他"}},
            "連結": {"url": "https://example.com"},
            "內容摘要": {"rich_text": [{"text": {"content": "這是測試"}}]},
            "收藏時間": {"date": {"start": now}},
        },
    }
    resp = requests.post(notion_url, headers=headers, json=payload)
    return {
        "status": resp.status_code,
        "notion_token_prefix": NOTION_TOKEN[:8] + "..." if NOTION_TOKEN else "EMPTY",
        "database_id": NOTION_DATABASE_ID or "EMPTY",
        "response": resp.json(),
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)