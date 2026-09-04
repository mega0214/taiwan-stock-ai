# STEP 23: 分析程式碼重構為模組化 stock.py*

import os
import pandas as pd
import numpy as np
import yfinance as yf
import requests
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score

# 配置日誌以抑制 介面警告

class StockAnalyzer:
    def __init__(self, ticker):
        self.ticker = ticker.replace('.TW', '') # 確保內部存儲不含 .TW
        self.df = pd.DataFrame()  # 主要存放K線數據
        self.df_fin = pd.DataFrame() # 存放財報數據
        self.df_inst_pivot = pd.DataFrame() # 存放法人籌碼
        self.df_margin_data = pd.DataFrame() # 存放融資融券數據
        self.df_futures_data = pd.DataFrame() # 存放外資台指期數據
        self.df_securities_lending = pd.DataFrame() # 存放借券數據
        self.df_extended = pd.DataFrame() # 綜合所有K線和籌碼數據
        self.ml_df = pd.DataFrame() # 機器學習的基礎數據
        self.mega_df_cleaned_final = pd.DataFrame() # 最終特徵矩陣
        self.final_feature_cols = []
        # Hugging Face Secrets / environment variables
        self.finmind_token = os.environ.get('FINMIND_TOKEN')
        self.gemini_api_key = os.environ.get('GEMINI_API_KEY')
        self.model_mega = None # 儲存訓練好的模型
        self.X_test_mega = pd.DataFrame() # 儲存測試集特徵
        self.y_pred_mega = np.array([]) # 儲存測試集預測結果

    def _fix_col_names(self, df_param: pd.DataFrame) -> pd.DataFrame:
        """根據指定邏輯清洗 DataFrame 的欄位名稱。"""
        if isinstance(df_param.columns, pd.MultiIndex):
            df_param.columns = [col[0].lower() for col in df_param.columns]
        else:
            df_param.columns = [col.lower() for col in df_param.columns]

        df_param.columns.name = None

        if 'adj close' in df_param.columns:
            df_param['close'] = df_param['adj close']
            df_param = df_param.drop(columns=['adj close'])
        elif 'adj close' in df_param.columns and 'close' not in df_param.columns:
            df_param = df_param.rename(columns={'adj close': 'close'})

        required_cols = ['open', 'high', 'low', 'close', 'volume']
        df_param = df_param[[col for col in required_cols if col in df_param.columns]]
        return df_param

    def _fetch_finmind_api_data(self, dataset, data_id, start_date, end_date, use_mock_data_on_error=False):
        """統一取得 FinMind 資料。使用 Bearer Header；資料缺失時絕不製造隨機假資料。"""
        if not self.finmind_token:
            print(f"警告: FINMIND_TOKEN 未設定，略過 {dataset}。")
            return pd.DataFrame()

        token = self.finmind_token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()

        url = "https://api.finmindtrade.com/api/v4/data"
        params = {
            "dataset": dataset,
            "data_id": data_id,
            "start_date": start_date,
            "end_date": end_date,
        }
        headers = {"Authorization": f"Bearer {token}"}

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=20)
            resp.raise_for_status()
            payload = resp.json()
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            if rows:
                return pd.DataFrame(rows)
            print(f"FinMind {dataset} 無資料。")
        except Exception as e:
            print(f"FinMind {dataset} 取得失敗：{e}")
        return pd.DataFrame()

    def _fetch_news(self, days=5):
        """抓最近數個日曆日的 FinMind TaiwanStockNews；單日查詢避免新聞資料量過大。"""
        if not self.finmind_token:
            return pd.DataFrame(columns=["日期", "來源", "標題", "連結"])

        end_date = pd.Timestamp.now(tz="Asia/Taipei").date()
        rows = []
        for i in range(max(1, int(days))):
            d = end_date - pd.Timedelta(days=i)
            ds = d.strftime("%Y-%m-%d")
            df_news = self._fetch_finmind_api_data(
                "TaiwanStockNews", self.ticker, ds, ds, use_mock_data_on_error=False
            )
            if df_news.empty:
                continue
            for _, r in df_news.iterrows():
                title = str(r.get("title", "")).strip()
                if not title:
                    title = str(r.get("description", "")).strip()[:120]
                rows.append({
                    "日期": str(r.get("date", ds)),
                    "來源": str(r.get("source", "未知")),
                    "標題": title,
                    "連結": str(r.get("link", "")).strip(),
                })

        if not rows:
            return pd.DataFrame(columns=["日期", "來源", "標題", "連結"])
        out = pd.DataFrame(rows).drop_duplicates(subset=["日期", "標題"]).sort_values("日期", ascending=False)
        return out.head(30).reset_index(drop=True)

    def fetch_data(self, period='3y'):
        """下載股票數據、財報、法人籌碼、融資融券、期貨借券等數據。"""
        print(f"\n----- 開始為 {self.ticker} 下載數據 (期間: {period}) -----")
        # STEP 2 & 3: 下載並清洗 K 線數據
        self.df = yf.download(f'{self.ticker}.TW', period=period, progress=False)
        if self.df.empty:
            print(f"錯誤: 無法下載 {self.ticker}.TW 的 K 線數據。")
            return False
        self.df = self._fix_col_names(self.df)
        self.df.index = pd.to_datetime(self.df.index)

        # STEP 5: 計算均線
        self.df['ma5'] = self.df['close'].rolling(window=5).mean()
        self.df['ma20'] = self.df['close'].rolling(window=20).mean()
        self.df['ma60'] = self.df['close'].rolling(window=60).mean()
        self.df['ma120'] = self.df['close'].rolling(window=120).mean()
        self.df['ma240'] = self.df['close'].rolling(window=240).mean()

        # STEP 6: 計算 RSI
        delta = self.df['close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = (-delta).where(delta < 0, 0)
        avg_gain = gain.ewm(com=13, adjust=False).mean()
        avg_loss = loss.ewm(com=13, adjust=False).mean()
        rs = avg_gain / avg_loss
        self.df['rsi'] = 100 - (100 / (1 + rs))

        # STEP 7: 計算 MACD
        self.df['ema12'] = self.df['close'].ewm(span=12, adjust=False).mean()
        self.df['ema26'] = self.df['close'].ewm(span=26, adjust=False).mean()
        self.df['macd_line'] = self.df['ema12'] - self.df['ema26']
        self.df['signal_line'] = self.df['macd_line'].ewm(span=9, adjust=False).mean()
        self.df['macd_histogram'] = self.df['macd_line'] - self.df['signal_line']

        # STEP 8: 計算布林通道
        window_bb = 20
        self.df['middle_band'] = self.df['close'].rolling(window=window_bb).mean()
        self.df['std_dev'] = self.df['close'].rolling(window=window_bb).std()
        self.df['upper_band'] = self.df['middle_band'] + (self.df['std_dev'] * 2)
        self.df['lower_band'] = self.df['middle_band'] - (self.df['std_dev'] * 2)
        # 直接保留在主 K 線資料，供領先指標與圖表共用
        self.df['BB_Width'] = (
            (self.df['upper_band'] - self.df['lower_band']) /
            self.df['middle_band'].replace(0, np.nan)
        ) * 100

        # STEP 12: 計算 ATR
        high_minus_low = self.df['high'] - self.df['low']
        high_minus_prev_close = abs(self.df['high'] - self.df['close'].shift(1))
        low_minus_prev_close = abs(self.df['low'] - self.df['close'].shift(1))
        self.df['tr'] = pd.concat([high_minus_low, high_minus_prev_close, low_minus_prev_close], axis=1).max(axis=1)
        self.df['atr'] = self.df['tr'].ewm(span=14, adjust=False).mean()

        # STEP 13: 補充技術指標
        # KD 隨機指標
        low_9 = self.df['low'].rolling(9).min()
        high_9 = self.df['high'].rolling(9).max()
        rsv = ((self.df['close'] - low_9) / (high_9 - low_9).replace(0, np.nan)) * 100
        self.df['K'] = rsv.ewm(com=2, adjust=False).mean()
        self.df['D'] = self.df['K'].ewm(com=2, adjust=False).mean()
        self.df['J'] = 3 * self.df['K'] - 2 * self.df['D']

        # CCI 20
        typical_price = (self.df['high'] + self.df['low'] + self.df['close']) / 3
        cci_ma = typical_price.rolling(20).mean()
        cci_md = typical_price.rolling(20).apply(
            lambda x: np.mean(np.abs(x - np.mean(x))), raw=True
        )
        self.df['CCI'] = (typical_price - cci_ma) / (0.015 * cci_md.replace(0, np.nan))

        # Williams %R 14
        low_14 = self.df['low'].rolling(14).min()
        high_14 = self.df['high'].rolling(14).max()
        self.df['Williams_R'] = -100 * (high_14 - self.df['close']) / (high_14 - low_14).replace(0, np.nan)

        # ROC 12
        self.df['ROC_12'] = self.df['close'].pct_change(12) * 100

        # MFI 14
        money_flow = typical_price * self.df['volume']
        positive_flow = money_flow.where(typical_price > typical_price.shift(1), 0)
        negative_flow = money_flow.where(typical_price < typical_price.shift(1), 0)
        positive_sum = positive_flow.rolling(14).sum()
        negative_sum = negative_flow.rolling(14).sum()
        money_ratio = positive_sum / negative_sum.replace(0, np.nan)
        self.df['MFI'] = 100 - (100 / (1 + money_ratio))

        # OBV
        volume_direction = np.sign(self.df['close'].diff()).fillna(0)
        self.df['OBV'] = (volume_direction * self.df['volume']).cumsum()

        # ADX / +DI / -DI（14）
        up_move = self.df['high'].diff()
        down_move = -self.df['low'].diff()
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
        atr14 = self.df['tr'].ewm(alpha=1/14, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(alpha=1/14, adjust=False).mean() / atr14.replace(0, np.nan)
        minus_di = 100 * minus_dm.ewm(alpha=1/14, adjust=False).mean() / atr14.replace(0, np.nan)
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        self.df['Plus_DI'] = plus_di
        self.df['Minus_DI'] = minus_di
        self.df['ADX'] = dx.ewm(alpha=1/14, adjust=False).mean()

        # PSY：12 日上漲天數比例
        self.df['PSY_12'] = (self.df['close'].diff() > 0).rolling(12).sum() / 12 * 100

        # 成交量指標
        self.df['Volume_MA20'] = self.df['volume'].rolling(20).mean()
        self.df['Volume_Ratio'] = self.df['volume'] / self.df['Volume_MA20'].replace(0, np.nan)

        # 布林通道衍生指標
        self.df['BB_PctB'] = (
            (self.df['close'] - self.df['lower_band']) /
            (self.df['upper_band'] - self.df['lower_band']).replace(0, np.nan)
        ) * 100

        # EMA / 動能
        self.df['EMA_50'] = self.df['close'].ewm(span=50, adjust=False).mean()
        self.df['EMA_200'] = self.df['close'].ewm(span=200, adjust=False).mean()
        self.df['Momentum_5'] = self.df['close'].pct_change(5) * 100
        self.df['Momentum_20'] = self.df['close'].pct_change(20) * 100

        # STEP 9: 抓取營收與毛利 (財報)
        ticker_yf = yf.Ticker(f'{self.ticker}.TW')
        quarterly_financials = ticker_yf.quarterly_income_stmt
        if not quarterly_financials.empty:
            recent_financials_raw = quarterly_financials.iloc[:, :12]
            df_fin_transposed = recent_financials_raw.T
            total_revenue_col = 'Total Revenue'
            gross_profit_col = 'Gross Profit'
            cost_of_revenue_col = 'Cost Of Revenue'

            if total_revenue_col in df_fin_transposed.columns:
                if gross_profit_col in df_fin_transposed.columns:
                    self.df_fin = df_fin_transposed[[total_revenue_col, gross_profit_col]].copy()
                elif cost_of_revenue_col in df_fin_transposed.columns:
                    self.df_fin = df_fin_transposed[[total_revenue_col, cost_of_revenue_col]].copy()
                    self.df_fin[total_revenue_col] = pd.to_numeric(self.df_fin[total_revenue_col], errors='coerce')
                    self.df_fin[cost_of_revenue_col] = pd.to_numeric(self.df_fin[cost_of_revenue_col], errors='coerce')
                    self.df_fin[gross_profit_col] = self.df_fin[total_revenue_col] - self.df_fin[cost_of_revenue_col]
                if not self.df_fin.empty: # Only proceed if df_fin has data after processing
                    self.df_fin.index = pd.to_datetime(self.df_fin.index)
                    self.df_fin = self.df_fin.sort_index()
                    self.df_fin['GrossProfitMargin'] = (self.df_fin[gross_profit_col] / self.df_fin[total_revenue_col]) * 100
                    self.df_fin['GrossProfitMargin'] = self.df_fin['GrossProfitMargin'].replace([float('inf'), -float('inf')], pd.NA).fillna(pd.NA)
            print(f"成功獲取 {self.ticker} 財報數據。")
        else:
            print(f"警告: 無法獲取 {self.ticker} 季度損益表數據。")

        # STEP 10: 外資與投信動向
        start_date_finmind = self.df.index.min().strftime('%Y-%m-%d')
        end_date_finmind = self.df.index.max().strftime('%Y-%m-%d')

        df_inst_raw = self._fetch_finmind_api_data("TaiwanStockInstitutionalInvestorsBuySell", self.ticker, start_date_finmind, end_date_finmind)
        if not df_inst_raw.empty:
            df_inst_raw['date'] = pd.to_datetime(df_inst_raw['date'])
            df_inst_raw = df_inst_raw.set_index('date').sort_index()
            df_inst_raw['net_buy_sell'] = df_inst_raw['buy'] - df_inst_raw['sell']
            df_inst_filtered = df_inst_raw[df_inst_raw['name'].isin(['Foreign_Investor', 'Investment_Trust'])].copy()
            self.df_inst_pivot = df_inst_filtered.reset_index().pivot_table(index='date', columns='name', values='net_buy_sell').fillna(0)
            self.df_inst_pivot = self.df_inst_pivot.rename(columns={'Foreign_Investor': '外資累積買賣超', 'Investment_Trust': '投信累積買賣超'})
            self.df_inst_pivot['外資累積買賣超'] = self.df_inst_pivot['外資累積買賣超'].cumsum()
            self.df_inst_pivot['投信累積買賣超'] = self.df_inst_pivot['投信累積買賣超'].cumsum()
            print(f"成功獲取 {self.ticker} 外資與投信數據。")
        else:
            print(f"警告: 無法獲取 {self.ticker} 外資與投信數據，跳過。")
            self.df_inst_pivot = pd.DataFrame(index=self.df.index)


        # STEP 11: 融資融券與散戶動態
        df_margin_raw = self._fetch_finmind_api_data("TaiwanStockMarginPurchaseShortSale", self.ticker, start_date_finmind, end_date_finmind)
        if not df_margin_raw.empty:
            df_margin_raw['date'] = pd.to_datetime(df_margin_raw['date'])
            self.df_margin_data = df_margin_raw.set_index('date').sort_index()
            print(f"成功獲取 {self.ticker} 融資融券數據。")
        else:
            print(f"警告: 無法獲取 {self.ticker} 融資融券數據，跳過。")
            self.df_margin_data = pd.DataFrame(index=self.df.index)

        # STEP 14: 期貨與借券
        start_date_one_year = (self.df.index.max() - pd.Timedelta(days=365)).strftime('%Y-%m-%d')

        df_futures_raw = self._fetch_finmind_api_data("TaiwanFuturesInstitutionalInvestors", "TX", start_date_one_year, end_date_finmind)
        if not df_futures_raw.empty:
            df_futures_raw['date'] = pd.to_datetime(df_futures_raw['date'])
            df_futures_raw = df_futures_raw.set_index('date').sort_index()
            df_foreign_futures = df_futures_raw[df_futures_raw['institutional_investors'] == '外資'].copy()
            df_foreign_futures['Foreign_TX_Net_OI'] = df_foreign_futures['long_open_interest_balance_volume'] - df_foreign_futures['short_open_interest_balance_volume']
            self.df_futures_data = df_foreign_futures[['Foreign_TX_Net_OI']]
            print("成功獲取外資台指期貨數據。")
        else:
            print("警告: 無法獲取外資台指期貨數據，跳過。")
            self.df_futures_data = pd.DataFrame(index=self.df.index)

        df_securities_raw = self._fetch_finmind_api_data("TaiwanStockSecuritiesLending", self.ticker, start_date_one_year, end_date_finmind)
        if not df_securities_raw.empty:
            df_securities_raw['date'] = pd.to_datetime(df_securities_raw['date'])
            df_securities_raw = df_securities_raw.set_index('date').sort_index()
            self.df_securities_lending = df_securities_raw.groupby('date')['volume'].sum().to_frame(name='sell_balance')
            print("成功獲取借券賣出餘額數據。")
        else:
            print("警告: 無法獲取借券賣出餘額數據，跳過。")
            self.df_securities_lending = pd.DataFrame(index=self.df.index)

        # 合併所有數據到 df_extended
        self.df_extended = self.df.copy()
        self.df_extended = pd.merge(self.df_extended, self.df_inst_pivot, left_index=True, right_index=True, how='left')
        self.df_extended = pd.merge(self.df_extended, self.df_margin_data[['MarginPurchaseTodayBalance', 'ShortSaleTodayBalance']], left_index=True, right_index=True, how='left')
        self.df_extended = pd.merge(self.df_extended, self.df_futures_data, left_index=True, right_index=True, how='left')
        self.df_extended = pd.merge(self.df_extended, self.df_securities_lending, left_index=True, right_index=True, how='left')
        self.df_extended = self.df_extended.ffill().fillna(0)

        print(f"----- {self.ticker} 數據下載與初步處理完成 -----")
        return True

    def engineer_features(self):
        """構建 AI 模型所需的 20+ 複合特徵矩陣。"""
        print(f"\n----- 開始為 {self.ticker} 構建 AI 特徵 -----")
        self.ml_df = self.df_extended.copy()

        # 20日乖離率 (Bias_20)
        self.ml_df['Bias_20'] = ((self.ml_df['close'] - self.ml_df['ma20']) / self.ml_df['ma20']) * 100

        # 日報酬率 (Daily_Return)
        self.ml_df['Daily_Return'] = self.ml_df['close'].pct_change() * 100

        # RSI
        self.ml_df['RSI'] = self.ml_df['rsi']

        # MACD 柱狀體
        self.ml_df['MACD_Histogram'] = self.ml_df['macd_histogram']

        # 布林通道寬度 (BB_Width)
        self.ml_df['BB_Width'] = ((self.ml_df['upper_band'] - self.ml_df['lower_band']) / self.ml_df['middle_band']) * 100

        # 正規化 ATR
        self.ml_df['Normalized_ATR'] = (self.ml_df['atr'] / self.ml_df['close'] * 100)

        # 跨市場特徵 (模擬 FinMind 或 yfinance 數據，此處為簡化假設已在 df_extended 中)
        # 如果是即時運行，需要單獨下載這些數據
        cross_market_tickers = {'^SOX': 'SOX_Change', '^TNX': 'TNX_Change', '^VIX': 'VIX_Change'}
        for ticker_symbol, col_name in cross_market_tickers.items():
            try:
                cross_market_data = yf.download(ticker_symbol, period='3y', progress=False)
                if not cross_market_data.empty:
                    cross_market_data[col_name] = cross_market_data['Close'].pct_change() * 100
                    self.ml_df[col_name] = cross_market_data[col_name].reindex(self.ml_df.index, method='ffill').fillna(0)
                else:
                    self.ml_df[col_name] = 0.0
            except Exception:
                self.ml_df[col_name] = 0.0

        # Lead-Lag 領先特徵：期貨未平倉與借券餘額的平移特徵
        lag_periods = [1, 3, 5]
        for col in ['Foreign_TX_Net_OI', 'sell_balance']:
            for lag in lag_periods:
                self.ml_df[f'{col}_Lag{lag}'] = self.ml_df[col].shift(lag)

        # 標籤工程：隔日收盤價大於今日收盤價標記為 1 (漲)，否則為 0 (Target)
        self.ml_df['Next_Close'] = self.ml_df['close'].shift(-1)
        self.ml_df['Target'] = (self.ml_df['Next_Close'] > self.ml_df['close']).astype(int)

        # 清理 NaN 值
        self.mega_df_cleaned_final = self.ml_df.dropna().copy()

        # 定義最終特徵集
        self.final_feature_cols = [
            'Bias_20', 'Daily_Return', 'RSI', 'K', 'D', 'J', 'CCI', 'Williams_R',
            'ROC_12', 'MFI', 'OBV', 'ADX', 'Plus_DI', 'Minus_DI', 'PSY_12',
            'Volume_Ratio', 'BB_PctB', 'EMA_50', 'EMA_200', 'Momentum_5', 'Momentum_20',
            '外資累積買賣超', '投信累積買賣超',
            'MarginPurchaseTodayBalance', 'ShortSaleTodayBalance',
            'Foreign_TX_Net_OI', 'sell_balance',
            'Daily_Return_1d', 'Daily_Return_3d', 'Daily_Return_5d', 'MACD_Histogram',
            'BB_Width', 'Normalized_ATR', 'SOX_Change', 'TNX_Change', 'VIX_Change',
            'Foreign_TX_Net_OI_Lag1', 'Foreign_TX_Net_OI_Lag3', 'Foreign_TX_Net_OI_Lag5',
            'sell_balance_Lag1', 'sell_balance_Lag3', 'sell_balance_Lag5'
        ]
        # 確保只包含 mega_df_cleaned_final 中實際存在的欄位
        self.final_feature_cols = [col for col in self.final_feature_cols if col in self.mega_df_cleaned_final.columns]

        print(f"----- {self.ticker} 特徵工程完成，最終特徵矩陣形狀: {self.mega_df_cleaned_final.shape} -----")

    def run_ai_analysis(self):
        """訓練 AI 模型並進行回測。"""
        print(f"\n----- 開始為 {self.ticker} 進行 AI 分析 -----")
        if self.mega_df_cleaned_final.empty or not self.final_feature_cols:
            print("錯誤: 特徵矩陣為空或未定義特徵欄位，無法進行 AI 分析。")
            return

        X = self.mega_df_cleaned_final[self.final_feature_cols]
        y = self.mega_df_cleaned_final['Target']

        if X.shape[0] < 2: # 至少要有兩筆資料才能分割
            print("錯誤: 數據量不足，無法進行訓練與測試分割。")
            return

        X_train, self.X_test_mega, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)

        self.model_mega = RandomForestClassifier(n_estimators=200, random_state=42)
        self.model_mega.fit(X_train, y_train)
        self.y_pred_mega = self.model_mega.predict(self.X_test_mega)
        accuracy = accuracy_score(y_test, self.y_pred_mega)
        print(f"擴充特徵後，模型在測試集上的準確度: {accuracy:.2f}")

        # 向量化回測
        backtest_df = pd.DataFrame(index=self.X_test_mega.index)
        backtest_df['Daily_Return'] = self.mega_df_cleaned_final.loc[self.X_test_mega.index, 'Daily_Return']
        backtest_df['Signal'] = self.y_pred_mega
        backtest_df['Strategy_Raw_Return'] = backtest_df['Signal'].shift(1) * backtest_df['Daily_Return']
        backtest_df['Buy_Hold_Return'] = backtest_df['Daily_Return']

        position_change = backtest_df['Signal'].shift(1).diff().abs()
        transaction_cost_per_trade = 0.003
        backtest_df['Transaction_Cost'] = position_change * transaction_cost_per_trade * 100
        backtest_df['Strategy_Net_Return'] = backtest_df['Strategy_Raw_Return'] - backtest_df['Transaction_Cost'].fillna(0)

        backtest_df['Buy_Hold_Cumulative'] = (1 + backtest_df['Buy_Hold_Return'].fillna(0) / 100).cumprod()
        backtest_df['Strategy_Cumulative'] = (1 + backtest_df['Strategy_Net_Return'].fillna(0) / 100).cumprod()

        if not backtest_df.empty:
            final_buy_hold_return = (backtest_df['Buy_Hold_Cumulative'].iloc[-1] - 1) * 100
            final_strategy_return = (backtest_df['Strategy_Cumulative'].iloc[-1] - 1) * 100
            print(f"買進持有最終累積總報酬率: {final_buy_hold_return:.2f}%")
            print(f"AI 策略最終累積總報酬率 (含成本): {final_strategy_return:.2f}%")
        else:
            print("回測數據為空，無法計算報酬率。")

        print(f"----- {self.ticker} AI 分析與回測完成 -----")

    def get_macd_diagnostics(self):
        """20+ 種 MACD 判別：描述目前 MACD 所處階段，不宣稱單一訊號必然預測上漲。"""
        if self.df.empty or len(self.df) < 40:
            return "資料不足，無法完成 MACD 深度判別。"
        d = self.df
        r = d.iloc[-1]
        prev = d.iloc[-2]
        hist = d['macd_histogram']
        macd = d['macd_line']
        signal = d['signal_line']
        close = d['close']
        atr = d['atr']

        def f(x, n=3):
            try:
                return f"{float(x):.{n}f}"
            except Exception:
                return "N/A"

        # cross / persistence
        golden_now = macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2]
        death_now = macd.iloc[-1] < signal.iloc[-1] and macd.iloc[-2] >= signal.iloc[-2]
        zero_bull = macd.iloc[-1] > 0
        zero_bear = macd.iloc[-1] < 0
        hist_pos = hist.iloc[-1] > 0
        hist_rising = hist.iloc[-1] > hist.iloc[-2]
        hist_accel = (hist.iloc[-1] - hist.iloc[-2]) > (hist.iloc[-2] - hist.iloc[-3])
        macd_slope5 = macd.iloc[-1] - macd.iloc[-6]
        signal_slope5 = signal.iloc[-1] - signal.iloc[-6]
        hist_slope5 = hist.iloc[-1] - hist.iloc[-6]
        hist_sum5 = hist.tail(5).sum()
        hist_sum20 = hist.tail(20).sum()
        persistence5 = int((hist.tail(5) > 0).sum())
        persistence10 = int((hist.tail(10) > 0).sum())
        above_ema12 = close.iloc[-1] > d['ema12'].iloc[-1]
        above_ema26 = close.iloc[-1] > d['ema26'].iloc[-1]
        price_up20 = close.iloc[-1] > close.iloc[-21]
        macd_up20 = macd.iloc[-1] > macd.iloc[-21]
        price_up60 = close.iloc[-1] > close.iloc[-61] if len(d) >= 62 else np.nan
        macd_up60 = macd.iloc[-1] > macd.iloc[-61] if len(d) >= 62 else np.nan
        atrv = float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) and atr.iloc[-1] else np.nan
        norm_hist = hist.iloc[-1] / atrv if np.isfinite(atrv) else np.nan
        macd_pct = macd.iloc[-1] / close.iloc[-1] * 100 if close.iloc[-1] else np.nan

        # 最近交叉距今
        cross_age = "無法判定"
        cross_type = "尚未發生近期交叉"
        for j in range(1, min(len(d)-1, 60)+1):
            a, b = macd.iloc[-j-1], signal.iloc[-j-1]
            c, e = macd.iloc[-j], signal.iloc[-j]
            if c > e and a <= b:
                cross_age, cross_type = j-1, "黃金交叉"
                break
            if c < e and a >= b:
                cross_age, cross_type = j-1, "死亡交叉"
                break

        # divergence: 比較 20/60 日前的價格與 MACD 方向
        div20 = "正向" if price_up20 and macd_up20 else "價格強於MACD（留意背離）" if price_up20 and not macd_up20 else "MACD強於價格（留意底背離）" if (not price_up20 and macd_up20) else "同步偏弱"
        if np.isfinite(price_up60):
            div60 = "正向" if price_up60 and macd_up60 else "價格強於MACD（留意頂背離）" if price_up60 and not macd_up60 else "MACD強於價格（留意底背離）" if (not price_up60 and macd_up60) else "同步偏弱"
        else:
            div60 = "資料不足"

        checks = [
            ("01｜MACD 線 vs Signal", "多方" if macd.iloc[-1] > signal.iloc[-1] else "空方"),
            ("02｜MACD 柱體正負", "正柱" if hist_pos else "負柱"),
            ("03｜MACD 零軸位置", "零軸上" if zero_bull else "零軸下"),
            ("04｜MACD 5日斜率", "上升" if macd_slope5 > 0 else "下降"),
            ("05｜Signal 5日斜率", "上升" if signal_slope5 > 0 else "下降"),
            ("06｜柱體即日變化", "擴大" if hist_rising else "縮小"),
            ("07｜柱體加速度", "加速" if hist_accel else "減速"),
            ("08｜當日交叉", "剛黃金交叉" if golden_now else "剛死亡交叉" if death_now else "無新交叉"),
            ("09｜最近交叉型態", f"{cross_type}／約 {cross_age} 日前" if isinstance(cross_age, int) else cross_type),
            ("10｜5日正柱天數", f"{persistence5}/5"),
            ("11｜10日正柱天數", f"{persistence10}/10"),
            ("12｜5日柱體合計", f(hist_sum5)),
            ("13｜20日柱體合計", f(hist_sum20)),
            ("14｜價格 vs EMA12", "價格在EMA12上" if above_ema12 else "價格在EMA12下"),
            ("15｜價格 vs EMA26", "價格在EMA26上" if above_ema26 else "價格在EMA26下"),
            ("16｜MACD/價格動能20日", "同步上升" if price_up20 and macd_up20 else div20),
            ("17｜MACD/價格動能60日", div60),
            ("18｜柱體/ATR標準化", f(norm_hist)),
            ("19｜MACD/股價百分比", f(macd_pct) + "%"),
            ("20｜零軸＋柱體組合", "強多區" if zero_bull and hist_pos else "多方修復" if zero_bull and not hist_pos else "空方修復" if zero_bear and hist_pos else "強空區"),
            ("21｜MACD/Signal/價格三者", "三者偏多" if macd.iloc[-1] > signal.iloc[-1] and above_ema12 and above_ema26 else "三者偏空" if macd.iloc[-1] < signal.iloc[-1] and not above_ema12 and not above_ema26 else "訊號分歧"),
            ("22｜MACD 柱體連續性", "偏多持續" if persistence10 >= 7 else "偏空持續" if persistence10 <= 3 else "震盪轉折"),
            ("23｜MACD 趨勢階段", "上升趨勢" if zero_bull and macd_slope5 > 0 else "高檔鈍化/轉弱" if zero_bull and macd_slope5 <= 0 else "下降趨勢" if zero_bear and macd_slope5 < 0 else "空方修復"),
            ("24｜綜合 MACD 結論", "偏多" if sum([macd.iloc[-1] > signal.iloc[-1], zero_bull, hist_pos, hist_rising, macd_slope5 > 0]) >= 4 else "偏空" if sum([macd.iloc[-1] > signal.iloc[-1], zero_bull, hist_pos, hist_rising, macd_slope5 > 0]) <= 1 else "中性/等待確認"),
        ]
        score = sum(v in ("多方", "正柱", "零軸上", "上升", "擴大", "加速", "剛黃金交叉", "強多區", "三者偏多", "偏多持續", "上升趨勢", "偏多") for _, v in checks)
        md = [f"## 📊 {self.ticker} MACD 24 項深度判別", "", f"**目前數值**：MACD {f(macd.iloc[-1])}｜Signal {f(signal.iloc[-1])}｜Histogram {f(hist.iloc[-1])}｜EMA12 {f(d['ema12'].iloc[-1])}｜EMA26 {f(d['ema26'].iloc[-1])}", "", f"### MACD 結構分數：{score}/24", ""]
        for name, result in checks:
            md.append(f"- **{name}**：{result}")
        md += ["", "> 判讀原則：MACD 是趨勢／動能工具；背離、交叉或柱體變化都應與價格結構、成交量及其他指標交叉驗證，不代表單一訊號必然預測未來漲跌。"]
        return "\n".join(md)

    def get_leading_indicators_analysis(self):
        """20 個先行訊號候選。這些是可能較早反映動能/資金變化的特徵，不保證真正領先價格。"""
        if self.df.empty or len(self.df) < 30:
            return "資料不足，無法完成領先指標分析。"
        # 領先指標需讀取 engineer_features() 產生的跨市場欄位
        d = self.ml_df if not self.ml_df.empty else self.df
        r = d.iloc[-1]
        def num(x):
            try:
                return float(x)
            except Exception:
                return np.nan
        def status(ok, bad=None):
            return "🟢 偏多" if ok else "🔴 偏空" if bad is None or bad else "🟡 中性"
        items = []
        vr = num(r.get('Volume_Ratio'))
        items.append(("01｜成交量相對20日均量", f"{vr:.2f}x" if np.isfinite(vr) else "N/A", "🟢 放量" if np.isfinite(vr) and vr >= 1.5 else "🔴 量縮" if np.isfinite(vr) and vr < 0.7 else "🟡 正常"))
        obv = d['OBV']
        obv_slope = obv.iloc[-1] - obv.iloc[-6]
        items.append(("02｜OBV 5日斜率", f"{obv_slope:,.0f}", "🟢 資金流入" if obv_slope > 0 else "🔴 資金流出"))
        mfi = num(r.get('MFI'))
        items.append(("03｜MFI 資金流", f"{mfi:.1f}", "🟢 >50" if mfi > 50 else "🔴 <50" if mfi < 40 else "🟡 中性"))
        rsi_slope = num(r['rsi'] - d['rsi'].iloc[-6])
        items.append(("04｜RSI 5日斜率", f"{rsi_slope:.2f}", "🟢 上升" if rsi_slope > 0 else "🔴 下降"))
        kd_diff = num(r['K'] - r['D'])
        kd_prev = num(d['K'].iloc[-2] - d['D'].iloc[-2])
        items.append(("05｜KD 交叉先行", f"K-D={kd_diff:.2f}", "🟢 黃金交叉" if kd_diff > 0 and kd_prev <= 0 else "🔴 死亡交叉" if kd_diff < 0 and kd_prev >= 0 else "🟢 K>D" if kd_diff > 0 else "🔴 K<D"))
        cci = num(r.get('CCI'))
        items.append(("06｜CCI 動能", f"{cci:.1f}", "🟢 >100" if cci > 100 else "🔴 <-100" if cci < -100 else "🟡 中性"))
        wr = num(r.get('Williams_R'))
        items.append(("07｜Williams %R", f"{wr:.1f}", "🟢 從超賣回升" if len(d)>=2 and wr > num(d['Williams_R'].iloc[-2]) and wr < -50 else "🟡 觀察"))
        roc = num(r.get('ROC_12'))
        items.append(("08｜ROC12 速度", f"{roc:.2f}%", "🟢 正動能" if roc > 0 else "🔴 負動能"))
        psy = num(r.get('PSY_12'))
        items.append(("09｜PSY12 上漲天數", f"{psy:.1f}%", "🟢 >58%" if psy > 58 else "🔴 <42%" if psy < 42 else "🟡 中性"))
        adx = num(r.get('ADX')); pdi = num(r.get('Plus_DI')); mdi = num(r.get('Minus_DI'))
        items.append(("10｜ADX +DI/-DI", f"ADX {adx:.1f}｜+DI {pdi:.1f}｜-DI {mdi:.1f}", "🟢 多方趨勢" if pdi > mdi and adx >= 20 else "🔴 空方趨勢" if mdi > pdi and adx >= 20 else "🟡 趨勢弱"))
        bbw = num(r.get('BB_Width'))
        if 'BB_Width' in d.columns and len(d) >= 6:
            bbw_prev = num(d['BB_Width'].iloc[-6])
            bbw_signal = "🟢 波動擴張" if np.isfinite(bbw) and np.isfinite(bbw_prev) and bbw > bbw_prev else "🟡 收斂"
        else:
            bbw_signal = "⚪ 無資料"
        items.append(("11｜布林寬度擴張", f"{bbw:.2f}%" if np.isfinite(bbw) else "N/A", bbw_signal))
        pctb = num(r.get('BB_PctB'))
        items.append(("12｜BB %B", f"{pctb:.1f}%", "🟢 強勢區" if pctb >= 80 else "🔴 弱勢區" if pctb <= 20 else "🟡 中間區"))
        bias = (num(r['close']) - num(r['ma20'])) / num(r['ma20']) * 100 if num(r['ma20']) else np.nan
        items.append(("13｜20日乖離", f"{bias:.2f}%", "🟢 多方" if bias > 0 else "🔴 空方"))
        mom5 = num(r.get('Momentum_5')); mom20 = num(r.get('Momentum_20'))
        items.append(("14｜5日 vs 20日動能", f"5D {mom5:.2f}%｜20D {mom20:.2f}%", "🟢 加速" if mom5 > mom20 and mom5 > 0 else "🔴 轉弱" if mom5 < mom20 else "🟡 同步"))
        # 籌碼
        if '外資累積買賣超' in d.columns and d['外資累積買賣超'].notna().any():
            x = d['外資累積買賣超'].dropna(); ch = x.iloc[-1] - x.iloc[-6] if len(x) >= 6 else x.iloc[-1]-x.iloc[0]
            items.append(("15｜外資累積買賣超斜率", f"{ch:,.0f}", "🟢 偏買" if ch > 0 else "🔴 偏賣"))
        else: items.append(("15｜外資累積買賣超斜率", "N/A", "⚪ 無資料"))
        if '投信累積買賣超' in d.columns and d['投信累積買賣超'].notna().any():
            x = d['投信累積買賣超'].dropna(); ch = x.iloc[-1] - x.iloc[-6] if len(x) >= 6 else x.iloc[-1]-x.iloc[0]
            items.append(("16｜投信累積買賣超斜率", f"{ch:,.0f}", "🟢 偏買" if ch > 0 else "🔴 偏賣"))
        else: items.append(("16｜投信累積買賣超斜率", "N/A", "⚪ 無資料"))
        if 'MarginPurchaseTodayBalance' in d.columns:
            ch = num(r['MarginPurchaseTodayBalance']) - num(d['MarginPurchaseTodayBalance'].iloc[-6])
            items.append(("17｜融資餘額5日變化", f"{ch:,.0f}", "🔴 融資增加" if ch > 0 else "🟢 融資下降"))
        else: items.append(("17｜融資餘額5日變化", "N/A", "⚪ 無資料"))
        if 'Foreign_TX_Net_OI' in d.columns and d['Foreign_TX_Net_OI'].notna().any():
            ch = num(r['Foreign_TX_Net_OI']) - num(d['Foreign_TX_Net_OI'].iloc[-6])
            items.append(("18｜外資台指期淨未平倉5日變化", f"{ch:,.0f}", "🟢 增加偏多" if ch > 0 else "🔴 增加偏空"))
        else: items.append(("18｜外資台指期淨未平倉5日變化", "N/A", "⚪ 無資料"))
        if 'sell_balance' in d.columns and d['sell_balance'].notna().any():
            ch = num(r['sell_balance']) - num(d['sell_balance'].iloc[-6])
            items.append(("19｜借券賣出量5日變化", f"{ch:,.0f}", "🔴 增加空方壓力" if ch > 0 else "🟢 下降"))
        else: items.append(("19｜借券賣出量5日變化", "N/A", "⚪ 無資料"))
        for n, col, label in [("20","SOX_Change","SOX 變化"),("21","VIX_Change","VIX 變化"),("22","TNX_Change","TNX 變化")]:
            v = num(r.get(col))
            if n == "20": sig = "🟢 科技風險偏正" if v > 0 else "🔴 科技風險偏弱"
            elif n == "21": sig = "🔴 風險升溫" if v > 0 else "🟢 風險降溫"
            else: sig = "🟡 利率觀察" if abs(v) < 1 else "🔴 利率壓力" if v > 0 else "🟢 利率回落"
            items.append((f"{n}｜{label}", f"{v:.2f}%" if np.isfinite(v) else "N/A", sig))

        bull = sum("🟢" in x[2] for x in items)
        bear = sum("🔴" in x[2] for x in items)
        md = [f"## 🧭 {self.ticker} 22 項先行訊號", "", f"**先行訊號統計：🟢 {bull}｜🔴 {bear}｜🟡/⚪ {len(items)-bull-bear}**", ""]
        for name, value, sig in items:
            md.append(f"- **{name}**：{value}　{sig}")
        md += ["", "> 注意：這裡的「領先／先行」是指可能較早反映動能、量價或資金變化的觀察特徵，不代表具有穩定的因果領先關係。"]
        return "\n".join(md)

    def get_multi_panel_plot(self, plot_df):
        """K線 + 均線 + 成交量 + MACD + RSI + KD，一次看完整結構。"""
        d = plot_df.tail(260).copy()
        fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.035,
                            row_heights=[0.48, 0.16, 0.20, 0.16],
                            subplot_titles=(f"{self.ticker} 股價／均線", "成交量", "MACD 12/26/9", "RSI / KD"))
        fig.add_trace(go.Candlestick(x=d.index, open=d['open'], high=d['high'], low=d['low'], close=d['close'], name='K線'), row=1, col=1)
        for col, name in [('ma5','MA5'),('ma20','MA20'),('ma60','MA60'),('EMA_200','EMA200')]:
            if col in d:
                fig.add_trace(go.Scatter(x=d.index, y=d[col], mode='lines', name=name), row=1, col=1)
        fig.add_trace(go.Bar(x=d.index, y=d['volume'], name='成交量'), row=2, col=1)
        fig.add_trace(go.Scatter(x=d.index, y=d['Volume_MA20'], mode='lines', name='Volume MA20'), row=2, col=1)
        fig.add_trace(go.Scatter(x=d.index, y=d['macd_line'], mode='lines', name='MACD'), row=3, col=1)
        fig.add_trace(go.Scatter(x=d.index, y=d['signal_line'], mode='lines', name='Signal'), row=3, col=1)
        fig.add_trace(go.Bar(x=d.index, y=d['macd_histogram'], name='Histogram'), row=3, col=1)
        fig.add_trace(go.Scatter(x=d.index, y=d['rsi'], mode='lines', name='RSI'), row=4, col=1)
        fig.add_trace(go.Scatter(x=d.index, y=d['K'], mode='lines', name='K'), row=4, col=1)
        fig.add_trace(go.Scatter(x=d.index, y=d['D'], mode='lines', name='D'), row=4, col=1)
        fig.add_hline(y=0, row=3, col=1)
        fig.add_hline(y=70, row=4, col=1)
        fig.add_hline(y=30, row=4, col=1)
        fig.update_layout(height=1050, template='plotly_white', xaxis_rangeslider_visible=False, hovermode='x unified', legend=dict(orientation='h'))
        return fig

    def get_candlestick_plot(self, plot_df):
        """繪製 K 線圖與均線"""
        fig = go.Figure(
            data=[
                go.Candlestick(
                    x=plot_df.index,
                    open=plot_df['open'],
                    high=plot_df['high'],
                    low=plot_df['low'],
                    close=plot_df['close'],
                    name='K線'
                ),
                go.Scatter(
                    x=plot_df.index,
                    y=plot_df['ma5'],
                    mode='lines',
                    name='MA5',
                    line=dict(color='blue', width=1)
                ),
                go.Scatter(
                    x=plot_df.index,
                    y=plot_df['ma20'],
                    mode='lines',
                    name='MA20',
                    line=dict(color='orange', width=1)
                ),
            ]
        )
        fig.update_layout(
            title=f"{self.ticker}.TW 股價走勢與均線",
            xaxis_rangeslider_visible=False,
            template='plotly_white',
            height=500
        )
        return fig

    def get_bias_text(self, plot_df):
        """計算並返回乖離率文字，避免因 Bias_20 尚未加入 K 線 df 而顯示 nan。"""
        if 'close' in plot_df.columns and 'ma20' in plot_df.columns:
            latest_close = float(plot_df['close'].iloc[-1])
            latest_ma20 = float(plot_df['ma20'].iloc[-1])
            latest_bias_20 = ((latest_close - latest_ma20) / latest_ma20 * 100) if latest_ma20 else np.nan
        else:
            latest_bias_20 = np.nan
        return f"{self.ticker}.TW 最新 20 日乖離率: {latest_bias_20:.2f}%"

    def get_indicator_summary(self):
        """整理最新技術指標，提供前端快速判讀。"""
        if self.df.empty:
            return "尚無技術指標資料。"
        r = self.df.iloc[-1]

        def val(name, digits=2):
            x = r.get(name, np.nan)
            try:
                x = float(x)
                return f"{x:.{digits}f}" if np.isfinite(x) else "N/A"
            except Exception:
                return "N/A"

        close = float(r['close']) if pd.notna(r.get('close')) else np.nan
        ma20 = float(r['ma20']) if pd.notna(r.get('ma20')) else np.nan
        trend = "多頭" if close > ma20 else "空頭"
        k = float(r.get('K', np.nan))
        d = float(r.get('D', np.nan))
        plus_di = float(r.get('Plus_DI', np.nan))
        minus_di = float(r.get('Minus_DI', np.nan))
        macd_hist = float(r.get('macd_histogram', np.nan))
        kd = "K>D（偏多）" if np.isfinite(k) and np.isfinite(d) and k > d else "K<D（偏空）"
        di = "＋DI > −DI（偏多）" if np.isfinite(plus_di) and np.isfinite(minus_di) and plus_di > minus_di else "＋DI < −DI（偏空）"
        macd = "MACD 柱體正值（偏多）" if np.isfinite(macd_hist) and macd_hist > 0 else "MACD 柱體負值（偏空）"

        return (
            f"**{self.ticker}.TW 最新技術指標**\n\n"
            f"**趨勢**　收盤 {close:.2f}｜MA5 {val('ma5')}｜MA20 {val('ma20')}｜MA60 {val('ma60')}｜MA120 {val('ma120')}｜MA240 {val('ma240')} → **{trend}**\n\n"
            f"**動能**　RSI {val('rsi')}｜MACD {val('macd_line')}｜Signal {val('signal_line')}｜柱體 {val('macd_histogram')}｜ROC12 {val('ROC_12')}｜5日動能 {val('Momentum_5')}%｜20日動能 {val('Momentum_20')}%\n\n"
            f"**KD / 超買超賣**　K {val('K')}｜D {val('D')}｜J {val('J')}｜CCI {val('CCI')}｜Williams %R {val('Williams_R')}｜MFI {val('MFI')}｜PSY12 {val('PSY_12')}%\n\n"
            f"**趨勢強度**　ADX {val('ADX')}｜＋DI {val('Plus_DI')}｜−DI {val('Minus_DI')} → **{di}**\n\n"
            f"**波動 / 布林**　ATR {val('atr')}｜ATR% {val('Normalized_ATR')}%｜BB Width {val('BB_Width')}%｜BB %B {val('BB_PctB')}%\n\n"
            f"**成交量**　Volume {val('volume', 0)}｜Volume MA20 {val('Volume_MA20', 0)}｜量比 {val('Volume_Ratio')}｜OBV {val('OBV', 0)}\n\n"
            f"**均線結構**　EMA50 {val('EMA_50')}｜EMA200 {val('EMA_200')}｜{kd}｜{macd}"
        )

    def get_news_analysis(self, days=5):
        """最近新聞的透明規則式事件摘要，不把關鍵字分類冒充成事實。"""
        news = self._fetch_news(days=days)
        if news.empty:
            return "## 📰 新聞分析\n\n近期未取得 FinMind 新聞，無法進行事件分析。"

        positive_kw = ["營收", "獲利", "成長", "增加", "上升", "接單", "訂單", "擴產",
                       "合作", "AI", "伺服器", "法說", "上調", "突破", "投資", "新品", "出貨"]
        negative_kw = ["下滑", "衰退", "下降", "減少", "虧損", "下修", "砍單", "取消",
                       "裁員", "處分", "違約", "訴訟", "調查", "制裁", "關稅", "風險",
                       "事故", "停工", "延遲", "利空"]
        event_kw = ["法說", "財報", "營收", "股利", "增資", "減資", "併購", "處分",
                    "接單", "訂單", "擴產", "新品", "合作", "投資", "訴訟", "調查",
                    "制裁", "關稅", "股東會"]

        rows, pos_count, neg_count, event_count = [], 0, 0, 0
        for _, r in news.iterrows():
            title = str(r.get("標題", "")).strip()
            low = title.lower()
            pos_hits = [k for k in positive_kw if k.lower() in low]
            neg_hits = [k for k in negative_kw if k.lower() in low]
            event_hits = [k for k in event_kw if k.lower() in low]
            pos_count += int(bool(pos_hits))
            neg_count += int(bool(neg_hits))
            event_count += int(bool(event_hits))
            if pos_hits and not neg_hits:
                tendency = "🟢 偏正面關鍵字"
            elif neg_hits and not pos_hits:
                tendency = "🔴 偏負面關鍵字"
            elif pos_hits and neg_hits:
                tendency = "🟡 混合訊號"
            else:
                tendency = "⚪ 中性/未分類"
            rows.append((str(r.get("日期", "")), str(r.get("來源", "")), title,
                         "、".join(event_hits[:3]) if event_hits else "一般", tendency))

        overall = ("🟢 近期標題關鍵字整體偏正面" if pos_count > neg_count
                   else "🔴 近期標題關鍵字整體偏負面" if neg_count > pos_count
                   else "🟡 近期標題關鍵字多空接近")
        lines = [
            f"## 📰 {self.ticker} 最近 {days} 日新聞分析", "",
            f"**新聞筆數：** {len(rows)}　｜　**正面關鍵字新聞：** {pos_count}　｜　**負面關鍵字新聞：** {neg_count}　｜　**事件型新聞：** {event_count}",
            f"**規則式摘要：** {overall}", "",
            "> ⚠️ 這是標題關鍵字的機械式分類，不等於新聞實際影響；重大事件仍應閱讀原文並確認日期、來源與公司公告。", ""
        ]
        for i, (date, source, title, event_type, tendency) in enumerate(rows[:15], 1):
            lines.append(f"{i}. **{date}｜{source}**　{event_type}　{tendency}  \n   {title}")
        return "\n".join(lines)

    def get_ai_prediction_text(self):
        """Gemini 綜合 MACD、先行訊號與新聞。"""
        if not self.gemini_api_key:
            return "Gemini API Key 未設定，無法提供 AI 盤勢分析。"
        if self.df.empty:
            return "無足夠數據進行 AI 盤勢分析。"
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.gemini_api_key)
            model = genai.GenerativeModel('gemini-3.7-flash')
            latest = self.df.tail(5)[['close','rsi','macd_line','signal_line','macd_histogram','K','D','MFI','Volume_Ratio','ADX','Plus_DI','Minus_DI','Momentum_5','Momentum_20']].to_string()
            news = self._fetch_news(days=5)
            news_text = news[['日期','來源','標題']].head(15).to_string(index=False) if not news.empty else '近期沒有取得 FinMind 新聞。'
            prompt = f"""你是一位資深台股量化分析師。請分析 {self.ticker}，嚴格區分『數據事實』與『推論』。\n\n近期指標：\n{latest}\n\nMACD 深度判別：\n{self.get_macd_diagnostics()}\n\n先行訊號：\n{self.get_leading_indicators_analysis()}\n\n近期新聞：\n{news_text}\n\n新聞規則式分析：\n{self.get_news_analysis(days=5)}\n\n請輸出：1. 多空結論 2. MACD 是否支持趨勢延續/轉折 3. 先行訊號是否一致 4. 新聞可能影響 5. 需要確認的風險位階。嚴格區分新聞事實與推論，不要保證獲利，不要捏造新聞。"""
            return model.generate_content(prompt).text
        except Exception as e:
            return f"呼叫 Gemini API 時發生錯誤: {e}"

    def scan_watchlist(self, tickers_str: str):
        """掃描自選股清單，返回基本資訊和多空訊號。"""
        results = []
        tickers = [t.strip() for t in tickers_str.split(',') if t.strip()]

        if not tickers:
            return pd.DataFrame(columns=['股票代號', '收盤價', '漲跌幅 (%)', 'MA20', '多空訊號'])

        for ticker in tickers:
            try:
                ticker_with_suffix = f'{ticker}.TW' if not ticker.endswith('.TW') else ticker
                df_scan = yf.download(ticker_with_suffix, period='60d', progress=False)

                if df_scan.empty:
                    results.append({'股票代號': ticker, '收盤價': np.nan, '漲跌幅 (%)': np.nan, 'MA20': np.nan, '多空訊號': '數據不足'})
                    continue

                df_scan = self._fix_col_names(df_scan) # 使用內部清洗方法
                latest_close = df_scan['close'].iloc[-1]
                previous_close = df_scan['close'].iloc[-2]
                daily_change_percent = ((latest_close - previous_close) / previous_close) * 100
                ma20 = df_scan['close'].rolling(window=20).mean().iloc[-1]

                signal = '多頭 (股價 > MA20)' if latest_close > ma20 else '空頭 (股價 < MA20)'

                results.append({
                    '股票代號': ticker,
                    '收盤價': f'{latest_close:.2f}',
                    '漲跌幅 (%)': f'{daily_change_percent:.2f}',
                    'MA20': f'{ma20:.2f}',
                    '多空訊號': signal
                })

            except Exception as e:
                results.append({
                    '股票代號': ticker,
                    '收盤價': np.nan,
                    '漲跌幅 (%)': np.nan,
                    'MA20': np.nan,
                    '多空訊號': f'錯誤: {e}'
                })
        return pd.DataFrame(results)



