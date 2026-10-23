#!/usr/bin/env python3
"""enrich_ipa.py · 为词库全部英语单词生成 IPA，产物 flashcard/data/ipa.json

数据源：dictation/data/wordbank.json（唯一事实源，不改写原文件）。
转换：eng-to-ipa（美式）→ 英式 DJ 常用写法归一化（教材普遍印 DJ）：
  1. oʊ → əʊ（GOAT 元音英式写法）
  2. ər 音节：重读（ˈ/ˌ 后无元音间隔，或单音节且前接辅音）→ ɜː；其余 → ə
  3. 元音+r 非卷日化（词尾或辅音前）：ɑr→ɑː ɔr→ɔː ɛr→eə ɪr/ir→ɪə ʊr→ʊə
  4. LOT/THOUGHT：拼写含 o 的 ɔ/ɑ → ɒ；不含 o 的 ɔ → ɔː、ɑ → ɑː
  5. ɛ → e
规则覆盖不了的（BATH 裂化 wa- 词 orV 词等）一律进 SPECIAL 人工校准，
宁可人工也不瞎猜（对应仓库 Stage 02：不静默改写课程事实）。
"""
import json
import re
import sys
from pathlib import Path

import eng_to_ipa as epi

ROOT = Path(__file__).resolve().parent.parent          # flashcard/
WORDBANK = ROOT.parent / "dictation" / "data" / "wordbank.json"
OUT = ROOT / "data" / "ipa.json"

VOWEL_CHARS = set("ɑæɒɔəɜɛeɪiʊuʌo")

