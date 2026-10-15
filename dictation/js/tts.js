/* =========================================================
 * tts.js · 语音播报适配层（架构契约实现）
 * 双引擎：api（LibreTTS 在线语音，默认）/ browser（浏览器内置 speechSynthesis）
 * 引擎选择读 Store.config.ttsEngine；api 失败自动降级 browser。
 * 契约见 docs/架构设计-v1.md 第 6 节
 * ========================================================= */
(function (global) {
  "use strict";

  /* ---------- api 引擎参数（LibreTTS 示例站） ---------- */
  var API_BASE = "https://libretts.is-an.org";
  var API_PATH = "/api/tts";
  var VOICE_ZH = "zh-CN-XiaoxiaoNeural";
  var VOICE_EN = "en-US-JennyNeural";
  var FETCH_TIMEOUT_MS = 15000;        // 单次合成超时
  var API_RETRY_COOLDOWN_MS = 60000;   // api 失败后的冷却期，期间直接走浏览器引擎
  var CACHE_MAX = 300;                 // 音频缓存上限（objectURL）

  var seqToken = 0;          // 序列播报取消令牌
  var curAudio = null;       // api 引擎当前播放的 Audio
  var apiDownUntil = 0;      // api 冷却截止时间戳
  var audioCache = new Map();
  var inflight = new Map();  // 进行中的合成请求（预取与播报去重）

  /* ---------- 引擎判定 ---------- */

  function currentEngine() {
    var e = null;
    try { if (global.Store && global.Store.config) e = global.Store.config.get().ttsEngine; } catch (err) {}
    return e === "browser" ? "browser" : "api";
  }
  function browserSupported() { return "speechSynthesis" in global; }
  function apiSupported() { return typeof global.Audio !== "undefined" && typeof global.fetch === "function"; }
  function supported() {
    return currentEngine() === "api" ? (apiSupported() || browserSupported()) : browserSupported();
  }

  /* ---------- 浏览器引擎 ---------- */

  function pickVoice(lang) {
    if (!browserSupported()) return null;
    var voices = global.speechSynthesis.getVoices();
    if (!voices || !voices.length) return null;
    var pref = String(lang || "zh-CN").slice(0, 2).toLowerCase();
    return voices.find(function (v) { return v.lang.toLowerCase().indexOf(pref) === 0; }) ||
           voices.find(function (v) { return /zh|中文|chinese/i.test(v.lang + v.name); }) ||
           null;
  }
  if (browserSupported()) {
    global.speechSynthesis.onvoiceschanged = function () {}; // 触发部分浏览器的 voices 加载
  }

  function speakBrowser(opts) {
    if (!browserSupported()) { if (opts.onEnd) opts.onEnd(); return false; }
    global.speechSynthesis.cancel();
    var u = new SpeechSynthesisUtterance(opts.text);
    u.lang = opts.lang || "zh-CN";
    u.rate = opts.rate || 0.9;
    u.pitch = 1.05;
    var v = pickVoice(u.lang);
    if (v) u.voice = v;
    if (opts.onEnd) u.onend = opts.onEnd;
    global.speechSynthesis.speak(u);
    return true;
  }

  /* ---------- api 引擎 ---------- */

  function voiceFor(lang) { return /^en/i.test(lang || "") ? VOICE_EN : VOICE_ZH; }
  // 本地倍速（0.6/0.9/1.2）→ LibreTTS 相对百分比（-100 ~ 100）
  function apiRate(rate) {
    var r = Math.round(((rate || 0.9) - 1) * 100);
    return Math.max(-100, Math.min(100, r));
  }
  function cacheKey(text, voice, rate) { return voice + "|" + rate + "|" + text; }

  // 取合成音频的 objectURL（带缓存）；失败 reject
  function fetchAudioUrl(text, voice, rate) {
    var key = cacheKey(text, voice, rate);
    var hit = audioCache.get(key);
    if (hit) return Promise.resolve(hit);
    var flying = inflight.get(key);
    if (flying) return flying;
    var ctl = new AbortController();
    var timer = setTimeout(function () { ctl.abort(); }, FETCH_TIMEOUT_MS);
    var p = global.fetch(API_BASE + API_PATH, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, voice: voice, rate: rate, pitch: 0, format: "mp3" }),
      signal: ctl.signal
    }).then(function (res) {
      clearTimeout(timer);
      if (!res.ok) throw new Error("tts http " + res.status);
      return res.blob();
    }).then(function (blob) {
      if (!blob || !blob.size) throw new Error("tts empty");
      if (audioCache.size >= CACHE_MAX) {
        audioCache.forEach(function (u) { URL.revokeObjectURL(u); });
        audioCache.clear();
      }
      var url = URL.createObjectURL(blob);
      audioCache.set(key, url);
      return url;
    }, function (err) { clearTimeout(timer); throw err; });
    inflight.set(key, p);
    p.then(function () { inflight.delete(key); }, function () { inflight.delete(key); });
    return p;
  }

  // 预取（后台静默，失败不影响流程）
  function prefetch(text, lang, rate) {
    if (!apiSupported() || Date.now() < apiDownUntil) return;
    fetchAudioUrl(text, voiceFor(lang), apiRate(rate)).catch(function () {});
  }

  function playUrl(url, onEnd) {
    var a = curAudio = new global.Audio(url);
    var done = false;
    function finish() {
      if (done) return;
      done = true;
      if (curAudio === a) curAudio = null;
      if (onEnd) onEnd();
    }
    a.onended = finish;
    a.onerror = finish; // 播放失败不阻塞流程
    var p = a.play();
    if (p && p.catch) p.catch(finish);
  }

  function speakApi(opts) {
    var token = seqToken;
    fetchAudioUrl(opts.text, voiceFor(opts.lang), apiRate(opts.rate))
      .then(function (url) {
        if (token !== seqToken) return; // 期间被 stop()
        playUrl(url, opts.onEnd);
      })
      .catch(function () {
        apiDownUntil = Date.now() + API_RETRY_COOLDOWN_MS; // 冷却期内整组降级
        if (token !== seqToken) return;
        if (browserSupported()) speakBrowser(opts);
        else if (opts.onEnd) opts.onEnd();
      });
    return true;
  }

  /* ---------- 统一入口 ---------- */

  /**
   * 播一个词/一句话
   * opts: { text, rate = 0.9, lang = "zh-CN", onEnd }
   */
  function speak(opts) {
    if (!opts || !opts.text) { if (opts && opts.onEnd) opts.onEnd(); return false; }
    if (currentEngine() === "api" && apiSupported() && Date.now() >= apiDownUntil) {
      return speakApi(opts);
    }
    return speakBrowser(opts);
  }

  /**
   * 按 repeat / gapSec 顺序播整组
   * items:  [{ text, lang? }]
   * opts:   { rate, repeat = 1, gapSec = 0, onEnd }
   */
  function speakSequence(items, opts) {
    opts = opts || {};
    var token = ++seqToken;
    var alive = function () { return token === seqToken; };
    var repeat = Math.max(1, opts.repeat || 1);
    var gapMs = Math.max(0, (opts.gapSec || 0)) * 1000;
    var i = 0, r = 0;

    function next() {
      if (!alive()) return;
      if (i >= items.length) { if (opts.onEnd) opts.onEnd(); return; }
      var item = items[i];
      speak({
        text: item.text,
        lang: item.lang || "zh-CN",
        rate: opts.rate,
        onEnd: function () {
          if (!alive()) return;
          r++;
          if (r < repeat) { next(); return; }
          r = 0;
          i++;
          // 播当前词的同时预取下一个词，掩盖网络延迟
          if (currentEngine() === "api" && i < items.length) {
            prefetch(items[i].text, items[i].lang, opts.rate);
          }
          if (i < items.length) setTimeout(next, gapMs);
          else if (opts.onEnd) opts.onEnd();
        }
      });
    }
    next();
  }

  function stop() {
    seqToken++;
    if (curAudio) {
      curAudio.onended = curAudio.onerror = null;
      curAudio.pause();
      curAudio = null;
    }
    if (browserSupported()) global.speechSynthesis.cancel();
  }
  function pause() {
    if (curAudio) curAudio.pause();
    if (browserSupported()) global.speechSynthesis.pause();
  }
  function resume() {
    if (curAudio) { var p = curAudio.play(); if (p && p.catch) p.catch(function () {}); }
    if (browserSupported()) global.speechSynthesis.resume();
  }

  global.TTS = {
    supported: supported,
    engine: currentEngine,
    speak: speak,
    speakSequence: speakSequence,
    stop: stop,
    pause: pause,
    resume: resume
  };
})(window);
