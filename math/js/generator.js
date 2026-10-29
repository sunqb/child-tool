/* =========================================================
 * generator.js · 出题引擎（纯函数，无 DOM / 无 Store 依赖）
 * 职责：模板实例化（随机参数 + 约束校验）与组卷
 * 契约见 docs/架构设计-v1.md 第 2、3 节
 * ========================================================= */
(function (global) {
  "use strict";

  var MAX_RETRY = 200; // 单模板实例化重试上限，超限放弃换下一模板

  /* ---------- 约束 DSL：极简表达式求值（递归下降，不用 eval） ---------- */

  var FN = {
    div: function (a, b) { return Math.floor(a / b); },
    mod: function (a, b) { return a - Math.floor(a / b) * b; },
    min: function (a, b) { return Math.min(a, b); },
    max: function (a, b) { return Math.max(a, b); }
  };

  function tokenize(src) {
    var toks = [], i = 0;
    while (i < src.length) {
      var ch = src.charAt(i);
      if (ch === " " || ch === "\t" || ch === "\n") { i++; continue; }
      if (/[0-9.]/.test(ch)) {
        var j = i;
        while (j < src.length && /[0-9.]/.test(src.charAt(j))) j++;
        toks.push({ k: "num", v: parseFloat(src.slice(i, j)) });
        i = j; continue;
      }
      if (/[A-Za-z_]/.test(ch)) {
        var k = i;
        while (k < src.length && /[A-Za-z0-9_]/.test(src.charAt(k))) k++;
        toks.push({ k: "id", v: src.slice(i, k) });
        i = k; continue;
      }
      var two = src.substr(i, 2);
      if (two === "==" || two === "!=" || two === ">=" || two === "<=") {
        toks.push({ k: "op", v: two }); i += 2; continue;
      }
      if ("+-*/%()<>,".indexOf(ch) >= 0) {
        toks.push({ k: "op", v: ch }); i++; continue;
      }
      throw new Error("expr 非法字符: " + ch);
    }
    return toks;
  }

  function evalExpr(src, scope) {
    var toks = tokenize(src), pos = 0;

    function peek() { return toks[pos]; }
    function isOp(v) { var t = peek(); return t && t.k === "op" && t.v === v; }
    function eat(v) {
      if (!isOp(v)) throw new Error("expr 期望 " + (v || "结尾") + " : " + src);
      pos++;
    }

    function primary() {
      var t = peek();
      if (!t) throw new Error("expr 意外结束: " + src);
      if (t.k === "num") { pos++; return t.v; }
      if (t.k === "id") {
        pos++;
        if (isOp("(")) { // 函数调用
          eat("(");
          var args = [comparison()];
          while (isOp(",")) { eat(","); args.push(comparison()); }
          eat(")");
          var fn = FN[t.v];
          if (!fn) throw new Error("expr 未知函数: " + t.v);
          return fn.apply(null, args);
        }
        if (!Object.prototype.hasOwnProperty.call(scope, t.v)) {
          throw new Error("expr 未知变量: " + t.v);
        }
        return scope[t.v];
      }
      if (isOp("(")) { eat("("); var v = comparison(); eat(")"); return v; }
      throw new Error("expr 意外符号: " + t.v);
    }
    function unary() {
      if (isOp("-")) { eat("-"); return -unary(); }
      if (isOp("+")) { eat("+"); return unary(); }
      return primary();
    }
    function mul() {
      var v = unary();
      while (isOp("*") || isOp("/") || isOp("%")) {
        var op = peek().v; pos++;
        var r = unary();
        if (op === "*") v = v * r;
        else if (op === "/") v = v / r;
        else v = v - Math.floor(v / r) * r;
      }
      return v;
    }
    function add() {
      var v = mul();
      while (isOp("+") || isOp("-")) {
        var op = peek().v; pos++;
        var r = mul();
        v = op === "+" ? v + r : v - r;
      }
      return v;
    }
    function comparison() {
      var v = add();
      var t = peek();
      if (t && t.k === "op" && t.v.length === 2) { // == != >= <=
        pos++;
        var r = add();
        if (t.v === "==") v = v === r ? 1 : 0;
        else if (t.v === "!=") v = v !== r ? 1 : 0;
        else if (t.v === ">=") v = v >= r ? 1 : 0;
        else v = v <= r ? 1 : 0;
      } else if (t && t.k === "op" && (t.v === ">" || t.v === "<")) {
        pos++;
        var r2 = add();
        v = t.v === ">" ? (v > r2 ? 1 : 0) : (v < r2 ? 1 : 0);
      }
      return v;
    }

    var val = comparison();
    if (pos < toks.length) throw new Error("expr 未消费完: " + src);
    return val;
  }

  /* ---------- 文本替换与数字格式化 ---------- */

  function formatNum(v) {
    if (typeof v !== "number" || !isFinite(v)) return String(v);
    if (Math.abs(v - Math.round(v)) < 1e-9) return String(Math.round(v));
    return String(Math.round(v * 100) / 100); // 计算值约定为整数，这里仅兜底
  }

  // 把 "每箱 {n2} 盒" 替换为具体数字；未知变量抛错（模板 bug，自检兜住）
  function substitute(text, scope) {
    return String(text).replace(/\{(\w+)\}/g, function (all, name) {
      if (!Object.prototype.hasOwnProperty.call(scope, name)) {
        throw new Error("未定义变量 {" + name + "}");
      }
      return formatNum(scope[name]);
    });
  }

  function subArray(arr, scope) {
    return (arr || []).map(function (s) { return substitute(s, scope); });
  }

  /* ---------- 随机参数 ---------- */

  function randInRange(spec) {
    var min = spec[0], max = spec[1], step = spec[2] || 1;
    if (!(min <= max) || step < 1) throw new Error("参数区间非法");
    var n = Math.floor((max - min) / step);
    return min + step * Math.floor(Math.random() * (n + 1));
  }

  function pickParams(tpl) {
    var scope = {};
    Object.keys(tpl.params).forEach(function (k) {
      scope[k] = randInRange(tpl.params[k]);
    });
    return scope;
  }

  /* ---------- 实例化 ---------- */

  function tryOnce(tpl) {
    var scope = pickParams(tpl);
    Object.keys(tpl.calc).forEach(function (name) { // 按声明顺序，后者可引用前者
      var v = evalExpr(substitute(tpl.calc[name], scope), scope);
      if (typeof v !== "number" || !isFinite(v)) throw new Error("calc 非数值");
      scope[name] = v;
    });
    (tpl.constraints || []).forEach(function (c) {
      if (evalExpr(substitute(c, scope), scope) === 0) throw new Error("constraint fail");
    });
    // 条件答案（如「够吗」题的够/不够双结果）：按声明顺序取第一个 when 非 0 的分支，
    // 全不匹配按失败处理（触发重试），保证实例一定落在某个分支上
    if (tpl.answers) {
      var hit = -1;
      for (var a = 0; a < tpl.answers.length; a++) {
        if (evalExpr(substitute(tpl.answers[a].when, scope), scope) !== 0) { hit = a; break; }
      }
      if (hit < 0) throw new Error("answers 无匹配分支");
      scope.__answerIdx = hit;
    }
    return scope;
  }

  // 成功返回全量文本快照实例；重试耗尽返回 null（组卷时跳过）
  function instantiate(tpl) {
    var scope = null;
    for (var i = 0; i < MAX_RETRY; i++) {
      try { scope = tryOnce(tpl); break; }
      catch (e) { scope = null; }
    }
    if (!scope) return null;

    var segs = (tpl.segments || []).map(function (s) {
      return { t: substitute(s.t, scope), type: s.type };
    });
    // 命中的 answers 分支覆盖 answer 与 steps（分支未给 steps 则沿用模板默认）
    var pick = tpl.answers && typeof scope.__answerIdx === "number" ? tpl.answers[scope.__answerIdx] : null;
    var inst = {
      tplId: tpl.id,
      unit: tpl.unit,
      chapter: tpl.chapter || null,
      type: tpl.type,
      skill: tpl.skill || [],
      level: tpl.level,
      params: (function () { // 只留随机参数，便于追溯
        var p = {};
        Object.keys(tpl.params).forEach(function (k) { p[k] = scope[k]; });
        return p;
      })(),
      story: substitute(tpl.story, scope),
      question: substitute(tpl.question, scope),
      segments: segs,
      relations: (tpl.relations || []).slice(),
      table: {
        known: subArray(tpl.table && tpl.table.known, scope),
        ask: tpl.table ? substitute(tpl.table.ask, scope) : "",
        supplyRow: tpl.table && tpl.table.supplyRow ? substitute(tpl.table.supplyRow, scope) : null
      },
      bridge: !tpl.bridge ? null : {
        ask: substitute(tpl.bridge.ask, scope),
        choices: subArray(tpl.bridge.choices, scope),
        answer: tpl.bridge.answer
      },
      steps: subArray((pick && pick.steps) || tpl.steps, scope),
      answer: substitute(pick ? pick.answer : tpl.answer, scope),
      tip: substitute(tpl.tip, scope)
    };
    if (tpl.supply) {
      inst.supply = {
        choices: subArray(tpl.supply.choices, scope),
        answer: tpl.supply.answer,
        why: substitute(tpl.supply.why, scope)
      };
    }
    return inst;
  }

  /* ---------- 组卷 ---------- */

  function shuffle(arr) {
    var a = arr.slice();
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }

  /**
   * 从模板库组一卷
   * cfg: { units: ["division",...], chapters: {division:["d-avg",...]}, level: "mix"|1|2|3, count: 5 }
   * chapters 缺键或空数组 = 该单元全部章节
   * 返回 { problems: [实例], poolSize: 可用模板数 }
   */
  function make(cfg, templates) {
    var level = cfg.level;
    var pool = templates.filter(function (t) {
      if (cfg.units.indexOf(t.unit) < 0) return false;
      var chs = cfg.chapters && cfg.chapters[t.unit];
      if (chs && chs.length && chs.indexOf(t.chapter || "") < 0) return false;
      if (level !== "mix" && level !== "all" && String(t.level) !== String(level)) return false;
      return true;
    });
    var bag = shuffle(pool);
    var problems = [];
    for (var i = 0; i < bag.length && problems.length < cfg.count; i++) {
      var inst = null;
      try { inst = instantiate(bag[i]); } catch (e) { inst = null; }
      if (inst) problems.push(inst);
    }
    return { problems: problems, poolSize: pool.length };
  }

  /**
   * 变式卷：给定模板列表（来自错题的 tplId），逐个重新实例化（新数字同结构）
   */
  function variant(templates) {
    var problems = [];
    shuffle(templates).forEach(function (t) {
      var inst = null;
      try { inst = instantiate(t); } catch (e) { inst = null; }
      if (inst) problems.push(inst);
    });
    return problems;
  }

  global.Generator = {
    instantiate: instantiate,
    make: make,
    variant: variant,
    evalExpr: evalExpr,
    substitute: substitute,
    formatNum: formatNum
  };
})(typeof window !== "undefined" ? window : globalThis);
