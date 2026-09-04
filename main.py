"""台股 AI 全市場選股與量化分析平台 - 無 Gradio API 版本

前端使用原生 HTML/CSS/JavaScript；後端使用 FastAPI。
API Key 僅存在伺服器環境變數，不暴露到瀏覽器。
"""
import os
from typing import Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from stock import StockAnalyzer, TaiwanMarketScanner

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(
    title="台股 AI 全市場選股與量化分析平台",
    description="無 Gradio：原生 Web 前端 + FastAPI 後端",
    version="1.0.0",
)

# 若前後端同網域，實際上不需要 CORS；保留設定方便本機分離開發。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    ticker: str = Field(default="2330", min_length=1, max_length=20)
    period: str = Field(default="1y")


class WatchlistRequest(BaseModel):
    tickers: str = Field(default="2330,2317,2454,2308,2382,3231,6669,3017")


def df_records(df: pd.DataFrame):
    if df is None or df.empty:
        return []
    clean = df.copy()
    clean = clean.where(pd.notna(clean), None)
    return clean.to_dict(orient="records")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "finmind_configured": bool(os.environ.get("FINMIND_TOKEN")),
        "gemini_configured": bool(os.environ.get("GEMINI_API_KEY")),
        "gradio": False,
    }


@app.get("/api/gainers")
def gainers(
    market: str = Query("上市＋上櫃"),
    top_n: int = Query(100, ge=1, le=100),
    min_trade_value: float = Query(0, ge=0),
):
    scanner = TaiwanMarketScanner()
    df, status = scanner.get_top_gainers(market, top_n, min_trade_value)
    return {"status": status, "data": df_records(df)}


@app.get("/api/scan")
def market_scan(
    market: str = Query("上市＋上櫃"),
    candidate_count: int = Query(60, ge=10, le=120),
    min_score: float = Query(60, ge=0, le=100),
):
    scanner = TaiwanMarketScanner()
    df, status = scanner.scan(market, candidate_count, min_score)
    return {"status": status, "data": df_records(df)}


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    allowed_periods = {"3mo", "6mo", "1y", "2y", "3y", "5y", "max"}
    if req.period not in allowed_periods:
        raise HTTPException(status_code=400, detail="不支援的資料區間")

    ticker = req.ticker.strip().upper().replace(".TW", "").replace(".TWO", "")
    if not ticker:
        raise HTTPException(status_code=400, detail="請輸入股票代號")

    analyzer = StockAnalyzer(ticker)
    if not analyzer.fetch_data(period=req.period):
        raise HTTPException(status_code=404, detail=f"無法取得 {ticker} 的股票資料")

    analyzer.engineer_features()
    plot = analyzer.get_multi_panel_plot(analyzer.df)

    return {
        "ticker": f"{ticker}.TW",
        "bias": analyzer.get_bias_text(analyzer.df),
        "indicator_summary": analyzer.get_indicator_summary(),
        "macd_analysis": analyzer.get_macd_diagnostics(),
        "leading_analysis": analyzer.get_leading_indicators_analysis(),
        "news": df_records(analyzer._fetch_news(days=5)),
        "news_analysis": analyzer.get_news_analysis(days=5),
        "ai_analysis": analyzer.get_ai_prediction_text(),
        # Plotly Figure 可直接轉成 JSON，前端用 Plotly.newPlot() 顯示。
        "plot": plot.to_plotly_json(),
    }


@app.post("/api/watchlist")
def watchlist(req: WatchlistRequest):
    analyzer = StockAnalyzer("2330")
    df = analyzer.scan_watchlist(req.tickers)
    return {
        "status": f"完成 {len(df)} 檔自選股量化排名。" if not df.empty else "沒有足夠歷史資料。",
        "data": df_records(df),
    }