SPECIAL = {  # 人工校准（英式 DJ）：不规则读音、BATH 词、wa- 词、orV 词、合成词等
    "a": "ə", "an": "æn", "the": "ðə", "to": "tuː", "do": "duː", "does": "dʌz",
    "are": "ɑː", "were": "wɜː", "was": "wɒz", "said": "sed", "says": "sez",
    "i": "aɪ", "you": "juː", "your": "jɔː", "our": "ˈaʊə", "hour": "ˈaʊə",
    "mr.": "ˈmɪstə", "mrs.": "ˈmɪsɪz", "ms.": "mɪz",
    "ok": "əʊˈkeɪ", "tv": "ˌtiːˈviː", "pe": "ˌpiːˈiː", "cd": "ˌsiːˈdiː",
    "uk": "ˌjuːˈkeɪ", "usa": "ˌjuːesˈeɪ", "bye": "baɪ",
    # BATH 词（æ→ɑː）
    "after": "ˈɑːftə", "afternoon": "ˌɑːftəˈnuːn", "ask": "ɑːsk",
    "answer": "ˈɑːnsə", "aunt": "ɑːnt", "banana": "bəˈnɑːnə",
    "can't": "kɑːnt", "class": "klɑːs", "classroom": "ˈklɑːsruːm",
    "dance": "dɑːns", "fast": "fɑːst", "glass": "ɡlɑːs", "grass": "ɡrɑːs",
    "last": "lɑːst", "laugh": "lɑːf", "mask": "mɑːsk", "pass": "pɑːs",
    "past": "pɑːst", "path": "pɑːθ", "plant": "plɑːnt", "rather": "ˈrɑːðə",
    "tomato": "təˈmɑːtəʊ", "bath": "bɑːθ", "example": "ɪɡˈzɑːmpl",
    "grasp": "ɡrɑːsp", "master": "ˈmɑːstə", "disaster": "dɪˈzɑːstə",
    # wa- 词（w + a → wɒ）
    "what": "wɒt", "want": "wɒnt", "watch": "wɒtʃ", "wash": "wɒʃ",
    "wasp": "wɒsp", "swan": "swɒn", "quality": "ˈkwɒləti", "wallet": "ˈwɒlɪt",
    "wander": "ˈwɒndə", "squash": "skwɒʃ", "quarrel": "ˈkwɒrəl",
    # orV / 特殊 r 词
    "orange": "ˈɒrɪnʤ", "sorry": "ˈsɒri", "forest": "ˈfɒrɪst",
    "tomorrow": "təˈmɒrəʊ", "borrow": "ˈbɒrəʊ", "worry": "ˈwʌri",
    "story": "ˈstɔːri", "boring": "ˈbɔːrɪŋ", "glory": "ˈɡlɔːri",
    "poor": "pɔː", "sure": "ʃʊə", "tour": "tʊə", "fire": "ˈfaɪə",
    "colour": "ˈkʌlə", "favourite": "ˈfeɪvrɪt", "umbrella": "ʌmˈbrelə",
    "again": "əˈɡen", "against": "əˈɡenst", "says": "sez",
    # STRUT 词（ə→ʌ）与零星校准
    "monday": "ˈmʌndeɪ", "sunday": "ˈsʌndeɪ", "among": "əˈmʌŋ",
    "another": "əˈnʌðə", "because": "bɪˈkɒz", "brother": "ˈbrʌðə",
    "bund": "bʌnd", "bus": "bʌs", "bus stop": "bʌs stɒp",
    "come": "kʌm", "come to school": "kʌm tə skuːl", "country": "ˈkʌntri",
    "cousin": "ˈkʌzən", "cover": "ˈkʌvə", "culture": "ˈkʌltʃə", "cup": "kʌp",
    "cupboard": "ˈkʌbəd", "customer": "ˈkʌstəmə", "customs": "ˈkʌstəmz",
    "duck": "dʌk", "dumpling": "ˈdʌmplɪŋ", "dust": "dʌst", "earth": "ɜːθ",
    "enough": "ɪˈnʌf", "front": "frʌnt", "fun": "fʌn", "funny": "ˈfʌni",
    "hundreds of": "ˈhʌndrədz əv", "hungry": "ˈhʌŋɡri", "husband": "ˈhʌzbənd",
    "love": "lʌv", "lovely": "ˈlʌvli", "lucky": "ˈlʌki", "lunch": "lʌntʃ",
    "money": "ˈmʌni", "month": "mʌnθ", "mother": "ˈmʌðə", "much": "mʌtʃ",
    "mum": "mʌm", "must": "mʌst", "mustn't": "ˈmʌsənt", "none": "nʌn",
    "nothing": "ˈnʌθɪŋ", "number": "ˈnʌmbə", "one": "wʌn", "ones": "wʌnz",
    "rubber": "ˈrʌbə", "rubbish": "ˈrʌbɪʃ", "run": "rʌn", "rusty": "ˈrʌsti",
    "some": "sʌm", "sometimes": "ˈsʌmtaɪmz", "subject": "ˈsʌbdʒɪkt",
    "suddenly": "ˈsʌdənli", "summer": "ˈsʌmə", "summer holiday": "ˈsʌmə ˈhɒlɪdeɪ",
    "summer palace": "ˈsʌmə ˈpæləs", "sun": "sʌn", "sunglasses": "ˈsʌnɡlɑːsɪz",
    "sunny": "ˈsʌni", "that": "ðæt", "tongue": "tʌŋ", "under": "ˈʌndə",
    "up": "ʌp", "wonder": "ˈwʌndə", "young": "jʌŋ", "uncomfortable": "ʌnˈkʌmfətəbəl",
    "and": "ænd",
    "surround": "səˈraʊnd", "middle-aged": "ˈmɪdl eɪdʒd", "swimming": "ˈswɪmɪŋ",
    # eng-to-ipa 未收录的合成词 / 专有名词 / 拟声词
    "maths": "mæθs", "aeroplane": "ˈeərəpleɪn", "e-friend": "ˈiːfrend",
    "greengrocer": "ˈɡriːnɡrəʊsə", "greengrocer's": "ˈɡriːnɡrəʊsəz",
    "instalment": "ɪnˈstɔːlmənt", "lamp-post": "ˈlæmpəʊst",
    "mid-autumn festival": "mɪd ˈɔːtəm ˈfestɪvl", "phrasebook": "ˈfreɪzbʊk",
    "schoolbag": "ˈskuːlbæɡ", "shh": "ʃ", "sport-lover": "spɔːt ˈlʌvə",
    "tian'anmen square": "tjænˈænmən skweə", "toothache": "ˈtuːθeɪk",
}

VR = [("ɑr", "ɑː"), ("ɔr", "ɔː"), ("ɛr", "eə"), ("er", "eə"),
      ("ɪr", "ɪə"), ("ir", "iə"), ("ʊr", "ʊə"), ("ur", "ʊə")]


