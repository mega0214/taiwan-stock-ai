# -*- coding: utf-8 -*-
"""輸入正規化：把「PDF」或「截圖資料夾」統一成「一頁一張 PNG（檔名含頁碼）」的資料夾。

為什麼要統一尺寸：ebook_layout.py 的門檻（文字行高 12–32px、圖像最長墨跡 >=90px 等）
是以頁寬約 600–700px 的截圖調出來的。PDF 若用 150dpi 渲染（頁寬 ~1240px）會讓門檻全部失準，
所以預設把 PDF 頁面渲染成頁寬 720px（可用 --pdf-width 調整）。

用法：
    python ingest.py <來源：PDF 檔 或 資料夾> <輸出頁面目錄> [--pdf-width 720]

規則：
    - 來源是 .pdf 檔            → 渲染成 <輸出>/<pdf主檔名>-001.png …
    - 來源是資料夾且有頁面圖    → 原樣使用（不複製），回傳該資料夾
    - 來源是資料夾、只有一個 PDF → 渲染該 PDF
    - 資料夾內有多個 PDF 且無圖  → 報錯（請改用 run_batch.py，一個 PDF 一本書）
"""
import argparse
import glob
import os
import re
import sys

IMG_RE = re.compile(r"[-_](\d{1,4})\.(png|jpg|jpeg|webp|bmp)$", re.I)


def has_page_images(folder):
    return any(IMG_RE.search(os.path.basename(f)) for f in glob.glob(os.path.join(folder, "*")))


def list_pdfs(folder):
    return sorted(f for f in glob.glob(os.path.join(folder, "*"))
                  if f.lower().endswith(".pdf"))


def render_pdf(pdf, out_dir, target_width=720, log=print):
    """逐頁渲染成 PNG。已存在且非空的頁面會跳過（可續傳）。回傳頁數。"""
    import pymupdf
    os.makedirs(out_dir, exist_ok=True)
    stem = re.sub(r"[^\w一-鿿.-]+", "_", os.path.splitext(os.path.basename(pdf))[0]) or "book"
    doc = pymupdf.open(pdf)
    if doc.needs_pass:
        raise RuntimeError(f"PDF 有密碼保護：{pdf}")
    n = len(doc)
    width = max(1, int(target_width))
    for i in range(n):
        dst = os.path.join(out_dir, f"{stem}-{i + 1:03d}.png")
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            continue
        page = doc[i]
        zoom = width / max(page.rect.width, 1)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        tmp = dst + ".tmp"
        pix.save(tmp, output="png")
        os.replace(tmp, dst)
        if (i + 1) % 50 == 0:
            log(f"  PDF 渲染 {i + 1}/{n}")
    doc.close()
    return n


def prepare(src, pages_dir, pdf_width=720, log=print):
    """回傳 (頁面圖資料夾, 說明字串)。"""
    src = os.path.abspath(src)
    if os.path.isfile(src):
        if not src.lower().endswith(".pdf"):
            raise ValueError(f"來源檔不是 PDF：{src}")
        n = render_pdf(src, pages_dir, pdf_width, log)
        return pages_dir, f"PDF {n} 頁 → {pages_dir}（頁寬 {pdf_width}px）"
    if not os.path.isdir(src):
        raise FileNotFoundError(src)
    if has_page_images(src):
        return src, "使用現有截圖資料夾"
    pdfs = list_pdfs(src)
    if len(pdfs) == 1:
        n = render_pdf(pdfs[0], pages_dir, pdf_width, log)
        return pages_dir, f"PDF {n} 頁 → {pages_dir}（頁寬 {pdf_width}px）"
    if len(pdfs) > 1:
        raise ValueError(f"{src} 內有 {len(pdfs)} 個 PDF，請用 run_batch.py（一個 PDF 一本書）")
    raise ValueError(f"{src} 找不到頁面圖（png/jpg/webp/bmp，檔名含頁碼）也沒有 PDF")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--pdf-width", type=int, default=720)
    a = ap.parse_args()
    d, msg = prepare(a.src, a.out, a.pdf_width)
    print(msg)
    print(d)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
