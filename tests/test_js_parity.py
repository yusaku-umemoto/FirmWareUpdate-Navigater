"""web/engine.js（ブラウザ版）と fwnav.py の結果が一字一句同じか確かめる。

Node が無ければ飛ばす。FWNAV_CORPUS に config の置き場を渡すと、その config も全部使う（既定は samples/）。
python3 -m unittest tests.test_js_parity
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from test_fwnav import CFG, MULTI_VDOM_CFG, VDOM_CFG, ZONE_CFG  # noqa: E402

CORPUS = Path(os.environ.get("FWNAV_CORPUS", ROOT / "samples"))
CASES = [("7.4.6", "7.6.8", "7.4.8,7.6.6"), ("", "7.6.8", ""), ("7.6.4", "7.6.8", ""), ("7.4.6", "7.6.6", "7.4.8"),
         ("7.4.7", "7.6.8", "7.6.4,7.6.6"), ("7.4.9", "7.6.8", "7.6.6"), ("7.4.13", "7.6.8", "direct")]


@unittest.skipUnless(shutil.which("node"), "node が無い")
class Parity(unittest.TestCase):
    def run_both(self, conf, frm, to, hops):
        with tempfile.TemporaryDirectory() as d:
            py, js = Path(d) / "py", Path(d) / "js"
            args = [sys.executable, str(ROOT / "fwnav.py"), "check", str(conf), "--to", to, "--out", str(py)]
            if frm:
                args += ["--from", frm]
            if hops:
                args += ["--path", hops]
            p = subprocess.run(args, capture_output=True, text=True)
            j = subprocess.run(["node", str(HERE / "js_runner.js"), str(conf), frm, to, hops, str(js)], capture_output=True, text=True)
            if p.returncode != 0 or j.returncode != 0:
                self.assertEqual(p.returncode != 0, j.returncode != 0, f"片方だけ失敗: py={p.stderr[-300:]} js={j.stderr[-300:]}")
                return
            for name in ("report.md", "ai-prompt.md"):
                self.assertEqual((py / name).read_text(encoding="utf-8"), (js / name).read_text(encoding="utf-8"), f"{conf.name} {frm}->{to} {name}")
            a = json.loads((py / "result.json").read_text(encoding="utf-8"))
            b = json.loads((js / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(a["results"], b["results"], f"{conf.name} result.json")

    def test_inline_configs(self):
        with tempfile.TemporaryDirectory() as d:
            for i, text in enumerate([CFG, VDOM_CFG, ZONE_CFG, MULTI_VDOM_CFG]):
                f = Path(d) / f"inline{i}.txt"
                f.write_text(text, encoding="utf-8")
                for frm, to, hops in CASES:
                    with self.subTest(conf=f.name, frm=frm, to=to):
                        self.run_both(f, frm or "7.4.6", to, hops)

    @unittest.skipUnless(CORPUS.is_dir(), "コーパスが無い")
    def test_corpus(self):
        files = sorted(p for p in CORPUS.iterdir() if p.suffix in (".txt", ".conf", ".cfg"))
        self.assertTrue(files)
        for f in files:
            for frm, to, hops in CASES:
                with self.subTest(conf=f.name, frm=frm, to=to):
                    self.run_both(f, frm, to, hops)


if __name__ == "__main__":
    unittest.main()
