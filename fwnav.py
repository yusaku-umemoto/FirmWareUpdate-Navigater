#!/usr/bin/env python3
"""FortiGate のファームアップの影響を、知識ベース（kb/）と config を照らし合わせて洗い出す。

標準ライブラリだけで動く。config はこの PC の外に出ない。

使い方:
  python3 fwnav.py check  <config> --to 7.6.8 [--from 7.4.6] [--path 7.4.8,7.6.6] [--model FGT60F] [--out out/]
  python3 fwnav.py flatten <config>          # 平らにした形を表示する
  python3 fwnav.py list --from 7.4.6 --to 7.6.8 [--path ...]   # 対象になる項目の一覧（config なし）

結果は out/ に次の 3 つを書く。
  report.md     人が読む結果（該当・AI 判定・要確認・非該当）
  ai-prompt.md  社内 AI に貼る問い（AI 判定の項目だけ。関係する設定の行だけを添える）
  result.json   機械が読む結果
"""
import argparse
import json
import re
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KB_DIR = HERE / "kb" / "fortios"
SECTION_JA = {
    "special_notice": "Special Notices",
    "upgrade": "Upgrade information",
    "cli": "Changes in CLI",
    "default_behavior": "Changes in default behavior",
    "default_value": "Changes in default values",
    "table_size": "Changes in table size",
    "removed": "機能の削除・非対応化",
    "known_issue": "Known issues（入れる版）",
    "upgrade_bug": "途中の版の不具合（後の版で修正）",
}
ANY = object()  # VDOM を問わない
SEV_ORDER = {"high": 0, "medium": 1, "low": 2}
SECRET_KEYS = re.compile(r"(passw|secret|psk|key$|-key|token|community|passphrase|private)", re.I)


# ---------------------------------------------------------------- config を平らにする

def tokenize(line):
    try:
        return shlex.split(line, posix=True)
    except ValueError:  # 閉じていない引用符（複数行の値）
        return line.split()


def flatten(text):
    """config を (vdom, path, key, value) の並びと、ブロックの一覧にする。

    戻り値: lines [(vdom, path, key, value)], blocks {(vdom, path): vdom}, header {model, version}
    同じ名前のブロックでも VDOM が違えば別のものとして持つ。
    key が None の行はブロックそのもの（中身が空の edit も拾うため）。
    """
    header = {}
    m = re.search(r"#config-version=([A-Z0-9-]+?)-(\d+\.\d+\.\d+)", text)
    if m:
        header = {"model": m.group(1), "version": m.group(2)}
    lines, blocks = [], {}
    stack = []  # 各要素: ("config", name) / ("edit", name)
    vdom = None
    in_multiline = False
    for raw in text.splitlines():
        s = raw.strip()
        if in_multiline:  # 複数行の値（置換メッセージ・証明書など）は読み飛ばす
            if s.endswith('"') and not s.endswith('\\"'):
                in_multiline = False
            continue
        if not s or s.startswith("#"):
            continue
        t = tokenize(s)
        if not t:
            if s.count('"') % 2 == 1:
                in_multiline = True
            continue
        cmd = t[0]
        if cmd == "config":
            name = " ".join(t[1:])
            if not stack and name in ("global", "vdom"):
                stack.append(("scope", name))
                vdom = "global" if name == "global" else None
                continue
            stack.append(("config", name))
            p = path_of(stack)
            blocks.setdefault((vdom, p), vdom)
        elif cmd == "edit":
            name = " ".join(t[1:])
            if len(stack) == 1 and stack[0] == ("scope", "vdom"):
                vdom = name
                stack.append(("vdomedit", name))
                continue
            stack.append(("edit", name))
            p = path_of(stack)
            blocks.setdefault((vdom, p), vdom)
            lines.append((vdom, p, None, None))
        elif cmd in ("next", "end"):
            if stack:
                kind, _ = stack.pop()
                if kind == "scope":
                    vdom = None
                elif kind == "vdomedit":
                    vdom = None
        elif cmd in ("set", "unset", "append"):
            if s.count('"') % 2 == 1 and not s.endswith('"'):
                in_multiline = True
            if len(t) < 2:
                continue
            key = t[1]
            value = " ".join(t[2:])
            p = path_of(stack)
            blocks.setdefault((vdom, p), vdom)
            if cmd == "unset":
                continue
            lines.append((vdom, p, key, value))
    return lines, blocks, header


