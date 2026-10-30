#!/usr/bin/env python3
"""从人教版小学语文课本书后「词语表」「写字表」重建默写词库。

词语表：按课录入，归入对应单元，单元名「第N单元 · 词语表」。
写字表：按课取会写的字，再造常用相关词，单元名「第N单元 · 写字表」
       （与词语表分开，互不混入）。

一年级课本没有词语表，只保留写字表相关词。

用法:
  python3 tools/build_yuwen.py
  python3 tools/build_yuwen.py --pdf-dir "/path/to/小学语文"
  python3 tools/build_yuwen.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict

import jieba
import pymupdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANK = os.path.join(ROOT, "data", "wordbank.json")
PARTS_DIR = os.path.join(ROOT, "data", "parts")
DEFAULT_PDF_DIR = "/Volumes/samsungssd/temp/中小学课本/课本/小学语文"

BOOKS = [
    {
        "id": "g1-up", "name": "一年级", "semester": "上册",
        "pdf": "（根据2022年版课程标准修订）义务教育教科书·语文一年级上册.pdf",
        "expect_xiezi": 100, "expect_ciyu": None,
        "kind": "g12",
        "units": [
            ("u1", "第1单元", [("识字", 2), ("识字", 3), ("识字", 4), ("园地", 1)]),
            ("u2", "第2单元", [("园地", 2)]),
            ("u3", "第3单元", [("园地", 3)]),
            ("u4", "第4单元", [("园地", 4)]),
            ("u5", "第5单元", [("阅读", 1), ("阅读", 2), ("阅读", 3), ("阅读", 4), ("园地", 5)]),
            ("u6", "第6单元", [("识字", 5), ("识字", 6), ("识字", 7), ("识字", 8), ("园地", 6)]),
            ("u7", "第7单元", [("阅读", 5), ("阅读", 6), ("阅读", 7), ("园地", 7)]),
            ("u8", "第8单元", [("阅读", 8), ("阅读", 9), ("阅读", 10), ("园地", 8)]),
        ],
    },
    {
        "id": "g1-down", "name": "一年级", "semester": "下册",
        "pdf": "（根据2022年版课程标准修订）义务教育教科书·语文一年级下册.pdf",
        "expect_xiezi": 200, "expect_ciyu": None,
        "kind": "g12",
        "units": [
            ("u1", "第1单元", [("识字", 1), ("识字", 2), ("识字", 3), ("识字", 4), ("园地", 1)]),
            ("u2", "第2单元", [("阅读", 1), ("阅读", 2), ("阅读", 3), ("园地", 2)]),
            ("u3", "第3单元", [("阅读", 4), ("阅读", 5), ("阅读", 6), ("园地", 3)]),
            ("u4", "第4单元", [("阅读", 7), ("阅读", 8), ("阅读", 9), ("园地", 4)]),
            ("u5", "第5单元", [("识字", 5), ("识字", 6), ("识字", 7), ("识字", 8), ("园地", 5)]),
            ("u6", "第6单元", [("阅读", 10), ("阅读", 11), ("阅读", 12), ("阅读", 13), ("园地", 6)]),
            ("u7", "第7单元", [("阅读", 14), ("阅读", 15), ("阅读", 16), ("阅读", 17), ("园地", 7)]),
            ("u8", "第8单元", [("阅读", 18), ("阅读", 19), ("阅读", 20), ("园地", 8)]),
        ],
    },
    {
        "id": "g2-up", "name": "二年级", "semester": "上册",
        "pdf": "（根据2022年版课程标准修订）义务教育教科书·语文二年级上册.pdf",
        "expect_xiezi": 250, "expect_ciyu": 236,
        "kind": "g12",
        "units": [
            ("u1", "第1单元", [("阅读", 1), ("阅读", 2), ("阅读", 3), ("园地", 1)]),
            ("u2", "第2单元", [("识字", 1), ("识字", 2), ("识字", 3), ("识字", 4), ("园地", 2)]),
            ("u3", "第3单元", [("阅读", 4), ("阅读", 5), ("阅读", 6), ("园地", 3)]),
            ("u4", "第4单元", [("阅读", 7), ("阅读", 8), ("阅读", 9), ("阅读", 10), ("园地", 4)]),
            ("u5", "第5单元", [("阅读", 11), ("阅读", 12), ("阅读", 13), ("园地", 5)]),
            ("u6", "第6单元", [("阅读", 14), ("阅读", 15), ("阅读", 16), ("阅读", 17), ("园地", 6)]),
            ("u7", "第7单元", [("阅读", 18), ("阅读", 19), ("阅读", 20), ("园地", 7)]),
            ("u8", "第8单元", [("阅读", 21), ("阅读", 22), ("阅读", 23), ("园地", 8)]),
        ],
    },
    {
        "id": "g2-down", "name": "二年级", "semester": "下册",
        "pdf": "（根据2022年版课程标准修订）义务教育教科书·语文二年级下册.pdf",
        "expect_xiezi": 250, "expect_ciyu": None,
        "kind": "g12",
        "units": [
            ("u1", "第1单元", [("阅读", 1), ("阅读", 2), ("阅读", 3), ("阅读", 4), ("园地", 1)]),
            ("u2", "第2单元", [("阅读", 5), ("阅读", 6), ("阅读", 7), ("园地", 2)]),
            ("u3", "第3单元", [("识字", 1), ("识字", 2), ("识字", 3), ("识字", 4), ("园地", 3)]),
            ("u4", "第4单元", [("阅读", 8), ("阅读", 9), ("阅读", 10), ("园地", 4)]),
            ("u5", "第5单元", [("阅读", 11), ("阅读", 12), ("阅读", 13), ("园地", 5)]),
            ("u6", "第6单元", [("阅读", 14), ("阅读", 15), ("阅读", 16), ("阅读", 17), ("园地", 6)]),
            ("u7", "第7单元", [("阅读", 18), ("阅读", 19), ("阅读", 20), ("阅读", 21), ("园地", 7)]),
            ("u8", "第8单元", [("阅读", 22), ("阅读", 23), ("阅读", 24), ("园地", 8)]),
        ],
    },
    {
        "id": "g3-up", "name": "三年级", "semester": "上册",
        "pdf": "（根据2022年版课程标准修订）义务教育教科书·语文三年级上册.pdf",
        "expect_xiezi": 250, "expect_ciyu": 250,
        "kind": "g36",
        "lesson_ranges": [(1, 3), (4, 7), (8, 10), (11, 13), (14, 15), (16, 19), (20, 22), (23, 26)],
    },
    {
        "id": "g3-down", "name": "三年级", "semester": "下册",
        "pdf": "（根据2022年版课程标准修订）义务教育教科书·语文三年级下册.pdf",
        "expect_xiezi": 250, "expect_ciyu": 271,
        "kind": "g36",
        "lesson_ranges": [(1, 4), (5, 8), (9, 11), (12, 15), (16, 17), (18, 21), (22, 24), (25, 27)],
    },
    {
        "id": "g4-up", "name": "四年级", "semester": "上册",
        "pdf": "（根据2022年版课程标准修订）义务教育教科书·语文四年级上册.pdf",
        "expect_xiezi": 250, "expect_ciyu": 220,
        "kind": "g36",
        "lesson_ranges": [(1, 3), (4, 7), (8, 10), (11, 14), (15, 16), (17, 19), (20, 23), (24, 27)],
    },
    {
        "id": "g4-down", "name": "四年级", "semester": "下册",
        "pdf": "义务教育教科书·语文四年级下册.pdf",
        "expect_xiezi": 250, "expect_ciyu": None,
        "kind": "g36",
        "lesson_ranges": [(1, 4), (5, 8), (9, 12), (13, 15), (16, 17), (18, 21), (22, 25), (26, 28)],
    },
    {
        "id": "g5-up", "name": "五年级", "semester": "上册",
        "pdf": "（根据2022年版课程标准修订）义务教育教科书·语文五年级上册.pdf",
        "expect_xiezi": 220, "expect_ciyu": 213,
        "kind": "g36",
        "lesson_ranges": [(1, 3), (4, 7), (8, 10), (11, 14), (15, 16), (17, 19), (20, 22), (23, 25)],
    },
    {
        "id": "g5-down", "name": "五年级", "semester": "下册",
        "pdf": "义务教育教科书·语文五年级下册.pdf",
        "expect_xiezi": 180, "expect_ciyu": None,
        "kind": "g36",
        # 第3单元是综合性学习，无课序号
        "lesson_ranges": [(1, 4), (5, 8), None, (9, 12), (13, 14), (15, 17), (18, 20), (21, 23)],
    },
    {
        "id": "g6-up", "name": "六年级", "semester": "上册",
        "pdf": "（根据2022年版课程标准修订）义务教育教科书·语文六年级上册.pdf",
        "expect_xiezi": 180, "expect_ciyu": 215,
        "kind": "g36",
        "lesson_ranges": [(1, 3), (4, 7), (8, 10), (11, 13), (14, 15), (16, 19), (20, 22), (23, 26)],
    },
    {
        "id": "g6-down", "name": "六年级", "semester": "下册",
        "pdf": "义务教育教科书·语文六年级下册.pdf",
        "expect_xiezi": 120, "expect_ciyu": None,
        "kind": "g36",
        # 第6单元是综合性学习
        "lesson_ranges": [(1, 4), (5, 7), (8, 9), (10, 13), (14, 17), None],
    },
]

NOISE = re.compile(
    r"仅供个人学习使用.*|未经授权不得另做他用|识字表中蓝色的字.*|"
    r"①|②|③|④|⑤|⑥|"
    r"共\s*\d+\s*个(?:生字|字|词)"
)
PINYIN = re.compile(r"^[A-Za-züÜv]+$")
HAN = re.compile(r"[\u4e00-\u9fff]")
PAGE_NO = re.compile(r"^\d{3}$")  # 书页码三位；两位数是课序号，不能丢
LESSON_HEAD = re.compile(r"^(\d{1,2})(?:\s+|$)(.*)$")
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}

DENY_FRAG = re.compile(
    r"死尸|嫖|赌|毒瘾|色情|裸体|性交|强奸|自杀|杀人|王八|狗屎|"
    r"淫秽|外阴|排便|梅毒|剖腹|他杀|太监|驾崩"
)
GENERIC_WORDS = {
    "一个", "一些", "一种", "一定", "不是", "什么", "我们", "他们", "自己",
    "可以", "没有", "这个", "那个", "因为", "所以", "已经", "还有", "以及",
    "这样", "那样", "如何", "怎么", "为什么", "只是", "还是", "或者",
    "二者", "三者", "二爷", "三相", "七言", "七律", "九品", "九姓",
    "合十", "日军", "吗啡", "荷兰籍", "一般", "统一", "银川", "波音",
    "历史系", "欧洲人", "检察官", "保险费", "纤维素", "解剖学", "核爆炸",
    "爆炸性", "爆炸声", "弃权票", "准备金", "侧重点", "清晰度", "监督权",
    "负责制", "获奖者", "承担者", "感染者", "流浪者", "牺牲品", "乃蛮",
    "奈良", "即墨", "扶余", "比绍", "泰币", "职介", "扭矩", "矩阵",
    "籍没", "籍授", "魔教", "援藏", "园圃", "昼间", "锄奸", "扳机",
    "剃度", "阻尼", "代码", "姆妈", "翘楚", "季梁", "轧钢", "废液",
    "呻唤", "媚外", "耽心", "恒业", "管式", "吟哦", "枢密院", "登陆舰",
    "考试院", "旧社会", "充其量", "其内", "穷汉", "外阴", "切块",
    "铅直", "森工", "番号", "防渗", "藤牌", "臀鳍", "囊中", "卖乖",
    "滴定", "庸碌", "商贾", "疑忌", "申遗", "侨眷", "酒肆", "干贝",
    "分贝", "光合", "藻井", "阳府", "古墓", "地衣", "临床", "主叫",
    "海胆", "洋芋", "金领", "秋审", "干吗", "仙桃", "岭子", "教众",
    "皂隶", "顿首", "企鹅", "摔交", "蝶泳", "赖帐", "斑竹", "几率",
    "人本", "右派", "大巴", "半岛", "太子", "太监", "个股", "皇上",
    "股东", "日益", "项目", "手段", "亲王", "王位", "郡王", "洋行",
    "起诉", "数论", "反切", "路由", "受理", "化肥", "探矿", "涨幅",
    "量级", "填报", "赤膊", "方丈", "割据", "回扣", "冶炼", "锻压",
    "市肆", "轧制", "犬吠", "耽于", "华筝", "羁绊", "绞盘", "寨主",
    "刑部", "阶级", "外戚", "贵戚", "霉素", "控股", "信贷",
}
FUNCTION_CHARS = set("一二三四五六七八九十")
NAME_POS = ("nr", "ns", "nt", "nz", "nrt", "nrf")
DATE_WORD = re.compile(
    r"^[一二三四五六七八九十百千万两零]+[日天月年号点分秒]$"
)


def norm_heading(s: str) -> str:
    return re.sub(r"[\s\u2002\u2003\u2009\u200a\u3000\xa0]+", "", s)


def appendix_text(pdf_path: str) -> str:
    doc = pymupdf.open(pdf_path)
    n = len(doc)
    start = max(0, n - 25)
    chunks = []
    for i in range(start, n):
        chunks.append(doc[i].get_text())
    doc.close()
    return "\n".join(chunks)


JUNK_WORD = re.compile(
    r"教科书|义务教育|主编|教育部|人民教育|版权|温儒敏|北京大学|"
    r"编修|责任编辑|未经授权|组织编写|总主编|教授"
)


def valid_ciyu(w: str) -> bool:
    if not re.fullmatch(r"[\u4e00-\u9fff]{2,8}儿?", w):
        return False
    if JUNK_WORD.search(w):
        return False
    return True


def split_sections(raw: str) -> dict[str, str]:
    """按书后附表切开。识字表以「共N个生字」结束，写字表以「共N个字」结束。"""
    flat = raw.replace("\u2002", " ").replace("\u2003", " ").replace("\u3000", " ")
    m_sheng = list(re.finditer(r"（?\s*共\s*\d+\s*个生字\s*）?", flat))
    m_zi = list(re.finditer(r"（?\s*共\s*\d+\s*个字\s*）?", flat))
    m_ci = list(re.finditer(r"（?\s*共\s*\d+\s*个词\s*）?", flat))

    def after(m):
        return m.end() if m else None

    xiezi_start = after(m_sheng[-1]) if m_sheng else None
    if xiezi_start is None:
        m = re.search(r"写\s*字\s*表", flat)
        xiezi_start = m.end() if m else 0
    ciyu_start = after(m_zi[-1]) if m_zi else None
    if ciyu_start is None:
        m = re.search(r"词\s*语\s*表", flat)
        ciyu_start = m.start() if m else None

    def first_end_after(pos: int) -> int:
        ends = []
        for pat in (r"后\s*记", r"义务教育教科书", r"版权所有"):
            for m in re.finditer(pat, flat):
                if m.start() > pos:
                    ends.append(m.start())
                    break
        return min(ends) if ends else len(flat)

    xiezi_end = first_end_after(xiezi_start)
    if ciyu_start is not None:
        xiezi_end = min(xiezi_end, ciyu_start)
    if m_zi:
        xiezi_end = m_zi[-1].end()
        ciyu_start = m_zi[-1].end()

    ciyu = ""
    if ciyu_start is not None:
        end = first_end_after(ciyu_start)
        if m_ci:
            end = min(end, m_ci[-1].end())
        ciyu = flat[ciyu_start:end]

    xiezi = flat[xiezi_start:xiezi_end]
    return {"xiezi": xiezi, "ciyu": ciyu}


def tokenize(text: str) -> list[str]:
    text = NOISE.sub(" ", text)
    text = text.replace("\u2002", " ").replace("\u2003", " ").replace("\u3000", " ")
    text = text.replace("\xa0", " ").replace("\t", "\n")
    parts = []
    for line in text.splitlines():
        line = line.strip()
        if not line or "仅供个人学习使用" in line:
            continue
        if PAGE_NO.match(line):
            continue
        if PINYIN.match(line):
            continue
        if norm_heading(line) in ("写字表", "词语表", "识字表"):
            continue
        parts.append(line)
    return parts


def add_chars(bucket, key, chars):
    if key is None:
        return
    for ch in chars:
        if ch not in bucket[key]:
            bucket[key].append(ch)


def parse_xiezi_g12(text: str) -> dict[tuple, list[str]]:
    """低年级写字表：字与拼音分行，课序号 / 语文园地N / 识字|阅读 分段。"""
    tokens = tokenize(text)
    section = "阅读"
    lesson = None
    yuandi = None
    bucket: dict[tuple, list[str]] = defaultdict(list)

    def key():
        if yuandi is not None:
            return ("园地", yuandi)
        if lesson is None:
            return None
        return (section, lesson)

    i = 0
    while i < len(tokens):
        t = tokens[i]
        nt = norm_heading(t)
        if nt in ("识字", "阅读", "汉语拼音"):
            section = "识字" if nt == "识字" else ("阅读" if nt == "阅读" else "阅读")
            if nt == "汉语拼音":
                section = "阅读"
            lesson = None
            yuandi = None
            i += 1
            continue
        if nt.startswith("语文园地"):
            rest = nt[len("语文园地"):]
            yuandi = CN_NUM.get(rest, 0)
            lesson = None
            i += 1
            continue
        m = LESSON_HEAD.match(t)
        if m and not HAN.search(m.group(1)):
            # 纯数字课序号，可能同行还有汉字
            lesson = int(m.group(1))
            yuandi = None
            add_chars(bucket, key(), HAN.findall(m.group(2) or ""))
            i += 1
            continue
        add_chars(bucket, key(), HAN.findall(t))
        i += 1
    return dict(bucket)


def parse_xiezi_g36(text: str) -> dict[tuple, list[str]]:
    """中高年级写字表：课序号后一串汉字；语文园地单独成块。"""
    tokens = tokenize(text)
    bucket: dict[tuple, list[str]] = defaultdict(list)
    i = 0
    last_lesson = 0
    while i < len(tokens):
        t = tokens[i]
        nt = norm_heading(t)
        if nt.startswith("语文园地"):
            chars = [c for c in HAN.findall(t) if c not in "语文园地"]
            i += 1
            while i < len(tokens):
                nt2 = norm_heading(tokens[i])
                if LESSON_HEAD.match(tokens[i]) and not HAN.search(LESSON_HEAD.match(tokens[i]).group(1) or ""):
                    break
                if nt2.startswith("语文园地"):
                    break
                chars += HAN.findall(tokens[i])
                i += 1
            add_chars(bucket, ("园地_raw", last_lesson), chars)
            continue
        m = LESSON_HEAD.match(t)
        if m:
            num = int(m.group(1))
            last_lesson = num
            chars = HAN.findall(m.group(2) or "")
            i += 1
            while i < len(tokens):
                nt2 = norm_heading(tokens[i])
                if LESSON_HEAD.match(tokens[i]):
                    break
                if nt2.startswith("语文园地"):
                    break
                chars += HAN.findall(tokens[i])
                i += 1
            add_chars(bucket, ("课", num), chars)
            continue
        i += 1
    return dict(bucket)


def parse_ciyu(text: str, kind: str) -> dict[tuple, list[str]]:
    """词语表：课序号 / 语文园地 / 识字|阅读 后跟以空白分隔的词。"""
    if not text.strip():
        return {}
    raw = text.replace("\u2002", " ").replace("\u2003", " ").replace("\u3000", " ")
    raw = raw.replace("\xa0", " ").replace("\t", " ")
    raw = NOISE.sub(" ", raw)
    lines = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or PAGE_NO.match(s):
            continue
        if "仅供个人学习使用" in s:
            continue
        if norm_heading(s) in ("词语表", "写字表", "识字表"):
            continue
        lines.append(s)
    blob = "\n".join(lines)

    bucket: dict[tuple, list[str]] = defaultdict(list)
    section = "阅读"
    current = None
    last_lesson = 0

    token_re = re.compile(
        r"(阅读|识字)|"
        r"(语文园地[一二三四五六七八]?)|"
        r"(?:(?<=\s)|^)(\d{1,2})(?=\s)|"
        r"([\u4e00-\u9fff]{2,8}儿?)",
        re.M,
    )
    src = "\n" + blob
    for m in token_re.finditer(src):
        if m.group(1):
            section = m.group(1)
            continue
        if m.group(2):
            tag = m.group(2)
            n = CN_NUM.get(tag.replace("语文园地", ""), 0)
            if kind == "g12":
                current = ("园地", n)
            else:
                current = ("园地_raw", last_lesson)
            continue
        if m.group(3):
            num = int(m.group(3))
            last_lesson = num
            current = (section, num) if kind == "g12" else ("课", num)
            continue
        word = m.group(4)
        if not word or current is None:
            continue
        if word in ("阅读", "识字", "语文园地"):
            continue
        if not valid_ciyu(word):
            continue
        if word not in bucket[current]:
            bucket[current].append(word)
    return dict(bucket)


def lesson_to_unit_g36(ranges, lesson: int) -> int | None:
    for i, rg in enumerate(ranges):
        if rg is None:
            continue
        a, b = rg
        if a <= lesson <= b:
            return i + 1
    return None


def attach_yuandi_g36(ranges, last_lesson: int) -> int | None:
    return lesson_to_unit_g36(ranges, last_lesson)


NUM_CHARS = set("一二三四五六七八九十百千万两零")
OK_POS = set("n v a vn an ad d f s i l ns nt nz vg vd ng zg b z".split())
BAD_POS = set("m mq q x e o u y w t nr r".split())


def load_freq() -> dict[str, tuple[int, str]]:
    path = os.path.join(os.path.dirname(jieba.__file__), "dict.txt")
    freq = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 3:
                continue
            w, fr, pos = parts[0], parts[1], parts[2]
            if not re.fullmatch(r"[\u4e00-\u9fff]{2,4}", w):
                continue
            if DENY_FRAG.search(w):
                continue
            if pos in BAD_POS or pos.startswith(NAME_POS):
                continue
            freq[w] = (int(fr), pos)
    return freq


def common_chars_from(freq: dict[str, tuple[int, str]], n: int = 2800) -> set[str]:
    from collections import Counter
    c = Counter()
    for w, (fr, _pos) in freq.items():
        if len(w) == 2:
            for ch in w:
                c[ch] += fr
    return {ch for ch, _ in c.most_common(n)}


def related_from_chars(chars: list[str], freq: dict[str, tuple[int, str]],
                       learned: set[str], exclude: set[str],
                       grade: int, common: set[str] | None = None) -> list[str]:
    """按写字表生字扩常用词。至少含一个本课会写的字。"""
    if not chars:
        return []
    unit_set = set(chars)
    pool = unit_set | learned
    common = common or set()
    min_freq = {1: 80, 2: 50, 3: 30, 4: 18, 5: 12, 6: 8}[grade]
    per_char = 2
    max_len = 2 if grade <= 2 else 3
    max_total = min(24, max(12, len(chars) + 4))
    allow_pos = set("n v a vn an i l ng vg s f".split())

    by_char: dict[str, list[tuple[int, str]]] = {c: [] for c in chars}
    for w, (fr, pos) in freq.items():
        if w in exclude or w in GENERIC_WORDS:
            continue
        if not (2 <= len(w) <= max_len):
            continue
        if len(w) == 3:
            if fr < max(min_freq * 4, 200):
                continue
            if w.endswith(("者", "性", "率", "权", "费", "票", "舰", "院", "籍")):
                continue
        if fr < min_freq or pos not in allow_pos or pos.startswith(NAME_POS):
            continue
        if all(c in NUM_CHARS for c in w) or DATE_WORD.match(w):
            continue
        if DENY_FRAG.search(w) or w.endswith("籍"):
            continue
        hits = [c for c in w if c in unit_set]
        if not hits:
            continue
        others = [c for c in w if c not in unit_set]
        if any(c not in common and c not in pool for c in others):
            continue
        # 「一/二/十/了」等虚字组词时，另一字尽量已学，避免「统一/二者」
        if hits and all(c in FUNCTION_CHARS for c in hits) and others:
            if any(c not in pool for c in others):
                continue
        if w[0] == w[1] and not (pos in {"n", "v"} and fr >= 400):
            continue
        rev = w[::-1]
        if rev != w and rev in freq and freq[rev][0] > fr:
            continue
        in_pool = sum(1 for c in w if c in pool)
        adj = any(w in (chars[i] + chars[i + 1], chars[i + 1] + chars[i])
                  for i in range(len(chars) - 1))
        score = (len(hits) * 40000
                 + in_pool * 8000
                 + (12000 if adj else 0)
                 + (4000 if len(w) == 2 else 0)
                 + min(fr, 6000))
        for c in hits:
            by_char[c].append((score, w))
    for c in chars:
        by_char[c].sort(reverse=True)

    picked, seen = [], set()
    for c in chars:
        n = 0
        for _, w in by_char[c]:
            if w in seen or w[::-1] in seen:
                continue
            picked.append(w)
            seen.add(w)
            n += 1
            if n >= per_char:
                break
        if len(picked) >= max_total:
            break
    if len(picked) < max_total:
        rest = []
        for c in chars:
            rest.extend(by_char[c])
        rest.sort(reverse=True)
        for _, w in rest:
            if w in seen or w[::-1] in seen:
                continue
            picked.append(w)
            seen.add(w)
            if len(picked) >= max_total:
                break
    return picked[:max_total]


def compact_json(obj) -> str:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    text = re.sub(
        r"\{\s*\n\s*\"word\": (\".*?\")(?:,\s*\n\s*\"tip\": (\".*?\"))?\s*\n\s*\}",
        lambda m: "{ \"word\": %s%s }" % (
            m.group(1), (", \"tip\": %s" % m.group(2)) if m.group(2) else ""),
        text,
    )
    return text + "\n"


def build_g12_units(book, xiezi, ciyu, freq, grade, common):
    units_out = []
    learned = set()
    book_ciyu = {w for ws in ciyu.values() for w in ws}
    for uid, uname, keys in book["units"]:
        xz_chars = []
        for k in keys:
            xz_chars += xiezi.get(k, [])
        # 去重保序
        seen = set()
        chars = []
        for c in xz_chars:
            if c not in seen:
                seen.add(c)
                chars.append(c)
        words = []
        for k in keys:
            words += ciyu.get(k, [])
        wseen, ci_words = set(), []
        for w in words:
            if w not in wseen and valid_ciyu(w):
                wseen.add(w)
                ci_words.append(w)
        if ci_words:
            units_out.append({
                "id": uid, "name": uname + " · 词语表",
                "words": [{"word": w} for w in ci_words],
            })
        rel = related_from_chars(chars, freq, learned, book_ciyu | set(ci_words), grade, common)
        if not rel and chars and not ci_words:
            rel = chars[:]  # 一年级拼音单元几乎没有可组词，退回本课会写的字
        if rel:
            units_out.append({
                "id": uid + "-xz", "name": uname + " · 写字表",
                "words": [{"word": w, "tip": "写字表组词" if len(w) > 1 else "写字表"} for w in rel],
            })
        learned.update(chars)
    return units_out, chars_count(xiezi), sum(len(v) for v in ciyu.values())


def chars_count(bucket) -> int:
    n, seen = 0, set()
    for ws in bucket.values():
        for c in ws:
            if c not in seen:
                seen.add(c)
                n += 1
    return n


def build_g36_units(book, xiezi, ciyu, freq, grade, common):
    ranges = book["lesson_ranges"]
    xz_lessons = {k[1]: v for k, v in xiezi.items() if k[0] == "课"}
    ci_lessons = {k[1]: v for k, v in ciyu.items() if k[0] == "课"}

    xz_yuandi_by_unit = defaultdict(list)
    ci_yuandi_by_unit = defaultdict(list)

    def assign_yuandi(bucket, dest):
        for k, vals in bucket.items():
            if k[0] != "园地_raw":
                continue
            last_lesson = k[1]
            u = lesson_to_unit_g36(ranges, last_lesson) if last_lesson else None
            if u is not None:
                dest[u].extend(vals)

    assign_yuandi(xiezi, xz_yuandi_by_unit)
    assign_yuandi(ciyu, ci_yuandi_by_unit)

    units_out = []
    learned = set()
    book_ciyu = set()
    for ws in ci_lessons.values():
        book_ciyu.update(ws)
    for ws in ci_yuandi_by_unit.values():
        book_ciyu.update(ws)

    n_units = len(ranges)
    all_xz = 0
    seen_c = set()
    for v in xz_lessons.values():
        for c in v:
            if c not in seen_c:
                seen_c.add(c)
                all_xz += 1
    for v in xz_yuandi_by_unit.values():
        for c in v:
            if c not in seen_c:
                seen_c.add(c)
                all_xz += 1

    for i, rg in enumerate(ranges):
        uid = "u%d" % (i + 1)
        uname = "第%d单元" % (i + 1)
        if rg is None:
            continue
        a, b = rg
        chars = []
        for n in range(a, b + 1):
            chars += xz_lessons.get(n, [])
        chars += xz_yuandi_by_unit.get(i + 1, [])
        seen = set()
        chars_u = []
        for c in chars:
            if c not in seen:
                seen.add(c)
                chars_u.append(c)
        ci_words = []
        wseen = set()
        for n in range(a, b + 1):
            for w in ci_lessons.get(n, []):
                if w not in wseen and valid_ciyu(w):
                    wseen.add(w)
                    ci_words.append(w)
        for w in ci_yuandi_by_unit.get(i + 1, []):
            if w not in wseen and valid_ciyu(w):
                wseen.add(w)
                ci_words.append(w)
        if ci_words:
            units_out.append({
                "id": uid, "name": uname + " · 词语表",
                "words": [{"word": w} for w in ci_words],
            })
        rel = related_from_chars(chars_u, freq, learned, book_ciyu | set(ci_words), grade, common)
        if rel:
            units_out.append({
                "id": uid + "-xz", "name": uname + " · 写字表",
                "words": [{"word": w, "tip": "写字表组词"} for w in rel],
            })
        learned.update(chars_u)
    ci_total = sum(len(v) for v in ci_lessons.values()) + sum(len(v) for v in ci_yuandi_by_unit.values())
    return units_out, all_xz, ci_total


def grade_num(gid: str) -> int:
    return int(gid[1])


def dump_parts(grades: list[dict]) -> None:
    groups = [
        ("yuwen-g1-g2.json", {"g1-up", "g1-down", "g2-up", "g2-down"}),
        ("yuwen-g3-g4.json", {"g3-up", "g3-down", "g4-up", "g4-down"}),
        ("yuwen-g5-g6.json", {"g5-up", "g5-down", "g6-up", "g6-down"}),
    ]
    by_id = {g["id"]: g for g in grades}
    for fname, ids in groups:
        part = {"grades": [by_id[i] for i in
                           ["g1-up", "g1-down", "g2-up", "g2-down",
                            "g3-up", "g3-down", "g4-up", "g4-down",
                            "g5-up", "g5-down", "g6-up", "g6-down"] if i in ids]}
        path = os.path.join(PARTS_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(compact_json(part))
        print("parts ->", path)


def merge_bank(grades: list[dict]) -> None:
    bank = json.load(open(BANK, encoding="utf-8"))
    pep = next(v for v in bank["versions"] if v["id"] == "pep")
    pep["grades"] = grades
    bank["meta"]["updatedAt"] = "2026-10-29"
    with open(BANK, "w", encoding="utf-8") as f:
        f.write(compact_json(bank))
    print("wordbank ->", BANK)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", default=DEFAULT_PDF_DIR)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    freq = load_freq()
    common = common_chars_from(freq)
    grades = []
    ok = True
    for book in BOOKS:
        pdf = os.path.join(args.pdf_dir, book["pdf"])
        if not os.path.isfile(pdf):
            print("缺少 PDF:", pdf, file=sys.stderr)
            return 1
        raw = appendix_text(pdf)
        sec = split_sections(raw)
        if book["kind"] == "g12":
            xiezi = parse_xiezi_g12(sec["xiezi"])
            ciyu = parse_ciyu(sec["ciyu"], "g12")
            units, nx, nc = build_g12_units(book, xiezi, ciyu, freq, grade_num(book["id"]), common)
        else:
            xiezi = parse_xiezi_g36(sec["xiezi"])
            ciyu = parse_ciyu(sec["ciyu"], "g36")
            units, nx, nc = build_g36_units(book, xiezi, ciyu, freq, grade_num(book["id"]), common)
        exp_x, exp_c = book["expect_xiezi"], book["expect_ciyu"]
        flag = ""
        if exp_x and abs(nx - exp_x) > 5:
            flag += " 写字表汉字偏差"
            ok = False
        if exp_c and abs(nc - exp_c) > 8:
            flag += " 词语表偏差"
            ok = False
        if any(len(u["words"]) == 0 for u in units):
            flag += " 空词单元"
            ok = False
        n_ci = sum(len(u["words"]) for u in units if u["name"].endswith("词语表"))
        n_xz = sum(len(u["words"]) for u in units if u["name"].endswith("写字表"))
        print("%s  写字表汉字 %d%s  词语表 %d%s  → 单元词 词语%d + 写字相关%d  单元%d%s" % (
            book["id"], nx, "" if not exp_x else "/%d" % exp_x,
            nc, "" if not exp_c else "/%d" % exp_c,
            n_ci, n_xz, len(units), flag))
        if args.debug:
            print("  xiezi keys", sorted(xiezi.keys(), key=str)[:40], "count", len(xiezi))
            print("  ciyu keys", sorted(ciyu.keys(), key=str)[:40], "count", len(ciyu))
            if units:
                print("  first unit", units[0]["name"], [w["word"] for w in units[0]["words"][:12]])
        if not units:
            print("  空单元!", file=sys.stderr)
            ok = False
        grades.append({
            "id": book["id"], "name": book["name"], "semester": book["semester"],
            "subjects": [{"id": "chinese", "name": "语文", "units": units}],
        })

    if args.dry_run:
        return 0 if ok else 2
    dump_parts(grades)
    merge_bank(grades)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
