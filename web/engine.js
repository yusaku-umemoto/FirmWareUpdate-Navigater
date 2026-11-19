/* FortiGate ファームアップ影響チェックの判定の本体（fwnav.py と同じ動き）。
 * ブラウザでも Node でも動く。通信はしない。
 * fwnav.py を直したら、ここも同じように直す（tests/test_js_parity.py で結果が一致するか確かめる）。
 */
(function (root) {
  "use strict";

  var SECTION_JA = {
    special_notice: "Special Notices",
    upgrade: "Upgrade information",
    cli: "Changes in CLI",
    default_behavior: "Changes in default behavior",
    default_value: "Changes in default values",
    table_size: "Changes in table size",
    removed: "機能の削除・非対応化",
    known_issue: "Known issues（上げた先の版）"
  };
  var SEV_ORDER = { high: 0, medium: 1, low: 2 };
  var ORDER = ["該当", "AI 判定", "要確認", "非該当"];
  var SECRET_KEYS = /(passw|secret|psk|key$|-key|token|community|passphrase|private)/i;

  // ------------------------------------------------------------ config を平らにする

  // Python の shlex.split(posix=True) と同じ分け方。引用符が閉じていなければ null
  function shlexSplit(s) {
    var out = [], cur = "", has = false, i = 0, q = null, c;
    while (i < s.length) {
      c = s[i];
      if (q === "'") {
        if (c === "'") q = null; else cur += c;
      } else if (q === '"') {
        if (c === '"') q = null;
        else if (c === "\\" && i + 1 < s.length && '\\"$`\n'.indexOf(s[i + 1]) >= 0) { cur += s[i + 1]; i++; }
        else cur += c;
      } else if (c === "'" || c === '"') {
        q = c; has = true;
      } else if (c === "\\") {
        if (i + 1 < s.length) { cur += s[i + 1]; i++; }
        has = true;
      } else if (/\s/.test(c)) {
        if (has || cur) { out.push(cur); cur = ""; has = false; }
      } else {
        cur += c; has = true;
      }
      i++;
    }
    if (q) return null;
    if (has || cur) out.push(cur);
    return out;
  }

  function tokenize(line) {
    var t = shlexSplit(line);
    return t === null ? line.split(/\s+/).filter(Boolean) : t;
  }

  function countQuotes(s) { return (s.match(/"/g) || []).length; }

  function pathOf(stack) {
    var out = [];
    stack.forEach(function (e) {
      if (e[0] === "config") out.push(e[1]);
      else if (e[0] === "edit") {
        if (out.length) out[out.length - 1] = out[out.length - 1] + "[" + e[1] + "]";
        else out.push("[" + e[1] + "]");
      }
    });
    return out.join(" ");
  }

  function flatten(text) {
    var header = {};
    var m = /#config-version=([A-Z0-9-]+?)-(\d+\.\d+\.\d+)/.exec(text);
    if (m) header = { model: m[1], version: m[2] };
    var lines = [], blocks = new Map(), stack = [], vdom = null, inMulti = false;
    var raws = text.split(/\r\n|\r|\n/);
    for (var n = 0; n < raws.length; n++) {
      var s = raws[n].trim();
      if (inMulti) {
        if (s.endsWith('"') && !s.endsWith('\\"')) inMulti = false;
        continue;
      }
      if (!s || s[0] === "#") continue;
      var t = tokenize(s);
      if (!t.length) {
        if (countQuotes(s) % 2 === 1) inMulti = true;
        continue;
      }
      var cmd = t[0], p, name;
      if (cmd === "config") {
        name = t.slice(1).join(" ");
        if (!stack.length && (name === "global" || name === "vdom")) {
          stack.push(["scope", name]);
          vdom = name === "global" ? "global" : null;
          continue;
        }
        stack.push(["config", name]);
        p = pathOf(stack);
        if (!blocks.has(p)) blocks.set(p, vdom);
      } else if (cmd === "edit") {
        name = t.slice(1).join(" ");
        if (stack.length === 1 && stack[0][0] === "scope" && stack[0][1] === "vdom") {
          vdom = name;
          stack.push(["vdomedit", name]);
          continue;
        }
        stack.push(["edit", name]);
        p = pathOf(stack);
        if (!blocks.has(p)) blocks.set(p, vdom);
        lines.push([vdom, p, null, null]);
      } else if (cmd === "next" || cmd === "end") {
        if (stack.length) {
          var kind = stack.pop()[0];
          if (kind === "scope" || kind === "vdomedit") vdom = null;
        }
      } else if (cmd === "set" || cmd === "unset" || cmd === "append") {
        if (countQuotes(s) % 2 === 1 && !s.endsWith('"')) inMulti = true;
        if (t.length < 2) continue;
        p = pathOf(stack);
        if (!blocks.has(p)) blocks.set(p, vdom);
        if (cmd === "unset") continue;
        lines.push([vdom, p, t[1], t.slice(2).join(" ")]);
      }
    }
    return { lines: lines, blocks: blocks, header: header };
  }

  function mask(key, value) {
    return key && SECRET_KEYS.test(key) ? "********" : value;
  }

  function fmtLine(ln) {
    var vdom = ln[0], path = ln[1], key = ln[2], value = ln[3];
    var pre = vdom && vdom !== "root" ? "[" + vdom + "] " : "";
    if (key === null || key === undefined) return pre + path;
    return pre + path + " | " + key + " = " + mask(key, value);
  }

  // ------------------------------------------------------------ 条件を照らし合わせる

  function escRe(s) { return s.replace(/[.*+?^${}()|[\]\\\/-]/g, "\\$&"); }

  var patCache = new Map();
  function pathRe(pat) {
    var r = patCache.get(pat);
    if (r) return r;
    if (pat[0] === "=") {
      r = new RegExp("^" + escRe(pat.slice(1)) + "$", "i");
    } else {
      var src = pat.split("[*]").map(function (part) {
        return part.split("*").map(escRe).join(".*");
      }).join("\\[[^\\]]*\\]");
      r = new RegExp("^" + src + "$", "i");
    }
    patCache.set(pat, r);
    return r;
  }

  function valuesOf(v) {
    v = v.trim();
    var t = shlexSplit(v);
    if (t === null) return [v.replace(/^"+|"+$/g, "")];
    return t.length ? t : [""];
  }

  function Config(text) {
    var f = flatten(text);
    this.lines = f.lines;
    this.blocks = f.blocks;
    this.header = f.header;
  }
  Config.prototype.matchBlocks = function (pat) {
    var r = pathRe(pat), out = [];
    this.blocks.forEach(function (_, p) { if (r.test(p)) out.push(p); });
    return out;
  };
  Config.prototype.matchLines = function (pat, key) {
    var r = pathRe(pat), out = [], k = key ? key.toLowerCase() : null;
    this.lines.forEach(function (ln) {
      if (ln[2] === null || !r.test(ln[1])) return;
      if (k === null || ln[2].toLowerCase() === k) out.push(ln);
    });
    return out;
  };

  function bindBlock(c, block) {
    if (Array.isArray(c)) return c.map(function (x) { return bindBlock(x, block); });
    if (!c || typeof c !== "object") return c;
    if ("per_block" in c) return c;
    var out = {};
    Object.keys(c).forEach(function (k) {
      out[k] = (k === "any" || k === "all" || k === "not") ? bindBlock(c[k], block) : c[k];
    });
    if (("key" in c || "op" in c) && !("path" in c)) out.path = "=" + block;
    return out;
  }

  // [成り立つか, 根拠の行の並び]
  function evalCond(cfg, c) {
    var ev = [], ok = false, i, r;
    if ("per_block" in c) {
      var inner = {};
      Object.keys(c).forEach(function (k) { if (k !== "per_block") inner[k] = c[k]; });
      cfg.matchBlocks(c.per_block).forEach(function (b) {
        var r2 = evalCond(cfg, bindBlock(inner, b));
        if (r2[0]) { ok = true; ev = ev.concat(r2[1]); }
      });
      return [ok, ev];
    }
    if ("any" in c) {
      for (i = 0; i < c.any.length; i++) {
        r = evalCond(cfg, c.any[i]);
        if (r[0]) { ok = true; ev = ev.concat(r[1]); }
      }
      return [ok, ev];
    }
    if ("all" in c) {
      for (i = 0; i < c.all.length; i++) {
        r = evalCond(cfg, c.all[i]);
        if (!r[0]) return [false, []];
        ev = ev.concat(r[1]);
      }
      return [true, ev];
    }
    if ("not" in c) return [!evalCond(cfg, c.not)[0], []];
    var pat = c.path, key = c.key === undefined ? null : c.key, op = c.op || "exists";
    if (op === "unset") {
      if (key === null) throw new Error("unset には key が要る");
      var bl = cfg.matchBlocks(pat);
      if (!bl.length) {
        if (pat.indexOf("[") >= 0 || pat.indexOf("*") >= 0 || pat[0] === "=") return [false, []];
        return [true, [[null, pat, null, null]]];
      }
      var kl = key.toLowerCase(), missing = [];
      bl.forEach(function (b) {
        var found = cfg.lines.some(function (ln) { return ln[1] === b && ln[2] && ln[2].toLowerCase() === kl; });
        if (!found) missing.push([cfg.blocks.get(b), b, key, "(未設定＝既定値)"]);
      });
      return [missing.length > 0, missing];
    }
    if (key === null) {
      var bs = cfg.matchBlocks(pat);
      if (op === "absent") return [!bs.length, []];
      return [bs.length > 0, bs.map(function (b) { return [cfg.blocks.get(b), b, null, null]; })];
    }
    var ls = cfg.matchLines(pat, key);
    if (op === "exists") return [ls.length > 0, ls];
    if (op === "absent") return [!ls.length, []];
    var want = c.value, hits = [];
    ls.forEach(function (ln) {
      var vals = valuesOf(ln[3]).map(function (x) { return x.toLowerCase(); }), hit;
      if (op === "eq") hit = vals.indexOf(String(want).toLowerCase()) >= 0;
      else if (op === "ne") hit = vals.indexOf(String(want).toLowerCase()) < 0;
      else if (op === "in") hit = want.some(function (w) { return vals.indexOf(String(w).toLowerCase()) >= 0; });
      else if (op === "regex") hit = new RegExp(want, "i").test(ln[3]);
      else throw new Error("知らない op: " + op);
      if (hit) hits.push(ln);
    });
    return [hits.length > 0, hits];
  }

  // ------------------------------------------------------------ 版の範囲

  function vtuple(v) { return v.split(".").map(Number); }
  function vcmp(a, b) {
    for (var i = 0; i < Math.max(a.length, b.length); i++) {
      var x = a[i] || 0, y = b[i] || 0;
      if (x !== y) return x < y ? -1 : 1;
    }
    return 0;
  }
  function br(t) { return t.slice(0, 2); }
  function brKey(t) { return t[0] + "." + t[1]; }
  function isVersion(v) { return /^\d+\.\d+\.\d+$/.test(String(v || "").trim()); }

  function sortedVersions(list) {
    return list.slice().sort(function (a, b) { return vcmp(vtuple(a), vtuple(b)); });
  }

  function versionsInRange(known, frm, to, hops) {
    var f = vtuple(frm), t = vtuple(to), last = {};
    hops.map(vtuple).concat([t]).forEach(function (h) {
      var k = brKey(h);
      if (!last[k] || vcmp(h, last[k]) > 0) last[k] = h;
    });
    return sortedVersions(known).filter(function (v) {
      var vt = vtuple(v);
      if (!(vcmp(f, vt) < 0 && vcmp(vt, t) <= 0)) return false;
      if (vcmp(br(vt), br(t)) === 0) return true;
      if (hops.length) return !!last[brKey(vt)] && vcmp(vt, last[brKey(vt)]) <= 0;
      return true;
    });
  }

  function alreadyPassed(it, frm) {
    var intro = it.introduced;
    if (!intro) return false;
    var f = vtuple(frm);
    if (typeof intro === "string") return vcmp(f, vtuple(intro)) >= 0;
    var keys = Object.keys(intro);
    for (var i = 0; i < keys.length; i++) {
      if (vcmp(vtuple(keys[i]), br(f)) === 0) return vcmp(f, vtuple(intro[keys[i]])) >= 0;
    }
    return keys.every(function (k) { return vcmp(vtuple(k), br(f)) < 0; });
  }

  function selectItems(kb, frm, to, hops) {
    var vers = versionsInRange(Object.keys(kb.versions), frm, to, hops);
    var items = [], seen = new Set();
    vers.slice().reverse().forEach(function (v) {
      kb.versions[v].items.forEach(function (it) {
        if (it.section === "known_issue" && v !== to) return;
        if (alreadyPassed(it, frm)) return;
        var dk = it.dedupe || (it.bug_id ? "bug:" + it.bug_id + ":" + it.section : null);
        if (dk && seen.has(dk)) return;
        if (dk) seen.add(dk);
        var copy = Object.assign({}, it, { version: v, reviewed: !!kb.versions[v].reviewed });
        items.push(copy);
      });
    });
    // 版の古い順。同じ版の中は元の並びのまま（Python の安定ソートと同じ）
    items = items.map(function (x, i) { return [x, i]; });
    items.sort(function (a, b) { return vcmp(vtuple(a[0].version), vtuple(b[0].version)) || a[1] - b[1]; });
    return { versions: vers, items: items.map(function (x) { return x[0]; }) };
  }

  function modelOk(it, model, groups) {
    var m = it.models;
    if (!m) return true;
    if (!model) return null;
    var up = model.toUpperCase();
    function hit(names) {
      return names.some(function (n) {
        return (groups[n] || [n]).some(function (p) { return up.indexOf(p.toUpperCase()) === 0; });
      });
    }
    if (m.only && !hit(m.only)) return false;
    if (m.exclude && hit(m.exclude)) return false;
    return true;
  }

  // ------------------------------------------------------------ 判定

  function judge(cfg, items, model, groups) {
    return items.map(function (it) {
      var r = { item: it, evidence: [] }, mo = modelOk(it, model, groups);
      if (mo === false) { r.result = "非該当"; r.why = "機種が対象外"; return r; }
      var res = it.when ? evalCond(cfg, it.when) : [true, []];
      r.evidence = res[1];
      if (!res[0]) { r.result = "非該当"; r.why = "条件の設定が無い"; }
      else if (mo === null) { r.result = "要確認"; r.why = "機種が分からない（機種を入れる）"; }
      else if (it.judge === "auto") { r.result = "該当"; r.why = "条件の設定がある"; }
      else if (it.judge === "ai") { r.result = "AI 判定"; r.why = "設定の中身で決まる"; }
      else { r.result = "要確認"; r.why = it.when ? "関係する設定があり、人が確かめる" : "人が確かめる項目"; }
      return r;
    });
  }

  function sortResults(res) {
    return res.slice().sort(function (a, b) {
      var x = a.item, y = b.item;
      return (ORDER.indexOf(a.result) - ORDER.indexOf(b.result)) ||
        ((SEV_ORDER[x.severity] === undefined ? 9 : SEV_ORDER[x.severity]) - (SEV_ORDER[y.severity] === undefined ? 9 : SEV_ORDER[y.severity])) ||
        vcmp(vtuple(x.version), vtuple(y.version)) ||
        (x.id < y.id ? -1 : x.id > y.id ? 1 : 0);
    });
  }

  function expectedVersions(frm, to, hops) {
    var f = vtuple(frm), last = {}, out = [];
    hops.map(vtuple).concat([vtuple(to)]).forEach(function (h) {
      var k = brKey(h);
      if (!last[k] || vcmp(h, last[k]) > 0) last[k] = h;
    });
    Object.keys(last).sort(function (a, b) { return vcmp(vtuple(a), vtuple(b)); }).forEach(function (k) {
      var b = vtuple(k), start = vcmp(br(f), b) === 0 ? f[2] + 1 : 0;
      for (var p = start; p <= last[k][2]; p++) out.push(b[0] + "." + b[1] + "." + p);
    });
    return out;
  }

  // check の全体。opts: {from, to, hops: [], model}
  function check(kb, text, opts) {
    var cfg = new Config(text);
    var frm = opts.from || cfg.header.version;
    if (!frm) throw new Error("上げる前の版が分からない（config の先頭に #config-version が無い）。上げる前の版を入れてください");
    var hops = opts.hops || [], to = opts.to;
    var model = opts.model || cfg.header.model || null;
    var sel = selectItems(kb, frm, to, hops);
    var res = sortResults(judge(cfg, sel.items, model, kb.groups || {}));
    var exp = (hops.length || vcmp(br(vtuple(frm)), br(vtuple(to))) === 0) ? expectedVersions(frm, to, hops) : [];
    var target = kb.versions[to];
    var meta = {
      from: frm, to: to, hops: hops, model: model, versions: sel.versions,
      unreviewed: sel.versions.filter(function (v) { return !kb.versions[v].reviewed; }),
      kb_missing: exp.filter(function (v) { return !kb.versions[v]; }),
      config: opts.configName || "",
      known_issue_missing: !!target && !((target.coverage || {}).known_issue !== false)
    };
    return { cfg: cfg, results: res, meta: meta };
  }

  // ------------------------------------------------------------ 出力（fwnav.py と同じ文面）

  function evLines(ev, limit) {
    limit = limit || 8;
    var out = ev.slice(0, limit).map(fmtLine);
    if (ev.length > limit) out.push("…ほか " + (ev.length - limit) + " 行");
    return out;
  }

  function counts(res) {
    var c = {};
    ORDER.forEach(function (k) { c[k] = res.filter(function (r) { return r.result === k; }).length; });
    return c;
  }

  function reportMarkdown(out) {
    var res = out.results, meta = out.meta;
    var L = ["# ファームアップ影響確認 " + meta.from + " → " + meta.to, ""];
    L.push("- 機種: " + (meta.model || "不明"));
    L.push("- 通るパス: " + [meta.from].concat(meta.hops, [meta.to]).join(" → "));
    L.push("- 見たリリースノートの版: " + meta.versions.join(", "));
    L.push("- 知識ベースで人が確かめていない版: " + (meta.unreviewed.join(", ") || "なし"));
    if (meta.known_issue_missing) L.push("- ★ 上げた先（" + meta.to + "）の Known issues は知識ベースに入っていない");
    if (meta.kb_missing.length) L.push("- ★ 知識ベースが無い版: " + meta.kb_missing.join(", ") + "（この版の変更は見ていない）");
    L.push("");
    var c = counts(res);
    L.push("| 該当 | AI 判定 | 要確認 | 非該当 |", "|---|---|---|---|");
    L.push("| " + c["該当"] + " | " + c["AI 判定"] + " | " + c["要確認"] + " | " + c["非該当"] + " |");
    ORDER.slice(0, 3).forEach(function (k) {
      var rs = res.filter(function (r) { return r.result === k; });
      if (!rs.length) return;
      L.push("", "## " + k + "（" + rs.length + "）", "");
      rs.forEach(function (r) {
        var it = r.item;
        L.push("### [" + it.severity + "] " + it.title, "");
        L.push("- 版: " + it.version + " ／ " + (SECTION_JA[it.section] || it.section) + (it.bug_id ? " ／ Bug ID " + it.bug_id : "") + " ／ `" + it.id + "`");
        L.push("- 内容: " + it.summary);
        L.push("- 影響: " + it.impact);
        L.push("- 対処: " + it.action);
        if (r.aiAnswer) L.push("- 社内 AI の答え: " + r.aiAnswer.answer + (r.aiAnswer.note ? "（" + r.aiAnswer.note + "）" : ""));
        if (r.evidence.length) {
          L.push("- 根拠の設定:", "  ```");
          evLines(r.evidence).forEach(function (x) { L.push("  " + x); });
          L.push("  ```");
        }
        L.push("- 原文: " + it.url, "");
      });
    });
    var ns = res.filter(function (r) { return r.result === "非該当"; });
    if (ns.length) {
      L.push("", "## 非該当（" + ns.length + "）", "", "| 版 | 重さ | 見出し | 理由 |", "|---|---|---|---|");
      ns.forEach(function (r) { L.push("| " + r.item.version + " | " + r.item.severity + " | " + r.item.title + " | " + r.why + " |"); });
    }
    return L.join("\n") + "\n";
  }

  function contextLines(cfg, pats, limit, firstBlocks) {
    limit = limit || 60;
    var seen = new Set(), out = [], first = new Set(firstBlocks || []);
    function keyOf(ln) { return ln.map(String).join("\u0000"); }
    cfg.lines.forEach(function (ln) {
      if (ln[2] !== null && first.has(ln[1]) && !seen.has(keyOf(ln))) { seen.add(keyOf(ln)); out.push(ln); }
    });
    pats.forEach(function (p) {
      var res = [p, p + " *", p + "[*]", p + "[*] *"].map(pathRe);
      cfg.lines.forEach(function (ln) {
        if (ln[2] === null) return;
        if (res.some(function (r) { return r.test(ln[1]); }) && !seen.has(keyOf(ln))) { seen.add(keyOf(ln)); out.push(ln); }
      });
    });
    if (out.length > limit) return out.slice(0, limit).map(fmtLine).concat(["…ほか " + (out.length - limit) + " 行（省略）"]);
    return out.map(fmtLine);
  }

  function aiPromptMarkdown(out) {
    var res = out.results, meta = out.meta, cfg = out.cfg;
    var rs = res.filter(function (r) { return r.result === "AI 判定"; });
    var L = [
      "# FortiGate ファームアップ影響の判定（" + meta.from + " → " + meta.to + "）", "",
      "あなたはネットワーク機器の設定を確認する担当です。",
      "下の各問いについて、添えた設定の行だけを根拠に答えてください。", "",
      "ルール:",
      "- 答えは YES / NO / 不明 のどれか一つ",
      "- 根拠にした設定の行をそのまま引用する。行が無いときは「該当行なし」と書く",
      "- 添えた行に無いことは推測しない。決められないときは「不明」",
      "- 設定の行は `パス | 項目 = 値` の形。パスの `[名前]` は edit の名前", "",
      "答えの形（問いごとに 1 行。表にする）:", "",
      "| ID | 答え | 根拠の行 | ひとこと |",
      "|---|---|---|---|", "", "---"
    ];
    rs.forEach(function (r, i) {
      var it = r.item, ai = it.ai || {};
      L.push("", "## 問い " + (i + 1) + "：" + it.id, "");
      L.push("背景: " + it.summary, "");
      L.push("問い: " + (ai.question || it.title), "");
      L.push("設定の行:", "```");
      var hitBlocks = r.evidence.map(function (e) { return e[1]; });
      var ctx = ai.context ? contextLines(cfg, ai.context, 60, hitBlocks) : evLines(r.evidence, 60);
      L = L.concat(ctx.length ? ctx : ["（関係する設定の行なし）"]);
      L.push("```");
    });
    if (!rs.length) L.push("", "（AI に聞く項目はありません）");
    return L.join("\n") + "\n";
  }

  function resultJson(out) {
    return JSON.stringify({
      meta: out.meta,
      results: out.results.map(function (r) {
        return {
          id: r.item.id, version: r.item.version, section: r.item.section, severity: r.item.severity,
          title: r.item.title, result: r.result, why: r.why,
          evidence: r.evidence.slice(0, 20).map(fmtLine), url: r.item.url,
          ai_answer: r.aiAnswer || undefined
        };
      })
    }, null, 1);
  }

  function csvCell(s) {
    s = s === undefined || s === null ? "" : String(s);
    return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  // Excel で開ける CSV（先頭に BOM を付けるのは呼ぶ側）
  function resultCsv(out) {
    var head = ["結果", "理由", "重さ", "版", "区分", "Bug ID", "見出し", "内容", "影響", "対処", "根拠の設定", "社内AIの答え", "ID", "原文"];
    var rows = [head].concat(out.results.map(function (r) {
      var it = r.item;
      return [r.result, r.why, it.severity, it.version, SECTION_JA[it.section] || it.section, it.bug_id || "",
        it.title, it.summary, it.impact, it.action, evLines(r.evidence, 20).join("\n"),
        r.aiAnswer ? r.aiAnswer.answer + (r.aiAnswer.note ? "（" + r.aiAnswer.note + "）" : "") : "", it.id, it.url];
    }));
    return rows.map(function (row) { return row.map(csvCell).join(","); }).join("\r\n") + "\r\n";
  }

  // 社内 AI の答えの表を読む。{id: {answer: "YES"|"NO"|"不明", evidence, note}}
  function parseAiAnswers(text, ids) {
    var known = new Set(ids), out = {};
    text.split(/\r\n|\r|\n/).forEach(function (line) {
      var s = line.trim();
      if (s[0] !== "|") return;
      var cells = s.replace(/^\|/, "").replace(/\|$/, "").split("|").map(function (x) { return x.trim().replace(/^`|`$/g, ""); });
      if (cells.length < 2) return;
      var id = cells[0];
      if (!known.has(id)) {
        // 「問い 1：7.6.8-…」や番号だけの書き方にも合わせる
        var m = /(\d+\.\d+\.\d+-[A-Za-z]+-[^\s|]+)/.exec(cells[0]);
        if (m && known.has(m[1])) id = m[1]; else return;
      }
      var a = cells[1].toUpperCase();
      var ans = /YES|はい/.test(a) ? "YES" : /NO|いいえ/.test(a) ? "NO" : /不明|UNKNOWN/.test(a) ? "不明" : null;
      if (!ans) return;
      out[id] = { answer: ans, evidence: cells[2] || "", note: cells[3] || "" };
    });
    return out;
  }

  // AI の答えを結果に反映する。YES→該当、NO→非該当、不明→要確認
  function applyAiAnswers(out, answers) {
    out.results.forEach(function (r) {
      var a = answers[r.item.id];
      if (!a || (r.result !== "AI 判定" && !r.aiAnswer)) return;
      r.aiAnswer = a;
      if (a.answer === "YES") { r.result = "該当"; r.why = "社内 AI の判定（YES）"; }
      else if (a.answer === "NO") { r.result = "非該当"; r.why = "社内 AI の判定（NO）"; }
      else { r.result = "要確認"; r.why = "社内 AI が決められなかった"; }
    });
    out.results = sortResults(out.results);
    return out;
  }

  root.FWNAV = {
    SECTION_JA: SECTION_JA, ORDER: ORDER,
    shlexSplit: shlexSplit, flatten: flatten, fmtLine: fmtLine, Config: Config, evalCond: evalCond,
    versionsInRange: versionsInRange, alreadyPassed: alreadyPassed, selectItems: selectItems,
    modelOk: modelOk, check: check, counts: counts, evLines: evLines, isVersion: isVersion, sortedVersions: sortedVersions,
    reportMarkdown: reportMarkdown, aiPromptMarkdown: aiPromptMarkdown, resultJson: resultJson, resultCsv: resultCsv,
    parseAiAnswers: parseAiAnswers, applyAiAnswers: applyAiAnswers
  };
  if (typeof module !== "undefined" && module.exports) module.exports = root.FWNAV;
})(typeof globalThis !== "undefined" ? globalThis : this);
