# Threads 文分類收藏器

透過 LINE Bot 收藏 Threads 貼文，自動分類存入 Notion 資料庫。

## 功能

- 📌 分享 Threads 連結到 LINE Bot → 自動收藏到 Notion
- 🏷️ 關鍵字自動分類（科技、投資理財、生活、健康、職涯、學習）
- 🔍 在 LINE 上直接搜尋收藏（按分類或關鍵字）
- 📝 也支援純文字筆記收藏

## LINE Bot 指令

| 指令 | 說明 |
|------|------|
| 分享 Threads 連結 | 自動收藏並分類 |
| `找 科技` | 查看科技分類的收藏 |
| `找 AI` | 用關鍵字搜尋所有收藏 |
| `分類` | 查看所有分類列表 |
| `說明` | 顯示使用說明 |

## 設定步驟

### 1. LINE Bot

1. 前往 [LINE Developers Console](https://developers.line.biz/)
2. 建立 Provider → 建立 Messaging API Channel
3. 記下 **Channel Secret**（Basic settings 頁面）
4. 發行 **Channel Access Token**（Messaging API 頁面）
5. 關閉 Auto-reply messages 和 Greeting messages

### 2. Notion

1. 前往 [Notion Integrations](https://www.notion.so/my-integrations)
2. 建立新 Integration，記下 **Internal Integration Secret**
3. 在 Notion 建立一個資料庫，欄位如下：

| 欄位名稱 | 類型 |
|----------|------|
| 名稱 | Title |
| 分類 | Select |
| 連結 | URL |
| 內容摘要 | Text |
| 收藏時間 | Date |

4. 在資料庫頁面按 ⋯ → Connections → 加入你的 Integration
5. 記下資料庫 ID（從 URL 取得：`notion.so/{DATABASE_ID}?v=...`）

### 3. 部署到 Render

1. 將此專案推到 GitHub
2. 前往 [Render](https://render.com) → New Web Service
3. 連結你的 GitHub repo
4. 設定環境變數：

```
LINE_CHANNEL_SECRET=你的值
LINE_CHANNEL_ACCESS_TOKEN=你的值
NOTION_TOKEN=你的值
NOTION_DATABASE_ID=你的值
```

5. 部署完成後取得 URL（如 `https://threads-collector.onrender.com`）

### 4. 設定 LINE Webhook

1. 回到 LINE Developers Console → Messaging API 頁面
2. Webhook URL 填入：`https://你的render網址/callback`
3. 啟用 Use webhook
4. 點 Verify 確認連線成功

### 5. 開始使用

掃描 LINE Bot 的 QR Code 加好友，分享 Threads 貼文試試！

## 自訂分類

編輯 `app.py` 中的 `CATEGORY_RULES` 字典，新增或修改分類和對應關鍵字。

## 注意事項

- Render 免費方案的服務會在 15 分鐘無流量後休眠，第一次喚醒需要約 30 秒
- Threads 沒有官方 API，內容抓取依賴 meta tag，部分貼文可能只存到連結
- 分類為簡單關鍵字比對，後續可升級為 AI 分類
