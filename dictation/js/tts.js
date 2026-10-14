/* =========================================================
 * tts.js · 语音播报适配层（架构契约实现）
 * 统一封装 speechSynthesis；未来换云端 TTS 只改这里。
 * 契约见 docs/架构设计-v1.md 第 6 节
 * ========================================================= */
(function (global) {
  "use strict";

  var seqToken = 0; // 序列播报取消令牌

  function supported() { return "speechSynthesis" in global; }

  function pickVoice(lang) {
    if (!supported()) return null;
    var voices = global.speechSynthesis.getVoices();
    if (!voices || !voices.length) return null;
    var pref = String(lang || "zh-CN").slice(0, 2).toLowerCase();
    return voices.find(function (v) { return v.lang.toLowerCase().indexOf(pref) === 0; }) ||
           voices.find(function (v) { return /zh|中文|chinese/i.test(v.lang + v.name); }) ||
           null;
  }
  if (supported()) {
    global.speechSynthesis.onvoiceschanged = function () {}; // 触发部分浏览器的 voices 加载
  }

  /**
   * 播一个词/一句话
   * opts: { text, rate = 0.9, lang = "zh-CN", onEnd }
   */
  function speak(opts) {
    if (!supported() || !opts || !opts.text) {
      if (opts && opts.onEnd) opts.onEnd();
      return false;
    }
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

  /**
   * 按 repeat / gapSec 顺序播整组
   * items:  [{ text, lang? }]
   * opts:   { rate, repeat = 1, gapSec = 0, onEnd }
   */
  function speakSequence(items, opts) {
    opts = opts || {};
    var token = ++seqToken;
    var alive = function () { return token === seqToken && supported(); };
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
          if (i < items.length) setTimeout(next, gapMs);
          else if (opts.onEnd) opts.onEnd();
        }
      });
    }
    next();
  }

  function stop() {
    seqToken++;
    if (supported()) global.speechSynthesis.cancel();
  }
  function pause() { if (supported()) global.speechSynthesis.pause(); }
  function resume() { if (supported()) global.speechSynthesis.resume(); }

  global.TTS = { supported: supported, speak: speak, speakSequence: speakSequence, stop: stop, pause: pause, resume: resume };
})(window);
