// Node から web/engine.js を動かし、fwnav.py と同じ 3 つのファイルを書く（結果が一致するかのテスト用）。
// 使い方: node tests/js_runner.js <config> <from> <to> <hops(カンマ区切り/空)> <出力先>
const fs = require("fs");
const path = require("path");
const F = require("../web/engine.js");
const root = path.join(__dirname, "..");
const [conf, from, to, hops, out] = process.argv.slice(2);
const kbDir = path.join(root, "kb", "fortios");
const kb = { versions: {}, groups: JSON.parse(fs.readFileSync(path.join(kbDir, "models.json"), "utf8")).groups };
for (const f of fs.readdirSync(kbDir).filter((x) => /^\d.*\.json$/.test(x)).sort()) {
  const d = JSON.parse(fs.readFileSync(path.join(kbDir, f), "utf8"));
  kb.versions[d.version] = d;
}
const text = fs.readFileSync(conf, "utf8");
const r = F.check(kb, text, { from: from || null, to, hops: hops ? hops.split(",") : [], configName: path.basename(conf) });
fs.mkdirSync(out, { recursive: true });
fs.writeFileSync(path.join(out, "report.md"), F.reportMarkdown(r));
fs.writeFileSync(path.join(out, "ai-prompt.md"), F.aiPromptMarkdown(r));
fs.writeFileSync(path.join(out, "result.json"), F.resultJson(r));
