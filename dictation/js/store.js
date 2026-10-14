/* =========================================================
 * store.js · 数据访问层（架构契约实现）
 * 所有状态统一走 Store，不散写 localStorage；前缀 dict.
 * 契约见 docs/架构设计-v1.md 第 4、5 节
 * ========================================================= */
(function (global) {
  "use strict";

  var PREFIX = "dict.";
  var mem = {}; // localStorage 不可用（隐私模式等）时的内存兜底

  function readRaw(key) {
    try {
      var raw = global.localStorage.getItem(PREFIX + key);
      return raw == null ? undefined : JSON.parse(raw);
    } catch (e) {
      return mem[key];
    }
  }
  function writeRaw(key, val) {
    mem[key] = val;
    try {
      global.localStorage.setItem(PREFIX + key, JSON.stringify(val));
    } catch (e) { /* 降级为内存态 */ }
  }
  function removeRaw(key) {
    delete mem[key];
    try { global.localStorage.removeItem(PREFIX + key); } catch (e) {}
  }

  /* ---------- 词库 ---------- */

  // 加载失败时的内置兜底单元（架构 v1 §7），保证学生端永远能跑
  var FALLBACK_WORDS = [
    { word: "颜料", tip: "绘画用的色彩材料" },
    { word: "邮票", tip: "寄信贴的凭证" },
    { word: "飘舞", tip: "随风飞舞" },
    { word: "紧张", tip: "精神处于紧绷状态" },
    { word: "争取", tip: "努力获得" },
    { word: "粗壮", tip: "粗大而结实" },
    { word: "排列", tip: "按顺序摆放" },
    { word: "规则", tip: "共同遵守的章程" }
  ];

  function fallbackBank() {
    return {
      meta: { schemaVersion: "1.0", fallback: true },
      versions: [{
        id: "__fallback", name: "内置示范词库",
        grades: [{
          id: "__g", name: "示范", semester: "",
          subjects: [{
            id: "chinese", name: "语文",
            units: [{ id: "__u", name: "示范单元 · 词语表", words: FALLBACK_WORDS }]
          }]
        }]
      }]
    };
  }

  // 极简结构校验（正式校验以 data/wordbank.schema.json + CI 为准）
  function validateBank(bank) {
    function need(cond) { if (!cond) throw new Error("wordbank invalid"); }
    need(bank && Array.isArray(bank.versions) && bank.versions.length);
    bank.versions.forEach(function (v) {
      need(v.id && Array.isArray(v.grades) && v.grades.length);
      v.grades.forEach(function (g) {
        need(Array.isArray(g.subjects) && g.subjects.length);
        g.subjects.forEach(function (s) {
          need(Array.isArray(s.units) && s.units.length);
          s.units.forEach(function (u) {
            need(Array.isArray(u.words) && u.words.length &&
                 u.words.every(function (w) { return w && typeof w.word === "string"; }));
          });
        });
      });
    });
    return bank;
  }

  var bankCache = null;

  var banks = {
    // 加载词库（静态 JSON，内存缓存）；失败自动降级内置兜底单元
    load: function () {
      if (bankCache) return Promise.resolve(bankCache);
      return fetch("data/wordbank.json", { cache: "no-cache" })
        .then(function (res) {
          if (!res.ok) throw new Error("wordbank http " + res.status);
          return res.json();
        })
        .then(validateBank)
        .catch(function () { return fallbackBank(); })
        .then(function (bank) { bankCache = bank; return bank; });
    },
    // 供选择器渲染；需先 await load()（缓存后同步可用）
    list: function () { return bankCache; },
    // 按四级取单元词；选择模型为 年级→学科→版本→单元，
    // 优先在包含该年级的版本里找 versionId，id 无效时逐级回退，保证有词可默
    pick: function (versionId, gradeId, subjectId, unitId) {
      var bankData = bankCache;
      if (!bankData) throw new Error("Store.banks.load() 未完成");
      var pool = bankData.versions.filter(function (v) {
        return v.grades.some(function (g) { return g.id === gradeId; });
      });
      if (!pool.length) pool = bankData.versions;
      var version = pool.find(function (v) { return v.id === versionId; }) || pool[0];
      var grade = version.grades.find(function (g) { return g.id === gradeId; }) || version.grades[0];
      var subject = grade.subjects.find(function (s) { return s.id === subjectId; }) || grade.subjects[0];
      var unit = subject.units.find(function (u) { return u.id === unitId; }) || subject.units[0];
      return {
        version: version, grade: grade, subject: subject, unit: unit,
        words: unit.words.map(function (w) { return Object.assign({}, w); })
      };
    }
  };

  /* ---------- 家长配置 ---------- */

  var DEFAULT_CONFIG = { speed: 0.9, repeat: 2, gapSec: 5, withMeaning: false };

  var config = {
    get: function () {
      var c = readRaw("parent.config");
      return Object.assign({}, DEFAULT_CONFIG, (c && typeof c === "object") ? c : {});
    },
    set: function (partial) {
      var next = Object.assign({}, config.get(), partial || {});
      writeRaw("parent.config", next);
      return next;
    }
  };

  var selection = {
    get: function () { return readRaw("parent.selection") || null; },
    set: function (sel) { writeRaw("parent.selection", sel); return sel; }
  };

  /* ---------- 默写会话 ---------- */

  var session = {
    get: function () { return readRaw("session.current") || null; },
    set: function (s) { writeRaw("session.current", s); return s; },
    clear: function () { removeRaw("session.current"); }
  };

  /* ---------- 错词本 ---------- */

  function wordKey(subject, word) { return subject + ":" + word; }

  var wrongbook = {
    // 错词入本：同词 wrongCount +1，保留 tip/meaning
    add: function (entry, subject) {
      var book = readRaw("wrongbook") || {};
      var key = wordKey(subject, entry.word);
      var prev = book[key];
      book[key] = {
        word: entry.word,
        tip: entry.tip,
        meaning: entry.meaning,
        subject: subject,
        wrongCount: (prev ? prev.wrongCount : 0) + 1,
        lastWrongAt: Date.now()
      };
      writeRaw("wrongbook", book);
    },
    list: function (subject) {
      var book = readRaw("wrongbook") || {};
      var arr = Object.keys(book).map(function (k) { return book[k]; });
      if (subject) arr = arr.filter(function (e) { return e.subject === subject; });
      return arr.sort(function (a, b) { return b.lastWrongAt - a.lastWrongAt; });
    },
    remove: function (key) {
      var book = readRaw("wrongbook") || {};
      delete book[key];
      writeRaw("wrongbook", book);
    },
    clear: function () { removeRaw("wrongbook"); }
  };

  /* ---------- 自定义词库（架构契约 dict.custom.banks，现为 P0） ---------- */

  var custom = {
    list: function () { return readRaw("custom.banks") || []; },
    get: function (id) {
      return custom.list().find(function (b) { return b.id === id; }) || null;
    },
    // words: string[]；返回新建的词库
    add: function (name, words) {
      var banks = custom.list();
      var bank = {
        id: "cb" + Date.now().toString(36),
        name: String(name || "").trim(),
        words: words.map(function (w) { return String(w).trim(); }).filter(Boolean)
      };
      banks.push(bank);
      writeRaw("custom.banks", banks);
      return bank;
    },
    remove: function (id) {
      writeRaw("custom.banks", custom.list().filter(function (b) { return b.id !== id; }));
    }
  };

  /* ---------- 判分（纯函数） ---------- */

  var judge = {
    // 语文：trim 后严格相等；英语：trim + 忽略大小写。
    // subject 可省略：按词面是否纯 ASCII 自动判断。
    equals: function (input, word, subject) {
      var a = String(input == null ? "" : input).trim();
      var b = String(word == null ? "" : word).trim();
      if (subject === "english") return a.toLowerCase() === b.toLowerCase();
      if (subject === "chinese") return a === b;
      return /^[\x21-\x7e]+$/.test(b) ? a.toLowerCase() === b.toLowerCase() : a === b;
    }
  };

  global.Store = {
    banks: banks,
    config: config,
    selection: selection,
    session: session,
    wrongbook: wrongbook,
    custom: custom,
    judge: judge,
    wordKey: wordKey
  };
})(window);
