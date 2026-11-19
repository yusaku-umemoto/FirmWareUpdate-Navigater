"""リリースノートの全項目と知識ベースを突き合わせ、「入れた／外した（理由）」の一覧を作る。

- 原文（cache/release-notes/）の Special Notices などの注意のページ、Changes in の表、New features の表、
  上げた先の Known issues を全部拾う
- 版をまたいで同じものは、Bug ID かページ番号（URL の数字。ページ名が変わっても同じ）でまとめる
- 知識ベースのどれかの項目が持っていれば「入れた」。無ければ kb/fortios/excluded/*.json の理由を出す

一覧には原文の書き出しが入るので、out/（公開しない）に書く。理由の表（excluded/*.json）は ID と日本語の理由だけなので公開してよい。

使い方: python3 tools/coverage.py --from 7.4.6 --to 7.6.8 --path 7.4.8,7.6.6 [--out out/coverage]
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import fwnav  # noqa: E402

CACHE = ROOT / "cache" / "release-notes"
EXCLUDED_DIR = ROOT / "kb" / "fortios" / "excluded"

# 注意のページではないページ（表や一覧のページ・案内だけのページ）
NOT_NOTICE = re.compile(
    r"^(change-log|introduction-and-supported-models|special-notices|upgrade-information|changes-in-.*|"
    r"new-features-or-enhancements|known-issues|resolved-issues|product-integration-and-support|language-support|"
    r"firmware-image-checksums|limitations|virtualization-environments|built-in-.*-engine|agentless-vpn-support|"
    r"ssl-vpn-support|fortiextender-modem-firmware-compatibility|.*-support)$")
TABLES = {
    "changes-in-cli": "cli",
    "changes-in-default-behavior": "default_behavior",
    "changes-in-default-values": "default_value",
    "changes-in-table-size": "table_size",
    "changes-in-gui-behavior": "gui",
    "new-features-or-enhancements": "new_feature",
}
SECTION_JA = dict(fwnav.SECTION_JA, gui="Changes in GUI behavior", new_feature="New features", notice="注意のページ",
                  resolved_upgrade="Resolved issues（途中の版。アップグレードに触れる行）")
ROW = re.compile(r"^\| (\d{5,7}(?:\s*[,/、]\s*\d{5,7})*) \| (.*)")  # ID が複数並ぶ行もある
UPGRADE_WORD = re.compile(r"upgrad", re.I)


def source_url(text):
    m = re.match(r"<!-- source: (\S+) -->", text)
    return m.group(1) if m else ""


def page_id(url):
    m = re.search(r"/(\d+)/[a-z0-9-]+$", url)
    return m.group(1) if m else ""


def excerpt(s, n=110):
    s = re.sub(r"`+|\s+", " ", s).strip(" |")
    return s if len(s) <= n else s[:n] + "…"


def resolved_versions(hops, to):
    """途中の版の不具合が載りうる Resolved issues の版（途中の版より後で、同じ枝で、上げた先まで）。"""
    out = set()
    for d in CACHE.iterdir() if CACHE.is_dir() else []:
        if not re.match(r"^\d+\.\d+\.\d+$", d.name):
            continue
        v = fwnav.vtuple(d.name)
        if v > fwnav.vtuple(to):
            continue
        if any(fwnav.vtuple(h)[:2] == v[:2] and fwnav.vtuple(h) < v for h in hops):
            out.add(d.name)
    return sorted(out, key=fwnav.vtuple)


def add_row(out, ids, v, sec, cat, text, url):
    key = f"bug:{ids[0]}"
    if key not in out:
        out[key] = {"key": key, "version": v, "section": sec, "ident": ", ".join(ids), "ids": ids,
                    "category": cat, "text": excerpt(text), "url": url, "versions": [v]}
    elif v not in out[key]["versions"]:
        out[key]["versions"].append(v)


def raw_entries(vers, to, hops=()):
    """原文の項目を (key, version, section, ident, category, text, url) で返す。key でまとめる。"""
    out = {}
    installed = set(hops) | {to}
    for v in resolved_versions(hops, to):
        f = CACHE / v / "resolved-issues.md"
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        cat = ""
        for line in text.splitlines():
            if line.startswith("#"):
                cat = line.lstrip("# ").strip()
            m = ROW.match(line)
            if m and UPGRADE_WORD.search(m.group(2)):
                add_row(out, re.findall(r"\d{5,7}", m.group(1)), v, "resolved_upgrade", cat, m.group(2), source_url(text))
    for v in sorted(set(vers) | installed, key=fwnav.vtuple):
        d = CACHE / v
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            slug = f.stem
            text = f.read_text(encoding="utf-8")
            url = source_url(text)
            if slug in TABLES or slug == "known-issues":
                if slug == "known-issues" and v not in installed:
                    continue
                if slug in TABLES and v not in vers:
                    continue
                sec = "known_issue" if slug == "known-issues" else TABLES[slug]
                cat = ""
                for line in text.splitlines():
                    if line.startswith("#"):
                        cat = line.lstrip("# ").strip()
                    m = ROW.match(line)
                    if m:
                        add_row(out, re.findall(r"\d{5,7}", m.group(1)), v, sec, cat, m.group(2), url)
            elif not NOT_NOTICE.match(slug) and v in vers:
                pid = page_id(url)
                key = f"page:{pid or slug}"
                title = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("#")), slug)
                if key not in out:
                    out[key] = {"key": key, "version": v, "section": "notice", "ident": slug, "category": "",
                                "text": title, "url": url, "versions": [v]}
                else:
                    out[key]["versions"].append(v)
                    out[key]["version"] = out[key]["version"]  # 最初に載った版のまま
    return list(out.values())


def kb_index(kb):
    bugs, pages, slugs, where = set(), set(), set(), {}
    for v, d in kb.items():
        for it in d["items"]:
            ids = set(re.findall(r"\d{5,7}", it["id"]))
            if it.get("bug_id"):
                ids.add(str(it["bug_id"]))
            for b in ids:
                bugs.add(b)
                where.setdefault(f"bug:{b}", it["id"])
            pid = page_id(it.get("url", ""))
            if pid and it["section"] not in ("cli", "default_behavior", "default_value", "table_size", "known_issue"):
                pages.add(pid)
                where.setdefault(f"page:{pid}", it["id"])
            dk = it.get("dedupe", "")
            if dk.startswith("sn:"):
                slugs.add(dk[3:])
                where.setdefault(f"slug:{dk[3:]}", it["id"])
    return bugs, pages, slugs, where


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--path", default="")
    ap.add_argument("--out", default=str(ROOT / "out" / "coverage"))
    a = ap.parse_args()
    hops = [h.strip() for h in a.path.split(",") if h.strip()]
    kb, _ = fwnav.load_kb()
    known = sorted({p.name for p in CACHE.iterdir() if p.is_dir()} | set(kb), key=fwnav.vtuple)
    vers = fwnav.versions_in_range(known, a.frm, a.to, hops)
    entries = raw_entries(vers, a.to, hops)
    bugs, pages, slugs, where = kb_index(kb)
    reasons = {}
    for f in sorted(EXCLUDED_DIR.glob("*.json")) if EXCLUDED_DIR.is_dir() else []:
        reasons.update(json.loads(f.read_text(encoding="utf-8")).get("reasons", {}))
    rows = []
    for e in entries:
        k = e["key"]
        hit = None
        if k.startswith("bug:") and any(i in bugs for i in e["ids"]):
            hit = next(where[f"bug:{i}"] for i in e["ids"] if i in bugs)
        elif k.startswith("page:") and (k[5:] in pages or e["ident"] in slugs):
            hit = where.get(k) or where.get(f"slug:{e['ident']}")
        e["status"] = "入れた" if hit else "外した"
        e["kb_id"] = hit or ""
        e["reason"] = "" if hit else reasons.get(k, "")
        rows.append(e)
    order = {"notice": 0, "cli": 1, "default_behavior": 2, "default_value": 3, "table_size": 4, "gui": 5, "known_issue": 6,
             "resolved_upgrade": 7, "new_feature": 8}
    rows.sort(key=lambda r: (order.get(r["section"], 9), r["status"] != "外した", fwnav.vtuple(r["version"]), r["ident"]))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    name = f"coverage-{a.frm}-{a.to}"
    with (out / f"{name}.csv").open("w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["区分", "状態", "知識ベースの項目", "外した理由", "Bug ID・ページ", "載っている版", "分類", "原文の書き出し", "原文"])
        for r in rows:
            w.writerow([SECTION_JA.get(r["section"], r["section"]), r["status"], r["kb_id"], r["reason"], r["ident"],
                        " ".join(r["versions"]), r["category"], r["text"], r["url"]])
    # まとめ
    L = [f"# 知識ベースの網羅状況 {a.frm} → {a.to}", "",
         f"- 通るパス: {' → '.join([a.frm] + hops + [a.to])}",
         f"- 見た版: {', '.join(vers)}",
         "- 版をまたいで同じ項目は 1 件に数える（Bug ID・ページ番号）",
         "- Known issues は入れる版（途中の版と上げた先）。Resolved issues は途中の版より後の版のうち、アップグレードに触れる行", "",
         "| 区分 | 原文の件数（重複なし） | 入れた | 外した | うち理由なし |", "|---|---|---|---|---|"]
    tot = [0, 0, 0, 0]
    for sec in order:
        rs = [r for r in rows if r["section"] == sec]
        if not rs:
            continue
        c = [len(rs), sum(r["status"] == "入れた" for r in rs), sum(r["status"] == "外した" for r in rs),
             sum(r["status"] == "外した" and not r["reason"] for r in rs)]
        tot = [x + y for x, y in zip(tot, c)]
        L.append(f"| {SECTION_JA[sec]} | {c[0]} | {c[1]} | {c[2]} | {c[3]} |")
    L.append(f"| **合計** | **{tot[0]}** | **{tot[1]}** | **{tot[2]}** | **{tot[3]}** |")
    raw_sn = 0
    for v in vers:
        f = CACHE / v / "special-notices.md"
        if f.exists():
            raw_sn += f.read_text(encoding="utf-8").count("\n-\n")
    L += ["", f"参考: Special Notices を版ごとに数えると延べ {raw_sn} 件（同じ注意が版ごとに繰り返し載るため）。", ""]
    for sec in order:
        rs = [r for r in rows if r["section"] == sec and r["status"] == "外した"]
        if not rs:
            continue
        L += [f"## 外した：{SECTION_JA[sec]}（{len(rs)}）", "", "| 版 | Bug ID・ページ | 原文の書き出し | 外した理由 |", "|---|---|---|---|"]
        for r in rs:
            t = r["text"].replace("|", "／")
            L.append(f"| {r['version']} | {r['ident']} | {t} | {r['reason'] or '★ 理由なし'} |")
        L.append("")
    (out / f"{name}.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:len(L)]) if False else "\n".join(L[6:6 + 12]))
    print(f"→ {out / (name + '.md')} ／ {out / (name + '.csv')}")


if __name__ == "__main__":
    main()
