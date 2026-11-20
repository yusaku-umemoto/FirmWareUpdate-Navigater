# FirmWareUpdate-Navigator（FortiGate 版・試作）

FortiGate のファームアップの前に、**リリースノートのどの項目が自分の機器に効くか**を洗い出す道具。

- リリースノートの項目を「どういう設定があると影響するか」の形にした**知識ベース**（`kb/`）を、前もって社外で作っておく（中身は公開のリリースノートだけ）
- 社内で、知識ベースと config を**機械的に**照らし合わせる（`fwnav.py`。標準ライブラリだけ・通信しない）
- 機械で決めきれない項目だけを、**YES/NO で答えられる問い**にして社内 AI に渡す

config は社外に出ない。

## 使い方（ブラウザ版・Python 不要）

**`dist/fortigate-fw-check.html` を 1 つ配るだけ**。ブラウザ（Edge・Chrome）でファイルを開けば動く。インストール・Python・ネット接続は要らず、config はブラウザの外に出ない（通信しない作り）。

1. config のファイルを画面に落とす（または貼り付ける）。機種と版は config の先頭から自動で入る
2. 上げる先の版と、Upgrade Path Tool に出る途中の版を入れて「判定する」。途中の版なしで直接上げる（例 7.4.13 → 7.6.8）なら「なし」と入れる
3. 結果を見る。`report.md`・CSV（Excel 用）・`result.json` を保存できる
4. 「社内 AI に聞く」の文をコピーして社内 AI に貼る。返ってきた答えの表を右の欄に貼って「反映」すると、AI 判定の項目が 該当（YES）／非該当（NO）／要確認（不明）に変わる

試しに使うなら `samples/sample-60f-7.4.6.conf`（危ない設定をわざと入れた架空の config）を落とす。

知識ベースを直したら、作る側（Python のある PC）で組み立て直して配り直す。

```bash
python3 tools/build_html.py
```

## 使い方（コマンド版・Python があるとき）

```bash
python3 fwnav.py check running-config.txt --from 7.4.6 --to 7.6.8 --path 7.4.8,7.6.6
```

