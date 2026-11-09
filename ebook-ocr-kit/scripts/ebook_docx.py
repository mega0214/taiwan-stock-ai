# -*- coding: utf-8 -*-
"""把逐頁轉錄（txt/part_*.md）+ 裁切插圖（figs/）+ 版面（layout.json）組裝成 Word。

用法：
    python ebook_docx.py <來源圖片目錄> <工作目錄> <輸出.docx> [書名] [副標] [作者]
"""
import os, re, sys, json, glob
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.section import WD_SECTION
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from PIL import Image

if len(sys.argv) < 4:
    sys.exit("用法: python ebook_docx.py <來源圖片目錄> <工作目錄> <輸出.docx> [書名] [副標] [作者]")

SRC = os.path.abspath(sys.argv[1])
WDIR = os.path.abspath(sys.argv[2])
TXT = os.path.join(WDIR, "txt")
FIGS = os.path.join(WDIR, "figs")
OUT = os.path.abspath(sys.argv[3])
BOOK_TITLE = sys.argv[4] if len(sys.argv) > 4 else "電子書"
BOOK_SUB = sys.argv[5] if len(sys.argv) > 5 else ""
BOOK_AUTHOR = sys.argv[6] if len(sys.argv) > 6 else ""

# 頁碼 → 原始檔名（不再假設檔名格式）
PAGE_FILE = {}
for _f in glob.glob(os.path.join(SRC, "*.png")):
    _m = re.search(r"[-_](\d{1,4})\.png$", os.path.basename(_f))
    if _m:
        PAGE_FILE[int(_m.group(1))] = os.path.basename(_f)

# 頁寬（px）：小圖縮放要用「實際頁寬」而不是固定 664
PAGE_W = 664
try:
    _k = sorted(PAGE_FILE.items())[0][1]
    PAGE_W = Image.open(os.path.join(SRC, _k)).size[0]
except Exception:
    pass

FONT_BODY = "Microsoft JhengHei"
FONT_HEAD = "Microsoft JhengHei"

# ---------- 解析轉錄檔 ----------
pages = {}
for f in sorted(glob.glob(os.path.join(TXT, "part_*.md"))):
    raw = open(f, encoding="utf-8").read()
    chunks = re.split(r"@@PAGE\s+(\d+)", raw)
    for i in range(1, len(chunks), 2):
        num = int(chunks[i]); body = chunks[i + 1]
        figdesc = ""
        m = re.search(r"\[FIG\]\s*(.*)", body)
        if m: figdesc = m.group(1).strip()
        if "[TEXT]" in body:
            body = body.split("[TEXT]", 1)[1]
        else:
            body = body.split("[FIG]", 1)[-1]
        paras, imgtx = [], []
        for ln in body.splitlines():
            t = ln.strip()
            if not t: continue
            if t.startswith("[BLANK]"): continue
            if t.startswith("（圖中文字）") or t.startswith("(圖中文字)"):
                imgtx.append(t.replace("（圖中文字）", "").replace("(圖中文字)", "").strip())
                continue
            if t.startswith("#"):
                paras.append(("h", t.lstrip("#").strip()))
            else:
                paras.append(("p", t))
        pages[num] = {"figdesc": figdesc, "paras": paras, "imgtx": imgtx}

_L = json.load(open(os.path.join(WDIR, "layout.json"), encoding="utf-8"))
layout = _L["layout"]; kinds = _L["kinds"]

# ---------- docx 樣式工具 ----------
def set_font(run, name=FONT_BODY, size=10.5, bold=False, color=None, italic=False):
    run.font.name = name
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    if color: run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts"); rpr.append(rf)
    rf.set(qn("w:eastAsia"), name)
    rf.set(qn("w:ascii"), name)
    rf.set(qn("w:hAnsi"), name)

def add_page_field(par):
    r = par.add_run()
    f1 = OxmlElement("w:fldChar"); f1.set(qn("w:fldCharType"), "begin")
    it = OxmlElement("w:instrText"); it.set(qn("xml:space"), "preserve"); it.text = " PAGE "
    f2 = OxmlElement("w:fldChar"); f2.set(qn("w:fldCharType"), "end")
    r._r.append(f1); r._r.append(it); r._r.append(f2)
    set_font(r, size=9, color=RGBColor(0x88, 0x88, 0x88))

def shade(par, hexcolor):
    pPr = par._p.get_or_add_pPr()
    sh = OxmlElement("w:shd")
    sh.set(qn("w:val"), "clear"); sh.set(qn("w:color"), "auto"); sh.set(qn("w:fill"), hexcolor)
    pPr.append(sh)

def bottom_border(par, color="DDDDDD"):
    pPr = par._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    b = OxmlElement("w:bottom")
    b.set(qn("w:val"), "single"); b.set(qn("w:sz"), "6")
    b.set(qn("w:space"), "4"); b.set(qn("w:color"), color)
    pbdr.append(b); pPr.append(pbdr)

