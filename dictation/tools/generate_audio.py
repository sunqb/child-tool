#!/usr/bin/env python3
"""预生成词库播报音频（LibreTTS 在线合成 → 本地 mp3）。

用法:  python3 tools/generate_audio.py
产物:  audio/zh/*.mp3  audio/en/*.mp3  audio/manifest.json
说明:  幂等增量——已存在且合法的音频直接跳过；失败项会在结尾汇总，重跑即可补齐。
       运行时（js/tts.js）以 manifest 为索引，命中播本地文件，未命中走在线合成。
"""
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANK = os.path.join(ROOT, "data", "wordbank.json")
OUT_DIR = os.path.join(ROOT, "audio")
API = "https://libretts.is-an.org/api/tts"
VOICES = {"zh": "zh-CN-XiaoxiaoNeural", "en": "en-US-JennyNeural"}
WORKERS = 4
RETRIES = 3
MIN_BYTES = 500  # 小于此体积视为异常响应
# 示例站经 Cloudflare，拦截 Python 默认 UA，需伪装浏览器
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

ILLEGAL = re.compile(r'[\\/:*?"<>|\s]+')


def safe_name(text, used):
    """词条 → 可读文件名（去非法字符；冲突时追加短哈希）
    used 按「小写名」登记：macOS 文件系统大小写不敏感，Art/art 会落同一文件，
    必须视为冲突，否则部署到大小写敏感的静态托管（Cloudflare Pages 等）会 404。"""
    base = ILLEGAL.sub("_", text).strip("._") or "x"
    base = base[:80]
    name = base
    if name.lower() in used and used[name.lower()] != text:
        name = base + "-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]
    used[name.lower()] = text
    return name


def collect_items():
    """遍历词库，收集 (text, lang) 去重集合：全部单词 + 英语词条的中文释义"""
    bank = json.load(open(BANK, encoding="utf-8"))
    items = set()
    for v in bank["versions"]:
        for g in v["grades"]:
            for s in g["subjects"]:
                lang = "en" if s["id"] == "english" else "zh"
                for u in s["units"]:
                    for w in u["words"]:
                        items.add((w["word"], lang))
                        if lang == "en" and w.get("meaning"):
                            items.add((w["meaning"], "zh"))
    return sorted(items)


def valid_mp3(data):
    if len(data) < MIN_BYTES:
        return False
    return data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)


def synthesize(text, lang):
    """合成一条，返回 mp3 bytes；重试 RETRIES 次后抛异常"""
    body = json.dumps({
        "text": text, "voice": VOICES[lang], "rate": 0, "pitch": 0, "format": "mp3"
    }).encode("utf-8")
    err = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(API, data=body,
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as res:
                ctype = res.headers.get("Content-Type", "")
                data = res.read()
            if res.status != 200 or "audio" not in ctype or not valid_mp3(data):
                raise ValueError("bad response: status=%s ctype=%s bytes=%d"
                                 % (res.status, ctype, len(data)))
            return data
        except Exception as e:  # noqa: BLE001 - 任何失败都重试
            err = e
            time.sleep(1 + attempt * 2)
    raise err


def main():
    items = collect_items()
    os.makedirs(os.path.join(OUT_DIR, "zh"), exist_ok=True)
    os.makedirs(os.path.join(OUT_DIR, "en"), exist_ok=True)

    used = {}  # 文件名冲突登记（按 lang 目录隔离）
    manifest, todo, failures = {}, [], []
    # 真实文件名集合：macOS 文件系统大小写不敏感，os.path.exists 会把
    # Australia/australia 误判为已存在而跳过，必须以目录列表的精确大小写为准
    real_files = set()
    for lang in ("zh", "en"):
        d = os.path.join(OUT_DIR, lang)
        real_files.update("%s/%s" % (lang, fn) for fn in os.listdir(d))
    # 大小写变体索引：Miss/miss 这类同词不同大小写共用同一音频文件
    # （发音完全一致；若各自生成，macOS 大小写不敏感 FS 上会落成同一文件导致索引悬空）
    lower_to_real = {r.lower(): r for r in real_files}
    for text, lang in items:
        name = safe_name(text, used.setdefault(lang, {}))
        rel = "%s/%s.mp3" % (lang, name)
        if rel not in real_files and rel.lower() in lower_to_real:
            rel = lower_to_real[rel.lower()]
        manifest["%s:%s" % (lang, text)] = rel
        if rel in real_files and os.path.getsize(os.path.join(OUT_DIR, rel)) >= MIN_BYTES:
            continue  # 增量跳过
        todo.append((text, lang, os.path.join(OUT_DIR, rel)))

    print("共 %d 条，待生成 %d 条（%d 条已存在跳过）"
          % (len(items), len(todo), len(items) - len(todo)))
    t0 = time.time()
    done = 0

    def work(job):
        text, lang, path = job
        data = synthesize(text, lang)
        with open(path, "wb") as f:
            f.write(data)
        return len(data)

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(work, j): j for j in todo}
        for fut in concurrent.futures.as_completed(futures):
            job = futures[fut]
            done += 1
            try:
                size = fut.result()
                if done % 50 == 0:
                    print("进度 %d/%d，用时 %.0fs" % (done, len(todo), time.time() - t0))
            except Exception as e:  # noqa: BLE001
                failures.append((job[0], job[1], str(e)))
                print("失败: [%s] %s -> %s" % (job[1], job[0], e))

    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=0, sort_keys=True)

    print("manifest.json 已写入，共 %d 条索引" % len(manifest))
    if failures:
        print("!! %d 条失败（重跑本脚本可增量补齐）:" % len(failures))
        for text, lang, err in failures:
            print("  - [%s] %s: %s" % (lang, text, err))
        sys.exit(1)
    print("全部完成，用时 %.0fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
