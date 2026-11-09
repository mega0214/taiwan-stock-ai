# ebook-ocr-kit

把「一頁一張截圖」的電子書轉成 **Word 圖文對照版 ＋ 圖表庫 ＋ 全文 Markdown**。
全程本機 GPU，不連外網。

## 30 秒上手

```bash
# 0. 確認服務在跑（沒開就先啟動，指令見說明書 §3.1）
curl -s --noproxy '*' -o NUL -w "%{http_code}" http://127.0.0.1:18976/health

# 1. 一鍵跑完
C:\Users\mega0\.workbuddy-ai\binaries\python\envs\ocrgpu\Scripts\python.exe ^
  run_book.py ^
  --src "E:\000\某書" ^
  --work "O:\out\某書_work" ^
  --title "某書" --sub "副標" --author "作者 著"
```

跑完會拿到：

```
O:\out\某書_OCR圖文版.docx
O:\out\某書_圖表庫\
O:\out\某書_全文.md
O:\out\某書_work\qc_report.md     ← 先看這份
```

## 新增功能（v3.1）

```bash
# PDF 也能直接吃：--src 給 PDF 檔，或只含一個 PDF 的資料夾（自動逐頁渲染成 720px 寬的 PNG）
python run_book.py --src "E:\000\某書.pdf" --work "O:\out\某書_work" --title "某書"

# 批次：母資料夾下「每個子資料夾 = 一本書」，PDF 檔也算一本；已完成的自動跳過，失敗不中斷
python run_batch.py --root "E:\000" --out "O:\out"
python run_batch.py --root "E:\000" --out "O:\out" --dry-run          # 只列出會處理哪些書
python run_batch.py --root "E:\000" --out "O:\out" -- --skip-pdf      # -- 之後原樣傳給 run_book.py
# 結果：O:\out\batch_report.md

# 形近字校對：階段 2 後自動跑，詞彙表在 data\ocr_fixes.tsv（可加 --fix-table 書專用表、--no-fix 關閉）
python scripts\tw_fix.py "O:\out\某書_work" --dry-run   # 先看會改什麼
python scripts\tw_fix.py "O:\out\某書_work" --restore   # 還原成原始轉錄（txt_raw/）
```

- PDF 預設渲染頁寬 720px，是為了對齊 `ebook_layout.py` 以 600–700px 截圖調出的門檻；**不要隨意加大**。
- 形近字校對只做整詞精確替換，每筆都記在 `tw_fix_report.md`（也併入 `qc_report.md`）。詞彙表只放「幾乎不可能是正確寫法」的詞。
- 離線測試：`python -m unittest discover -s tests -v`

## 需要什麼

- AMD／Intel GPU（DirectML）＋ `onnxruntime-directml`
- llama.cpp Vulkan 版 ＋ PaddleOCR-VL GGUF 模型
- Python 3.13+、python-docx、pymupdf、opencc
- MS Word（僅用於匯出 PDF 對照頁碼，可跳過）

安裝步驟見 **說明書_給AI代理.md §2**。

## 兩顆引擎在做什麼

| 引擎 | 負責 | 速度 |
|---|---|---|
| RapidOCR（DirectML） | 文字在**哪裡** → 切插圖 | 0.35 秒/頁 |
| PaddleOCR-VL（Vulkan） | 這頁**寫什麼** → Word 內文 | 1–4 秒/頁 |

## 文件

- **說明書_給AI代理.md** — 完整流程、參數、14 條注意事項、故障排除、改造建議
- **run_book.py** — 六階段編排器，可 `--from N` 單獨重跑某階段

## 實測基準

| 書 | 頁數 | 全流程 | Word | 插圖 |
|---|---|---|---|---|
| 《圖解力教學》 | 190 | 約 6 分鐘 | 239 頁 | 137 |
| 《塗鴉吧》 | 237 | 約 12 分鐘 | 351 頁 | 348 |

## 授權與資料

所有處理都在本機完成，不上傳任何檔案到外部服務。
