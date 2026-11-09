# 電子書 OCR 作業說明書（給 AI 代理）

> 版本 3.0 ｜ 2026-11-08
> 適用對象：**Codex / Claude Code / Claude AI**（會操作本機檔案與命令列的代理）
> 實戰驗證：兩本繁體中文圖解書，共 427 頁
> 　・《圖解力教學》190 頁 → Word 239 頁、137 張圖
> 　・《塗鴉吧！用視覺模板翻轉人生》237 頁 → Word 351 頁、348 張圖

---

## 0. 先讀這段：你要做什麼

你的任務是把「一頁一張截圖」的電子書，轉成 **Word 圖文對照版 ＋ 圖表庫 ＋ 全文 Markdown**。

**核心策略是兩顆引擎分工，不要用一顆做全部：**

| 引擎 | 回答什麼 | 速度 | 輸出 |
|---|---|---|---|
| **RapidOCR**（DirectML／GPU） | 文字在**哪裡** | 0.35 秒/頁 | 文字框座標 → 用來切插圖 |
| **PaddleOCR-VL**（Vulkan／GPU） | 這頁**寫了什麼** | 1–4 秒/頁 | 繁體中文全文 → 放進 Word |

兩顆都跑本機 GPU，**不連外網、不花錢**。

### 三條不可違反的規則

1. **不要相信「有設定 GPU」**——要驗證 session 真的建在 GPU 上（見 §3.2）。設定被靜默忽略是常態。
2. **不要用 RapidOCR 的繁體文字**——它只有簡體模型，會系統性誤判（學習→享智、讓→護），放大也救不回。
3. **沒有視覺驗證不算完成**——程式跑完不等於文件正確，必須把 Word 轉成圖片逐頁看。

---

## 1. 本機環境現況（這台機器已裝好）

| 項目 | 路徑／版本 |
|---|---|
| GPU | AMD Radeon RX 7600 XT 16GB |
| 系統 Python | `C:\Python314\python.exe`（3.14.2） |
| OCR 專用 venv | `C:\Users\mega0\.workbuddy-ai\binaries\python\envs\ocrgpu\Scripts\python.exe` |
| llama.cpp | `D:\llama-b12020-bin-win-vulkan-x64\llama-server.exe` |
| PaddleOCR-VL 主模型 | `...\models\SanjeevSOLANKI\PaddleOCR-VL-1.6-Q8_0.gguf`（475 MB） |
| 圖像投影模型 | `...\models\SanjeevSOLANKI\PaddleOCR-VL-1.6-mmproj.gguf`（841 MB） |
| Word | MS Word 16.0（匯出 PDF 用；**沒有 LibreOffice**） |
| 工具包 | `D:\ebook-ocr-kit\`（本說明書所在） |

### ocrgpu venv 已安裝套件（實測版本）

```
numpy                       2.5.4
onnxruntime-directml        1.24.4     ← 只能有這個，不能有 onnxruntime
rapidocr-onnxruntime        1.2.3      ← 需修補 utils.py（見 §2.2）
opencv-python               5.0.0.93
pillow                      13.0.0
python-docx                 1.2.0
pymupdf                     2.1
opencc-python-reimplemented 0.1.7
psutil                      7.2.2
```

---

## 2. 從零安裝（換機器或環境壞掉時）

### 2.1 建 venv 並裝套件

```bash
python -m venv C:\ocr-venv
C:\ocr-venv\Scripts\python.exe -m pip install --upgrade pip
C:\ocr-venv\Scripts\python.exe -m pip install onnxruntime-directml==1.24.4 rapidocr-onnxruntime==1.2.3 opencv-python pillow numpy python-docx pymupdf opencc-python-reimplemented psutil
```

> **絕對不要** `pip install onnxruntime`。它與 `onnxruntime-directml` 會互相覆蓋 DLL，
> 結果是 `get_available_providers()` 只剩 CPU，**而程式不會報錯**，你會以為在用 GPU。

### 2.2 修補 RapidOCR 的 DirectML 支援（必做）

`rapidocr_onnxruntime` 1.2.3 的 `utils.py` **只寫了 CPU 與 CUDA 分支，沒有 DirectML**。
即使 provider 正常，它還是會建 CPU session。傳 `engine_cfg` 會被**靜默忽略**。

```bash
C:\ocr-venv\Scripts\python.exe D:\ebook-ocr-kit\scripts\fix_dml.py
```

腳本會依序：移除衝突套件 → 補上 DML 分支 → 調整省 CPU 參數 → 實測 session。
可重複執行；`--check` 只檢查不改。

> ⚠️ **重裝或升級 `rapidocr-onnxruntime` 會覆蓋掉這個修補**，升級後要重跑一次。

### 2.3 llama.cpp + 模型

1. 下載 llama.cpp **Vulkan 版**（本機用 b12020）解壓到 `D:\llama-b12020-bin-win-vulkan-x64\`
2. 下載 GGUF 模型（Hugging Face：`SanjeevSOLANKI/PaddleOCR-VL-1.6-GGUF`）：
   - `PaddleOCR-VL-1.6-Q8_0.gguf`
   - `PaddleOCR-VL-1.6-mmproj.gguf`
3. 放到 `D:\llama-b12020-bin-win-vulkan-x64\models\SanjeevSOLANKI\`

不需要 CUDA、不需要 API key、不需要重新訓練。

### 2.4 驗證安裝（三步都必須通過）

```bash
# 1. DirectML 可用
C:\ocr-venv\Scripts\python.exe -c "import onnxruntime as ort; print(ort.get_available_providers())"
# 必須看到 'DmlExecutionProvider'