def path_of(stack):
    out = []
    for kind, name in stack:
        if kind == "config":
            out.append(name)
        elif kind == "edit":
            if out:
                out[-1] = f"{out[-1]}[{name}]"
            else:
                out.append(f"[{name}]")
    return " ".join(out)


def mask(key, value):
    if key and SECRET_KEYS.search(key):
        return "********"
    return value


def fmt_line(vdom, path, key, value):
    pre = f"[{vdom}] " if vdom and vdom != "root" else ""
    if key is None:
        return f"{pre}{path}"
    return f"{pre}{path} | {key} = {mask(key, value)}"


# ---------------------------------------------------------------- 条件を照らし合わせる

_pat_cache = {}


def path_re(pat):
    r = _pat_cache.get(pat)
    if r is None and pat.startswith("="):  # そのままの名前（per_block の中）
        r = _pat_cache[pat] = re.compile(r"^" + re.escape(pat[1:]) + r"$", re.I)
    if r is None:
        esc = re.escape(pat).replace(r"\[\*\]", r"\[[^\]]*\]").replace(r"\*", r".*")
        r = _pat_cache[pat] = re.compile(r"^" + esc + r"$", re.I)
    return r


def values_of(v):
    v = v.strip()
    try:
        return [x for x in shlex.split(v)] or [""]
    except ValueError:
        return [v.strip('"')]


FULL_GLOBAL_KEYS = 100  # show では system global は多くて十数行、show full では 200 行を超える


def has_value(ln):
    """set x '' のような空の値は、設定が無いのと同じに扱う（show full に出る）。"""
    return values_of(ln[3]) != [""]


class Config:
    def __init__(self, text):
        self.lines, self.blocks, self.header = flatten(text)
        # show full-configuration は既定値も全部書くので、「未設定＝既定値」で見る項目が判定できない
        self.full = sum(1 for ln in self.lines if ln[1] == "system global" and ln[2]) >= FULL_GLOBAL_KEYS

    def match_blocks(self, pat, vdom=ANY):
        """(vdom, path) の並び。"""
        r = path_re(pat)
        return [b for b in self.blocks if r.match(b[1]) and (vdom is ANY or b[0] == vdom)]

    def match_lines(self, pat, key=None, vdom=ANY):
        r = path_re(pat)
        out = []
        for ln in self.lines:
            if ln[2] is None or not r.match(ln[1]) or (vdom is not ANY and ln[0] != vdom):
                continue
            if key is None or ln[2].lower() == key.lower():
                out.append(ln)
        return out

    def zone_members(self, name, vdom):
        """system zone[name] の interface（同じ VDOM）。ゾーンでなければ空。"""
        for ln in self.lines:
            if ln[0] == vdom and ln[2] == "interface" and ln[1].lower() == f"system zone[{name}]".lower():
                return ln, values_of(ln[3])
        return None, []


def bind_block(c, block):
    """中の条件の path を、具体的なブロック（(vdom, path)）に置き換える。"""
    if isinstance(c, list):
        return [bind_block(x, block) for x in c]
    if not isinstance(c, dict):
        return c
    if "per_block" in c:
        return c
    out = {k: bind_block(v, block) if k in ("any", "all", "not") else v for k, v in c.items()}
    if any(k in c for k in ("key", "op")) and "path" not in c:
        out["path"] = "=" + block[1]
        out["_vdom"] = block[0]
    return out


