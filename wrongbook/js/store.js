/* =========================================================
 * store.js · 错题本数据访问层（架构契约实现）
 * IndexedDB（questions + images）+ localStorage 小配置（前缀 wb.）
 * 契约见 docs/架构设计-v1.md 第 4、5 节
 * IndexedDB 不可用时降级内存态，并通过 Store.storage.onDegrade 通知页面
 * ========================================================= */
(function (global) {
  "use strict";

  var PREFIX = "wb.";
  var DB_NAME = "child-tool-wrongbook";
  var DB_VERSION = 1;
  var BACKUP_APP = "child-tool/wrongbook";
  var BACKUP_SCHEMA = "1.0";
  var MAX_EXAM_HINTS = 50;
  var MAX_TAG_HINTS = 100;

  var mem = {};                 // localStorage 不可用时的内存兜底
  var degraded = false;         // IndexedDB 不可用，内存态
  var degradeListeners = [];
  var memQuestions = null;      // 内存态：Map<id, meta>
  var memImages = null;         // 内存态：Map<imageId, {blob,width,height,size}>
  var dbPromise = null;

  /* ---------- localStorage 小键（同 dictation 手法） ---------- */

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

  /* ---------- 工具 ---------- */

  function uid(prefix) {
    var rand = "";
    var chars = "abcdefghijklmnopqrstuvwxyz0123456789";
    for (var i = 0; i < 6; i++) rand += chars.charAt(Math.floor(Math.random() * chars.length));
    return prefix + "_" + Date.now() + "_" + rand;
  }

  function fail(e) {
    return { ok: false, error: (e && e.message) ? e.message : String(e) };
  }

  function attempt(fn) {
    try {
      return Promise.resolve(fn()).catch(function (e) { return fail(e); });
    } catch (e) {
      return Promise.resolve(fail(e));
    }
  }

  function reqP(req) {
    return new Promise(function (resolve, reject) {
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error || new Error("indexeddb request failed")); };
    });
  }

  function blobToBase64(blob) {
    return new Promise(function (resolve, reject) {
      var fr = new FileReader();
      fr.onload = function () {
        var s = String(fr.result);
        resolve(s.indexOf(",") >= 0 ? s.slice(s.indexOf(",") + 1) : s);
      };
      fr.onerror = function () { reject(fr.error || new Error("read file failed")); };
      fr.readAsDataURL(blob);
    });
  }

  function base64ToBlob(b64, type) {
    var bin = atob(b64);
    var arr = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return new Blob([arr], { type: type || "image/jpeg" });
  }

  function normalizeMeta(raw, imageId) {
    return {
      id: raw.id,
      subject: String(raw.subject || ""),
      exam: String(raw.exam || ""),
      tags: Array.isArray(raw.tags) ? raw.tags.map(function (t) { return String(t); }) : [],
      date: String(raw.date || ""),
      note: String(raw.note || ""),
      createdAt: Number(raw.createdAt) || Date.now(),
      imageId: imageId
    };
  }

  /* ---------- IndexedDB ---------- */

  function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise(function (resolve, reject) {
      var req;
      try {
        req = global.indexedDB.open(DB_NAME, DB_VERSION);
      } catch (e) { reject(e); return; }
      req.onupgradeneeded = function () {
        var db = req.result;
        if (!db.objectStoreNames.contains("questions")) {
          var qs = db.createObjectStore("questions", { keyPath: "id" });
          qs.createIndex("byDate", "date", { unique: false });
          qs.createIndex("bySubject", "subject", { unique: false });
        }
        if (!db.objectStoreNames.contains("images")) {
          db.createObjectStore("images", { keyPath: "imageId" });
        }
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error || new Error("indexeddb open failed")); };
      req.onblocked = function () { reject(new Error("indexeddb blocked")); };
    });
    return dbPromise;
  }

  function degrade() {
    if (degraded) return;
    degraded = true;
    memQuestions = new Map();
    memImages = new Map();
    degradeListeners.forEach(function (fn) {
      try { fn(); } catch (e) { /* 忽略通知异常 */ }
    });
  }

  // 启动即申请持久化存储，降低被浏览器自动清理的概率（架构 §4）
  try {
    if (global.navigator && global.navigator.storage && global.navigator.storage.persist) {
      global.navigator.storage.persist().catch(function () {});
    }
  } catch (e) { /* 不支持就忽略 */ }

  openDb().catch(degrade);

  // 统一后端入口：IndexedDB 就绪返回 db；已降级或打开失败返回 null（走内存态）
  function backendReady() {
    if (degraded) return Promise.resolve(null);
    return openDb().then(function (db) { return db; }, function () { degrade(); return null; });
  }

  /* ---------- 底层：两种后端（IndexedDB / 内存）统一签名 ---------- */

  // 写一道题（meta + 图片）
  function dbPutQuestion(meta, imageRec) {
    return backendReady().then(function (db) {
      if (!db) {
        memQuestions.set(meta.id, meta);
        memImages.set(imageRec.imageId, imageRec);
        return;
      }
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(["questions", "images"], "readwrite");
        tx.objectStore("images").put(imageRec);
        tx.objectStore("questions").put(meta);
        tx.oncomplete = function () { resolve(); };
        tx.onerror = function () { reject(tx.error || new Error("write failed")); };
        tx.onabort = function () { reject(tx.error || new Error("write aborted")); };
      });
    });
  }

  function dbAllMetas() {
    return backendReady().then(function (db) {
      if (!db) return Array.from(memQuestions.values());
      return reqP(db.transaction("questions", "readonly").objectStore("questions").getAll());
    });
  }

  function dbGetQuestion(id) {
    return backendReady().then(function (db) {
      if (!db) {
        var m = memQuestions.get(id) || null;
        if (!m) return null;
        return { meta: m, image: memImages.get(m.imageId) || null };
      }
      var qStore = db.transaction("questions", "readonly").objectStore("questions");
      return reqP(qStore.get(id)).then(function (meta) {
        if (!meta) return null;
        var iStore = db.transaction("images", "readonly").objectStore("images");
        return reqP(iStore.get(meta.imageId)).then(function (image) {
          return { meta: meta, image: image || null };
        });
      });
    });
  }

  function dbUpdateMeta(meta) {
    return backendReady().then(function (db) {
      if (!db) {
        if (!memQuestions.has(meta.id)) throw new Error("题目不存在");
        memQuestions.set(meta.id, meta);
        return;
      }
      return reqP(db.transaction("questions", "readwrite").objectStore("questions").put(meta));
    }).then(function () {});
  }

  function dbDeleteQuestion(id) {
    return backendReady().then(function (db) {
      if (!db) {
        var m = memQuestions.get(id);
        if (m) memImages.delete(m.imageId);
        memQuestions.delete(id);
        return;
      }
      return reqP(db.transaction("questions", "readonly").objectStore("questions").get(id))
        .then(function (meta) {
          return new Promise(function (resolve, reject) {
            var tx = db.transaction(["questions", "images"], "readwrite");
            tx.objectStore("questions").delete(id);
            if (meta && meta.imageId) tx.objectStore("images").delete(meta.imageId);
            tx.oncomplete = function () { resolve(); };
            tx.onerror = function () { reject(tx.error || new Error("delete failed")); };
            tx.onabort = function () { reject(tx.error || new Error("delete aborted")); };
          });
        });
    });
  }

  function dbAllImages() {
    return backendReady().then(function (db) {
      if (!db) {
        return Array.from(memImages.entries()).map(function (e) {
          return { imageId: e[0], blob: e[1].blob, width: e[1].width, height: e[1].height, size: e[1].size };
        });
      }
      return reqP(db.transaction("images", "readonly").objectStore("images").getAll());
    });
  }

  function dbClearAll() {
    return backendReady().then(function (db) {
      if (!db) {
        memQuestions.clear();
        memImages.clear();
        return;
      }
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(["questions", "images"], "readwrite");
        tx.objectStore("questions").clear();
        tx.objectStore("images").clear();
        tx.oncomplete = function () { resolve(); };
        tx.onerror = function () { reject(tx.error || new Error("clear failed")); };
        tx.onabort = function () { reject(tx.error || new Error("clear aborted")); };
      });
    });
  }

  /* ---------- 筛选（架构 §5） ---------- */

  function matchFilter(meta, filter) {
    if (!filter) return true;
    if (filter.subject && meta.subject !== filter.subject) return false;
    if (filter.exam && meta.exam !== filter.exam) return false;
    if (filter.tag && meta.tags.indexOf(filter.tag) < 0) return false;
    if (filter.from && meta.date < filter.from) return false;
    if (filter.to && meta.date > filter.to) return false;
    return true;
  }

  function sortMetas(arr) {
    return arr.sort(function (a, b) {
      if (a.date !== b.date) return a.date < b.date ? 1 : -1;
      return (b.createdAt || 0) - (a.createdAt || 0);
    });
  }

  /* ---------- 学科（localStorage） ---------- */

  var DEFAULT_SUBJECTS = ["语文", "数学", "英语"];

  var subjects = {
    get: function () {
      var s = readRaw("subjects");
      if (!Array.isArray(s) || !s.length) return DEFAULT_SUBJECTS.slice();
      return s.map(function (x) { return String(x); });
    },
    add: function (name) {
      var n = String(name || "").trim();
      if (!n) return { ok: false, reason: "empty" };
      var list = subjects.get();
      if (list.indexOf(n) >= 0) return { ok: true, subjects: list };
      list.push(n);
      writeRaw("subjects", list);
      return { ok: true, subjects: list };
    },
    remove: function (name) {
      var n = String(name || "").trim();
      var list = subjects.get();
      if (list.indexOf(n) < 0) return { ok: true, subjects: list };
      return attempt(function () {
        return dbAllMetas().then(function (metas) {
          var inUse = metas.some(function (m) { return m.subject === n; });
          if (inUse) return { ok: false, reason: "in-use" };
          var next = list.filter(function (s) { return s !== n; });
          writeRaw("subjects", next);
          return { ok: true, subjects: next };
        });
      });
    }
  };

  /* ---------- 题目 ---------- */

  var questions = {
    // Store.questions.add({ subject, exam, tags, date, note?, imageBlob, width, height })
    add: function (input) {
      return attempt(function () {
        if (!input || !input.imageBlob) throw new Error("缺少题目图片");
        var id = uid("q");
        var imageId = uid("img");
        var meta = normalizeMeta({
          id: id,
          subject: input.subject,
          exam: input.exam,
          tags: input.tags,
          date: input.date,
          note: input.note,
          createdAt: Date.now()
        }, imageId);
        var imageRec = {
          imageId: imageId,
          blob: input.imageBlob,
          width: Number(input.width) || 0,
          height: Number(input.height) || 0,
          size: input.imageBlob.size || 0
        };
        return dbPutQuestion(meta, imageRec).then(function () {
          return { ok: true, meta: meta };
        });
      });
    },

    // filter: { subject?, exam?, tag?, from?, to? }；按 date desc, createdAt desc
    list: function (filter) {
      return attempt(function () {
        return dbAllMetas().then(function (metas) {
          var arr = metas.filter(function (m) { return matchFilter(m, filter); });
          return { ok: true, metas: sortMetas(arr) };
        });
      });
    },

    get: function (id) {
      return attempt(function () {
        return dbGetQuestion(id).then(function (rec) {
          if (!rec) return { ok: false, reason: "not-found" };
          return { ok: true, meta: rec.meta, blob: rec.image ? rec.image.blob : null };
        });
      });
    },

    // 仅允许改 meta 字段：subject/exam/tags/date/note
    update: function (id, partial) {
      return attempt(function () {
        return dbGetQuestion(id).then(function (rec) {
          if (!rec) return { ok: false, reason: "not-found" };
          var next = normalizeMeta({
            id: rec.meta.id,
            subject: (partial && partial.subject !== undefined) ? partial.subject : rec.meta.subject,
            exam: (partial && partial.exam !== undefined) ? partial.exam : rec.meta.exam,
            tags: (partial && partial.tags !== undefined) ? partial.tags : rec.meta.tags,
            date: (partial && partial.date !== undefined) ? partial.date : rec.meta.date,
            note: (partial && partial.note !== undefined) ? partial.note : rec.meta.note,
            createdAt: rec.meta.createdAt
          }, rec.meta.imageId);
          return dbUpdateMeta(next).then(function () {
            return { ok: true, meta: next };
          });
        });
      });
    },

    remove: function (id) {
      return attempt(function () {
        return dbDeleteQuestion(id).then(function () {
          return { ok: true };
        });
      });
    },

    distinctExams: function (subject) {
      return attempt(function () {
        return dbAllMetas().then(function (metas) {
          var set = {};
          metas.forEach(function (m) {
            if (subject && m.subject !== subject) return;
            if (m.exam) set[m.exam] = true;
          });
          return { ok: true, exams: Object.keys(set).sort() };
        });
      });
    },

    distinctTags: function (subject) {
      return attempt(function () {
        return dbAllMetas().then(function (metas) {
          var set = {};
          metas.forEach(function (m) {
            if (subject && m.subject !== subject) return;
            (m.tags || []).forEach(function (t) { if (t) set[t] = true; });
          });
          return { ok: true, tags: Object.keys(set).sort() };
        });
      });
    }
  };

  /* ---------- 录入历史记忆（localStorage） ---------- */

  function pushHint(key, names, max) {
    var list = readRaw(key);
    if (!Array.isArray(list)) list = [];
    names.forEach(function (n) {
      n = String(n || "").trim();
      if (!n) return;
      if (list.indexOf(n) >= 0) return;
      list.unshift(n);
    });
    list = list.slice(0, max);
    writeRaw(key, list);
    return list;
  }

  var hints = {
    exams: function () {
      var list = readRaw("hints.exams");
      return Array.isArray(list) ? list.map(function (x) { return String(x); }) : [];
    },
    addExam: function (name) {
      pushHint("hints.exams", [name], MAX_EXAM_HINTS);
      return { ok: true };
    },
    tags: function () {
      var list = readRaw("hints.tags");
      return Array.isArray(list) ? list.map(function (x) { return String(x); }) : [];
    },
    addTags: function (names) {
      pushHint("hints.tags", Array.isArray(names) ? names : [], MAX_TAG_HINTS);
      return { ok: true };
    }
  };

  /* ---------- 打印设置（localStorage） ---------- */

  var DEFAULT_PRINT = { perPage: 2, blankCm: 3, showTags: true, showMeta: true };

  var printSettings = {
    get: function () {
      var p = readRaw("print.settings");
      var out = Object.assign({}, DEFAULT_PRINT, (p && typeof p === "object") ? p : {});
      if ([1, 2, 4].indexOf(out.perPage) < 0) out.perPage = 2;
      if ([0, 3, 6, 9].indexOf(out.blankCm) < 0) out.blankCm = 3;
      out.showTags = !!out.showTags;
      out.showMeta = !!out.showMeta;
      return out;
    },
    set: function (partial) {
      var next = Object.assign({}, printSettings.get(), partial || {});
      writeRaw("print.settings", next);
      return next;
    }
  };

  /* ---------- 备份 ---------- */

  function doExportDownload() {
    return Promise.all([dbAllMetas(), dbAllImages()]).then(function (r) {
      var metas = sortMetas(r[0].slice());
      var imageMap = {};
      r[1].forEach(function (img) { imageMap[img.imageId] = img; });
      return Promise.all(metas.map(function (m) {
        var img = imageMap[m.imageId];
        if (!img || !img.blob) return Promise.resolve(null);
        return blobToBase64(img.blob).then(function (b64) {
          return {
            imageId: m.imageId,
            mime: img.blob.type || "image/jpeg",
            width: img.width, height: img.height, size: img.size,
            dataBase64: b64
          };
        });
      })).then(function (images) {
        var payload = {
          app: BACKUP_APP,
          schemaVersion: BACKUP_SCHEMA,
          exportedAt: new Date().toISOString(),
          subjects: subjects.get(),
          questions: metas,
          images: images.filter(Boolean)
        };
        var blob = new Blob([JSON.stringify(payload)], { type: "application/json" });
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        var d = new Date();
        var pad = function (x) { return (x < 10 ? "0" : "") + x; };
        a.href = url;
        a.download = "wrongbook-backup-" + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + ".json";
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 5000);
        return { ok: true, count: payload.questions.length };
      });
    });
  }

  var backup = {
    export: function () {
      return attempt(doExportDownload);
    },

    // 校验后全量覆盖导入；导入前自动导出一份"导入前备份"
    import: function (file) {
      return attempt(function () {
        return Promise.resolve(file.text ? file.text() : new Promise(function (res, rej) {
          var fr = new FileReader();
          fr.onload = function () { res(String(fr.result)); };
          fr.onerror = function () { rej(fr.error || new Error("read file failed")); };
          fr.readAsText(file);
        })).then(function (text) {
          var data;
          try { data = JSON.parse(text); } catch (e) { throw new Error("备份文件不是有效 JSON"); }
          if (!data || data.app !== BACKUP_APP) throw new Error("备份文件不属于错题本");
          var ver = String(data.schemaVersion || "");
          if (ver !== "1" && ver.indexOf("1.") !== 0) throw new Error("备份版本不兼容：" + ver);
          if (!Array.isArray(data.questions)) throw new Error("备份文件缺少题目数据");

          // 导入前自动备份（尽力而为，失败不阻断导入）
          return doExportDownload().catch(function () {}).then(function () {
            var images = Array.isArray(data.images) ? data.images : [];
            var imageMap = {};
            images.forEach(function (img) { imageMap[img.imageId] = img; });

            var metas = [];
            var imageRecs = [];
            var skipped = 0;
            data.questions.forEach(function (raw) {
              if (!raw || !raw.id || !raw.imageId) { skipped++; return; }
              var img = imageMap[raw.imageId];
              if (!img || !img.dataBase64) { skipped++; return; }
              var blob;
              try {
                blob = base64ToBlob(img.dataBase64, img.mime || "image/jpeg");
              } catch (e) { skipped++; return; }
              var meta = normalizeMeta(raw, raw.imageId);
              metas.push(meta);
              imageRecs.push({
                imageId: raw.imageId,
                blob: blob,
                width: Number(img.width) || 0,
                height: Number(img.height) || 0,
                size: blob.size || Number(img.size) || 0
              });
            });

            return dbClearAll().then(function () {
              var chain = Promise.resolve();
              metas.forEach(function (meta, i) {
                chain = chain.then(function () { return dbPutQuestion(meta, imageRecs[i]); });
              });
              return chain.then(function () {
                if (Array.isArray(data.subjects) && data.subjects.length) {
                  var list = data.subjects.map(function (s) { return String(s); }).filter(Boolean);
                  var merged = subjects.get().concat(list.filter(function (s) {
                    return subjects.get().indexOf(s) < 0;
                  }));
                  writeRaw("subjects", merged);
                }
                return { ok: true, imported: metas.length, skipped: skipped };
              });
            });
          });
        });
      });
    }
  };

  /* ---------- 存储状态（降级通知） ---------- */

  var storage = {
    degraded: function () { return degraded; },
    onDegrade: function (fn) {
      if (typeof fn === "function") degradeListeners.push(fn);
      if (degraded) { try { fn(); } catch (e) {} }
    }
  };

  global.Store = {
    subjects: subjects,
    questions: questions,
    hints: hints,
    backup: backup,
    printSettings: printSettings,
    storage: storage
  };
})(window);
