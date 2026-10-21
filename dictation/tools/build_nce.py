#!/usr/bin/env python3
"""新概念英语第一册词库构建（一次性）。

数据源: github.com/LiDuoMiao/new-concept-english notes/NCE1/*.json（72 个课次对文件）
        与 github.com/720info/nce1-dictation data/words.json 交叉一致（同一上游）。
产物:   data/parts/yingyu-nce.json + 合入 data/wordbank.json（version id: nce）
用法:   python3 tools/build_nce.py /tmp/nce1_notes
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARTS = os.path.join(ROOT, "data", "parts", "yingyu-nce.json")
BANK = os.path.join(ROOT, "data", "wordbank.json")

# NCE1 奇数课标题（1997 新版沿用至今的定名）；单元 = 奇偶课对，标题取奇数课
TITLES = {
    1: "Excuse me!", 3: "Sorry, sir.", 5: "Nice to meet you.", 7: "Are you a teacher?",
    9: "How are you today?", 11: "Is this your shirt?", 13: "A new dress",
    15: "Your passports, please.", 17: "How do you do?", 19: "Tired and thirsty",
    21: "Which book?", 23: "Which glasses?", 25: "Mrs. Smith's kitchen",
    27: "Mrs. Smith's living room", 29: "Come in, Amy.", 31: "Where's Sally?",
    33: "A fine day", 35: "Our village", 37: "Making a bookcase", 39: "Don't drop it!",
    41: "Penny's bag", 43: "Hurry up!", 45: "The boss's letter", 47: "A cup of coffee",
    49: "At the butcher's", 51: "A pleasant climate", 53: "An interesting climate",
    55: "The Sawyer family", 57: "An unusual day", 59: "Is that all?", 61: "A bad cold",
    63: "Thank you, doctor.", 65: "Not a baby", 67: "The weekend", 69: "The car race",
    71: "He's awful!", 73: "The way to King Street", 75: "Uncomfortable shoes",
    77: "Terrible toothache", 79: "Carol's shopping list", 81: "Roast beef and potatoes",
    83: "Going on holiday", 85: "Paris in the spring", 87: "A car crash", 89: "For sale",
    91: "Poor Ian!", 93: "Our new neighbour", 95: "Tickets, please.",
    97: "A small blue case", 99: "Owl!", 101: "A card from Jimmy", 103: "The French test",
    105: "Full of mistakes", 107: "It's too small.", 109: "A good idea",
    111: "The most expensive model", 113: "Small change", 115: "Knock, knock!",
    117: "Tommy's breakfast", 119: "A true story", 121: "The man in a hat",
    123: "A trip to Australia", 125: "Tea for two", 127: "A famous actress",
    129: "Seventy miles an hour", 131: "Don't be so sure!", 133: "Sensational news!",
    135: "The latest report", 137: "A pleasant dream", 139: "Is that you, John?",
    141: "Sally's first train ride", 143: "A walk through the woods"
}


def build_units(notes_dir):
    units = []
    for path in sorted(glob.glob(os.path.join(notes_dir, "*.json"))):
        d = json.load(open(path, encoding="utf-8"))
        l1, l2 = (int(x) for x in d["lesson"].split("-"))
        seen, words = set(), []
        for v in d["vocabulary"]:
            w = v["word"].strip()
            if not w or w.lower() in seen:
                continue
            seen.add(w.lower())
            meaning = (v.get("meanings") or [{}])[0].get("meaning", "").strip()
            words.append({"word": w, "meaning": meaning})
        if not words:
            raise SystemExit("空词表: " + path)
        units.append({
            "id": "l%03d-%03d" % (l1, l2),
            "name": "Lesson %d-%d · %s" % (l1, l2, TITLES[l1]),
            "words": words
        })
    if len(units) != 72:
        raise SystemExit("课次数不符: %d != 72" % len(units))
    return units


def main():
    notes_dir = sys.argv[1] if len(sys.argv) > 1 else "/tmp/nce1_notes"
    units = build_units(notes_dir)
    grade = {
        "id": "nce-1", "name": "第一册",
        "subjects": [{"id": "english", "name": "英语", "units": units}]
    }

    # parts 源文件（词条一行一个，与既有 parts 风格一致）
    def one_line(obj, level=0):
        """词条对象压成单行，其余按 indent 展开"""
        if isinstance(obj, dict) and set(obj.keys()) <= {"word", "meaning", "tip"}:
            return "{ " + ", ".join(json.dumps(v, ensure_ascii=False)
                                    for v in obj.values()) + " }"
        return None

    part = {"grades": [grade]}
    text = json.dumps(part, ensure_ascii=False, indent=2)
    # 压缩词条为多行 JSON 中的一行对象
    import re
    text = re.sub(
        r'\{\s*\n\s*"word": (".*?"),\n\s*"meaning": (".*?")\n\s*\}',
        r'{ "word": \1, "meaning": \2 }', text)
    with open(PARTS, "w", encoding="utf-8") as f:
        f.write(text + "\n")

    # 合入 wordbank.json：幂等，nce 已存在则整版本替换
    bank = json.load(open(BANK, encoding="utf-8"))
    version = {"id": "nce", "name": "新概念英语", "grades": [grade]}
    bank["versions"] = [v for v in bank["versions"] if v["id"] != "nce"] + [version]
    bank["meta"]["updatedAt"] = "2026-10-21"
    with open(BANK, "w", encoding="utf-8") as f:
        json.dump(bank, f, ensure_ascii=False, indent=2)
        f.write("\n")

    n = sum(len(u["words"]) for u in units)
    uniq = len({w["word"].lower() for u in units for w in u["words"]})
    print("完成: 72 单元, %d 词条（去重后 %d）" % (n, uniq))
    print("parts ->", PARTS)
    print("wordbank 版本:", [v["id"] for v in bank["versions"]])


if __name__ == "__main__":
    main()