def eval_cond(cfg, c):
    """(成り立つか, 根拠の行の並び) を返す。"""
    if "per_block" in c:
        # 同じブロックの中で条件がそろうか。中の条件は path を書かない（そのブロックを見る）
        ev, ok = [], False
        inner = {k: v for k, v in c.items() if k != "per_block"}
        for b in cfg.match_blocks(c["per_block"]):
            o, e = eval_cond(cfg, bind_block(inner, b))
            if o:
                ok = True
                ev += e
        return ok, ev
    if "any" in c:
        ev, ok = [], False
        for x in c["any"]:
            o, e = eval_cond(cfg, x)
            if o:
                ok = True
                ev += e
        return ok, ev
    if "all" in c:
        ev = []
        for x in c["all"]:
            o, e = eval_cond(cfg, x)
            if not o:
                return False, []
            ev += e
        return True, ev
    if "not" in c:
        o, _ = eval_cond(cfg, c["not"])
        return (not o), []
    pat = c["path"]
    key = c.get("key")
    op = c.get("op", "exists")
    vd = c.get("_vdom", ANY)
    if op == "unset":
        if key is None:
            raise ValueError(f"unset には key が要る: {c}")
        bl = cfg.match_blocks(pat, vd)
        if not bl:
            if "[" in pat or "*" in pat or pat.startswith("="):
                return False, []
            return True, [(None, pat, None, None)]
        missing = []
        for bv, b in bl:
            if not any(ln[0] == bv and ln[1] == b and ln[2] and ln[2].lower() == key.lower() for ln in cfg.lines):
                missing.append((bv, b, key, "(未設定＝既定値)"))
        return bool(missing), missing
    if key is None:
        bl = cfg.match_blocks(pat, vd)
        if op == "absent":
            return (not bl), []
        return bool(bl), [(bv, b, None, None) for bv, b in bl]
    ls = [ln for ln in cfg.match_lines(pat, key, vd) if has_value(ln)]
    if op == "exists":
        return bool(ls), ls
    if op == "absent":
        return (not ls), []
    want = c.get("value")
    hits = []
    if op == "intf_regex":
        # インターフェースの名前が正規表現に合うか。ゾーンの名前ならメンバーもたどる
        rx = re.compile(want, re.I)
        for ln in ls:
            for name in values_of(ln[3]):
                if rx.search(name):
                    hits.append(ln)
                    break
                zl, members = cfg.zone_members(name, ln[0])
                if any(rx.search(m) for m in members):
                    hits += [ln, zl]
                    break
        return bool(hits), hits
    for ln in ls:
        vals = [x.lower() for x in values_of(ln[3])]
        if op == "eq":
            ok = str(want).lower() in vals
        elif op == "ne":
            ok = str(want).lower() not in vals
        elif op == "in":
            ok = any(str(w).lower() in vals for w in want)
        elif op == "regex":
            ok = re.search(want, ln[3], re.I) is not None
        else:
            raise ValueError(f"知らない op: {op}")
        if ok:
            hits.append(ln)
    return bool(hits), hits


# ---------------------------------------------------------------- 版の範囲

def vtuple(v):
    return tuple(int(x) for x in v.split("."))


