# -*- coding: utf-8 -*-
"""把一本書的插圖／表格整理成獨立圖表庫，供對照閱讀與知識庫使用。

用法：
    python ebook_figlib.py <工作目錄> <輸出目錄> "<書名>" [--describe] [--workers 3]

產出結構：
    <輸出目錄>/figs/p027_fig01.png      依原書頁碼重新命名的插圖
    <輸出目錄>/text/p027_fig01.md       圖表內容（Chart Recognition，可選）
    <輸出目錄>/index.md                 人看的索引（含原書頁／Word 頁對照）
    <輸出目錄>/index.json               機器讀的索引（給 Agent 用）
    <輸出目錄>/README.md                使用說明
"""
import argparse
import base64
import json
import os
import re
import shutil
import sys
import glob
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

ENDPOINT = "http://127.0.0.1:18976/v1/chat/completions"
MODEL = "paddleocr-vl-1.6"

# 模型對「非圖表」會套模板回「类别 | 数值」這類無意義輸出，需過濾
GENERIC = ("类别", "類別", "数值", "數值", "数量", "數量", "项目", "項目",
           "category", "value", "item", "description", "描述")

try:
    from opencc import OpenCC
    _cc = OpenCC("s2twp")
except Exception:
    _cc = None


def to_trad(s):
    return _cc.convert(s) if _cc else s


def is_generic(txt):
    lines = [x for x in txt.splitlines() if x.strip()]
    if not lines:
        return True
    if len(lines) > 3:
        return False
    joined = " ".join(lines).lower()
    hits = sum(1 for g in GENERIC if g.lower() in joined)
    return hits >= 2


def table_to_md(txt):
    """把 PaddleOCR-VL 的 <fcel>/<ucel>/<nl> 表格標記轉成 Markdown 表格。"""
    if "<fcel>" not in txt and "<nl>" not in txt:
        return txt
    rows = []
    for raw in txt.replace("<nl>", "\n").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        cells = [c.strip().replace("\\n", " ").strip()
                 for c in re.split(r"<f?u?cel>", raw)]
        cells = [c for c in cells]
        if not any(cells):
            continue
        rows.append(cells)
    if not rows:
        return txt
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |",
           "|" + "---|" * width]
    for r in rows[1:]:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def is_tabular(lines):
    """用文字框左緣是否整齊對齊，判斷這張圖是不是表格。"""
    if len(lines) < 8:
        return False
    from collections import Counter
    lefts = [round(min(p[0] for p in ln["box"]) / 6) * 6 for ln in lines]
    c = Counter(lefts)
    top2 = sum(v for _, v in c.most_common(2))
    return top2 / len(lefts) >= 0.5


def page_of(key):
    m = re.search(r"[-_](\d{1,4})\.png$", os.path.basename(key), re.I)
    return int(m.group(1)) if m else None


def word_pages(pdf_path):
    """回傳 {原書頁碼: Word 頁碼}，靠掃 PDF 上的「原書 p.XX」標記。"""
    if not pdf_path or not os.path.exists(pdf_path):
        return {}
    try:
        import pymupdf
    except ImportError:
        return {}
    out = {}
    doc = pymupdf.open(pdf_path)
    for i in range(len(doc)):
        for m in re.finditer(r"原書\s*p\.(\d+)", doc[i].get_text()):
            n = int(m.group(1))
            out.setdefault(n, i + 1)
    return out


