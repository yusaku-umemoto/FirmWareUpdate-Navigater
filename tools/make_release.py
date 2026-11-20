"""配る ZIP を作る（GitHub のリリースに載せる用）。

out/release/fortigate-fw-check-<版>.zip に、ブラウザ版の HTML・使い方（README.txt）・変更の記録・ライセンスを入れる。
版数は version.json。先に tools/build_html.py で HTML を作り直しておく（版数が合わなければ止まる）。

使い方:
  python3 tools/make_release.py            # ZIP を作るだけ
  python3 tools/make_release.py --publish  # ZIP を作り、gh で GitHub のリリース v<版> を作って載せる
"""
import argparse
import json
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "dist" / "fortigate-fw-check.html"

HOWTO = """FortiGate ファームアップ影響チェック {version}（{date}）

使い方
1. fortigate-fw-check.html をブラウザ（Edge・Chrome）で開く。インストールも Python も要らない
2. config（show の出力）を画面に落とすか貼り付ける
3. 上げる先の版と、Fortinet の Upgrade Path Tool に出る途中の版を入れて「判定する」
   （途中の版なしで直接上げるなら「なし」）
4. 結果を見る。report.md・CSV・result.json を保存できる

config はブラウザの中だけで処理し、どこにも送らない。
通信は、開いたときに GitHub の version.json（版数だけ）を読んで最新版かを確かめる 1 回だけ。
古い版なら画面の上に警告が出るので、最新のリリースを取り直す。

最新版: https://github.com/yusaku-umemoto/FirmWareUpdate-Navigater/releases/latest
変更の記録: CHANGELOG.md
"""


def changelog_line(version):
    for line in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| (\S+) \| (\S+) \| (.+) \|$", line)
        if m and m.group(1) == version:
            return m.group(3)
    raise SystemExit(f"CHANGELOG.md に {version} の行が無い")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true", help="gh で GitHub のリリースを作って ZIP を載せる")
    a = ap.parse_args()
    ver = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
    v, date = ver["version"], ver["date"]
    html = HTML.read_text(encoding="utf-8")
    if f'"version": "{v}"' not in html:
        raise SystemExit(f"dist の HTML が版 {v} ではない。先に python3 tools/build_html.py を動かす")
    notes = changelog_line(v)
    out = ROOT / "out" / "release"
    out.mkdir(parents=True, exist_ok=True)
    zpath = out / f"fortigate-fw-check-{v}.zip"
    top = f"fortigate-fw-check-{v}"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(HTML, f"{top}/fortigate-fw-check.html")
        # 古い展開ツールでも名前が化けないよう英字の名前にし、メモ帳で読めるよう BOM つき・CRLF にする
        z.writestr(f"{top}/README.txt", "\ufeff" + HOWTO.format(version=v, date=date).replace("\n", "\r\n"))
        z.write(ROOT / "CHANGELOG.md", f"{top}/CHANGELOG.md")
        z.write(ROOT / "LICENSE", f"{top}/LICENSE")
    print(f"{zpath}（{zpath.stat().st_size // 1024} KB）")
    if a.publish:
        body = (f"{notes}\n\n"
                "**使い方**：ZIP を展開して `fortigate-fw-check.html` をブラウザで開く。config はブラウザの外に出ません。\n\n"
                "通信は、開いたときに GitHub の `version.json`（版数だけ）を読んで最新版かを確かめる 1 回だけです。")
        subprocess.run(["gh", "release", "create", f"v{v}", str(zpath), "--title", f"{v}（{date}）", "--notes", body,
                        "--target", "main"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
