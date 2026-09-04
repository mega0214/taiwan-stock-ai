---
title: 台股 AI 全市場選股與量化分析平台
emoji: 📈
colorFrom: blue
colorTo: green
sdk: static
---

# 台股 AI 全市場選股與量化分析平台

無 Gradio 版本：**Hugging Face Static 前端 + Render FastAPI 後端**。

## 目前部署架構

- 前端：Hugging Face Spaces Static（免費）
- 後端：Render Web Service / FastAPI（Free）
- 資料：yfinance + TWSE / TPEx + FinMind
- AI：Gemini API（Key 僅放後端環境變數）
- 不使用 Gradio

## Render 後端

目前 API：
`https://taiwan-stock-ai-00iw.onrender.com`

Render Start Command：
```text
uvicorn main:app --host 0.0.0.0 --port $PORT
```

## 環境變數

請在 Render → Environment 設定：

- `FINMIND_TOKEN`
- `GEMINI_API_KEY`

不要把 Key 寫進 `main.py`、`stock.py` 或前端 HTML。

## API

- `GET /api/health`
- `GET /api/gainers`
- `GET /api/scan`
- `POST /api/analyze`
- `POST /api/watchlist`

## 本次修正版

已修正全市場選股 `/api/scan` 回傳 pandas / numpy `NaN` 時造成的 JSON 500 錯誤；現在會將無效數值轉成 JSON `null`。

前端也已加入非 JSON 錯誤的安全處理，避免再次出現 `Unexpected token 'I'`。