doc = Document()
sec = doc.sections[0]
sec.page_width = Cm(21.0); sec.page_height = Cm(29.7)
sec.left_margin = Cm(2.4); sec.right_margin = Cm(2.4)
sec.top_margin = Cm(2.2); sec.bottom_margin = Cm(2.0)

st = doc.styles["Normal"]
st.font.name = FONT_BODY; st.font.size = Pt(10.5)
st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT_BODY)
st.paragraph_format.line_spacing = 1.5
st.paragraph_format.space_after = Pt(0)

# 頁尾頁碼
foot = sec.footer.paragraphs[0]
foot.alignment = WD_ALIGN_PARAGRAPH.CENTER
add_page_field(foot)

# ---------- 封面 ----------
for _ in range(6):
    doc.add_paragraph()
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
set_font(p.add_run(BOOK_TITLE), size=34, bold=True)
if BOOK_SUB:
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(p.add_run(BOOK_SUB), size=18, bold=True)
if BOOK_AUTHOR:
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(18)
    set_font(p.add_run(BOOK_AUTHOR), size=12)
for _ in range(6):
    doc.add_paragraph()
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
set_font(p.add_run("全文 OCR 圖文對照版"), size=13, bold=True, color=RGBColor(0x33, 0x33, 0x33))
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
_np = len(PAGE_FILE)
set_font(p.add_run(f"{_np} 頁原書掃描 · 文字全文轉錄 · 插圖依原頁裁切重排"), size=10,
         color=RGBColor(0x77, 0x77, 0x77))

doc.add_page_break()

# ---------- 編輯說明 ----------
p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(10)
set_font(p.add_run("關於這個版本"), size=16, bold=True)
bottom_border(p, "2F6FD0")

_nfig = sum(1 for v in layout.values() for b in v if b["type"] == "fig")
_book = f"《{BOOK_TITLE}》" + (f"，{BOOK_AUTHOR}" if BOOK_AUTHOR else "")
notes = [
 ("來源", f"{_book}，原書掃描圖檔 {_np} 頁。"),
 ("文字", f"全書 {_np} 頁逐頁視覺轉錄，中文原文照抄，還原書上段落；簡體書已轉為繁體。"),
 ("插圖", f"以版面分析自動切分「文字帶／圖像帶」，依原位置裁切，共收錄 {_nfig} 幅。"),
 ("編排", "每頁以「原書 p.XX」標示起始頁碼，其後依原頁由上而下的順序排列圖與文，盡量貼近原書閱讀動線。"),
 ("限制", "部分手繪小字與模板細節以圖片為準；轉錄文字若有疑義，仍請對照原頁圖像。"),
]
for k, v in notes:
    p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.35
    set_font(p.add_run(f"{k}｜"), size=10, bold=True, color=RGBColor(0x2F, 0x6F, 0xD0))
    set_font(p.add_run(v), size=10)

doc.add_page_break()

# ---------- 目錄（章節） ----------
p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(10)
set_font(p.add_run("章節一覽"), size=16, bold=True)
bottom_border(p, "2F6FD0")

chap_re = re.compile(r"^(第?\s*\d+\s*[章篇部]|CH\s*\d+|附錄|推薦序|作者序|結語|後記|前言)")
toc = []
for num in sorted(pages):
    for kind, t in pages[num]["paras"]:
        if kind == "h" and (len(t) <= 30) and (t.startswith("CH") or re.match(r"^第[一二三四五六七八九十\d]+[章篇部]", t) or t in ("推薦序","作者序","結語","後記","附錄")):
            toc.append((num, t))
seen = set(); toc2 = []
for n, t in toc:
    if t in seen: continue
    seen.add(t); toc2.append((n, t))
for n, t in toc2:
    p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.3
    set_font(p.add_run(t), size=10.5, bold=True)
    set_font(p.add_run(f"　　p.{n}"), size=9, color=RGBColor(0x88, 0x88, 0x88))

doc.add_page_break()

# ---------- 正文 ----------
MAXW = Cm(15.6)
def add_fig(path, cap=None):
    try:
        im = Image.open(path)
        w, h = im.size
    except Exception:
        return
    width = MAXW
    if w < 380:
        width = Cm(min(15.6, max(6.0, w / PAGE_W * 15.6)))
    # 高度上限 19cm，避免整頁圖溢出
    maxh = Cm(19.0)
    if h / w * width > maxh:
        width = int(maxh * w / h)
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6); p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(path, width=width)
    if cap:
        c = doc.add_paragraph(); c.alignment = WD_ALIGN_PARAGRAPH.CENTER
        c.paragraph_format.space_after = Pt(8)
        set_font(c.add_run(cap), size=8, color=RGBColor(0x99, 0x99, 0x99))

