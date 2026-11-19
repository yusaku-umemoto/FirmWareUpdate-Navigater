"""ブラウザ版（1 枚の HTML）を組み立てる。知識ベース（kb/）と判定の本体（web/engine.js）を埋め込む。

できた dist/fortigate-fw-check.html は、Python も通信も要らない。ブラウザで開くだけで動く。
知識ベースを直したら、これを動かして作り直す。

使い方: python3 tools/build_html.py [--out dist/fortigate-fw-check.html]
"""
import argparse
import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB_DIR = ROOT / "kb" / "fortios"


def kb_bundle():
    versions = {}
    for f in sorted(KB_DIR.glob("[0-9]*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        versions[d["version"]] = d
    groups = json.loads((KB_DIR / "models.json").read_text(encoding="utf-8"))["groups"]
    return {"product": "fortios", "built": datetime.date.today().isoformat(), "versions": versions, "groups": groups}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist" / "fortigate-fw-check.html"))
    a = ap.parse_args()
    tpl = (ROOT / "web" / "app.html").read_text(encoding="utf-8")
    engine = (ROOT / "web" / "engine.js").read_text(encoding="utf-8")
    kb = json.dumps(kb_bundle(), ensure_ascii=False, separators=(",", ":"))
    kb = kb.replace("</", "<\\/")  # <script> の中で閉じタグにならないように
    if "</script" in engine.lower():
        raise SystemExit("engine.js に </script が入っている")
    html = tpl.replace("__KB_JSON__", kb).replace("__ENGINE_JS__", engine)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    n = sum(len(v["items"]) for v in json.loads(kb.replace("<\\/", "</"))["versions"].values())
    print(f"{out}（{out.stat().st_size // 1024} KB、{n} 項目）")


if __name__ == "__main__":
    main()
