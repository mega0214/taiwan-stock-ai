# 想法日記

記錄想法，並用 Claude 分析多種可能性。純靜態網頁（PWA），資料存在瀏覽器 localStorage。

## 使用
```bash
cd thought-journal && python3 -m http.server 8080
```
開啟 http://localhost:8080 。右上角設定可填 Claude API key（只存在本機瀏覽器）；沒填時分析顯示示範內容。

## 資料
- 存放：瀏覽器 localStorage（本機）。
- 備份／換裝置：設定裡「匯出 JSON」「匯入 JSON」。
- 雲端同步：尚未實作（下一步）。