def describe(path, prompt="Chart Recognition:", timeout=180):
    import urllib.request
    b64 = base64.b64encode(open(path, "rb").read()).decode("ascii")
    body = {"model": MODEL, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
        {"type": "text", "text": prompt}]}],
        "temperature": 0, "top_k": 1, "max_tokens": 1536, "stream": False}
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.load(r)
    return (res["choices"][0]["message"].get("content") or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("out")
    ap.add_argument("title")
    ap.add_argument("--describe", action="store_true", help="呼叫 PaddleOCR-VL 產生圖表內容")
    ap.add_argument("--prompt", default="Chart Recognition:")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--min-lines", type=int, default=8,
                    help="只對文字行數 ≥ N 的圖做內容辨識（預設 8，避免對裝飾圖硬套模板）")
    ap.add_argument("--no-trad", action="store_true", help="不做簡轉繁")
    ap.add_argument("--pdf", default="", help="Word 匯出的 PDF，用來對照 Word 頁碼")
    args = ap.parse_args()

    layout_path = os.path.join(args.work, "layout.json")
    if not os.path.exists(layout_path):
        sys.exit(f"找不到 {layout_path}（先跑 ebook_layout.py）")
    L = json.load(open(layout_path, encoding="utf-8"))["layout"]

    # 文字行數：判斷這張圖是否含大量文字（表格／圖表）
    ocr = {}
    for f in glob.glob(os.path.join(args.work, "ocr_part_*.json")):
        ocr.update(json.load(open(f, encoding="utf-8")))

    items = []
    for key, bands in L.items():
        pno = page_of(key)
        if pno is None:
            continue
        idx = 0
        for b in bands:
            if b["type"] != "fig":
                continue
            idx += 1
            src = os.path.join(args.work, "figs", b["file"])
            if not os.path.exists(src):
                continue
            nlines = 0
            inner = []
            for ln in ocr.get(key, []):
                if "box" not in ln:
                    continue
                cy = (min(p[1] for p in ln["box"]) + max(p[1] for p in ln["box"])) / 2
                if b["y0"] - 6 <= cy <= b["y1"] + 6:
                    nlines += 1
                    inner.append(ln)
            items.append({
                "page": pno, "idx": idx, "src": src,
                "name": f"p{pno:03d}_fig{idx:02d}.png",
                "w": b["w"], "h": b["h"], "lines": nlines,
                "type": "表格" if is_tabular(inner) else ("圖文" if nlines >= 8 else "插圖"),
                "tabular": is_tabular(inner),
            })
    items.sort(key=lambda x: (x["page"], x["idx"]))

    figdir = os.path.join(args.out, "figs")
    txtdir = os.path.join(args.out, "text")
    os.makedirs(figdir, exist_ok=True)
    os.makedirs(txtdir, exist_ok=True)
    for it in items:
        dst = os.path.join(figdir, it["name"])
        if not os.path.exists(dst):
            shutil.copy2(it["src"], dst)
        it["file"] = os.path.relpath(dst, args.out).replace("\\", "/")

    # 圖表內容
    if args.describe:
        # 先清掉先前產生的模板雜訊（模型對裝飾圖會硬套「類別／數值」）
        purged = 0
        for p in glob.glob(os.path.join(txtdir, "*.md")):
            try:
                if is_generic(open(p, encoding="utf-8").read()):
                    os.remove(p)
                    purged += 1
            except Exception:
                pass
        # 未達文字行門檻的圖，一律不留內容（避免模板雜訊污染知識庫）
        for it in items:
            p = os.path.join(txtdir, it["name"].replace(".png", ".md"))
            if it["lines"] < args.min_lines and os.path.exists(p):
                try:
                    os.remove(p)
                    purged += 1
                except Exception:
                    pass
        if purged:
            print(f"[figlib] 清除模板雜訊 {purged} 個", flush=True)

        todo = [it for it in items
                if it["lines"] >= args.min_lines
                and not os.path.exists(os.path.join(txtdir, it["name"].replace(".png", ".md")))]
        print(f"[figlib] 圖表內容：符合門檻 {sum(1 for i in items if i['lines'] >= args.min_lines)} 張，"
              f"待處理 {len(todo)} 張", flush=True)
        lock = threading.Lock()
        done = [0]
        skipped = []
        t0 = time.time()

        def work(it):
            try:
                prompt = "Table Recognition:" if it["tabular"] else args.prompt
                txt = describe(it["src"], prompt)
                return it, txt, None
            except Exception as e:
                return it, "", str(e)

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(work, it) for it in todo]
            for fut in as_completed(futs):
                it, txt, err = fut.result()
                with lock:
                    done[0] += 1
                    if err:
                        print(f"  {it['name']} 失敗: {err}", flush=True)
                    else:
                        if not args.no_trad:
                            txt = to_trad(txt)
                        if it["tabular"]:
                            txt = table_to_md(txt)
                        if is_generic(txt):
                            skipped.append(it["name"])
                        else:
                            open(os.path.join(txtdir, it["name"].replace(".png", ".md")),
                                 "w", encoding="utf-8").write(txt + "\n")
                    if done[0] % 20 == 0:
                        print(f"  {done[0]}/{len(todo)}  {time.time()-t0:.0f}s", flush=True)
        print(f"[figlib] 圖表內容完成，耗時 {time.time()-t0:.0f}s"
              + (f"（跳過模板雜訊 {len(skipped)} 張）" if skipped else ""), flush=True)

    # 讀回圖表內容摘要
    for it in items:
        p = os.path.join(txtdir, it["name"].replace(".png", ".md"))
        if os.path.exists(p):
            txt = open(p, encoding="utf-8").read().strip()
            it["has_text"] = bool(txt)
            cells = []
            for ln in txt.splitlines():
                for c in re.split(r"[|\t]", re.sub(r"<[^>]+>", " ", ln)):
                    c = c.strip()
                    if c and c not in cells:
                        cells.append(c)
                if len(cells) >= 3:
                    break
            it["summary"] = "、".join(cells[:3])[:44]
        else:
            it["has_text"] = False
            it["summary"] = ""

    wmap = word_pages(args.pdf) if args.pdf else {}
    for it in items:
        it["word_page"] = wmap.get(it["page"])

    # index.json
    meta = {
        "book": args.title,
        "figure_count": len(items),
        "table_count": sum(1 for it in items if it["type"] == "表格"),
        "diagram_count": sum(1 for it in items if it["type"] == "圖文"),
        "figures": [{k: it[k] for k in
                     ("name", "page", "word_page", "idx", "type", "w", "h", "lines",
                      "file", "has_text", "summary")} for it in items],
    }
    json.dump(meta, open(os.path.join(args.out, "index.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # index.md
    md = [f"# 《{args.title}》圖表索引", "",
          f"- 圖表總數：**{len(items)}**（表格 {meta['table_count']}／圖文 {meta['diagram_count']}／"
          f"純插圖 {len(items)-meta['table_count']-meta['diagram_count']}）",
          "- 「原書頁」= 掃描頁碼；「Word 頁」= 對應 Word 檔的頁碼",
          "- 圖表內容（`text/`）由 PaddleOCR-VL 自動辨識，**數字與專有名詞仍需校對**", "",
          "| 編號 | 原書頁 | Word 頁 | 類型 | 尺寸 | 文字行 | 內容摘要 | 檔案 |",
          "|---|---|---|---|---|---|---|---|"]
    for i, it in enumerate(items, 1):
        wp = it["word_page"] if it["word_page"] else "—"
        sm = it["summary"].replace("|", "／") or "—"
        md.append(f"| F{i:03d} | p.{it['page']} | {wp} | {it['type']} | "
                  f"{it['w']}×{it['h']} | {it['lines']} | {sm} | `{it['name']}` |")
    open(os.path.join(args.out, "index.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")

    # README
    readme = f"""# 《{args.title}》圖表庫

從原書掃描檔自動切出的插圖與圖表，供與 Word／Markdown 對照閱讀，或作為 Agent 知識庫的圖像來源。

## 檔案

| 路徑 | 內容 |
|---|---|
| `figs/` | 依原書頁碼命名的圖檔：`p027_fig01.png` = 原書第 27 頁第 1 張 |
| `text/` | 每張圖表的結構化內容（Markdown 表格），由 PaddleOCR-VL 辨識 |
| `index.md` | 人看的索引：編號、原書頁、Word 頁、類型、尺寸、摘要 |
| `index.json` | 機器讀的索引，欄位同 index.md，供程式或 Agent 查詢 |

## 怎麼用

**對照閱讀**：在 Word 或 Markdown 看到插圖位置時，用 `原書 p.XX` 對到 `figs/p0XX_*.png`，
圖表內容可直接看 `text/p0XX_*.md`，不必再開圖。

**知識庫**：`index.json` 的 `figures` 陣列每筆含
`name / page / word_page / type / w / h / lines / file / has_text / summary`，
可直接匯入向量資料庫或作為檢索表。

## 已知限制

- 圖表內容為**機器辨識**，形近字與數字可能出錯（例：閱讀→關鎖、未來→末來），引用前請對照原圖。
- 多欄／泡泡圖的閱讀順序可能交錯，`type=圖表` 且文字行數多者風險較高。
- 裁切邊界依墨跡自動判定，少數圖可能有 1–8px 白邊。
"""
    open(os.path.join(args.out, "README.md"), "w", encoding="utf-8").write(readme)

    print(f"[figlib] 完成：{len(items)} 張 → {args.out}")
    print(f"[figlib] 索引：{os.path.join(args.out, 'index.md')}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
