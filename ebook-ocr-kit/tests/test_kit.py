# -*- coding: utf-8 -*-
"""離線測試（不需要 GPU / llama-server）。執行：python -m unittest discover -s tests -v"""
import json, os, shutil, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts")); sys.path.insert(0, ROOT)
import ingest, tw_fix, run_batch  # noqa: E402


def make_pdf(path, pages=3):
    import pymupdf
    d = pymupdf.open()
    for i in range(pages):
        pg = d.new_page(width=595, height=842)
        pg.insert_text((72, 100), f"page {i+1}", fontsize=20)
    d.save(path); d.close()


def make_pngs(folder, n=2, stem="b"):
    from PIL import Image
    os.makedirs(folder, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (660, 860), "white").save(os.path.join(folder, f"{stem}-{i+1:03d}.png"))


class IngestTest(unittest.TestCase):
    def setUp(self): self.t = tempfile.mkdtemp()
    def tearDown(self): shutil.rmtree(self.t, ignore_errors=True)

    def test_pdf_file_rendered_at_target_width(self):
        from PIL import Image
        pdf = os.path.join(self.t, "書.pdf"); make_pdf(pdf, 3)
        d, msg = ingest.prepare(pdf, os.path.join(self.t, "pages"), 720)
        files = sorted(os.listdir(d))
        self.assertEqual(files, ["書-001.png", "書-002.png", "書-003.png"])
        self.assertTrue(ingest.has_page_images(d))
        self.assertEqual(Image.open(os.path.join(d, files[0])).size[0], 720)

    def test_resume_skips_existing(self):
        pdf = os.path.join(self.t, "a.pdf"); make_pdf(pdf, 2)
        out = os.path.join(self.t, "p")
        ingest.render_pdf(pdf, out); f = os.path.join(out, "a-001.png")
        m = os.path.getmtime(f); ingest.render_pdf(pdf, out)
        self.assertEqual(m, os.path.getmtime(f))

    def test_image_folder_used_as_is(self):
        make_pngs(os.path.join(self.t, "s"))
        d, _ = ingest.prepare(os.path.join(self.t, "s"), os.path.join(self.t, "p"))
        self.assertEqual(d, os.path.join(self.t, "s"))

    def test_folder_with_single_pdf(self):
        os.makedirs(os.path.join(self.t, "f")); make_pdf(os.path.join(self.t, "f", "x.pdf"), 2)
        d, _ = ingest.prepare(os.path.join(self.t, "f"), os.path.join(self.t, "p"))
        self.assertEqual(len(os.listdir(d)), 2)

    def test_multi_pdf_folder_and_empty_rejected(self):
        os.makedirs(os.path.join(self.t, "m"))
        for n in "ab": make_pdf(os.path.join(self.t, "m", n + ".pdf"), 1)
        with self.assertRaises(ValueError): ingest.prepare(os.path.join(self.t, "m"), self.t + "/p")
        os.makedirs(os.path.join(self.t, "e"))
        with self.assertRaises(ValueError): ingest.prepare(os.path.join(self.t, "e"), self.t + "/p")


class TwFixTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(); os.makedirs(os.path.join(self.t, "txt"))
        self.part = os.path.join(self.t, "txt", "part_01.md")
        open(self.part, "w", encoding="utf-8").write(
            "@@PAGE 5\n[FIG] 無\n[TEXT]\n# 關鎖的方法\n這本書談影象與末來，關鎖很重要。\n@@PAGE 6\n[TEXT]\n正常內容\n")
        open(os.path.join(self.t, "qc_report.md"), "w", encoding="utf-8").write("# QC\n")
    def tearDown(self): shutil.rmtree(self.t, ignore_errors=True)

    def run_fix(self, *extra):
        sys.argv = ["tw_fix", self.t, *extra]; tw_fix.main()

    def test_fix_log_backup_idempotent(self):
        self.run_fix(); txt = open(self.part, encoding="utf-8").read()
        self.assertIn("閱讀的方法", txt); self.assertIn("圖像與未來", txt)
        self.assertIn("@@PAGE 5", txt); self.assertIn("[TEXT]", txt)
        self.assertIn("關鎖", open(os.path.join(self.t, "txt_raw", "part_01.md"), encoding="utf-8").read())
        log = json.load(open(os.path.join(self.t, "tw_fix_log.json"), encoding="utf-8"))
        self.assertEqual({x["page"] for x in log}, {5}); self.assertEqual(len(log), 4)
        self.run_fix()  # 第二次結果必須一致，且 QC 只出現一次該段落
        self.assertEqual(open(self.part, encoding="utf-8").read(), txt)
        self.assertEqual(open(os.path.join(self.t, "qc_report.md"), encoding="utf-8").read().count("形近字自動校對"), 1)

    def test_dry_run_and_restore_and_extra(self):
        before = open(self.part, encoding="utf-8").read()
        self.run_fix("--dry-run"); self.assertEqual(open(self.part, encoding="utf-8").read(), before)
        self.assertFalse(os.path.exists(os.path.join(self.t, "txt_raw")))
        extra = os.path.join(self.t, "x.tsv"); open(extra, "w", encoding="utf-8").write("正常\t特殊\n")
        self.run_fix("--extra", extra); self.assertIn("特殊內容", open(self.part, encoding="utf-8").read())
        self.run_fix("--restore"); self.assertEqual(open(self.part, encoding="utf-8").read(), before)

    def test_longest_match_wins_and_contract_untouched(self):
        t = {"AB": "X", "ABC": "Y"}
        self.assertEqual(tw_fix.fix_text("ABC AB\n", t), "Y X\n")
        self.assertEqual(tw_fix.fix_text("[TEXT]\n", {"TEXT": "x"}), "[TEXT]\n")


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(); self.root = os.path.join(self.t, "root")
        make_pngs(os.path.join(self.root, "書A")); make_pdf(os.path.join(self.root, "書B.pdf"))
        os.makedirs(os.path.join(self.root, "空")); os.makedirs(os.path.join(self.root, "多"))
        for n in "xy": make_pdf(os.path.join(self.root, "多", n + ".pdf"), 1)
    def tearDown(self): shutil.rmtree(self.t, ignore_errors=True)

    def test_discover(self):
        self.assertEqual([n for n, _ in run_batch.discover(self.root)], ["多_x", "多_y", "書A", "書B"])

    def test_skip_done_and_continue_after_failure(self):
        out = os.path.join(self.t, "out"); calls = []
        def fake(name, src, outdir, pt, python=None):
            calls.append(name); os.makedirs(outdir, exist_ok=True)
            open(os.path.join(outdir, "run.log"), "w").write("boom")
            if name == "書A":
                open(os.path.join(outdir, f"{name}_OCR圖文版.docx"), "w").write("x"); return True, 1, outdir
            return False, 1, outdir
        run_batch.run_one = fake
        argv = ["--root", self.root, "--out", out, "--skip-vl-check", "--only", "書"]
        sys.argv = ["rb", *argv]
        with self.assertRaises(SystemExit) as c: run_batch.main()
        self.assertEqual(c.exception.code, 1); self.assertEqual(calls, ["書A", "書B"])  # 失敗不中斷
        calls.clear()
        with self.assertRaises(SystemExit): run_batch.main()
        self.assertEqual(calls, ["書B"])  # 書A 已完成 → 跳過
        rep = open(os.path.join(out, "batch_report.md"), encoding="utf-8").read()
        self.assertIn("skipped", rep); self.assertIn("失敗詳情", rep)

if __name__ == "__main__":
    unittest.main()
