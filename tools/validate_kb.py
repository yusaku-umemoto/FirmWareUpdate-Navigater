"""知識ベース（kb/fortios/*.json）の形を確かめる。

- 必須のキー・値の種類・id の重複
- 条件（when）の形と op の名前
- 条件を空の config と、渡した config に当てて、エラーにならないこと

使い方: python3 tools/validate_kb.py [kb/fortios/7.6.0.json ...] [--config 確かめに使う config ...]
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import fwnav  # noqa: E402

SECTIONS = set(fwnav.SECTION_JA)
JUDGES = {"auto", "ai", "manual"}
SEVS = {"high", "medium", "low"}
OPS = {"exists", "absent", "eq", "ne", "in", "regex", "unset"}
REQ = ["id", "section", "title", "summary", "severity", "judge", "impact", "action", "url"]


def check_cond(c, where, errs, inside_block=False):
    if not isinstance(c, dict):
        errs.append(f"{where}: 条件が dict でない")
        return
    if "per_block" in c:
        rest = {k: v for k, v in c.items() if k != "per_block"}
        if not rest:
            errs.append(f"{where}: per_block の中身が無い")
        check_cond(rest, where + ".per_block", errs, True)
        return
    for k in ("any", "all"):
        if k in c:
            if not isinstance(c[k], list) or not c[k]:
                errs.append(f"{where}: {k} は空でない配列")
                return
            for i, x in enumerate(c[k]):
                check_cond(x, f"{where}.{k}[{i}]", errs, inside_block)
            return
    if "not" in c:
        check_cond(c["not"], where + ".not", errs, inside_block)
        return
    if inside_block:
        if "path" in c:
            errs.append(f"{where}: per_block の中では path を書かない")
    elif "path" not in c:
        errs.append(f"{where}: path が無い")
    op = c.get("op", "exists")
    if op not in OPS:
        errs.append(f"{where}: 知らない op {op}")
    if op in ("eq", "ne", "regex") and "value" not in c:
        errs.append(f"{where}: {op} には value が要る")
    if op == "in" and not isinstance(c.get("value"), list):
        errs.append(f"{where}: in の value は配列")
    if op == "unset" and "key" not in c:
        errs.append(f"{where}: unset には key が要る")
    extra = set(c) - {"path", "key", "op", "value"}
    if extra:
        errs.append(f"{where}: 知らないキー {sorted(extra)}")


def check_file(f, cfgs, groups):
    errs = []
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"{f.name}: JSON として読めない: {e}"]
    for k in ("product", "version", "source", "items"):
        if k not in d:
            errs.append(f"{f.name}: {k} が無い")
    if f.stem != d.get("version"):
        errs.append(f"{f.name}: ファイル名と version が違う")
    ids = set()
    for i, it in enumerate(d.get("items", [])):
        w = f"{f.name}#{it.get('id', i)}"
        for k in REQ:
            if not it.get(k):
                errs.append(f"{w}: {k} が無い")
        if it.get("id") in ids:
            errs.append(f"{w}: id が重複")
        ids.add(it.get("id"))
        if not str(it.get("id", "")).startswith(d.get("version", "?") + "-"):
            errs.append(f"{w}: id は版で始める")
        if it.get("section") not in SECTIONS:
            errs.append(f"{w}: section が違う {it.get('section')}")
        if it.get("judge") not in JUDGES:
            errs.append(f"{w}: judge が違う")
        if it.get("severity") not in SEVS:
            errs.append(f"{w}: severity が違う")
        if it.get("judge") == "ai":
            if not (it.get("ai") or {}).get("question"):
                errs.append(f"{w}: judge ai には ai.question が要る")
            if "when" not in it:
                errs.append(f"{w}: judge ai には when（前提の条件）が要る")
        if it.get("judge") == "auto" and "when" not in it and not it.get("models"):
            errs.append(f"{w}: judge auto で when も models も無いと全機種で必ず「該当」になる（manual にする）")
        m = it.get("models")
        if m:
            for n in m.get("only", []) + m.get("exclude", []):
                if n.islower() and n not in groups:
                    errs.append(f"{w}: models の群 {n} が models.json に無い")
        intro = it.get("introduced")
        if intro is not None and not isinstance(intro, (str, dict)):
            errs.append(f"{w}: introduced は文字列か dict")
        if "when" in it:
            check_cond(it["when"], w + ".when", errs)
            for cf in cfgs:
                try:
                    fwnav.eval_cond(cf, it["when"])
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{w}: 条件を当てるとエラー: {e}")
                    break
    return errs


def warn_introduced_mismatch():
    """同じ dedupe の項目で introduced が版ごとに違えば知らせる（出るのは新しい版の項目なので、古い版の書き方が効かない）。"""
    seen = {}
    for f in sorted(fwnav.KB_DIR.glob("[0-9]*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for it in d["items"]:
            k = it.get("dedupe")
            if k:
                seen.setdefault(k, set()).add(json.dumps(it.get("introduced"), sort_keys=True))
    for k, v in seen.items():
        if len(v) > 1:
            print(f"注意: {k} の introduced が版ごとに違う: {sorted(v)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--config", nargs="*", default=[])
    a = ap.parse_args()
    files = [Path(x) for x in a.files] or sorted(fwnav.KB_DIR.glob("[0-9]*.json"))
    cfgs = [fwnav.Config("")] + [fwnav.Config(Path(c).read_text(encoding="utf-8", errors="replace")) for c in a.config]
    _, groups = fwnav.load_kb()
    bad = 0
    warn_introduced_mismatch()
    for f in files:
        errs = check_file(f, cfgs, groups)
        if errs:
            bad += 1
            print("\n".join(errs))
        else:
            n = len(json.loads(f.read_text(encoding="utf-8"))["items"])
            print(f"{f.name}: OK（{n} 項目）")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