def _has_vowel(seg: str) -> bool:
    return any(c in VOWEL_CHARS for c in seg)


def _fix_schwa_r(s: str) -> str:
    """ər → ɜː（重读）/ ə（非重读）。重读判定：紧跟在 ˈ/ˌ 之后且中间无元音，
    或整词无重音符号且 ər 前是辅音（单音节实词）。"""
    while "ər" in s:
        i = s.find("ər")
        prev = s[i - 1] if i else ""
        stressed = False
        k = max(s.rfind("ˈ", 0, i), s.rfind("ˌ", 0, i))
        if k >= 0 and not _has_vowel(s[k + 1:i]):
            stressed = True                    # ˈwərkər 的第一个 ər
        elif "ˈ" not in s and "ˌ" not in s and prev and prev not in VOWEL_CHARS:
            stressed = True                    # bird→bərd、work→wərk 这类单音节
        repl = "ɜː" if stressed else "ə"
        s = s[:i] + repl + s[i + 2:]
    return s


def britishize(am: str, word: str) -> str:
    s = am
    w = word.lower()
    # 0. 字符归一：连音 affricate / 印刷体 ɡ
    s = s.replace("ʧ", "tʃ").replace("ʤ", "dʒ").replace("g", "ɡ")
    s = s.replace("oʊ", "əʊ")                        # GOAT
    s = _fix_schwa_r(s)                              # NURSE / lettER
    for a, b in VR:                                  # 元音+r 非卷日化（词尾/辅音前）
        s = re.sub(a + r"(?![ɑæɒɔəɜɛeɪiʊuʌo])", b, s)
    s = re.sub(r"r(?=[bdfgklmnptvwzðθʃʒŋʤʧ])", "", s)  # 残余辅音前 r 脱落
    s = re.sub(r"r$", "", s)
    # LOT/THOUGHT/PALM：ː 前的长音已由上面的规则定型，跳过
    s = re.sub(r"[ɔɑ](?!ː)", lambda m: ("ɒ" if "o" in w else {"ɔ": "ɔː", "ɑ": "ɑː"}[m.group(0)]), s)
    s = s.replace("ɛ", "e")
    # ASCII i/u 归一：重读开音节 i → iː（teacher/people），其余非词尾 i → ɪ；
    # u 一律 uː（eng-to-ipa 中 ASCII u 只出现在 /uː//juː/ 语境）
    s = re.sub(r"(ˈ[bcdfghjklmnpqrstvwxzðθʃʒŋ]{0,3})i(?!ː)", r"\1iː", s)
    s = re.sub(r"u(?!ː)", "uː", s)
    s = re.sub(r"i(?!ː|$)", "ɪ", s)
    s = s.replace("ˌ", "")                           # 次重音符号省略（教材卡片从简）
    return s


def to_ipa(word: str) -> str | None:
    lw = word.lower().strip()
    if lw in SPECIAL:
        return SPECIAL[lw]
    # 短语逐词转换后拼接：逐词走 SPECIAL/规则，比整短语扔给词典更稳
    if " " in lw:
        parts = [to_ipa(tok) for tok in lw.split()]
        if any(p is None for p in parts):
            return None
        return " ".join(parts)
    am = epi.convert(lw)
    if "*" in am:            # eng-to-ipa 查不到时回显原文加 *
        return None
    return britishize(am, lw)


def main() -> int:
    bank = json.loads(WORDBANK.read_text(encoding="utf-8"))
    words: dict[str, str] = {}
    for v in bank["versions"]:
        for g in v["grades"]:
            for s in g["subjects"]:
                if s["id"] != "english":
                    continue
                for u in s["units"]:
                    for w in u["words"]:
                        words.setdefault(w["word"], "")

    missing = []
    for word in sorted(words):
        ipa = to_ipa(word)
        if ipa is None:
            missing.append(word)
        else:
            words[word] = "/" + ipa + "/"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({k: v for k, v in sorted(words.items()) if v},
                   ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8")

    got = sum(1 for v in words.values() if v)
    print(f"total {len(words)} english words, ipa {got}, missing {len(missing)}")
    if missing:
        print("MISSING:", ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