- `--path` は Fortinet の [Upgrade Path Tool](https://docs.fortinet.com/upgrade-tool/fortigate) が出す途中の版（機種と今の版で変わる）。**必ず入れる**。渡さないと、上げる前の枝の版を全部見る（多めに出る）うえ、途中の版の不具合を見ない。途中の版なしで直接上げるなら `--path direct`
- `--from`・`--model` は config の先頭（`#config-version=FGT60F-7.4.6-…`）から読む。無ければ渡す
- config は `show`（`show full-configuration` でなくてよい）。既定値が変わった項目は「設定が書かれていない＝既定値で動いている」として判定する

`out/` に次の 3 つができる。

| ファイル | 中身 |
|---|---|
| `report.md` | 結果。**該当**（設定の行が根拠）・**AI 判定**・**要確認**（人が見る）・**非該当**（理由つき） |
| `ai-prompt.md` | 社内 AI にそのまま貼る文。AI 判定の項目だけを、関係する設定の行だけ添えて並べる。答えは表（ID・YES/NO/不明・根拠の行） |
| `result.json` | 機械が読む形 |

ほかのコマンド:

```bash
python3 fwnav.py list --from 7.4.6 --to 7.6.8 --path 7.4.8,7.6.6   # 対象の項目の一覧（config なし）
python3 fwnav.py flatten running-config.txt                          # 平らにした形を見る
```

## 版の範囲の決め方

- 上げる前の枝（7.4）：`from` より後で、その枝で最後に通る版まで（例 7.4.7・7.4.8）
- 上げた先の枝（7.6）：7.6.0 から `to` まで
- Known issues は**入れる版（途中の版と上げた先）**。途中の版は上げる作業に関わるものだけ
- 途中の版の不具合（後の版の Resolved issues に載ったもの）は、途中の版がその不具合の範囲（`present_from` ≤ 版 < `fixed_in`）に入れば出る
- Special Notices は後の版にも繰り返し載るので一つにまとめ、`introduced`（始まった版）を上げる前の版が過ぎていれば出さない
- 7.6 の変更が後から 7.4 の版にも入ったもの（例 自動更新の既定が daily になる：7.4.10・7.6.5）は、`introduced` に枝ごとの版を書く。7.4.13 から上げるなら、もう済んでいるので出さない

## 構成

```
dist/fortigate-fw-check.html ブラウザ版（配るのはこれだけ。tools/build_html.py で作る）
web/engine.js                ブラウザ版の判定の本体（fwnav.py と同じ動き）
web/app.html                 ブラウザ版の画面のひな形
samples/                     試し用の架空の config
fwnav.py                     判定の本体（標準ライブラリだけ）
kb/fortios/<版>.json         知識ベース（1 版 1 ファイル）
kb/fortios/models.json       機種の群（2GB RAM 機など）
docs/kb-schema.md            知識ベースの書き方
docs/kb-extraction-guide.md  リリースノートから知識ベースを作る手順（AI に渡す用）
tools/fetch_release_notes.py リリースノートを取る（cache/ に置く。公開しない）
tools/validate_kb.py         知識ベースの形を確かめる
tools/coverage.py            原文の全項目と知識ベースを突き合わせる（入れなかった理由の一覧）
kb/fortios/excluded/         知識ベースに入れなかった項目と理由
tools/build_html.py          ブラウザ版を組み立てる（知識ベースと web/engine.js を埋め込む）
tests/                       python3 -m unittest discover tests（Node があれば、ブラウザ版と結果が一字一句同じかも確かめる）
```

## 版を足す

`docs/kb-extraction-guide.md` の手順。原文を取り → AI か人が `kb/fortios/<版>.json` を書き → `tools/validate_kb.py` で確かめ → 人が原文と突き合わせて `"reviewed": true` にする。

## 知識ベースの今の状態（2026-11-20）

| 版 | 項目 | Known issues | 人の確認 |
|---|---|---|---|
| 7.4.7・7.4.8 | 27・45 | 上げる作業に関わるものだけ（途中の版として） | まだ |
| 7.6.0〜7.6.3・7.6.5・7.6.7 | 15〜32 | 入れていない | まだ |
| 7.6.4・7.6.6 | 36・41 | 上げる作業に関わるものだけ（途中の版として） | まだ |
| 7.6.8 | 41 | 全部見た | まだ |

項目は AI（Claude）がリリースノートから書いたもので、条件のいくつかは CLI の名前を実機で確かめていない（`system npu` のキーなど）。

### 何を見ているか

- **通る版の変更**：Special Notices・Upgrade information・Changes in（CLI・既定の動き・既定値・上限）・New features のうち既存の設定に効くもの
- **入れる版の Known issues**：上げた先の版は全部。途中の版（Upgrade Path Tool の途中の版）は、上げる作業に関わるもの（設定の消失・通信断・管理断・次へ上げられない など）
- **途中の版の不具合**：途中の版にあって、後の版の Resolved issues に載ったもの（「7.6.6 へ上げるとパッシブ FTP が通らない（7.6.7 で修正）」など）。途中の版を入れないと見ない

### 網羅状況（入れなかった項目と理由）

対象の原文の項目には、**全件に「知識ベースに入れた」か「入れなかった理由」が付いている**（理由は `kb/fortios/excluded/`。Bug ID かページ番号と日本語の理由）。

| 経路（Upgrade Path Tool） | 原文の項目（重複なし） | 入れた | 入れなかった | 判定の対象 |
|---|---|---|---|---|
| 90G・120G：7.4.7 → 7.6.4 → 7.6.6 → 7.6.8 | 640 | 147 | 493 | 137 |
| 101F：7.4.9 → 7.6.6 → 7.6.8 | 610 | 137 | 473 | 118 |
| VM：7.4.13 → 7.6.8（直接） | 517 | 107 | 410 | 81 |
| 60F：7.4.6 → 7.4.8 → 7.6.6 → 7.6.8 | 744 | 166 | 578 | 153 |

原文の項目は、通る版の Special Notices などの注意のページ・Changes in の表・New features、入れる版の Known issues、途中の版より後の Resolved issues のうちアップグレードに触れる行。Resolved issues のそれ以外の行（数千件）は見ていない。

```bash
python3 tools/fetch_release_notes.py 7.4.7 7.4.8 7.4.9 7.4.10 7.4.11 7.6.0 7.6.1 7.6.2 7.6.3 7.6.4 7.6.5 7.6.6 7.6.7 7.6.8
python3 tools/coverage.py --from 7.4.7 --to 7.6.8 --path 7.6.4,7.6.6
```

で、原文の書き出しつきの一覧（`out/coverage/`。Markdown と CSV）ができる。ほかのツールや AI が出した確認項目と突き合わせるときに使う。Special Notices は同じものが版ごとに繰り返し載るので、版ごとに数えると件数が大きく膨らむ。

## 社内 AI に渡す

`out/ai-prompt.md` をそのまま貼る。中身は「ルール（YES/NO/不明・根拠の行を引用・推測しない）」と、問いごとの「背景・問い・関係する設定の行」だけ。config 全体は渡さない。答えの表を `report.md` の「AI 判定」の項目と突き合わせる。

## 注意

- Fortinet とは関係のない個人の道具。FortiGate・FortiOS は Fortinet, Inc. の商標

- 知識ベースは**要約と判定の条件**で、リリースノートの原文ではない。最後は原文（各項目の URL）で確かめる
- `reviewed: false` の版は、AI が書いたまま人が確かめていない。結果の先頭に出る
- 「非該当」は「知識ベースの条件に当たらなかった」という意味。知識ベースに無い変更は見ていない

## ライセンス

MIT（`LICENSE`）。知識ベースの各項目はリリースノートの要約で、原文の著作権は Fortinet にある。原文（`cache/`）はリポジトリに入れていない。
