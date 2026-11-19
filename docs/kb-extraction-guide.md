# リリースノートから知識ベースを作る手順

新しい版を足すときの手順。AI（Claude など、社外でよい。読むのは公開のリリースノートだけ）に任せるときも、この手順を渡す。

## 1. 原文を取る

```bash
python3 tools/fetch_release_notes.py 7.6.9
```

`cache/release-notes/<版>/` にページごとのテキストができる。原文なので公開しない（`.gitignore` 済み）。

## 2. 読むページと読まないページ

| ページ（ファイル名） | 扱い |
|---|---|
| `special-notices.md` と、そこに並ぶ見出しのページ | **全部読む**。Special Notices の各ページは別ファイルになっている |
| `changes-in-cli.md` | 全部読む |
| `changes-in-default-behavior.md` | 全部読む |
| `changes-in-default-values.md` | 全部読む |
| `changes-in-table-size.md` | 全部読む |
| `changes-in-gui-behavior.md` | 運用に効くもの（設定の場所が消えるなど）だけ |
| `upgrade-information.md` とその下（`fortigate-6000-…`・`fortigate-vm-…` など） | アップグレードの手順や条件に効くものだけ |
| `new-features-or-enhancements.md` | **既存の設定の動きが変わる・機能が無くなる**ものだけ（区分 `removed` か該当する区分）。新機能の紹介は入れない |
| `known-issues.md` | 上げた先として使う版だけ（入れないときはファイルの先頭に `"coverage": {"known_issue": false}`） |
| `resolved-issues.md`・`product-integration-and-support.md`・`language-support.md`・`firmware-image-checksums.md`・`change-log.md`・`introduction-…`・`*-engine.md`・`virtualization-environments.md`・`agentless-vpn-support.md` | 読まない |

## 3. 項目にするかどうか

入れる：

- 上げると**設定が消える・変わる・使えなくなる**
- **既定値が変わる**（`show` に出ない既定値で動いている機器に効く。条件は `op: unset` で書く）
- 通信・管理・認証・HA・VPN が**止まる・変わる**おそれ
- 特定の機種だけの注意（`models`）
- 上げる前の作業が要るもの（パスワードの要件・管理側の版など）

入れない：

- GUI の見た目だけの変更、ログの文言だけの変更
- 新機能の追加（既存の設定に影響しないもの）
- 6000/7000・Hyperscale の細かい既知の問題（まとめて 1 項目にするのはよい）

## 4. 条件の書き方のこつ

- **パスは実機の CLI の名前で書く**。分からなければ実機の `show full-configuration` や Fortinet の CLI リファレンスで確かめる。`config` の名前を空白でつなぎ、`edit` の名前は `[*]`
- **「機能を使っている」をできるだけ確かに表す**。たとえば SSL VPN の既定のポータル（`full-access` など）はどの機器にもあるので、ポータルがあるだけでは使っている証拠にならない。`firewall policy[*] | srcintf` が `ssl.` で始まるものと組み合わせる
- 同じ `edit` の中でそろう必要がある条件は `per_block` で書く（別のインターフェースの値を拾ってしまうのを防ぐ）
- config だけで決めきれないものは `judge: "ai"` にして、`ai.question` を **YES/NO で答えられる問い**にする。`ai.context` には AI に見せる設定のパス（前方一致）を並べる
- config では全く分からないもの（パスワードの強さ、FortiSwitch 側の設定など）は `judge: "manual"`
- 迷ったら、**見落とすより多めに当たる**ほうを選ぶ

## 5. Special Notices の重複

Special Notices は後の版にも同じものが繰り返し載る。

- `dedupe` に `sn:<ページのファイル名（.md を除く）>` を書く。どの版のファイルでも同じ鍵にする
- `introduced` に、その変更が始まった版を書く（本文に「Starting in 7.6.3」などとあればそれ。無ければ、その注意が初めて載った版）
- 既にほかの版のファイルに同じページの項目があれば、中身をそろえる（`url` だけ自分の版にする）。ツールは通る版の中で一番新しい版の項目を出すので、見出しや対処に特定の版の番号を書き込まない

## 6. 書いたら確かめる

```bash
python3 tools/validate_kb.py kb/fortios/7.6.9.json --config <手元の config ...>
python3 fwnav.py list --from 7.6.8 --to 7.6.9
```

最後に人が原文と突き合わせ、よければファイルの `"reviewed"` を `true` にする。
