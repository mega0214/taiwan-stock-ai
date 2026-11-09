# -*- coding: utf-8 -*-
"""產生帶圖的全文 Markdown，圖片連到圖表庫，供對照閱讀與知識庫使用。

用法：
    python ebook_fullmd.py <工作目錄> <圖表庫目錄> "<書名>" <輸出.md>
"""
import json
import os
import re
import sys


def main():
    if len(sys.argv) < 5:
        sys.exit("用法: python ebook_fullmd.py <工作目錄> <圖表庫> <書名> <輸出.md>")
    work, lib, title, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

    meta = json.load(open(os.path.join(work, "paddle_meta.json"), encoding="utf-8"))

    # 文字以 txt/part_*.md 為準（已做 LaTeX 清理與重複壓縮），meta 只取頁碼
    pages = {}
    import glob
    for f in sorted(glob.glob(os.path.join(work, "txt", "part_*.md"))):
        raw = open(f, encoding="utf-8").read()
        chunks = re.split(r"@@PAGE\s+(\d+)", raw)
        for i in range(1, len(chunks), 2):
            num = int(chunks[i])
            body = chunks[i + 1]
            if "[TEXT]" in body:
                body = body.split("[TEXT]", 1)[1]
            lines_ = [x.strip() for x in body.splitlines()
                      if x.strip() and not x.strip().startswith("[FIG")]
            pages[num] = lines_
    idx_path = os.path.join(lib, "index.json")
    figs = {}
    if os.path.exists(idx_path):
        for f in json.load(open(idx_path, encoding="utf-8"))["figures"]:
            figs.setdefault(f["page"], []).append(f)

    rel = os.path.relpath(os.path.join(lib, "figs"), os.path.dirname(os.path.abspath(out)))
    rel = rel.replace("\\", "/")

    lines = [f"# 《{title}》全文（OCR 圖文對照）", "",
             f"> 文字由 PaddleOCR-VL 本機辨識，圖片連到 `{rel}/`。",
             "> 圖解頁的閱讀順序可能與原書不同，引用前請對照原圖。", ""]

    for n in sorted(pages):
        txt_lines = pages[n]
        lines.append(f"## p.{n}")
        lines.append("")
        for f in figs.get(n, []):
            cap = f["summary"] or f["type"]
            lines.append(f"![p.{n} {f['name']}]({rel}/{f['name']})")
            lines.append(f"*{f['type']}：{cap}*")
            lines.append("")
        for s in txt_lines:
            if s.startswith("# "):
                lines.append("### " + s[2:])
            else:
                lines.append(s)
            lines.append("")
        lines.append("")

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"已產出 {out}（{len(meta)} 頁，{sum(len(v) for v in figs.values())} 張圖）")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
