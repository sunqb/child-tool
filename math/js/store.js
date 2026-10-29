/* =========================================================
 * store.js · 审题雷达数据访问层
 * localStorage 前缀 math.；不可用时内存兜底（同 dictation 手法）
 * 契约见 docs/架构设计-v1.md 第 4、5 节
 * ========================================================= */
(function (global) {
  "use strict";

  var PREFIX = "math.";
  var MAX_SHEETS = 30;
  var KNOWN_UNITS = ["division", "periarea", "mulrel", "mixed2", "bignum", "bigcalc", "average"];
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
    subject: "math",             // 学科（catalog.subjects）
    version: "sujiao2022",           // 教材版本（catalog.versions）
    grade: "g4s",                // 年级（catalog.grades）
    units: KNOWN_UNITS.slice(),  // 练哪些单元
    chapters: {},                // unitId -> [chapterId]；缺键/空数组 = 该单元全部章节
    level: "mix",                // mix | 1 | 2 | 3
    scaffold: "A",               // A 全程扶 | B 半扶 | C 放手
    count: 5,                    // 每卷题量 3-10
    withReview: true             // 卷尾附自查讲评页
  };

  // 单元白名单：catalog 加载后由页面端注入（含新增单元）；注入前用内置默认
  var catalogUnits = null;

  var config = {
    get: function () {
      var known = catalogUnits || KNOWN_UNITS;
      var c = readRaw("config");
      var out = Object.assign({}, DEFAULT_CONFIG, (c && typeof c === "object") ? c : {});
      if (typeof out.subject !== "string" || !out.subject) out.subject = DEFAULT_CONFIG.subject;
      if (typeof out.version !== "string" || !out.version) out.version = DEFAULT_CONFIG.version;
      if (typeof out.grade !== "string" || !out.grade) out.grade = DEFAULT_CONFIG.grade;
      if (!Array.isArray(out.units) || !out.units.length) out.units = known.slice();
      out.units = out.units.filter(function (u) { return known.indexOf(u) >= 0; });
      if (!out.units.length) out.units = known.slice();
      // chapters 只保留「已知单元 + 字符串数组」；与 catalog 的深层对齐在页面端 normalizeSelection 做
      var ch = {};
      if (out.chapters && typeof out.chapters === "object" && !Array.isArray(out.chapters)) {
        Object.keys(out.chapters).forEach(function (k) {
          if (known.indexOf(k) < 0) return;
          var arr = Array.isArray(out.chapters[k])
            ? out.chapters[k].filter(function (x) { return typeof x === "string" && x; })
            : [];
          if (arr.length) ch[k] = arr;
        });
      }
      out.chapters = ch;
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
    setCatalogUnits: function (ids) {
      if (Array.isArray(ids) && ids.length) catalogUnits = ids.slice();
    },
    unitName: function (u) {
      return { division: "一 两、三位数除以两位数", periarea: "二 周长与面积", mulrel: "三 乘法数量关系", mixed2: "四 混合运算与数量关系（二）", bignum: "五 多位数的认识", bigcalc: "六 大数的运算", average: "平均数（四下）" }[u] || u;
    }
  };
})(window);