# 2. RapidOCR 真的建在 DML 上（不是只有 provider 存在）
C:\ocr-venv\Scripts\python.exe -c "from rapidocr_onnxruntime import RapidOCR; e=RapidOCR(); print(e.text_detector.infer.session.get_providers()[0])"
# 必須印出 DmlExecutionProvider

# 3. VL 服務
curl -s --noproxy '*' -o NUL -w "%{http_code}" http://127.0.0.1:18976/health
# 必須是 200
```

---

## 3. 啟動 PaddleOCR-VL 服務

### 3.1 啟動指令（**不要用 start-ocr.bat**）

`start-ocr.bat` 最後有 `pause`，在自動化環境會卡住。直接呼叫 exe：

```bash
cd /d D:\llama-b12020-bin-win-vulkan-x64
llama-server.exe -m "models/SanjeevSOLANKI/PaddleOCR-VL-1.6-Q8_0.gguf" --mmproj "models/SanjeevSOLANKI/PaddleOCR-VL-1.6-mmproj.gguf" --alias paddleocr-vl-1.6 --device Vulkan0 --n-gpu-layers 99 --mmproj-offload --threads 6 --threads-batch 6 --ctx-size 12288 --parallel 3 --batch-size 2048 --ubatch-size 1024 --host 127.0.0.1 --port 18976 --temp 0
```

工作目錄要設在 `D:\llama-b12020-bin-win-vulkan-x64`（模型路徑是相對的）。

**啟動約 6 秒**，健康檢查：

```bash
curl -s --noproxy '*' -o NUL -w "%{http_code}" http://127.0.0.1:18976/health
```

> `curl` 一定要加 `--noproxy '*'`。系統代理會回 **502**，讓你誤判服務沒起來。

### 3.2 驗證真的在 GPU 上（不要只看 log）

```powershell
$p = @(Get-Process llama-server)[0]
(Get-Counter '\GPU Process Memory(*)\Dedicated Usage').CounterSamples |
  Where-Object { $_.InstanceName -like ("pid_" + $p.Id + "_*") }
