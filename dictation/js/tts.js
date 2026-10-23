/* =========================================================
 * tts.js · 语音播报适配层（架构契约实现）
 * 双引擎：api（默认）/ browser（浏览器内置 speechSynthesis）
 * api 引擎三级音源，逐级降级：
 *   1. 本地预生成音频 audio/（内置词库全覆盖，零网络零延迟）
 *   2. LibreTTS 在线合成（自建词库新词；带缓存/预取/去重）
 *   3. 浏览器内置语音（断网/合成失败/播放失败兜底）
 * 引擎选择读 Store.config.ttsEngine。契约见 docs/架构设计-v1.md 第 6 节
 * ========================================================= */
(function (global) {
  "use strict";

  /* ---------- api 引擎参数 ---------- */
  var API_BASE = "https://libretts.is-an.org";
  var API_PATH = "/api/tts";
  var VOICE_ZH = "zh-CN-XiaoxiaoNeural";
  var VOICE_EN = "en-US-JennyNeural";
  // 预生成音频目录（tools/generate_audio.py 产物）；其他目录的页面（如 flashcard/）
  // 可在加载本脚本前设 window.TTS_AUDIO_BASE = "../dictation/audio/" 复用同一套音频
  var AUDIO_BASE = global.TTS_AUDIO_BASE || "audio/";
  var MANIFEST_URL = AUDIO_BASE + "manifest.json";
  var FETCH_TIMEOUT_MS = 15000;        // 单次合成超时
  var API_RETRY_COOLDOWN_MS = 60000;   // 在线合成失败后的冷却期，期间直接走浏览器引擎
  var CACHE_MAX = 300;                 // 在线合成音频缓存上限（objectURL）

  var seqToken = 0;          // 序列播报取消令牌
  var curAudio = null;       // api 引擎当前播放的 Audio
  var apiDownUntil = 0;      // 在线合成冷却截止时间戳
  var audioCache = new Map();
  var inflight = new Map();  // 进行中的合成请求（预取与播报去重）

  /* ---------- 预生成音频索引（加载一次，缺失时静默降级在线合成） ---------- */

  var manifestPromise = null;
  function loadManifest() {
    if (!manifestPromise) {
      manifestPromise = global.fetch(MANIFEST_URL, { cache: "no-cache" })
        .then(function (res) { return res.ok ? res.json() : null; })
        .catch(function () { return null; });
    }
    return manifestPromise;
  }
  if (apiSupported()) loadManifest(); // 页面加载即开始拉索引
  function localKey(text, lang) { return (/^en/i.test(lang || "") ? "en:" : "zh:") + text; }

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

  /* ---------- 在线合成（LibreTTS 示例站） ---------- */

  function voiceFor(lang) { return /^en/i.test(lang || "") ? VOICE_EN : VOICE_ZH; }
  // 本地倍速（0.6/0.9/1.2）→ LibreTTS 相对百分比（-100 ~ 100）
  function apiRate(rate) {
    var r = Math.round(((rate || 0.9) - 1) * 100);
    return Math.max(-100, Math.min(100, r));
  }
  function cacheKey(text, voice, rate) { return voice + "|" + rate + "|" + text; }

  // 在线合成取 objectURL（带缓存/在途去重）；失败 reject
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
      // 示例站异常时可能 200 但返回非音频内容，必须校验
      var ctype = res.headers.get("Content-Type") || "";
      if (!res.ok || ctype.indexOf("audio") !== 0) throw new Error("tts bad response " + res.status + " " + ctype);
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

  // 预取下一条（后台静默；本地音频命中时无网络开销）
  function prefetch(text, lang, rate) {
    if (!apiSupported()) return;
    loadManifest().then(function (manifest) {
      if (manifest && manifest[localKey(text, lang)]) return; // 本地已有，无需合成
      if (Date.now() < apiDownUntil) return;
      fetchAudioUrl(text, voiceFor(lang), apiRate(rate)).catch(function () {});
    });
  }

  /* ---------- 播放 ---------- */

  // playbackRate 仅用于本地预生成音频（在线合成的语速已烘焙进音频）
  function playUrl(url, playbackRate, onEnd, onError) {
    var a = curAudio = new global.Audio(url);
    var done = false;
    function finish(isError) {
      if (done) return;
      done = true;
      if (curAudio === a) curAudio = null;
      if (isError && onError) onError();
      else if (onEnd) onEnd();
    }
    if (playbackRate && playbackRate !== 1) {
      a.playbackRate = playbackRate;
      if ("preservesPitch" in a) a.preservesPitch = true;
    }
    a.onended = function () { finish(false); };
    a.onerror = function () { finish(true); }; // 播放失败走降级，不静默跳过
    var p = a.play();
    if (p && p.catch) p.catch(function () { finish(true); });
  }

  /* ---------- api 引擎主流程 ---------- */

  function speakApi(opts) {
    var token = seqToken;
    var rate = opts.rate || 0.9;
    loadManifest().then(function (manifest) {
      if (token !== seqToken) return; // 期间被 stop()
      var rel = manifest && manifest[localKey(opts.text, opts.lang)];
      if (rel) {
        // 1) 本地预生成音频：零网络，语速用 playbackRate 实现
        playUrl(AUDIO_BASE + rel, rate, opts.onEnd, function () {
          if (token === seqToken) fallbackBrowser(opts, token);
        });
        return;
      }
      // 2) 在线合成（自建词库新词等）
      if (Date.now() < apiDownUntil) { fallbackBrowser(opts, token); return; }
      fetchAudioUrl(opts.text, voiceFor(opts.lang), apiRate(rate))
        .then(function (url) {
          if (token !== seqToken) return;
          playUrl(url, 1, opts.onEnd, function () {
            if (token === seqToken) fallbackBrowser(opts, token);
          });
        })
        .catch(function () {
          apiDownUntil = Date.now() + API_RETRY_COOLDOWN_MS; // 冷却期内整组降级
          if (token === seqToken) fallbackBrowser(opts, token);
        });
    });
    return true;
  }

  // 3) 浏览器内置语音兜底
  function fallbackBrowser(opts, token) {
    if (token !== seqToken) return;
    if (browserSupported()) speakBrowser(opts);
    else if (opts.onEnd) opts.onEnd();
  }

  /* ---------- 统一入口 ---------- */

  /**
   * 播一个词/一句话
   * opts: { text, rate = 0.9, lang = "zh-CN", onEnd }
   */
  function speak(opts) {
    if (!opts || !opts.text) { if (opts && opts.onEnd) opts.onEnd(); return false; }
    if (currentEngine() === "api" && apiSupported()) {
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
          // 播当前词的同时预取下一个词，掩盖在线合成延迟
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