for num in sorted(pages):
    key = PAGE_FILE.get(num, "")
    bands = layout.get(key, [])
    pg = pages[num]
    paras = pg["paras"]
    imgtx = pg["imgtx"]

    # 頁碼標記
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(14); p.paragraph_format.space_after = Pt(6)
    shade(p, "F2F5FA")
    p.paragraph_format.keep_with_next = True
    set_font(p.add_run(f"  原書 p.{num}  "), size=8.5, bold=True, color=RGBColor(0x55, 0x66, 0x88))

    if num == 1:
        for b in bands:
            if b["type"] == "fig":
                add_fig(os.path.join(FIGS, b["file"]))
        continue

    if kinds.get(key) == "graphic":
        figs = [b for b in bands if b["type"] == "fig"]
        for b in figs:
            add_fig(os.path.join(FIGS, b["file"]))
        heads = [t for k, t in paras if k == "h"]
        bodyp = [t for k, t in paras if k == "p"]
        for t in heads:
            q = doc.add_paragraph()
            q.paragraph_format.space_before = Pt(8); q.paragraph_format.space_after = Pt(4)
            set_font(q.add_run(t), size=13, bold=True, color=RGBColor(0x1A, 0x1A, 0x1A))
        for t in bodyp:
            q = doc.add_paragraph()
            q.paragraph_format.first_line_indent = Pt(21)
            q.paragraph_format.space_after = Pt(5)
            set_font(q.add_run(t), size=10.5)
        if imgtx:
            q = doc.add_paragraph()
            q.paragraph_format.left_indent = Cm(0.8)
            q.paragraph_format.line_spacing = 1.25
            set_font(q.add_run("圖中文字："), size=8.5, bold=True, color=RGBColor(0x8A, 0x8A, 0x8A))
            set_font(q.add_run(" ／ ".join(imgtx)), size=8.5, color=RGBColor(0x8A, 0x8A, 0x8A))
        continue

    text_bands = [b for b in bands if b["type"] == "text"]
    fig_bands = [b for b in bands if b["type"] == "fig"]

    # 若該頁無任何版面資訊（純圖頁未偵測到），整頁視為圖
    if not bands and pg["figdesc"] and pg["figdesc"] != "無":
        add_fig(os.path.join(FIGS, f"{os.path.splitext(key)[0]}_fig00.png"), pg["figdesc"])
        for kind, t in paras:
            q = doc.add_paragraph(); set_font(q.add_run(t), size=10.5)
        continue

    # 段落分配到文字帶
    assign = [[] for _ in text_bands]
    if text_bands and paras:
        total = sum(max(1, b.get("nlines", 1)) for b in text_bands)
        idx = 0
        for bi, b in enumerate(text_bands):
            wgt = max(1, b.get("nlines", 1)) / total
            quota = max(1, round(wgt * len(paras))) if bi < len(text_bands) - 1 else len(paras) - idx
            quota = min(quota, len(paras) - idx)
            assign[bi] = paras[idx: idx + quota]
            idx += quota
        if idx < len(paras):
            assign[-1].extend(paras[idx:])

    ti = 0
    figtxt_done = False
    for b in bands:
        if b["type"] == "text":
            group = assign[ti] if ti < len(assign) else []
            ti += 1
            for kind, t in group:
                if kind == "h":
                    q = doc.add_paragraph()
                    q.paragraph_format.space_before = Pt(12); q.paragraph_format.space_after = Pt(6)
                    set_font(q.add_run(t), size=14, bold=True, color=RGBColor(0x1A, 0x1A, 0x1A))
                else:
                    q = doc.add_paragraph()
                    q.paragraph_format.first_line_indent = Pt(21)
                    q.paragraph_format.space_after = Pt(6)
                    q.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                    set_font(q.add_run(t), size=10.5)
        else:
            add_fig(os.path.join(FIGS, b["file"]))
            if not figtxt_done and imgtx:
                q = doc.add_paragraph()
                q.paragraph_format.left_indent = Cm(0.8)
                q.paragraph_format.space_after = Pt(3)
                q.paragraph_format.line_spacing = 1.25
                set_font(q.add_run("圖中文字："), size=8.5, bold=True, color=RGBColor(0x8A, 0x8A, 0x8A))
                set_font(q.add_run(" ／ ".join(imgtx)), size=8.5, color=RGBColor(0x8A, 0x8A, 0x8A))
                figtxt_done = True

    # 沒有圖帶但有圖說 → 補圖
    if not fig_bands and pg["figdesc"] and pg["figdesc"] != "無":
        pass

    # 圖中文字若還沒輸出
    if not figtxt_done and imgtx:
        q = doc.add_paragraph()
        q.paragraph_format.left_indent = Cm(0.8)
        q.paragraph_format.space_after = Pt(3)
        set_font(q.add_run("圖中文字："), size=8.5, bold=True, color=RGBColor(0x8A, 0x8A, 0x8A))
        set_font(q.add_run(" ／ ".join(imgtx)), size=8.5, color=RGBColor(0x8A, 0x8A, 0x8A))

doc.save(OUT)
print("saved", OUT)
print("pages", len(pages), "figs", sum(1 for v in layout.values() for b in v if b["type"] == "fig"))
