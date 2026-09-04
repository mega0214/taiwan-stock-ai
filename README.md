---
title: 台股 AI 全市場選股與量化分析平台
emoji: 📈
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
---

# 台股 AI 全市場選股與量化分析平台

無 Gradio 版本：原生 HTML/CSS/JavaScript + FastAPI。

## 專案結構

所有必要檔案都直接放在 Space 根目錄，不需要額外資料夾。

## Secrets

請在 Space Settings → Variables and secrets → Secrets 設定：

- `FINMIND_TOKEN`：FinMind API Token
- `GEMINI_API_KEY`：Gemini API Key

不要把 Key 寫進 `main.py`、`stock.py` 或 `index.html`。
