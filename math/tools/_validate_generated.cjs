#!/usr/bin/env node
/* _validate_generated.cjs · 校验 generated/*.json 的模板能否正确实例化
 * 由 generate_templates.py --merge 调用；直接运行也可：
 *   node math/tools/_validate_generated.cjs math/data/generated/xxx.json [...]
 * 退出码 0 = 全部通过
 */
"use strict";

var fs = require("fs");
var path = require("path");

eval(fs.readFileSync(path.join(__dirname, "..", "js", "generator.js"), "utf8"));
var bank = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "data", "problems.json"), "utf8"));
var catalogUnits = {};
bank.meta.catalog.units.forEach(function (u) { catalogUnits[u.id] = (u.chapters || []).map(function (c) { return c.id; }); });

var N = 60; // 每模板试实例化次数（比 selfcheck 少，合并前的粗筛）
var fail = 0;

function err(id, msg) { console.error("  [FAIL] " + id + ": " + msg); fail++; }

var files = process.argv.slice(2);
if (!files.length) { console.error("用法: node _validate_generated.cjs <generated.json ...>"); process.exit(2); }

files.forEach(function (file) {
  var gen = JSON.parse(fs.readFileSync(file, "utf8"));
  console.log(path.basename(file) + "（" + gen.templates.length + " 个模板）");
  gen.templates.forEach(function (tpl, idx) {
    var id = tpl.unit + "/" + tpl.chapter + "#" + (idx + 1);
    if (!catalogUnits[tpl.unit]) { err(id, "unit 不在 catalog"); return; }
    if (catalogUnits[tpl.unit].indexOf(tpl.chapter) < 0) { err(id, "chapter 不在 catalog"); return; }
    if (["standard", "distractor", "supply"].indexOf(tpl.type) < 0) { err(id, "type 非法"); return; }
    if ([1, 2, 3].indexOf(tpl.level) < 0) { err(id, "level 非法"); return; }
    if (!tpl.story || !tpl.question || !tpl.tip) { err(id, "story/question/tip 缺失"); return; }
    if (!tpl.answer && !Array.isArray(tpl.answers)) { err(id, "answer/answers 缺失"); return; }
    if (!Array.isArray(tpl.segments) || tpl.segments.join === undefined) { err(id, "segments 缺失"); return; }
    if (tpl.type === "supply" && !(tpl.supply && Array.isArray(tpl.supply.choices) && tpl.supply.why)) {
      err(id, "supply 字段缺失"); return;
    }
    var ok = 0, ansSeen = {};
    for (var i = 0; i < N; i++) {
      var inst = null;
      try { inst = Generator.instantiate(tpl); } catch (e) { inst = null; }
      if (!inst) continue;
      ok++;
      ansSeen[inst.answer] = true;
      var joined = inst.segments.map(function (s) { return s.t; }).join("");
      if (joined !== inst.story) { err(id, "segments 拼接 != story"); break; }
      if (/\{\w+\}/.test(inst.story + inst.question + inst.answer)) { err(id, "残留占位变量"); break; }
    }
    if (ok === 0) { err(id, N + " 次实例化全部失败（约束过紧/表达式非法）"); return; }
    if (ok < N * 0.5) { err(id, "实例化成功率仅 " + Math.round(ok / N * 100) + "%（约束过紧）"); return; }
    if (tpl.answers && Object.keys(ansSeen).length < 2) err(id, "answers 分支未真正生效");
  });
});

if (fail) { console.error("\n共 " + fail + " 处问题"); process.exit(1); }
console.log("\n生成产物校验通过");