def load_kb():
    kb = {}
    for f in sorted(KB_DIR.glob("[0-9]*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        kb[d["version"]] = d
    models = {}
    mf = KB_DIR / "models.json"
    if mf.exists():
        models = json.loads(mf.read_text(encoding="utf-8")).get("groups", {})
    return kb, models


def versions_in_range(known, frm, to, hops):
    """from より後で to まで、通る版を古い順に返す。

    - 枝（7.4 など）ごとに考える。上げる前の枝は「from < 版 <= その枝で最後に通る版」
    - 途中の枝・上げた先の枝は「x.y.0 <= 版 <= その枝で最後に通る版（先の枝は to）」
    - パスを渡さないときは、上げる前の枝と途中の枝は持っている版を全部入れる（多めに出す）
    """
    f, t = vtuple(frm), vtuple(to)
    hop_t = sorted(vtuple(h) for h in hops)
    last_in_branch = {}
    for h in hop_t + [t]:
        last_in_branch[h[:2]] = max(last_in_branch.get(h[:2], h), h)
    out = []
    for v in sorted(known, key=vtuple):
        vt = vtuple(v)
        if not (f < vt <= t):
            continue
        br = vt[:2]
        if br == t[:2]:
            out.append(v)
        elif hops:
            if br in last_in_branch and vt <= last_in_branch[br]:
                out.append(v)
        else:
            out.append(v)
    return out


def already_passed(it, frm):
    """introduced（その変更が入った版）を、上げる前の版がもう過ぎていれば True。

    introduced は "7.6.3" か、枝ごとの {"7.4": "7.4.6", "7.6": "7.6.1"}。
    枝ごとのときは、上げる前の枝の版と比べる。上げる前の枝が書かれていなければ、
    それより新しい枝の版が書かれているかで決める（古い枝なら未通過）。
    """
    intro = it.get("introduced")
    if not intro:
        return False
    f = vtuple(frm)
    if isinstance(intro, str):
        return f >= vtuple(intro)
    for br, v in intro.items():
        if vtuple(br) == f[:2]:
            return f >= vtuple(v)
    return all(vtuple(br) < f[:2] for br in intro)


def upgrade_bug_hits(it, installed):
    """後の版で直った不具合が、入れる版（途中の版と上げた先）のどれかにあるか。

    fixed_in（直った版）より前で、present_from（無ければその枝の x.y.0）以後の版が当たる。
    """
    fx = vtuple(it["fixed_in"])
    pf = vtuple(it.get("present_from") or f"{fx[0]}.{fx[1]}.0")
    return [v for v in installed if pf <= vtuple(v) < fx]


def select_items(kb, frm, to, hops):
    vers = versions_in_range(kb.keys(), frm, to, hops)
    installed = sorted(set(hops) | {to}, key=vtuple)
    items, seen = [], set()
    # 同じ注意は後の版ほど書き直されているので、新しい版から見て最初のものを残す
    for v in reversed(vers):
        for it in kb[v]["items"]:
            if it["section"] == "upgrade_bug":
                continue
            if it["section"] == "known_issue" and v not in installed:
                continue
            if already_passed(it, frm):
                continue
            # 同じ注意が版をまたいで繰り返し載るので、Bug ID か見出しで一つにまとめる
            dk = it.get("dedupe") or (it.get("bug_id") and f"bug:{it['bug_id']}:{it['section']}") or None
            if dk and dk in seen:
                continue
            if dk:
                seen.add(dk)
            items.append(dict(it, version=v, reviewed=kb[v].get("reviewed", False)))
    # 途中の版の不具合（後の版の Resolved issues に載ったもの）は、版の範囲によらず全ファイルから拾う
    for v in sorted(kb, key=vtuple, reverse=True):
        for it in kb[v]["items"]:
            if it["section"] != "upgrade_bug" or not upgrade_bug_hits(it, installed):
                continue
            dk = it.get("dedupe") or (it.get("bug_id") and f"bug:{it['bug_id']}:upgrade_bug")
            if dk and dk in seen:
                continue
            if dk:
                seen.add(dk)
            items.append(dict(it, version=v, reviewed=kb[v].get("reviewed", False)))
    items.sort(key=lambda x: vtuple(x["version"]))
    return vers, items


def model_ok(it, model, groups):
    m = it.get("models")
    if not m:
        return True
    if not model:
        return None

    def hit(names):
        for n in names:
            for p in groups.get(n, [n]):
                if model.upper().startswith(p.upper()):
                    return True
        return False

    if "only" in m and not hit(m["only"]):
        return False
    if "exclude" in m and hit(m["exclude"]):
        return False
    return True


# ---------------------------------------------------------------- 判定と出力

def judge(cfg, items, model, groups):
    res = []
    for it in items:
        r = {"item": it, "evidence": []}
        mo = model_ok(it, model, groups)
        if mo is False:
            r["result"], r["why"] = "非該当", "機種が対象外"
        else:
            if "when" in it:
                ok, ev = eval_cond(cfg, it["when"])
            else:
                ok, ev = True, []
            r["evidence"] = ev
            if not ok:
                r["result"], r["why"] = "非該当", "条件の設定が無い"
            elif mo is None:
                r["result"], r["why"] = "要確認", "機種が分からない（機種を入れる）"
            elif it["judge"] == "auto":
                r["result"], r["why"] = "該当", "条件の設定がある"
            elif it["judge"] == "ai":
                r["result"], r["why"] = "AI 判定", "設定の中身で決まる"
            else:
                r["result"], r["why"] = "要確認", "人が確かめる項目" if "when" not in it else "関係する設定があり、人が確かめる"
        res.append(r)
    return res


ORDER = ["該当", "AI 判定", "要確認", "非該当"]


def sort_key(r):
    it = r["item"]
    return (ORDER.index(r["result"]), SEV_ORDER.get(it["severity"], 9), vtuple(it["version"]), it["id"])


def ev_lines(ev, limit=8):
    out = [fmt_line(*e) for e in ev[:limit]]
    if len(ev) > limit:
        out.append(f"…ほか {len(ev) - limit} 行")
    return out


def meta_notes(meta):
    """結果の先頭に出す注意（ブラウザ版と同じ文面）。"""
    out = []
    if meta.get("known_issue_missing"):
        out.append(f"★ 上げた先（{meta['to']}）の Known issues は知識ベースに入っていない")
    if meta.get("hop_ki_missing"):
        out.append(f"★ 途中の版（{', '.join(meta['hop_ki_missing'])}）の Known issues は知識ベースに入っていない")
    if meta.get("ki_partial"):
        out.append(f"途中の版（{', '.join(meta['ki_partial'])}）の Known issues は、上げる作業に関わるもの（設定の消失・通信断・管理断など）だけを見ている")
    if meta.get("full_config"):
        out.append("★ show full-configuration の出力に見える。既定値が変わる項目（設定が書かれていない＝既定値で動く）は判定できないので、show の出力を使う")
    if vtuple(meta["from"]) >= vtuple(meta["to"]):
        out.append(f"★ 今の版（{meta['from']}）が上げる先（{meta['to']}）と同じか後なので、見る項目が無い。今の版には上げる前の版を入れる")
    if not meta.get("hops"):
        out.append("★ 途中の版を入れていないので、途中の版の不具合は見ていない（Upgrade Path Tool の途中の版を入れる）")
    return out


def ki_cov(kb, v):
    """その版の Known issues の収録の度合い: True（全部）／"upgrade"（上げる作業に関わるものだけ）／False（無し）。"""
    if v not in kb:
        return False
    return kb[v].get("coverage", {}).get("known_issue", True)


def write_report(res, meta, out):
    L = [f"# ファームアップ影響確認 {meta['from']} → {meta['to']}", ""]
    L.append(f"- 機種: {meta['model'] or '不明'}")
    L.append(f"- 通るパス: {' → '.join([meta['from']] + meta['hops'] + [meta['to']])}")
    L.append(f"- 見たリリースノートの版: {', '.join(meta['versions'])}")
    L.append(f"- 知識ベースで人が確かめていない版: {', '.join(meta['unreviewed']) or 'なし'}")
    for line in meta_notes(meta):
        L.append(f"- {line}")
    missing = meta.get("kb_missing") or []
    if missing:
        L.append(f"- ★ 知識ベースが無い版: {', '.join(missing)}（この版の変更は見ていない）")
    L.append("")
    cnt = {k: sum(1 for r in res if r["result"] == k) for k in ORDER}
    L.append("| 該当 | AI 判定 | 要確認 | 非該当 |")
    L.append("|---|---|---|---|")
    L.append(f"| {cnt['該当']} | {cnt['AI 判定']} | {cnt['要確認']} | {cnt['非該当']} |")
    for k in ORDER[:3]:
        rs = [r for r in res if r["result"] == k]
        if not rs:
            continue
        L += ["", f"## {k}（{len(rs)}）", ""]
        for r in rs:
            it = r["item"]
            L.append(f"### [{it['severity']}] {it['title']}")
            L.append("")
            L.append(f"- 版: {it['version']} ／ {SECTION_JA.get(it['section'], it['section'])}" + (f" ／ Bug ID {it['bug_id']}" if it.get("bug_id") else "") + f" ／ `{it['id']}`")
            L.append(f"- 内容: {it['summary']}")
            L.append(f"- 影響: {it['impact']}")
            L.append(f"- 対処: {it['action']}")
            if r["evidence"]:
                L.append("- 根拠の設定:")
                L.append("  ```")
                L += ["  " + x for x in ev_lines(r["evidence"])]
                L.append("  ```")
            L.append(f"- 原文: {it['url']}")
            L.append("")
    rs = [r for r in res if r["result"] == "非該当"]
    if rs:
        L += ["", f"## 非該当（{len(rs)}）", "", "| 版 | 重さ | 見出し | 理由 |", "|---|---|---|---|"]
        for r in rs:
            it = r["item"]
            L.append(f"| {it['version']} | {it['severity']} | {it['title']} | {r['why']} |")
    (out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def context_lines(cfg, pats, limit=60, first_blocks=()):
    """ai.context のパスに前方一致する設定の行（`system interface` は `system interface[port1] ...` も含む）。

    first_blocks（条件に当たったブロック）の行を先に並べる。
    """
    seen, out = set(), []
    first = set(first_blocks)
    for ln in cfg.lines:
        if ln[2] is not None and ln[1] in first and ln not in seen:
            seen.add(ln)
            out.append(ln)
    for p in pats:
        res = [path_re(x) for x in (p, p + " *", p + "[*]", p + "[*] *")]
        for ln in cfg.lines:
            if ln[2] is None:
                continue
            if any(r.match(ln[1]) for r in res):
                if ln not in seen:
                    seen.add(ln)
                    out.append(ln)
    if len(out) > limit:
        return [fmt_line(*x) for x in out[:limit]] + [f"…ほか {len(out) - limit} 行（省略）"]
    return [fmt_line(*x) for x in out]


def write_ai_prompt(cfg, res, meta, out):
    rs = [r for r in res if r["result"] == "AI 判定"]
    L = [
        f"# FortiGate ファームアップ影響の判定（{meta['from']} → {meta['to']}）",
        "",
        "あなたはネットワーク機器の設定を確認する担当です。",
        "下の各問いについて、添えた設定の行だけを根拠に答えてください。",
        "",
        "ルール:",
        "- 答えは YES / NO / 不明 のどれか一つ",
        "- 根拠にした設定の行をそのまま引用する。行が無いときは「該当行なし」と書く",
        "- 添えた行に無いことは推測しない。決められないときは「不明」",
        "- 設定の行は `パス | 項目 = 値` の形。パスの `[名前]` は edit の名前",
        "",
        "答えの形（問いごとに 1 行。表にする）:",
        "",
        "| ID | 答え | 根拠の行 | ひとこと |",
        "|---|---|---|---|",
        "",
        "---",
    ]
    for i, r in enumerate(rs, 1):
        it = r["item"]
        ai = it.get("ai", {})
        L += ["", f"## 問い {i}：{it['id']}", ""]
        L.append(f"背景: {it['summary']}")
        L.append("")
        L.append(f"問い: {ai.get('question', it['title'])}")
        L.append("")
        L.append("設定の行:")
        L.append("```")
        hit_blocks = [e[1] for e in r["evidence"]]
        ctx = context_lines(cfg, ai.get("context", []), first_blocks=hit_blocks) if ai.get("context") else ev_lines(r["evidence"], 60)
        L += ctx or ["（関係する設定の行なし）"]
        L.append("```")
    if not rs:
        L += ["", "（AI に聞く項目はありません）"]
    (out / "ai-prompt.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def write_json(res, meta, out):
    data = {"meta": meta, "results": [
        {"id": r["item"]["id"], "version": r["item"]["version"], "section": r["item"]["section"],
         "severity": r["item"]["severity"], "title": r["item"]["title"], "result": r["result"],
         "why": r["why"], "evidence": [fmt_line(*e) for e in r["evidence"][:20]], "url": r["item"]["url"]}
        for r in res]}
    (out / "result.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- コマンド

def cmd_flatten(a):
    cfg = Config(Path(a.config).read_text(encoding="utf-8", errors="replace"))
    for ln in cfg.lines:
        print(fmt_line(*ln))


def range_meta(a, kb, header=None):
    header = header or {}
    frm = a.frm or header.get("version")
    if not frm:
        sys.exit("--from が要る（config の先頭に #config-version が無いため）")
    hops = [h.strip() for h in (a.path or "").split(",") if h.strip()]
    return frm, hops


def expected_versions(frm, to, hops):
    """知識ベースに無い版を知らせるために、本来通るはずの版の候補を作る（パッチ番号を 0 から数える）。"""
    f, t = vtuple(frm), vtuple(to)
    out = []
    lasts = {}
    for h in [vtuple(x) for x in hops] + [t]:
        lasts[h[:2]] = max(lasts.get(h[:2], h), h)
    for br, last in sorted(lasts.items()):
        start = f[2] + 1 if br == f[:2] else 0
        for p in range(start, last[2] + 1):
            out.append(f"{br[0]}.{br[1]}.{p}")
    return out


def cmd_check(a):
    kb, groups = load_kb()
    cfg = Config(Path(a.config).read_text(encoding="utf-8", errors="replace"))
    frm, hops = range_meta(a, kb, cfg.header)
    model = a.model or cfg.header.get("model")
    vers, items = select_items(kb, frm, a.to, hops)
    res = sorted(judge(cfg, items, model, groups), key=sort_key)
    exp = expected_versions(frm, a.to, hops) if hops or vtuple(frm)[:2] == vtuple(a.to)[:2] else []
    meta = {"from": frm, "to": a.to, "hops": hops, "model": model, "versions": vers,
            "unreviewed": [v for v in vers if not kb[v].get("reviewed")],
            "kb_missing": [v for v in exp if v not in kb], "config": Path(a.config).name,
            "known_issue_missing": a.to in kb and ki_cov(kb, a.to) is False,
            "hop_ki_missing": [h for h in hops if ki_cov(kb, h) is False],
            "ki_partial": [h for h in hops if ki_cov(kb, h) == "upgrade"], "full_config": cfg.full}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    write_report(res, meta, out)
    write_ai_prompt(cfg, res, meta, out)
    write_json(res, meta, out)
    cnt = {k: sum(1 for r in res if r["result"] == k) for k in ORDER}
    print(f"{meta['config']}: {frm} → {a.to}（{model or '機種不明'}）  " + "  ".join(f"{k} {v}" for k, v in cnt.items()))
    print(f"  → {out / 'report.md'} ／ {out / 'ai-prompt.md'}")


def cmd_list(a):
    kb, _ = load_kb()
    frm, hops = range_meta(a, kb)
    vers, items = select_items(kb, frm, a.to, hops)
    print(f"版: {', '.join(vers)}  項目: {len(items)}")
    for it in sorted(items, key=lambda x: (SEV_ORDER.get(x["severity"], 9), vtuple(x["version"]))):
        print(f"{it['version']:>7} {it['severity']:<6} {it['judge']:<6} {it['section']:<16} {it['title']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("config")
    c.add_argument("--from", dest="frm")
    c.add_argument("--to", required=True)
    c.add_argument("--path", help="Upgrade Path Tool の途中の版（カンマ区切り）。例 7.4.8,7.6.6")
    c.add_argument("--model")
    c.add_argument("--out", default="out")
    f = sub.add_parser("flatten")
    f.add_argument("config")
    li = sub.add_parser("list")
    li.add_argument("--from", dest="frm", required=True)
    li.add_argument("--to", required=True)
    li.add_argument("--path")
    a = ap.parse_args()
    {"check": cmd_check, "flatten": cmd_flatten, "list": cmd_list}[a.cmd](a)


if __name__ == "__main__":
    main()
