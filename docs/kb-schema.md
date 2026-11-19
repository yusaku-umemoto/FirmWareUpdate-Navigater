# 知識ベース（kb/）の書き方

リリースノートの項目を「どういう設定があると影響するか」の形にしたもの。1 版 1 ファイル（`kb/fortios/<版>.json`）。
原文は載せない。要約・判定条件・原文の URL だけを持つ。

## ファイルの形

```json
{
  "product": "fortios",
  "version": "7.6.0",
  "source": "https://docs.fortinet.com/document/fortigate/7.6.0/fortios-release-notes",
  "reviewed": false,
  "items": [ { ...項目... } ]
}
```

`reviewed` は人が原文と突き合わせたら `true` にする。

## 項目

| キー | 必須 | 中身 |
|---|---|---|
| `id` | ○ | `<版>-<区分の略>-<Bug ID か連番>`。例 `7.6.0-CLI-1003063` |
| `section` | ○ | 下の「区分」のどれか |
| `bug_id` | | リリースノートの Bug ID（あれば） |
| `title` | ○ | 日本語の短い見出し |
| `summary` | ○ | 何が変わるか（日本語・2〜3 文。原文の訳ではなく要約） |
| `severity` | ○ | `high`（通信断・管理断・設定消失のおそれ）／`medium`（動作が変わる）／`low`（知っておけばよい） |
| `models` | | 機種の条件。無ければ全機種。`{"only": ["ram_2gb"]}`・`{"exclude": ["vm"]}`。名前は `kb/fortios/models.json` の群か機種名（`FGT60F`）。前方一致 |
| `when` | | 影響が出る設定の条件（下の「条件」）。無ければ常に当たる |
| `judge` | ○ | `auto`：条件が成り立てば「該当」と決める／`ai`：条件が成り立てば社内 AI に聞く／`manual`：条件が成り立てば人が確かめる |
| `ai` | judge が ai のとき | `{"question": "...", "context": ["パス", ...]}`。`question` は YES/NO で答えられる問い。`context` は AI に見せる設定のパス |
| `impact` | ○ | 当たったときに何が起きるか |
| `action` | ○ | 何をすればよいか |
| `url` | ○ | 原文のページ |
| `introduced` | | その変更が入った版。上げる前の版がもう過ぎていれば出さない。`"7.6.3"` か、枝ごとに `{"7.4": "7.4.6", "7.6": "7.6.1"}`。Special Notices は後の版にも同じものが繰り返し載るので、始まった版をここに書く |
| `dedupe` | | 版をまたいで同じ項目を一つにまとめる鍵（通る版の中で一番新しい版のものを残す）。Special Notices はページの名前（URL の最後）を使い `sn:<名前>` とする。無ければ `bug_id` と区分でまとめる |

## 区分（section）

| 値 | リリースノートの章 | 版の範囲での扱い |
|---|---|---|
| `special_notice` | Special Notices | 通る版すべて |
| `upgrade` | Upgrade information | 通る版すべて |
| `cli` | Changes in CLI | 通る版すべて |
| `default_behavior` | Changes in default behavior | 通る版すべて |
| `default_value` | Changes in default values | 通る版すべて |
| `table_size` | Changes in table size | 通る版すべて |
| `removed` | 機能の削除・非対応化（New features などに書かれたもの） | 通る版すべて |
| `known_issue` | Known issues | **最後の版（上げた先）だけ** |

Resolved issues は扱わない（数が多く、多くは直ったことの知らせなので）。

## 設定の表し方（平らにした形）

ツールは config を 1 行 1 設定に直してから照らし合わせる。

```
config firewall ssl-ssh-profile
    edit "deep"
        config https
            set udp-not-quic allow
```

は

```
firewall ssl-ssh-profile[deep] https | udp-not-quic = allow
```

になる。`config` の名前を空白でつなぎ、`edit` の名前を `[ ]` で付ける。マルチ VDOM の `config global`・`config vdom` は外す（VDOM 名は別に持つ）。

## 条件（when）

```json
"when": {"any": [ 条件, ... ]}      // どれか
"when": {"all": [ 条件, ... ]}      // 全部
"when": 条件                         // 一つ
```

`any`・`all`・`not` は入れ子にできる。同じブロックの中で条件がそろうかを見るときは `per_block` を使う（中の条件には `path` を書かない）。

```json
{"per_block": "system interface[*]", "all": [
  {"key": "fortilink", "op": "eq", "value": "enable"},
  {"any": [{"key": "lldp-reception", "op": "in", "value": ["vdom", "disable"]},
           {"key": "lldp-reception", "op": "unset"}]}
]}
```

条件は次の形。

```json
{"path": "vpn ssl settings", "key": "status", "op": "eq", "value": "enable"}
```

ファイルの先頭に `"coverage": {"known_issue": false}` と書くと、その版の Known issues はまだ入っていないことを表す（上げた先の版がそうなら、結果に注意を出す）。

- `path`：上の平らにした形のパス。`[*]` は edit の名前なら何でも、`*` は何でも
- `key`：`set` の語。無ければブロックそのものを見る
- `op`：
  - `exists`（既定）：そのブロック・その `set` がある
  - `absent`：無い
  - `eq`・`ne`：値が等しい・等しくない（`"` は外して比べる。複数の値はどれか一つ）
  - `in`：`value` に配列を書き、値がそのどれか
  - `regex`：値が正規表現に合う
  - `unset`：ブロックはあるが `set` が書かれていない（＝既定値で動いている）。`show` は既定値の行を出さないので、**既定値が変わった項目はこれで書く**。`[*]` を含まないブロック（`system global` など）が丸ごと無いときも既定値で動いているので当たる

## 判定の結果

| 結果 | 意味 |
|---|---|
| 該当 | judge が auto で条件が成り立った。設定の行を根拠として出す |
| AI 判定 | judge が ai で条件が成り立った。社内 AI 用の問いに入る |
| 要確認 | judge が manual で条件が成り立った／機種が分からない |
| 非該当 | 条件が成り立たない／機種が対象外 |

## 入れなかった項目（kb/fortios/excluded/）

リリースノートの項目のうち知識ベースに入れなかったものは、理由を `kb/fortios/excluded/*.json` に書く。

```json
{"note": "…", "reasons": {"bug:1013290": "新しい設定項目の追加：既定は今までと同じ動き", "page:933460": "別の項目に含めた：7.6.5-DB-1138921"}}
```

鍵は表の行なら `bug:<Bug ID>`、注意のページなら `page:<URL のページ番号>`。理由は「分類：補足」。`tools/coverage.py` が原文の全項目と突き合わせ、理由の無いものを「★ 理由なし」と出す。