```

應看到 **1500–1800 MB** VRAM。看不到就是跑在 CPU。

### 3.3 為什麼是 `--parallel 3`

`parallel` 是同時處理幾個請求。單槽時 GPU 會在「等 HTTP／等 base64」的空檔閒置。

| 設定 | 9 頁耗時 | 有效速度 |
|---|---|---|
| `parallel 1` ＋ 1 執行緒 | 22 秒 | 2.09 秒/頁 |
| **`parallel 3` ＋ 3 執行緒** | **7 秒** | **0.78 秒/頁**（3.1 倍） |

實測 GPU 使用率：**平均 82%、峰值 147%**，VRAM 1.5 GB。

> `ctx-size` 會被 `parallel` 平分。要每槽 4096 就設 `12288`；設太小長頁會被截斷。

---

## 4. 一鍵執行（推薦）

```bash
C:\Users\mega0\.workbuddy-ai\binaries\python\envs\ocrgpu\Scripts\python.exe D:\ebook-ocr-kit\run_book.py --src "E:\000\某書" --work "O:\out\某書_work" --title "某書" --sub "副標" --author "作者 著"
```

會依序跑完六個階段，每階段自動驗收，失敗會停下來報原因。

| 參數 | 說明 |
|---|---|
| `--src` | 一頁一張圖的目錄（檔名需含頁碼，如 `xxx-001.png`） |
| `--work` | 工作目錄（自動建 `txt/` `figs/`） |
| `--workers-gpu` | RapidOCR 進程數，預設 2 |
| `--workers-vl` | VL 並行數，預設 3（要與 server `--parallel` 一致） |
| `--from N` | 從第 N 階段重跑（0–6） |
| `--skip-vl` | 跳過轉錄（除錯用） |
| `--skip-pdf` | 不匯出 PDF（沒有 Word 或沙盒禁止呼叫 PowerShell 時） |

> 若你的執行環境禁止從程式內呼叫 PowerShell，PDF 階段會警告並跳過（不影響 Word）。
> 這時圖表庫不會有「Word 頁」對照，可事後補：用 PowerShell 匯出 PDF 後，
> 單獨跑 `ebook_figlib.py ... --pdf <pdf>` 重建索引。

---

## 5. 六個階段（可單獨重跑）

所有腳本在 `D:\ebook-ocr-kit\scripts\`，皆可獨立執行、皆支援續傳。

### 階段 0／素材盤點

確認頁數、檔名頁碼連續、解析度 ≥ 600px。缺頁會警告但不中止。

### 階段 1／版面定位（RapidOCR，只取座標）

```bash
cd <工作目錄>
for i in 0 1; do
  <ocrgpu-python> D:\ebook-ocr-kit\scripts\ocr_worker.py $i 2 "<來源目錄>" > log_$i.txt 2>&1 &
done
wait
```

- 產出 `ocr_part_<i>.json`：`{檔名: [{box:[[x,y],…], text, score}, …]}`
- 每個 log 第一行會印 `det=DmlExecutionProvider rec=DmlExecutionProvider`；出現 `⚠ DirectML 未生效` 就是退回 CPU
- **worker 數：GPU 用 2 個**。1→2 有加速，3 持平，4 變慢（搶同一張 GPU 的提交佇列）

### 階段 2／文字轉錄（PaddleOCR-VL）

```bash
<python> D:\ebook-ocr-kit\scripts\paddle_ocr_pages.py "<來源目錄>" "<工作目錄>" --workers 3
```

- 產出 `txt/part_01.md…`（含 `@@PAGE` 契約，可直接餵給 Word 組裝）＋ `paddle_meta.json`
- 內建兩個必要的清理（見 §7.3、§7.4）
- 可續傳：已完成的頁會跳過

接著跑品質檢查：

```bash
<python> D:\ebook-ocr-kit\scripts\paddle_qc.py "<工作目錄>"
# 產出 qc_report.md：圖解頁／疑似雙欄／疑似截斷／重複迴圈
```

### 階段 3／版面分析與插圖裁切

```bash
<ocrgpu-python> D:\ebook-ocr-kit\scripts\ebook_layout.py "<來源目錄>" "<工作目錄>"
# 產出 figs/*.png 與 layout.json
```

### 階段 4／Word 組裝

```bash
<ocrgpu-python> D:\ebook-ocr-kit\scripts\ebook_docx.py "<來源目錄>" "<工作目錄>" "<輸出.docx>" "書名" "副標" "作者"
```

A4／微軟正黑體 10.5pt／行距 1.5，每頁標「原書 p.XX」，插圖依原位置插入。

### 階段 5／匯出 PDF（供頁碼對照與驗證）

```powershell
$w = New-Object -ComObject Word.Application
$w.Visible = $false
$d = $w.Documents.Open("<docx 絕對路徑>")
$d.SaveAs([ref]"<pdf 路徑>", [ref]17)   # 17 = wdFormatPDF
$pages = $d.ComputeStatistics(2)
$d.Close(0); $w.Quit()
```

### 階段 6／圖表庫與全文 MD

```bash
# 圖表庫（含 Word 頁對照）
<python> D:\ebook-ocr-kit\scripts\ebook_figlib.py "<工作目錄>" "<書名>_圖表庫" "<書名>" --describe --workers 3 --pdf "<PDF 路徑>"

