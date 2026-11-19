"""FortiOS のリリースノートを docs.fortinet.com から取り、本文だけをテキストにして手元に置く。

- robots.txt の Crawl-delay（2 秒）を守る
- 置き場は cache/（.gitignore 済み）。原文なので公開しない
- 知識ベース（kb/）を作るときの読み物にする

使い方: python3 tools/fetch_release_notes.py 7.6.0 7.6.1 ... [--out cache/release-notes]
"""
import argparse
import html
import re
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://docs.fortinet.com"
UA = "Mozilla/5.0 (compatible; fw-release-note-research)"
DELAY = 2.0
_last = 0.0


def get(path):
    global _last
    wait = DELAY - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.geturl(), r.read().decode("utf-8", "replace")


class ProseText(HTMLParser):
    """<div class="prose ..."> の中だけを、見出し・表・コードを残したテキストにする。"""

    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "tr", "pre", "br", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0  # prose div の中の div の深さ
        self.out = []
        self.cell = None
        self.row = None
        self.pre = 0
        self.tables = 0  # 表の入れ子の深さ。2 以上（セルの中の表）は外のセルの文字にする

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.depth == 0:
            if tag == "div" and "prose" in (a.get("class") or "").split():
                self.depth = 1
            return
        if tag == "div":
            self.depth += 1
        if tag in ("h1", "h2", "h3", "h4", "h5"):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "table":
            self.tables += 1
            if self.tables > 1:
                self._emit("\n")
        elif self.tables > 1 and tag in ("tr", "td", "th"):
            self._emit(" " if tag != "tr" else "\n")
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = []
        elif tag == "pre":
            self.pre += 1
            self._emit("\n```\n")
        elif tag == "li":
            self._emit("\n- ")
        elif tag in self.BLOCK:
            self._emit("\n")

    def handle_endtag(self, tag):
        if self.depth == 0:
            return
        if tag == "div":
            self.depth -= 1
            return
        if tag == "table":
            self.tables = max(0, self.tables - 1)
            return
        if self.tables > 1 and tag in ("tr", "td", "th"):
            return
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            c = "".join(self.cell)
            parts = re.split(r"(\n```\n.*?\n```\n)", c, flags=re.S)
            c = "".join(x if x.startswith("\n```") else re.sub(r"\s+", " ", x) for x in parts)
            self.row.append(c.strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.out.append("\n| " + " | ".join(self.row) + " |")
            self.row = None
        elif tag == "pre":
            self.pre -= 1
            self._emit("\n```\n")
        elif tag in ("p", "li") and self.cell is not None:
            self.cell.append("\n")

    def _emit(self, s):
        if self.cell is not None:
            self.cell.append(s)
        else:
            self.out.append(s)

    def handle_data(self, data):
        if self.depth == 0:
            return
        if not self.pre:
            data = re.sub(r"[ \t\r\n]+", " ", data)
        self._emit(data)

    def text(self):
        t = "".join(self.out)
        t = re.sub(r"[ \t]+\n", "\n", t)
        return re.sub(r"\n{3,}", "\n\n", t).strip() + "\n"


def page_text(page):
    p = ProseText()
    p.feed(page)
    return p.text()


def fetch_version(ver, out, only=None, force=False):
    root = f"/document/fortigate/{ver}/fortios-release-notes"
    try:
        url, top = get(root)
    except urllib.error.HTTPError as e:
        print(f"{ver}: {e.code}", file=sys.stderr)
        return False
    if f"/{ver}/" not in url:
        print(f"{ver}: 無い（{url} に飛ばされた）", file=sys.stderr)
        return False
    links = sorted(set(re.findall(rf'href="({re.escape(root)}/\d+/[a-z0-9-]+)"', top)))
    d = out / ver
    d.mkdir(parents=True, exist_ok=True)
    for link in links:
        slug = link.rsplit("/", 1)[1]
        f = d / f"{slug}.md"
        if only and not re.search(only, slug):
            continue
        if f.exists() and not force:
            continue
        try:
            _, page = get(link)
        except urllib.error.HTTPError as e:
            print(f"  {slug}: {e.code}", file=sys.stderr)
            continue
        f.write_text(f"<!-- source: {BASE}{link} -->\n" + page_text(page), encoding="utf-8")
        print(f"  {ver}/{slug}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("versions", nargs="+")
    ap.add_argument("--only", help="ページの名前がこの正規表現に合うものだけ取る")
    ap.add_argument("--force", action="store_true", help="取ってあるページも取り直す")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "cache/release-notes"))
    a = ap.parse_args()
    out = Path(a.out)
    for v in a.versions:
        print(v)
        fetch_version(v, out, a.only, a.force)


if __name__ == "__main__":
    main()
