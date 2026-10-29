#!/usr/bin/env node
/* =========================================================
 * selfcheck.js · 题库批量自检（开发期工具，不部署）
 * 用法: node math/tools/selfcheck.js
 * 检查项见 docs/架构设计-v1.md 第 7 节
 * ========================================================= */
"use strict";

var fs = require("fs");
var path = require("path");

var root = path.join(__dirname, "..");
/* eslint-disable no-eval */
eval(fs.readFileSync(path.join(root, "js", "generator.js"), "utf8"));
var data = JSON.parse(fs.readFileSync(path.join(root, "data", "problems.json"), "utf8"));

var N = 100;
var fail = 0;

function err(id, msg) {
  console.error("  [FAIL] " + id + ": " + msg);
  fail++;
}

/* catalog 结构校验：模板 unit/chapter 必须落在 catalog 树内 */
var catalog = data.meta && data.meta.catalog;
if (!catalog || !Array.isArray(catalog.units) || !catalog.units.length) {
  err("meta", "meta.catalog.units 缺失或为空");
} else {
  var catUnits = {};
  catalog.units.forEach(function (u) {
    if (catUnits[u.id]) err("meta", "catalog 单元 id 重复: " + u.id);
    catUnits[u.id] = {};
    (u.chapters || []).forEach(function (c) {
      if (catUnits[u.id][c.id]) err("meta", "catalog 章节 id 重复: " + u.id + "/" + c.id);
      catUnits[u.id][c.id] = 0;
    });
  });
  data.templates.forEach(function (tpl) {
    if (!catUnits[tpl.unit]) { err(tpl.id, "unit 不在 catalog: " + tpl.unit); return; }
    var chs = Object.keys(catUnits[tpl.unit]);
    if (chs.length) {
      if (!tpl.chapter || !(tpl.chapter in catUnits[tpl.unit])) {
        err(tpl.id, "章节缺失或不在 catalog: " + tpl.unit + "/" + (tpl.chapter || "(无)"));
      } else catUnits[tpl.unit][tpl.chapter]++;
    }
  });
  catalog.units.forEach(function (u) {
    (u.chapters || []).forEach(function (c) {
      if (!catUnits[u.id][c.id]) err("meta", "章节下无模板: " + u.id + "/" + c.id);
    });
  });
}

data.templates.forEach(function (tpl) {
  var id = tpl.id;
  var ok = 0, segBad = 0, relBad = 0, leftoverBad = 0, nonInt = 0, scopeDump = null;
  var answersSeen = {}; // answers 条件分支（如「够吗」题）必须真的出现多种结果

  for (var i = 0; i < N; i++) {
    var inst = null;
    try { inst = Generator.instantiate(tpl); } catch (e) { inst = null; }
    if (!inst) continue;
    ok++;
    scopeDump = inst;
    if (tpl.answers) answersSeen[inst.answer] = true;

    // 1) segments 拼接 === story
    var joined = inst.segments.map(function (s) { return s.t; }).join("");
    if (joined !== inst.story) {
      if (segBad === 0) err(id, "segments 拼接不等 story\n    拼接=" + joined + "\n    题干=" + inst.story);
      segBad++;
    }

    // 2) relations 都能在 题干+问题 中找到（关系词可能出现在问句里，如「平均」）
    var hay = inst.story + " " + inst.question;
    inst.relations.forEach(function (r) {
      if (hay.indexOf(r) < 0 && relBad === 0) { err(id, "关系词不在题干或问题中: " + r); relBad++; }
    });

    // 3) 解析后的文本无残留 {
    var texts = [inst.story, inst.question, inst.answer, inst.tip]
      .concat(inst.steps)
      .concat(inst.table.known || [])
      .concat(inst.table.supplyRow ? [inst.table.supplyRow] : [])
      .concat(inst.table.ask ? [inst.table.ask] : [])
      .concat(inst.bridge ? inst.bridge.choices.concat([inst.bridge.ask]) : [])
      .concat(inst.supply ? inst.supply.choices.concat([inst.supply.why]) : []);
    texts.forEach(function (t) {
      if (/\{\w+\}/.test(t)) { if (leftoverBad === 0) err(id, "残留未替换变量: " + t); leftoverBad++; }
    });

    // 4) 参数与 calc 结果应为整数（模板约定）——在下方 probe 中统一验证
  }

  // 5) 随机参数整数性（params 都是整数区间，天然整数）；calc 整数性用重算验证
  var probe = null;
  try { probe = Generator.instantiate(tpl); } catch (e) { probe = null; }
  if (probe) {
    var scope = {};
    Object.keys(tpl.params).forEach(function (k) { scope[k] = probe.params[k]; });
    Object.keys(tpl.calc).forEach(function (name) {
      var v = Generator.evalExpr(Generator.substitute(tpl.calc[name], scope), scope);
      scope[name] = v;
      if (!Number.isInteger(v)) { err(id, "calc 结果非整数: " + name + " = " + v); nonInt++; }
    });
  }

  var rate = Math.round(ok / N * 100);
  var ansInfo = "";
  if (tpl.answers) {
    var distinct = Object.keys(answersSeen).length;
    ansInfo = " · 答案 " + distinct + " 种";
    if (distinct < 2) err(id, "answers 100 次只出现 " + distinct + " 种答案（条件分支未真正生效）");
  }
  var flag = (segBad || relBad || leftoverBad || nonInt) ? " ✗" : "";
  console.log(id + " " + tpl.unit + " L" + tpl.level + " " + tpl.type + " | 实例化成功率 " + rate + "%" + ansInfo + flag);
  if (ok === 0) err(id, "100 次实例化全部失败（约束过紧或模板 bug）");
});

console.log("");
if (fail) { console.error("自检未通过，共 " + fail + " 处问题"); process.exit(1); }
console.log("自检通过: " + data.templates.length + " 个模板 × " + N + " 次实例化");
