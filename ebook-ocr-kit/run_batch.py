# -*- coding: utf-8 -*-
"""批次處理多本書：母資料夾下，每個子資料夾（截圖）或每個 PDF 檔 = 一本書。

用法：
    <ocrgpu-python> run_batch.py --root "E:/000" --out "O:/out" [--only 書名片段] [--force]
                                 [--dry-run] [-- 其餘參數原樣傳給 run_book.py]

行為：
    - 書名 = 子資料夾名（或 PDF 主檔名）；每本輸出到 <out>/<書名>/
    - 已完成的書自動跳過（batch_state.json 記錄 + docx 存在）；--force 全部重跑
    - 單本失敗不影響其他本；最後產出 <out>/batch_report.md
    - 啟動前先檢查 VL 服務，沒開就直接停（避免每本都白跑到階段 2 才失敗）

範例（附加參數放在 -- 之後）：
    run_batch.py --root E:/000 --out O:/out -- --skip-pdf --pdf-width 720
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "scripts"))
import ingest  # noqa: E402

VL_HEALTH = "http://127.0.0.1:18976/health"


def discover(root):
    """回傳 [(書名, 來源路徑)]。來源可為資料夾或 PDF 檔。"""
    books = []
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if os.path.isdir(p):
            if ingest.has_page_images(p):
                books.append((name, p))
            else:
                pdfs = ingest.list_pdfs(p)
                if len(pdfs) == 1:
                    books.append((name, p))
                elif len(pdfs) > 1:  # 一個 PDF 一本書
                    books.extend((f"{name}_{os.path.splitext(os.path.basename(f))[0]}", f) for f in pdfs)
        elif name.lower().endswith(".pdf"):
            books.append((os.path.splitext(name)[0], p))
    return books


def safe(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "book"


def vl_ready():
    import urllib.request
    try:
        with urllib.request.urlopen(VL_HEALTH, timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def load_state(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


def save_state(path, st):
    tmp = path + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def is_done(st, name, outdir):
    docx = os.path.join(outdir, f"{name}_OCR圖文版.docx")
    return st.get(name, {}).get("status") == "ok" and os.path.exists(docx)


def tw_fix_count(work):
    try:
        return len(json.load(open(os.path.join(work, "tw_fix_log.json"), encoding="utf-8")))
    except Exception:
        return None


def run_one(name, src, outdir, passthrough, python=sys.executable):
    work = os.path.join(outdir, f"{name}_work")
    os.makedirs(outdir, exist_ok=True)
    cmd = [python, os.path.join(HERE, "run_book.py"),
           "--src", src, "--work", work, "--title", name] + passthrough
    t0 = time.time()
    with open(os.path.join(outdir, "run.log"), "w", encoding="utf-8") as lg:
        p = subprocess.run(cmd, stdout=lg, stderr=subprocess.STDOUT)
    return p.returncode == 0, time.time() - t0, work


def tail(path, n=6):
    try:
        return open(path, encoding="utf-8", errors="replace").read().strip().splitlines()[-n:]
    except Exception:
        return []


def write_report(out, rows, t_total):
    ok = [r for r in rows if r["status"] == "ok"]
    sk = [r for r in rows if r["status"] == "skipped"]
    bad = [r for r in rows if r["status"] == "failed"]
    md = ["# 批次處理報告", "",
          f"- 共 {len(rows)} 本：成功 **{len(ok)}**／跳過 {len(sk)}／失敗 **{len(bad)}**",
          f"- 本次耗時 {t_total / 60:.1f} 分鐘", "",
          "| 書名 | 狀態 | 耗時 | 形近字替換 | 品檢報告 |", "|---|---|---|---|---|"]
    for r in rows:
        el = f"{r['sec'] / 60:.1f} 分" if r.get("sec") else "—"
        fx = r.get("fixes")
        qc = os.path.join(r["outdir"], f"{r['name']}_work", "qc_report.md")
        md.append(f"| {r['name']} | {r['status']} | {el} | {fx if fx is not None else '—'} | "
                  f"{'`' + os.path.relpath(qc, out) + '`' if os.path.exists(qc) else '—'} |")
    if bad:
        md += ["", "## 失敗詳情", ""]
        for r in bad:
            md += [f"### {r['name']}", "```", *r.get("tail", []), "```",
                   f"完整 log：`{os.path.relpath(os.path.join(r['outdir'], 'run.log'), out)}`", ""]
        md.append("修好原因後重跑同一指令即可（成功的書會自動跳過）。")
    open(os.path.join(out, "batch_report.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")


def main():
    argv = sys.argv[1:]
    passthrough = []
    if "--" in argv:
        i = argv.index("--")
        argv, passthrough = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="母資料夾")
    ap.add_argument("--out", required=True, help="輸出根目錄")
    ap.add_argument("--only", default="", help="只跑書名含此字串者")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="只列出會處理哪些書")
    ap.add_argument("--skip-vl-check", action="store_true")
    a = ap.parse_args(argv)

    root, out = os.path.abspath(a.root), os.path.abspath(a.out)
    books = [(safe(n), s) for n, s in discover(root) if a.only in n]
    if not books:
        sys.exit(f"{root} 底下沒找到任何書（截圖資料夾或 PDF）")
    names = [n for n, _ in books]
    if len(set(names)) != len(names):
        sys.exit("書名重複：" + ", ".join(sorted({n for n in names if names.count(n) > 1})))

    print(f"找到 {len(books)} 本：")
    for n, s in books:
        print(f"  - {n}  ← {s}")
    if a.dry_run:
        return

    if "--skip-vl" not in passthrough and not a.skip_vl_check and not vl_ready():
        sys.exit("PaddleOCR-VL 服務未啟動（127.0.0.1:18976），先啟動再批次跑。")

    os.makedirs(out, exist_ok=True)
    state_path = os.path.join(out, "batch_state.json")
    st = load_state(state_path)
    rows, t0 = [], time.time()
    for k, (name, src) in enumerate(books, 1):
        outdir = os.path.join(out, name)
        row = {"name": name, "outdir": outdir}
        if not a.force and is_done(st, name, outdir):
            row["status"] = "skipped"
            print(f"[{k}/{len(books)}] 跳過（已完成）：{name}", flush=True)
        else:
            print(f"[{k}/{len(books)}] 開始：{name}", flush=True)
            good, sec, work = run_one(name, src, outdir, passthrough)
            row.update(status="ok" if good else "failed", sec=sec, fixes=tw_fix_count(work))
            if not good:
                row["tail"] = tail(os.path.join(outdir, "run.log"))
            st[name] = {"status": row["status"], "seconds": round(sec), "at": time.strftime("%F %T")}
            save_state(state_path, st)
            print(f"    → {row['status']}（{sec / 60:.1f} 分）", flush=True)
        rows.append(row)
        write_report(out, rows, time.time() - t0)

    print(f"\n完成。報告：{os.path.join(out, 'batch_report.md')}")
    sys.exit(0 if all(r["status"] != "failed" for r in rows) else 1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