# 帶圖全文 MD
<python> D:\ebook-ocr-kit\scripts\ebook_fullmd.py "<工作目錄>" "<書名>_圖表庫" "<書名>" "<書名>_全文.md"
```

---

## 6. 兩次實測基準（判斷「正常」的依據）

| 項目 | 《圖解力教學》 | 《塗鴉吧》 |
|---|---|---|
| 原書頁數 | 190 | 237 |
| 頁面解析度 | 664×865 | 609×904 |
| RapidOCR（2 worker） | 50 秒 | 83 秒 |
| PaddleOCR-VL（3 並行） | 171 秒 | 338 秒 |
| 版面分析 | 約 60 秒 | 約 90 秒 |
| 切出插圖 | 137 張 | 348 張 |
| Word 頁數 | 239（1.26×） | 351（1.48×） |
| 需人工核對 | 82 頁（43%） | 86 頁（36%） |
| **全流程** | **約 6 分鐘** | **約 12 分鐘** |

**判讀原則**

- VL 每頁 0.9–1.5 秒正常；**超過 3 秒/頁** 通常是並行沒設好或 GPU 沒吃到。
- Word 頁數約原書 **1.2–1.5 倍**正常。**超過 2 倍** 幾乎都是重複迴圈沒清掉。
- 需人工核對 35–45% 對圖解書是正常的（不是失敗）。

---

## 7. 注意事項（按嚴重度排序）

### 致命：會毀掉成品且不會報錯

**7.1 `onnxruntime` 與 `onnxruntime-directml` 不可共存**
CPU 版會讓 DML 的 DLL 載不到，provider 只剩 CPU，**程式照跑、不報錯**。
修法：`fix_dml.py`。移除 CPU 版後 directml 的檔案會一起被刪，要 `--no-deps --force-reinstall` 重裝。

**7.2 `rapidocr_onnxruntime` 1.2.3 沒有 DirectML 分支**
`utils.py` 只判斷 `use_cuda`，沒有 DML 選項。你傳的 `engine_cfg={"providers":["DmlExecutionProvider"]}`
會被**靜默忽略**。必須改 `EP_list` 邏輯。`fix_dml.py` 會處理。

**7.3 重複迴圈（最隱蔽）**
模型遇到重複圖樣（表格格線、虛線、連續箭頭）會狂印同一行。實測單頁出現 **476–921 次**，
Word 從 351 頁暴增到 448 頁。
`paddle_ocr_pages.py` 已內建：同頁同一行佔比 > 40% 時，保留前 3 次並加註「請對照原圖」。

**7.4 LaTeX 化**
手寫標題會被輸出成 `\(\uwave{\text{塗鴉吧！}}\)`，甚至一頁幾百個 `\(\uwave{1111}\)`。
已內建清理：剝除外層、抽出 `\text{}` 內容、同 token 重複 > 60% 壓成「1111 ×168」。

**7.5 標題誤判會讓頁數暴增**
曾用「短行即標題」的規則，把 2531 段中的 **1810 段**判成標題，Word 從 171 頁漲到 270 頁。
現在只認「第 N 章／推薦序／`1.` 開頭」等明確樣式，降到 91 段。**寧可少判，不要多判。**

**7.6 不要對所有圖用 `Chart Recognition:`**
對裝飾插圖硬套，模型會回「類別／數量」的假表格，把知識庫塞滿雜訊。
正確做法：文字框左緣整齊（≥50% 落在同兩個 x）才用 `Table Recognition:`，其餘用 `OCR:`。

### 嚴重：影響內容品質

**7.7 圖解頁與多欄頁的閱讀順序會錯**
模型大致由左往右讀。兩本書合計 98 頁被判定有順序風險。
這些頁的 `type` 在 `qc_report.md` 會標為「圖解頁」或「疑似雙欄」，**必須對照原圖核對**。

**7.8 形近字錯誤抓不到**
閱讀→關鎖、塗鴉→違須、圖像→影象、盛讚→盛韻。這類錯誤**信心度很高**，
用信心度過濾無效，只能靠人工或詞彙表校正。

**7.9 氣泡文字會重複出現**
圖裡有對話框，文字又被當段落輸出一次。目前未自動去除，閱讀時會覺得囉唆。

**7.10 `ctx-size` 被 `parallel` 平分**
`--parallel 3 --ctx-size 12288` = 每槽 4096。設成 `4096/3` 會讓長頁被截斷。

### 操作：容易踩的坑

**7.11 `curl` 查 health 回 502** → 系統代理攔截，加 `--noproxy '*'`。

**7.12 不要用 `start-ocr.bat`** → 內含 `pause`，自動化會卡住。直接呼叫 exe。

**7.13 背景任務標記 failed 但檔案其實寫完了** → 沙盒在最後 `.tmp` flush 失敗。
**永遠驗磁碟檔案，不要只信 task 狀態。**

**7.14 不要停別人的 llama-server** → 這台機器上可能有使用者自己的服務（實測有一個佔 7.7 GB VRAM）。
只操作 `127.0.0.1:18976` 這個。

**7.15 中文路徑要 URL-encode** → 用 Chrome headless 截圖時，`file:///` 路徑的中文必須編碼，否則空白。

