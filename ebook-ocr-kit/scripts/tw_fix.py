# -*- coding: utf-8 -*-
"""形近字校對：用詞彙表修正 PaddleOCR-VL 轉錄中「信心度很高但寫錯」的詞。

說明書 §7.8：形近字錯誤（閱讀→關鎖）用信心度抓不到，只能靠詞彙表或人工。
本工具只做「整詞精確替換」，不做模糊比對，並且：
  - 第一次執行前把 txt/ 備份到 txt_raw/（之後不覆蓋，永遠可還原）
  - 每次替換都記錄 頁碼／原詞／新詞，輸出 tw_fix_report.md 與 tw_fix_log.json
  - 不動 @@PAGE / [TEXT] / [FIG] 等契約標記行
  - 可重複執行（冪等）

用法：
    python tw_fix.py <工作目錄> [--extra 書專用表.tsv] [--dry-run] [--restore]
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TABLE = os.path.join(HERE, "..", "data", "ocr_fixes.tsv")
CONTRACT = re.compile(r"^\s*(@@PAGE\b|\[TEXT\]|\[FIG\]|\[BLANK\])")


def load_table(*paths):
    """回傳 {錯誤詞: 正確詞}。後面的表覆蓋前面的。"""
    table = {}
    for p in paths:
        if not p or not os.path.exists(p):
            continue
        for ln in open(p, encoding="utf-8"):
            ln = ln.rstrip("\n")
            if not ln.strip() or ln.lstrip().startswith("#"):
                continue
            cols = ln.split("\t")
            if len(cols) < 2:
                continue
            bad, good = cols[0].strip(), cols[1].strip()
            if bad and good and bad != good:
                table[bad] = good
    return table


def fix_text(text, table, page=None, log=None):
    """對整段文字做替換；長詞優先，避免短詞蓋掉長詞。"""
    if not table:
        return text
    keys = sorted(table, key=len, reverse=True)
    pat = re.compile("|".join(re.escape(k) for k in keys))
    out = []
    cur = page
    for line in text.splitlines(keepends=True):
        m = re.match(r"\s*@@PAGE\s+(\d+)", line)
        if m:
            cur = int(m.group(1))
        if CONTRACT.match(line):
            out.append(line)
            continue

        def sub(mo):
            if log is not None:
                log.append({"page": cur, "from": mo.group(0), "to": table[mo.group(0)]})
            return table[mo.group(0)]
        out.append(pat.sub(sub, line))
    return "".join(out)


def restore(work):
    raw, txt = os.path.join(work, "txt_raw"), os.path.join(work, "txt")
    if not os.path.isdir(raw):
        sys.exit("沒有 txt_raw/ 備份，無法還原")
    for f in glob.glob(os.path.join(raw, "*.md")):
        shutil.copy2(f, os.path.join(txt, os.path.basename(f)))
    print("已還原 txt/ 為備份內容")


def write_reports(work, log, table):
    json.dump(log, open(os.path.join(work, "tw_fix_log.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    from collections import Counter, defaultdict
    by_pair = Counter((x["from"], x["to"]) for x in log)
    pages = defaultdict(set)
    for x in log:
        pages[(x["from"], x["to"])].add(x["page"])
    md = ["## 形近字自動校對", "",
          f"- 詞彙表 {len(table)} 條；本次共替換 **{len(log)}** 處（{len({x['page'] for x in log})} 頁）",
          "- 原始轉錄備份在 `txt_raw/`，可用 `tw_fix.py --restore` 還原", ""]
    if log:
        md += ["| 錯誤詞 | 改為 | 次數 | 頁碼 |", "|---|---|---|---|"]
        for (a, b), n in by_pair.most_common():
            ps = sorted(p for p in pages[(a, b)] if p is not None)
            md.append(f"| {a} | {b} | {n} | {', '.join(map(str, ps[:15]))}{'…' if len(ps) > 15 else ''} |")
    else:
        md.append("（沒有命中任何條目）")
    text = "\n".join(md) + "\n"
    open(os.path.join(work, "tw_fix_report.md"), "w", encoding="utf-8").write(text)
    # 併入 qc_report.md（先移除舊段落，保持冪等）
    qc = os.path.join(work, "qc_report.md")
    if os.path.exists(qc):
        old = open(qc, encoding="utf-8").read()
        old = re.split(r"\n## 形近字自動校對", old)[0].rstrip() + "\n"
        open(qc, "w", encoding="utf-8").write(old + "\n" + text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--table", default=DEFAULT_TABLE)
    ap.add_argument("--extra", default="", help="書專用詞彙表（TSV，覆蓋預設表）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()
    work = os.path.abspath(a.work)
    if a.restore:
        return restore(work)

    txt, raw = os.path.join(work, "txt"), os.path.join(work, "txt_raw")
    files = sorted(glob.glob(os.path.join(txt, "part_*.md")))
    if not files:
        sys.exit(f"{txt} 沒有 part_*.md（先跑階段 2）")
    table = load_table(a.table, a.extra)

    # 永遠從原始轉錄開始算，這樣重跑結果一致、詞彙表刪條目也能生效
    if not os.path.isdir(raw) and not a.dry_run:
        os.makedirs(raw)
        for f in files:
            shutil.copy2(f, os.path.join(raw, os.path.basename(f)))
    src_dir = raw if os.path.isdir(raw) else txt

    log = []
    for f in files:
        base = os.path.basename(f)
        original = open(os.path.join(src_dir, base), encoding="utf-8").read()
        fixed = fix_text(original, table, log=log)
        if not a.dry_run:
            open(f, "w", encoding="utf-8").write(fixed)
    if not a.dry_run:
        write_reports(work, log, table)
    print(f"[tw_fix] 詞彙表 {len(table)} 條，替換 {len(log)} 處"
          + ("（dry-run，未寫檔）" if a.dry_run else ""))
    for x in log[:10]:
        print(f"  p.{x['page']}: {x['from']} → {x['to']}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
