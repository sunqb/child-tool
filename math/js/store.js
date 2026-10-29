/* =========================================================
 * store.js · 审题雷达数据访问层
 * localStorage 前缀 math.；不可用时内存兜底（同 dictation 手法）
 * 契约见 docs/架构设计-v1.md 第 4、5 节
 * ========================================================= */
(function (global) {
  "use strict";

  var PREFIX = "math.";
  var MAX_SHEETS = 30;
  var KNOWN_UNITS = ["volume", "division", "strategy", "average"];
  var KNOWN_LEVELS = ["mix", "1", "2", "3"];
  var KNOWN_SCAFFOLDS = ["A", "B", "C"];

  var mem = {}; // localStorage 不可用（隐私模式等）时的内存兜底

  function readRaw(key) {
    try {
      var raw = global.localStorage.getItem(PREFIX + key);
      return raw == null ? undefined : JSON.parse(raw);
    } catch (e) { return mem[key]; }
  }
  function writeRaw(key, val) {
    mem[key] = val;
    try { global.localStorage.setItem(PREFIX + key, JSON.stringify(val)); } catch (e) {}
  }

  function uid() {
    return "s" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  }

  // 本地时区日期 YYYY-MM-DD（toISOString 是 UTC，凌晨出卷会记成前一天）
  function localDate() {
    var d = new Date();
    return d.getFullYear() + "-" +
      ("0" + (d.getMonth() + 1)).slice(-2) + "-" +
      ("0" + d.getDate()).slice(-2);
  }

  /* ---------- 家长配置 ---------- */

  var DEFAULT_CONFIG = {
    units: KNOWN_UNITS.slice(),  // 练哪些单元
    level: "mix",                // mix | 1 | 2 | 3
    scaffold: "A",               // A 全程扶 | B 半扶 | C 放手
    count: 5,                    // 每卷题量 3-10
    withReview: true             // 卷尾附自查讲评页
  };

  var config = {
    get: function () {
      var c = readRaw("config");
      var out = Object.assign({}, DEFAULT_CONFIG, (c && typeof c === "object") ? c : {});
      if (!Array.isArray(out.units) || !out.units.length) out.units = KNOWN_UNITS.slice();
      out.units = out.units.filter(function (u) { return KNOWN_UNITS.indexOf(u) >= 0; });
      if (!out.units.length) out.units = KNOWN_UNITS.slice();
      if (KNOWN_LEVELS.indexOf(String(out.level)) < 0) out.level = "mix";
      if (KNOWN_SCAFFOLDS.indexOf(out.scaffold) < 0) out.scaffold = "A";
      out.count = Math.min(10, Math.max(3, Number(out.count) || 5));
      out.withReview = !!out.withReview;
      return out;
    },
    set: function (partial) {
      var next = Object.assign({}, config.get(), partial || {});
      writeRaw("config", next);
      return next;
    }
  };

  /* ---------- 历史卷 ---------- */

  function normalizeSheets(arr) {
    if (!Array.isArray(arr)) return [];
    return arr.filter(function (s) {
      return s && s.id && Array.isArray(s.problems);
    }).map(function (s) {
      s.problems = s.problems.map(function (p) {
        p.marked = !!p.marked;
        return p;
      });
      return s;
    });
  }

  var sheets = {
    list: function () { return normalizeSheets(readRaw("sheets")); },
    get: function (id) {
      return sheets.list().find(function (s) { return s.id === id; }) || null;
    },
    // 存卷：补 id/no/date，超上限丢最旧
    add: function (sheet) {
      var all = sheets.list();
      var no = noNext();
      var rec = Object.assign({}, sheet, {
        id: uid(), no: no,
        date: localDate()
      });
      all.push(rec);
      while (all.length > MAX_SHEETS) all.shift();
      writeRaw("sheets", all);
      return rec;
    },
    remove: function (id) {
      writeRaw("sheets", sheets.list().filter(function (s) { return s.id !== id; }));
    },
    // 标记/取消错题
    mark: function (id, index, marked) {
      var all = sheets.list();
      var s = all.find(function (x) { return x.id === id; });
      if (!s || !s.problems[index]) return null;
      s.problems[index].marked = !!marked;
      writeRaw("sheets", all);
      return s;
    }
  };

  /* ---------- 卷号 ---------- */

  function noNext() {
    var n = Number(readRaw("no")) || 0;
    writeRaw("no", n + 1);
    return n + 1;
  }

  global.Store = {
    config: config,
    sheets: sheets,
    today: localDate,
    UNITS: KNOWN_UNITS,
    unitName: function (u) {
      return { volume: "升和毫升", division: "除法应用", strategy: "列表策略", average: "平均数" }[u] || u;
    }
  };
})(window);