---

## 8. 故障排除

| 症狀 | 原因 | 處理 |
|---|---|---|
| `⚠ DirectML 未生效` | 套件衝突或分支缺失 | 跑 `fix_dml.py` |
| `No module named 'encodings'` | PYTHONHOME 污染 | `env -u PYTHONHOME -u PYTHONPATH <python>` |
| VL 每頁 > 3 秒 | 並行沒開或 GPU 沒吃到 | 檢查 `--parallel`、查 VRAM |
| VL 回傳空字串 | 圖片過大／格式不支援 | 只收 PNG/JPEG/WebP/BMP |
| VL 輸出被截斷 | `ctx-size / parallel` 太小 | 加大 `--ctx-size` |
| Word 頁數暴增 | 重複迴圈或標題誤判 | 看 §7.3、§7.5 |
| 圖片被擠到下一頁 | 圖高超過版心 | 圖高上限 19cm，橫式節預留 caption 空間 |
| 插圖切到對話框 | 邊界取太緊 | `loose`(dist>8) 修邊 ＋ 外擴 8px |
| 一張圖被切成碎片 | 圖內氣泡文字干擾 | 提高合併間距、改用頁型分類整頁處理 |
| 標題色帶被當成圖 | 缺色帶過濾 | `lum≥195 & chroma≥10` 佔比 >30% 且高 ≤230 |
| 照片淺色區被裁掉 | 用標準墨跡判空白 | 照片頁改用 `dist>8` |
| 截圖全白 | 中文路徑未 encode | URL-encode 後再丟給 Chrome |

---

## 9. 交付物規格

```
<書名>_OCR圖文版.docx      主成品：封面→關於→章節一覽→正文（圖文交錯）
<書名>_圖表庫/
  figs/p027_fig01.png      依原書頁碼命名（p027 = 原書第 27 頁，fig01 = 該頁第 1 張）
  text/p027_fig01.md       表格／圖文的結構化內容（表格已轉 Markdown）
  index.md                 人看的索引：編號／原書頁／Word 頁／類型／尺寸／摘要
  index.json               機器讀的索引（給 Agent 查詢）
  README.md                用法與已知限制
<書名>_全文.md             帶圖全文（## p.XX 分頁，圖片用相對路徑內嵌）
<工作目錄>/qc_report.md    品質檢查：需人工核對的頁碼清單
<工作目錄>/layout.json     版面分析原始資料
```

### 驗收標準

| 項目 | 合格線 |
|---|---|
| 頁覆蓋率 | 100%，零缺頁 |
| 插圖數 | 與肉眼估計誤差 < 15% |
| 圖表庫 | 每張圖都有檔、有索引、有原書頁 |
| Word 頁數 | 原書 1.2–1.5 倍 |
| 需人工核對 | 已全部標出並可追蹤 |

### 人工複核（不可省）

1. 開 `qc_report.md`，先看「圖解頁／雙欄／重複迴圈」那幾頁
2. 抽查 3 頁與原圖逐字對照
3. 確認沒有整頁漏掉

---

## 10. 給 Codex 既有 OCR 程式的改造建議

如果你手上那個程式是「RapidOCR 直接吐文字」，以下是依實測得出的改造順序：

| 優先 | 改動 | 為什麼 |
|---|---|---|
| 1 | **驗證 session provider，不要只信設定** | 這是兩本書最大的坑：設定被靜默忽略 |
| 2 | **文字改用 PaddleOCR-VL，RapidOCR 只留座標** | 繁體正確率差距是「不能用」與「幾乎逐字對」 |
| 3 | **加後處理：LaTeX 清理 ＋ 重複壓縮** | 沒有這兩層，成品會出現整頁雜訊 |
| 4 | **RapidOCR 改 det-only** | 只要座標卻連 rec 一起跑，實測可省約 60% 時間 |
| 5 | **輸出契約化（`@@PAGE` / `[TEXT]`）** | 讓「轉錄」與「排版」解耦，任一段可單獨重跑 |
| 6 | **加品質報告** | 沒有 QC 清單，人工不知道從哪一頁開始看 |
| 7 | **圖片獨立歸檔 ＋ 索引** | 圖片與文字分開，才能做知識庫與對照閱讀 |

