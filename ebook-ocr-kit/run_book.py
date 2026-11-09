# -*- coding: utf-8 -*-
"""一鍵跑完電子書 OCR：六個階段，每階段自動驗收。

給 AI 代理（Codex / Claude Code）用的入口。所有階段都可單獨重跑，
已完成的產物會跳過（RapidOCR 與 PaddleOCR-VL 皆支援續傳）。

用法：
    <ocrgpu-python> run_book.py --src "<來源圖片目錄>" --work "<工作目錄>" \
        --title "書名" [--sub "副標"] [--author "作者"] [--skip-pdf]

範例：
    C:/Users/mega0/.workbuddy-ai/binaries/python/envs/ocrgpu/Scripts/python.exe \
      run_book.py --src "E:/000/某書" --work "O:/out/某書_work" \
      --title "某書" --sub "副標" --author "作者 著"

參數：
    --src      一頁一張 PNG/JPG 的目錄（檔名需含頁碼，如 xxx-001.png），
               或一個 PDF 檔／只含一個 PDF 的資料夾（自動逐頁渲染成圖）
    --pdf-width PDF 渲染頁寬 px（預設 720，對齊版面分析門檻，勿隨意加大）
    --no-fix   不做形近字校對（預設會依 data/ocr_fixes.tsv 校對並留紀錄）
    --fix-table 書專用形近字表（TSV，覆蓋預設表）
    --work     工作目錄（會建立 txt/ figs/）
    --workers-gpu   RapidOCR 進程數（預設 2）
    --workers-vl    PaddleOCR-VL 並行數（預設 3，需與 server --parallel 一致）
    --skip-vl  跳過 PaddleOCR-VL（只用於除錯）
    --skip-pdf 不匯出 PDF（沒有 MS Word 時使用）
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
SCRIPTS = os.path.join(HERE, "scripts")
PY = sys.executable
sys.path.insert(0, SCRIPTS)
import ingest  # noqa: E402  PDF／截圖資料夾 正規化
VL_HEALTH = "http://127.0.0.1:18976/health"


def log(msg):
    print(f"\n{'='*60}\n{msg}\n{'='*60}", flush=True)


def ok(msg):
    print(f"  ✓ {msg}", flush=True)


def die(msg):
    print(f"\n  ✗ 中止：{msg}", flush=True)
    sys.exit(1)


def run(cmd, cwd=None, quiet=False):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if not quiet and p.stdout.strip():
        print("\n".join("    " + x for x in p.stdout.strip().splitlines()[-8:]), flush=True)
    if p.returncode != 0:
        print(p.stderr[-1500:], flush=True)
    return p.returncode == 0, p.stdout


def pages_of(src):
    out = []
    for f in glob.glob(os.path.join(src, "*")):
        m = re.search(r"[-_](\d{1,4})\.(png|jpg|jpeg|webp|bmp)$", os.path.basename(f), re.I)
        if m:
            out.append((int(m.group(1)), f))
    return sorted(out)


def stage0(src, work):
    log("階段 0／素材盤點")
    files = pages_of(src)
    if not files:
        die(f"{src} 找不到頁面圖檔（需 png/jpg/webp/bmp，檔名含頁碼）")
    nums = [n for n, _ in files]
    missing = [n for n in range(min(nums), max(nums) + 1) if n not in set(nums)]
    ok(f"頁數 {len(files)}，頁碼 {min(nums)}–{max(nums)}")
    if missing:
        print(f"  ⚠ 缺頁 {len(missing)} 張：{missing[:15]}{'…' if len(missing) > 15 else ''}", flush=True)
    try:
        from PIL import Image
        im = Image.open(files[0][1])
        ok(f"解析度 {im.size[0]}×{im.size[1]}")
        if im.size[0] < 600:
            print("  ⚠ 頁寬 < 600px，插圖裁切會偏模糊", flush=True)
    except Exception as e:
        print(f"  ⚠ 無法讀取圖片尺寸：{e}", flush=True)
    os.makedirs(os.path.join(work, "txt"), exist_ok=True)
    os.makedirs(os.path.join(work, "figs"), exist_ok=True)
    return files


def stage1(src, work, workers):
    log("階段 1／版面定位（RapidOCR + DirectML，只取座標）")
    procs = []
    for i in range(workers):
        procs.append(subprocess.Popen(
            [PY, os.path.join(SCRIPTS, "ocr_worker.py"), str(i), str(workers), src],
            cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    for p in procs:
        p.wait()
    parts = glob.glob(os.path.join(work, "ocr_part_*.json"))
    if not parts:
        die("RapidOCR 沒有產出 ocr_part_*.json")
    total = sum(len(json.load(open(f, encoding="utf-8"))) for f in parts)
    ok(f"{len(parts)} 個 worker 完成，共 {total} 頁座標")
    if total == 0:
        die("座標檔是空的，檢查 log 或先跑 fix_dml.py")


def vl_ready():
    import urllib.request
    try:
        with urllib.request.urlopen(VL_HEALTH, timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def stage2(src, work, workers, trad=False, no_fix=False, fix_table=""):
    log("階段 2／文字轉錄（PaddleOCR-VL + Vulkan）" + ("＋簡轉繁" if trad else ""))
    if not vl_ready():
        die("PaddleOCR-VL 服務未啟動（127.0.0.1:18976）。"
            "先執行 docs/啟動OCR服務.md 的指令，或加 --skip-vl")
    ok("服務健康檢查 200")
    cmd = [PY, os.path.join(SCRIPTS, "paddle_ocr_pages.py"),
           src, work, "--workers", str(workers)]
    if trad:
        cmd.append("--trad")
    good, out = run(cmd)
    if not good:
        die("轉錄失敗")
    meta = json.load(open(os.path.join(work, "paddle_meta.json"), encoding="utf-8"))
    ok(f"轉錄 {len(meta)} 頁")
    run([PY, os.path.join(SCRIPTS, "paddle_qc.py"), work], quiet=False)
    if not no_fix:
        cmd = [PY, os.path.join(SCRIPTS, "tw_fix.py"), work]
        if fix_table:
            cmd += ["--extra", fix_table]
        good, _ = run(cmd)
        if not good:
            print("  ⚠ 形近字校對失敗（不影響後續，可事後單獨跑 tw_fix.py）", flush=True)


def stage3(src, work):
    log("階段 3／版面分析與插圖裁切")
    good, out = run([PY, os.path.join(SCRIPTS, "ebook_layout.py"), src, work])
    if not good:
        die("版面分析失敗")
    lay = json.load(open(os.path.join(work, "layout.json"), encoding="utf-8"))
    figs = sum(1 for v in lay["layout"].values() for b in v if b["type"] == "fig")
    from collections import Counter
    ok(f"頁型 {dict(Counter(lay['kinds'].values()))}，切出 {figs} 張圖")
    if figs == 0:
        die("沒有切出任何插圖，檢查墨跡門檻")


def stage4(src, work, title, sub, author, out_docx):
    log("階段 4／Word 組裝")
    good, out = run([PY, os.path.join(SCRIPTS, "ebook_docx.py"),
                     src, work, out_docx, title, sub, author])
    if not good:
        die("Word 組裝失敗")
    ok(f"{out_docx}")
    return out_docx


def stage5_pdf(docx_path, work):
    log("階段 5／匯出 PDF（Word COM，供頁碼對照與驗證）")
    pdf = os.path.join(work, "book.pdf")
    ps = (
        f"$src='{docx_path}'; $pdf='{pdf}';"
        "if (Test-Path $pdf) { Remove-Item $pdf -Force };"
        "$w=New-Object -ComObject Word.Application; $w.Visible=$false;"
        "$d=$w.Documents.Open($src); $d.SaveAs([ref]$pdf,[ref]17);"
        "$p=$d.ComputeStatistics(2); $d.Close(0); $w.Quit();"
        "Write-Output ('pages=' + $p)"
    )
    p = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True)
    m = re.search(r"pages=(\d+)", p.stdout or "")
    if m:
        ok(f"PDF 匯出完成，Word 共 {m.group(1)} 頁")
        return pdf
    print("  ⚠ PDF 匯出失敗（可能沒有 MS Word），圖表庫將沒有 Word 頁對照", flush=True)
    return ""


def stage6(work, lib, title, out_md, pdf):
    log("階段 6／圖表庫與全文 MD")
    cmd = [PY, os.path.join(SCRIPTS, "ebook_figlib.py"), work, lib, title, "--describe"]
    if pdf:
        cmd += ["--pdf", pdf]
    good, out = run(cmd)
    if not good:
        print("  ⚠ 圖表庫建立失敗（不影響 Word）", flush=True)
    else:
        idx = json.load(open(os.path.join(lib, "index.json"), encoding="utf-8"))
        ok(f"{idx['figure_count']} 張圖（表格 {idx['table_count']}／圖文 {idx['diagram_count']}）")
    good, out = run([PY, os.path.join(SCRIPTS, "ebook_fullmd.py"), work, lib, title, out_md])
    if good:
        ok(f"全文 MD：{out_md}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--sub", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--workers-gpu", type=int, default=2)
    ap.add_argument("--workers-vl", type=int, default=3)
    ap.add_argument("--trad", action="store_true", help="簡體書轉繁體（OpenCC s2twp）")
    ap.add_argument("--pdf-width", type=int, default=720)
    ap.add_argument("--no-fix", action="store_true")
    ap.add_argument("--fix-table", default="")
    ap.add_argument("--skip-vl", action="store_true")
    ap.add_argument("--skip-pdf", action="store_true")
    ap.add_argument("--from", dest="from_stage", type=int, default=0,
                    help="從第 N 階段開始（0–6），用於重跑單一階段")
    args = ap.parse_args()

    work = os.path.abspath(args.work)
    os.makedirs(work, exist_ok=True)
    try:
        src, how = ingest.prepare(args.src, os.path.join(work, "pages"), args.pdf_width,
                                  log=lambda m: print(m, flush=True))
    except Exception as e:
        die(str(e))
    print(f"  來源：{how}", flush=True)
    base = os.path.dirname(work)
    out_docx = os.path.join(base, f"{args.title}_OCR圖文版.docx")
    lib = os.path.join(base, f"{args.title}_圖表庫")
    out_md = os.path.join(base, f"{args.title}_全文.md")

    t0 = time.time()
    files = stage0(src, work)
    if args.from_stage <= 1:
        stage1(src, work, args.workers_gpu)
    if args.from_stage <= 2 and not args.skip_vl:
        stage2(src, work, args.workers_vl, args.trad, args.no_fix, args.fix_table)
    if args.from_stage <= 3:
        stage3(src, work)
    if args.from_stage <= 4:
        stage4(src, work, args.title, args.sub, args.author, out_docx)
    pdf = ""
    if args.from_stage <= 5 and not args.skip_pdf:
        pdf = stage5_pdf(out_docx, work)
    if args.from_stage <= 6:
        stage6(work, lib, args.title, out_md, pdf)

    el = time.time() - t0
    log(f"全部完成，總耗時 {el/60:.1f} 分鐘")
    print(f"""
交付物：
  Word      {out_docx}
  圖表庫    {lib}/
  全文 MD   {out_md}
  品檢報告  {os.path.join(work, 'qc_report.md')}

下一步（人工）：
  1. 開 qc_report.md，先看「圖解頁／雙欄／重複迴圈」那幾頁
  2. 抽查 3 頁與原圖逐字對照
  3. 確認沒有整頁漏掉
""", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
