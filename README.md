# 連結分類收藏器

Cloud Run + Notion 資料庫。

---

## 架構

```
iPhone 分享連結 → LINE Bot → Cloud Run → Notion 收藏資料庫
```

---

## Notion 設定（1 個資料庫）

| 欄位名稱 | 類型 |
|----------|------|
| 名稱 | Title |
| 分類 | Select |
| 連結 | URL |
| 內容摘要 | Text |
| 收藏時間 | Date |

接上 Integration，記下 Database ID。

---

## 部署到 Cloud Run

```bash
gcloud auth login
gcloud config set project 專案ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com

gcloud run deploy threads-collector \
  --source . \
  --region asia-east1 \
  --allow-unauthenticated \
  --set-env-vars "LINE_CHANNEL_SECRET=你的值" \
  --set-env-vars "LINE_CHANNEL_ACCESS_TOKEN=你的值" \
  --set-env-vars "NOTION_TOKEN=你的值" \
  --set-env-vars "NOTION_DATABASE_ID=你的值"
```

拿到網址 → LINE Webhook URL 填 `https://xxx/callback`。

更新：`gcloud run deploy threads-collector --source . --region asia-east1`

---

## 環境變數（只要 4 個）

| 變數 | 說明 |
|------|------|
| `LINE_CHANNEL_SECRET` | LINE Channel Secret |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Access Token |
| `NOTION_TOKEN` | Notion Integration Secret |
| `NOTION_DATABASE_ID` | 收藏資料庫 ID |

---

## 使用方式

### 收藏

```
https://threads.com/...          → 自動分類存入
#科技 https://threads.com/...    → 指定分類
#書籍 https://medium.com/...     → 新分類自動建立！
```

### 存入後修改（2 分鐘內）

```
#生活           → 改最新一筆的分類
AI投資筆記       → 改最新一筆的標題（≤10字）
```

> 超過 10 字會被當成筆記收藏，不會改標題。

### 查詢

```
找 科技    → 按分類或關鍵字搜尋
分類       → 列出所有分類
說明       → 使用說明
```

---

## 費用
$0。Cloud Run 免費額度 + Notion 免費方案 + LINE 免費方案。