反過來說，**不要**在這些方向花時間：
- 想用 RapidOCR 硬解繁體（模型不對，調參沒用）
- 想用單一模型同時做定位與辨識
- 想靠信心度分數抓錯字（形近字錯誤的信心度通常很高）

---

## 11. 可直接貼給代理的任務指令

```
請依《電子書 OCR 作業說明書 v3.0》處理新書：
來源：<來源圖片目錄>
工具包：D:\ebook-ocr-kit\

步驟：
1. 先確認環境：跑 fix_dml.py --check，確認 DmlExecutionProvider 可用；
   確認 http://127.0.0.1:18976/health 回 200（沒開就先啟動，見說明書 §3.1）。
2. 盤點素材：頁數、頁碼是否連續、解析度，回報給我再繼續。
3. 執行：run_book.py --src "<來源>" --work "<工作目錄>" --title "<書名>" --sub "<副標>" --author "<作者>"
4. 逐階段回報：產出檔案、數量統計、任何警告。
5. 完成後把 qc_report.md 的統計與需核對頁碼清單整理給我。

禁止事項：
- 不要用 start-ocr.bat（有 pause 會卡住）
- 不要安裝 onnxruntime（會破壞 DirectML）
- 不要停用 127.0.0.1:18976 以外任何 llama-server
- 不要宣稱完成，除非 Word 已匯出 PDF 並抽查過 3 頁
```

---

## 附錄 A／參數速查

### 版面分析（ebook_layout.py）

| 常數 | 值 | 用途 |
|---|---|---|
| `ink` 門檻 | `dist > 20` | 標準墨跡 |
| `loose` 門檻 | `dist > 8` | 照片修邊 |
| 圖像判定 | 最長水平連續墨跡 `≥ 90` | 文字行 26–53px vs 圖 114–560px |
| 圖像判定（輔助） | `h ≥ 150 且 rowwith ≥ 0.72` | 大面積照片 |
| 通欄文字行 | `h 12–32, w ≥ 0.62×cw, x0 ≤ cx0+0.30×cw, 字數 ≥ 8` | 頁型分類 |
| 頁型門檻 | 通欄行 `< 2` → 全頁圖解 | 決定要不要細切 |
| 色帶過濾 | `lum ≥ 195 & chroma ≥ 10`，佔比 > 30%，高 ≤ 230 | 排除標題帶 |
| 空白細分 | 全白 `≥ 16px` | 切開黏連塊 |
| 圖合併 | 間距 `≤ 10px` | 避免圖被切半 |
| 裁切留邊 | `± 8px` | 避免切到邊線 |

### PaddleOCR-VL

| 參數 | 值 |
|---|---|
| `--parallel` | 3（客戶端 `--workers` 要相同） |
| `--ctx-size` | 12288（每槽 4096） |
| `--n-gpu-layers` | 99 |
| `--device` | Vulkan0 |
| `--threads` | 6 |
| 溫度 | 0、top_k 1 |

### Word 版面（ebook_docx.py）

| 項目 | 值 |
|---|---|
| 紙張 | A4，邊界 上下 2.2/2.0、左右 2.4cm |
| 正文 | 微軟正黑體 10.5pt / 行距 1.5 |
| 圖寬 | 15.6cm（版心） |
| 小圖縮放 | `w / 實際頁寬 × 15.6`，下限 6cm（寬 < 380px 時） |
| 圖高上限 | 19cm |

---

## 附錄 B／檔案清單

```
D:\ebook-ocr-kit\
  README.md                       ← 30 秒上手
  說明書_給AI代理.md               ← 本文件
  run_book.py                     ← 一鍵跑完六階段
  scripts\
    fix_dml.py                    ← 修 DirectML（必跑）
    ocr_worker.py                 ← RapidOCR 單一 worker
    ebook_layout.py               ← 版面分析＋切圖
    paddle_ocr_pages.py           ← VL 批次轉錄（含清理）
    paddle_qc.py                  ← 品質檢查
    ebook_docx.py                 ← Word 組裝
    ebook_figlib.py               ← 圖表庫
    ebook_fullmd.py               ← 帶圖全文 MD
    ocr_error_check.py            ← 舊版信心度檢查（參考用）
    tw_terms.py                   ← 兩岸用語對照（簡體書用）
```