class TaiwanMarketScanner:
    """全市場選股：先用 TWSE/TPEx 當日快照縮小候選池，再用 yfinance 歷史K線做多因子評分。"""

    TWSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    TPEx_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
    TWSE_VALUE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"

    @staticmethod
    def _num(x):
        try:
            if x is None or str(x).strip() in ("", "-", "--"):
                return np.nan
            return float(str(x).replace(",", "").replace("%", "").strip())
        except Exception:
            return np.nan

    @staticmethod
    def _pick(row, keys, default=""):
        for k in keys:
            if k in row:
                return row[k]
        return default

    def _fetch_json(self, url):
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            raise ValueError("API 回傳格式不是清單")
        return data

    def get_market_snapshot(self, market="上市＋上櫃"):
        rows = []
        if market in ("上市＋上櫃", "上市"):
            try:
                for x in self._fetch_json(self.TWSE_URL):
                    code = str(x.get("Code", "")).strip()
                    if not code.isdigit() or len(code) != 4 or code.startswith("00"):
                        continue
                    rows.append({
                        "市場": "上市", "股票代號": code,
                        "股票名稱": str(x.get("Name", "")).strip(),
                        "收盤價": self._num(x.get("ClosingPrice")),
                        "漲跌幅": self._num(x.get("Change")),
                        "成交股數": self._num(x.get("TradeVolume")),
                        "成交金額": self._num(x.get("TradeValue")),
                    })
            except Exception as e:
                print(f"TWSE 市場快照失敗：{e}")

        if market in ("上市＋上櫃", "上櫃"):
            try:
                for x in self._fetch_json(self.TPEx_URL):
                    code = str(self._pick(x, ["SecuritiesCompanyCode", "Code", "SecuritiesCode"])).strip()
                    if not code.isdigit() or len(code) != 4 or code.startswith("00"):
                        continue
                    rows.append({
                        "市場": "上櫃", "股票代號": code,
                        "股票名稱": str(self._pick(x, ["CompanyName", "Name", "SecuritiesCompanyName"])).strip(),
                        "收盤價": self._num(self._pick(x, ["Close", "ClosingPrice"])),
                        "漲跌幅": self._num(self._pick(x, ["Change", "ChangePercent"])),
                        "成交股數": self._num(self._pick(x, ["TradingShares", "TradeVolume"])),
                        "成交金額": self._num(self._pick(x, ["TradingAmount", "TradeValue"])),
                    })
            except Exception as e:
                print(f"TPEx 市場快照失敗：{e}")

        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df = df.dropna(subset=["收盤價"]).copy()
        df["成交金額"] = df["成交金額"].fillna(0)
        df["成交股數"] = df["成交股數"].fillna(0)
        return df.sort_values("成交金額", ascending=False).reset_index(drop=True)

    def get_top_gainers(self, market="上市＋上櫃", top_n=100, min_trade_value=0):
        """取得最新交易日全市場漲幅排行榜。
        TWSE/TPEx 的 Change 為價格漲跌，統一換算成百分比後合併排名。
        """
        snapshot = self.get_market_snapshot(market)
        if snapshot.empty:
            return pd.DataFrame(), "市場快照取得失敗，無法產生漲幅排行。"

        df = snapshot.copy()

        # Change / (前一日收盤) = 漲跌幅；前一日收盤 = 今日收盤 - Change
        prev_close = df["收盤價"] - df["漲跌幅"]
        df["漲幅%"] = np.where(
            prev_close > 0,
            df["漲跌幅"] / prev_close * 100,
            np.nan
        )

        # 避免 API 欄位異常造成不合理數字
        df.loc[(df["漲幅%"] < -30) | (df["漲幅%"] > 30), "漲幅%"] = np.nan

        # 流動性條件：成交金額以元計
        if min_trade_value and float(min_trade_value) > 0:
            df = df[df["成交金額"] >= float(min_trade_value) * 1e8].copy()

        df = df.dropna(subset=["漲幅%"]).copy()
        df = df.sort_values(
            ["漲幅%", "成交金額"],
            ascending=[False, False]
        ).head(int(top_n)).reset_index(drop=True)

        if df.empty:
            return pd.DataFrame(), "沒有符合條件的股票。"

        df.insert(0, "排名", np.arange(1, len(df) + 1))

        # 保留可直接閱讀的欄位
        out = df[[
            "排名", "市場", "股票代號", "股票名稱",
            "收盤價", "漲跌幅", "漲幅%", "成交股數", "成交金額"
        ]].copy()
        out = out.rename(columns={
            "漲跌幅": "漲跌",
            "成交股數": "成交量",
            "成交金額": "成交金額(元)"
        })
        out["收盤價"] = out["收盤價"].round(2)
        out["漲跌"] = out["漲跌"].round(2)
        out["漲幅%"] = out["漲幅%"].round(2)
        out["成交量"] = out["成交量"].round(0).astype("int64")
        out["成交金額(億)"] = (out["成交金額(元)"] / 1e8).round(2)
        out = out.drop(columns=["成交金額(元)"])

        latest_date = "最新交易日"
        try:
            latest_date = pd.Timestamp.now(tz="Asia/Taipei").strftime("%Y-%m-%d")
        except Exception:
            pass

        min_text = f"，成交金額至少 {float(min_trade_value):g} 億元" if float(min_trade_value or 0) > 0 else ""
        summary = (
            f"🔥 {latest_date} 漲幅排行：共取得 {len(snapshot)} 檔市場快照，"
            f"目前顯示漲幅前 {len(out)} 名{min_text}。\n\n"
            "漲幅% 是依今日收盤與 API 回傳的價格漲跌換算，"
            "再以成交金額作為同漲幅時的排序依據。"
        )
        return out, summary

    def get_twse_value_data(self):
        try:
            rows = self._fetch_json(self.TWSE_VALUE_URL)
            df = pd.DataFrame(rows)
            if df.empty:
                return pd.DataFrame()
            df["PE"] = pd.to_numeric(df.get("PEratio"), errors="coerce")
            df["殖利率"] = pd.to_numeric(df.get("DividendYield"), errors="coerce")
            df["PB"] = pd.to_numeric(df.get("PBratio"), errors="coerce")
            df["股票代號"] = df.get("Code", "").astype(str).str.strip()
            return df[["股票代號", "PE", "殖利率", "PB"]]
        except Exception as e:
            print(f"TWSE 估值資料取得失敗：{e}")
            return pd.DataFrame()

    @staticmethod
    def _score_one(hist, value_row=None, market_return=np.nan):
        if hist is None or hist.empty or len(hist) < 120:
            return None
        df = hist.copy()
        for c in ["Open", "High", "Low", "Close", "Volume"]:
            if c not in df.columns:
                return None
        df = df.dropna(subset=["Close", "High", "Low", "Volume"])
        if len(df) < 120:
            return None

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()
        ma120 = close.rolling(120).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean()
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
        rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        hist_macd = macd - signal
        tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1/14, adjust=False).mean()
        vol_ma20 = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma20.replace(0, np.nan)
        high20_prev = high.rolling(20).max().shift(1)
        high60_prev = high.rolling(60).max().shift(1)
        ret5 = close.pct_change(5) * 100
        ret20 = close.pct_change(20) * 100
        ret60 = close.pct_change(60) * 100
        obv = (np.sign(close.diff()).fillna(0) * volume).cumsum()
        obv_ma20 = obv.rolling(20).mean()
        bias20 = (close - ma20) / ma20 * 100
        atr_pct = atr / close * 100

        r = df.index[-1]
        c = float(close.iloc[-1])
        vals = {
            "收盤價": c, "5日漲幅": float(ret5.iloc[-1]), "20日漲幅": float(ret20.iloc[-1]),
            "60日漲幅": float(ret60.iloc[-1]), "RSI": float(rsi.iloc[-1]),
            "MACD柱體": float(hist_macd.iloc[-1]), "量比": float(vol_ratio.iloc[-1]),
            "ATR%": float(atr_pct.iloc[-1]), "乖離20": float(bias20.iloc[-1]),
            "MA20": float(ma20.iloc[-1]), "MA60": float(ma60.iloc[-1]),
            "MA120": float(ma120.iloc[-1]), "EMA50": float(ema50.iloc[-1]),
            "EMA200": float(ema200.iloc[-1]), "前20日高": float(high20_prev.iloc[-1]),
            "前60日高": float(high60_prev.iloc[-1]), "OBV": float(obv.iloc[-1]),
            "OBV_MA20": float(obv_ma20.iloc[-1])
        }
        if not all(np.isfinite(v) for v in vals.values()):
            return None

        score = 0.0
        reasons = []
        risks = []

        # 1. 趨勢 25
        trend = 0
        if c > vals["MA20"]: trend += 7
        if vals["MA20"] > vals["MA60"]: trend += 6
        if vals["MA60"] > vals["MA120"]: trend += 5
        if c > vals["EMA200"]: trend += 4
        if vals["EMA50"] > vals["EMA200"]: trend += 3
        score += trend
        if trend >= 20: reasons.append("均線多頭排列")
        elif trend < 12: risks.append("趨勢結構偏弱")

        # 2. 動能 20
        momentum = 0
        if vals["RSI"] >= 50: momentum += 6
        if 50 <= vals["RSI"] <= 70: momentum += 3
        elif vals["RSI"] > 75: risks.append("RSI 偏熱")
        if vals["MACD柱體"] > 0: momentum += 5
        if vals["20日漲幅"] > 0: momentum += 3
        if vals["5日漲幅"] > 0: momentum += 3
        score += min(momentum, 20)
        if momentum >= 15: reasons.append("動能轉強")

        # 3. 量價 15
        flow = 0
        if vals["量比"] >= 1.2: flow += 7
        if vals["量比"] >= 1.8: flow += 2
        if vals["OBV"] > vals["OBV_MA20"]: flow += 6
        score += min(flow, 15)
        if flow >= 10: reasons.append("量價配合")
        elif vals["量比"] < 0.7: risks.append("成交量偏低")

        # 4. 突破 15
        breakout = 0
        if c >= vals["前20日高"] * 0.98: breakout += 8
        if c >= vals["前60日高"] * 0.98: breakout += 7
        score += breakout
        if breakout >= 8: reasons.append("接近/突破前高")

        # 5. 相對強弱 10
        rel = 0
        if np.isfinite(market_return):
            if vals["20日漲幅"] > market_return: rel += 7
            if vals["60日漲幅"] > market_return: rel += 3
        else:
            rel = 5 if vals["20日漲幅"] > 0 else 0
        score += rel
        if rel >= 7: reasons.append("相對大盤強勢")

        # 6. 風險控制 5
        risk_score = 5
        if vals["ATR%"] > 8:
            risk_score = 1
            risks.append("波動率過高")
        elif vals["ATR%"] > 6:
            risk_score = 3
        score += risk_score

        pe = pb = div = np.nan
        if value_row is not None:
            pe = value_row.get("PE", np.nan)
            pb = value_row.get("PB", np.nan)
            div = value_row.get("殖利率", np.nan)
            if np.isfinite(pe) and 0 < pe <= 25:
                score += 2
                reasons.append("本益比合理")
            if np.isfinite(pb) and 0 < pb <= 2:
                score += 1
            if np.isfinite(div) and div >= 4:
                score += 1

        score = min(score, 100)
        if score >= 80:
            grade = "A｜強勢候選"
        elif score >= 70:
            grade = "B｜值得追蹤"
        elif score >= 60:
            grade = "C｜觀察"
        else:
            grade = "D｜淘汰"

        if not reasons:
            reasons.append("尚未形成明確優勢")
        return {
            "選股分數": round(score, 1), "評級": grade,
            "收盤價": round(c, 2), "5日漲幅%": round(vals["5日漲幅"], 2),
            "20日漲幅%": round(vals["20日漲幅"], 2), "60日漲幅%": round(vals["60日漲幅"], 2),
            "RSI": round(vals["RSI"], 1), "量比": round(vals["量比"], 2),
            "20日乖離%": round(vals["乖離20"], 2), "ATR%": round(vals["ATR%"], 2),
            "PE": round(pe, 2) if np.isfinite(pe) else np.nan,
            "PB": round(pb, 2) if np.isfinite(pb) else np.nan,
            "殖利率%": round(div, 2) if np.isfinite(div) else np.nan,
            "理由": "、".join(reasons[:4]), "風險": "、".join(risks[:3]) if risks else "目前無明顯技術風險"
        }

    def scan(self, market="上市＋上櫃", candidate_count=60, min_score=60):
        snapshot = self.get_market_snapshot(market)
        if snapshot.empty:
            return pd.DataFrame(), "市場快照取得失敗，請稍後再試。"

        # 先用成交金額做流動性初篩，避免逐檔下載整個市場造成等待過久。
        pool = snapshot.sort_values("成交金額", ascending=False).head(int(candidate_count)).copy()
        value_df = self.get_twse_value_data()
        if not value_df.empty:
            pool = pool.merge(value_df, on="股票代號", how="left")
        else:
            pool["PE"] = np.nan; pool["PB"] = np.nan; pool["殖利率"] = np.nan

        tickers = [f"{c}.{('TW' if m == '上市' else 'TWO')}" for c, m in zip(pool["股票代號"], pool["市場"])]
        print(f"----- 全市場初篩 {len(snapshot)} 檔 → 歷史K線深度分析 {len(tickers)} 檔 -----")

        # 大盤相對強弱基準
        market_return = np.nan
        try:
            mkt = yf.download("0050.TW", period="4mo", progress=False, auto_adjust=False, threads=False)
            if isinstance(mkt.columns, pd.MultiIndex):
                mkt.columns = [c[0] for c in mkt.columns]
            if "Close" in mkt.columns and len(mkt) >= 25:
                market_return = float(mkt["Close"].pct_change(20).iloc[-1] * 100)
        except Exception:
            pass

        try:
            raw = yf.download(tickers, period="8mo", progress=False, auto_adjust=False, threads=True, group_by="ticker")
        except Exception as e:
            return pd.DataFrame(), f"歷史行情下載失敗：{e}"

        results = []
        for _, meta in pool.iterrows():
            code = meta["股票代號"]
            suffix = "TW" if meta["市場"] == "上市" else "TWO"
            symbol = f"{code}.{suffix}"
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    if symbol in raw.columns.get_level_values(0):
                        hist = raw[symbol].copy()
                    elif symbol in raw.columns.get_level_values(1):
                        hist = raw.xs(symbol, axis=1, level=1).copy()
                    else:
                        continue
                else:
                    hist = raw.copy() if len(tickers) == 1 else None
                if hist is None or hist.empty:
                    continue
                value_row = {
                    "PE": meta.get("PE", np.nan), "PB": meta.get("PB", np.nan), "殖利率": meta.get("殖利率", np.nan)
                }
                scored = self._score_one(hist, value_row, market_return)
                if scored:
                    scored.update({
                        "市場": meta["市場"], "股票代號": code, "股票名稱": meta["股票名稱"],
                        "成交金額(億)": round(float(meta.get("成交金額", 0)) / 1e8, 2)
                    })
                    results.append(scored)
            except Exception as e:
                print(f"{symbol} 評分失敗：{e}")

        if not results:
            return pd.DataFrame(), "沒有足夠的歷史資料完成選股評分。"
        out = pd.DataFrame(results).sort_values("選股分數", ascending=False)
        out = out[out["選股分數"] >= float(min_score)].reset_index(drop=True)
        if out.empty:
            return out, f"完成 {len(results)} 檔深度評分，但沒有股票達到 {min_score} 分；可降低最低分數。"

        summary = (
            f"完成：全市場快照 {len(snapshot)} 檔 → 流動性前 {len(pool)} 檔 → 深度技術評分 {len(results)} 檔。\n\n"
            f"大盤 0050 近20日報酬：{market_return:.2f}%（若資料可取得）。\n\n"
            f"目前顯示 {len(out)} 檔達到 {min_score} 分以上。分數是量化篩選工具，不代表保證上漲。"
        )
        cols = ["評級","選股分數","市場","股票代號","股票名稱","收盤價","5日漲幅%","20日漲幅%","60日漲幅%","RSI","量比","20日乖離%","ATR%","PE","PB","殖利率%","成交金額(億)","理由","風險"]
        return out[[c for c in cols if c in out.columns]], summary
