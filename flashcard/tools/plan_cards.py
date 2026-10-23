#!/usr/bin/env python3
"""plan_cards.py · 把单元词表分组为场景卡方案（Stage 03/04：CardPlan + SceneBrief）

分组由 Doubao 文本模型完成（语义场景连贯性需要判断），结果经确定性校验：
  - 单元内每个词恰好出现一次（对应 Stage 03 Review Gate）
  - 每卡 1-6 词，优先 3-5（7+ 拆卡）
  - mapping_mode 枚举合法；connector 策略按场景简报规则推导，不信模型
产物：flashcard/cards/plans/<version>--<grade>--<unit>.json（中间产物，不部署）

用法：
  .venv/bin/python plan_cards.py --version yilin --grade g3-up          # 指定年级
  .venv/bin/python plan_cards.py --version yilin --grade g3-up --unit u1
  .venv/bin/python plan_cards.py --all                                  # 全量
key 读取顺序：ARK_API_KEY 环境变量 > tools/.env
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
WORDBANK = ROOT.parent / "dictation" / "data" / "wordbank.json"
IPA_JSON = ROOT / "data" / "ipa.json"
PLANS_DIR = ROOT / "cards" / "plans"

ARK_BASE = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
TEXT_MODEL = os.environ.get("ARK_TEXT_MODEL", "doubao-1-5-pro-32k-250115")

MAPPING_MODES = ["direct_anchor", "action_anchor", "relation_anchor",
                 "scene_state", "dialogue_phrase", "timeline_segment", "comparison_zone"]
CARD_TYPES = ["Object/Place", "Action", "Relation", "Narrative",
              "Choice/Contrast", "Dialogue/Expression", "Timeline", "Spatial/Cutaway"]
# scene_brief_rules.md：connector 策略由 mapping mode 决定，模型输出只作参考
CONNECTOR_BY_MODE = {"direct_anchor": "required", "action_anchor": "optional",
                     "relation_anchor": "optional", "scene_state": "none",
                     "dialogue_phrase": "none", "timeline_segment": "none",
                     "comparison_zone": "none"}

PROMPT_TMPL = """你是小学英语教材的场景闪卡策划。把下面的单词分成若干张「场景学习卡」。

【分组规则】（必须严格遵守）
- 同卡单词必须能出现在同一幅儿童绘本场景里：同场景同见、同一动作链、同一空间/所属关系、同一对比或时间线，至少满足其一
- 每张卡 3-5 个词最佳，允许 1-6 个；不得按固定词数硬凑
- 抽象词/功能词（very、please 等）不要硬挂靠物体，用 dialogue_phrase 模式
- 每个词必须且只能出现一次

【画面硬约束】（极其重要，违反即作废）
- 底图严禁出现任何文字：scene_brief 和 visual_target 都不得描述"写着某字的课程表/门牌/气泡/封面/标牌/横幅"，不得出现"对话气泡中的单词"这类说法
- direct_anchor / action_anchor 的 visual_target 必须是不依赖任何文字就能一眼认出的具体可见物或动作（例：Maths→算盘和几何尺，Art→调色盘和画笔，Music→小提琴，PE→跳绳，Science→烧杯和放大镜，English→摊开的英文绘本（封面空白））
- mapping_mode 为 dialogue_phrase / scene_state 的词，visual_target 一律写"（无挂靠）"

【输出】只输出 JSON（不要任何额外文字），结构：
{{
  "cards": [
    {{
      "scene_title_cn": "宠物角",
      "scene_title_en": "Pets",
      "card_type": "Object/Place",
      "scene_brief": "一句话描述画面：谁在哪做什么，有哪些可见物体（中文，40 字内）",
      "targets": [
        {{"word": "dog", "visual_target": "金毛犬", "mapping_mode": "direct_anchor"}},
        {{"word": "like", "visual_target": "男孩摸狗的动作", "mapping_mode": "action_anchor"}}
      ]
    }}
  ]
}}
字段说明：
- card_type 只能是：{card_types}
- mapping_mode 只能是：{mapping_modes}
  direct_anchor=具体可见物/人/身体部位/地点；action_anchor=可见动作；relation_anchor=空间/所属关系；
  scene_state=整体状态；dialogue_phrase=功能/语气词；timeline_segment=时间；comparison_zone=对比
- visual_target：该词在画面里的具体挂靠对象（中文短语），供插图构图与锚点定位用

【单词列表】（word | 中文释义）
{word_lines}
"""


def load_env() -> str:
    key = os.environ.get("ARK_API_KEY", "").strip()
    env_file = TOOLS / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*ARK_API_KEY\s*=\s*(.+)", line)
            if m:
                key = m.group(1).strip().strip('"').strip("'")
    if not key:
        sys.exit("缺少 ARK_API_KEY：写入环境变量或 flashcard/tools/.env")
    return key


def chat(key: str, prompt: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            r = requests.post(
                ARK_BASE + "/chat/completions",
                headers={"Authorization": "Bearer " + key},
                json={"model": TEXT_MODEL, "temperature": 0.3, "max_tokens": 8192,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=120)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = 5 * (attempt + 1)
            print(f"  chat 调用失败（{e}），{wait}s 后重试…", file=sys.stderr)
            time.sleep(wait)
    raise AssertionError("unreachable")


def extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("模型输出不含 JSON")
    return json.loads(m.group(0))


def validate(data: dict, unit_words: list[str], ipa_map: dict) -> list[dict]:
    """确定性校验（Stage 03 Review Gate）：覆盖恰好一次、词数 1-6、枚举合法。"""
    cards = data.get("cards")
    if not isinstance(cards, list) or not cards:
        raise ValueError("cards 为空")
    seen: dict[str, int] = {}
    for i, card in enumerate(cards):
        targets = card.get("targets") or []
        if not 1 <= len(targets) <= 6:
            raise ValueError(f"卡 {i} 词数 {len(targets)} 越界（1-6）")
        if card.get("card_type") not in CARD_TYPES:
            raise ValueError(f"卡 {i} card_type 非法: {card.get('card_type')}")
        card.setdefault("scene_title_cn", "")
        card.setdefault("scene_title_en", "")
        card.setdefault("scene_brief", "")
        for t in targets:
            w = (t.get("word") or "").strip()
            if not w:
                raise ValueError(f"卡 {i} 存在空 word")
            if t.get("mapping_mode") not in MAPPING_MODES:
                raise ValueError(f"卡 {i} 词 {w} mapping_mode 非法")
            t["connector"] = CONNECTOR_BY_MODE[t["mapping_mode"]]  # 策略以规则为准
            t["ipa"] = ipa_map.get(w, "")
            seen[w] = seen.get(w, 0) + 1
    missing = [w for w in unit_words if w not in seen]
    dup = [w for w, n in seen.items() if n > 1]
    extra = [w for w in seen if w not in unit_words]
    if missing or dup or extra:
        raise ValueError(f"覆盖校验失败 missing={missing} dup={dup} extra={extra}")
    return cards


def plan_unit(key: str, version: str, grade: dict, subject_name: str,
              unit: dict, ipa_map: dict) -> Path:
    word_lines = "\n".join(f"{w['word']} | {w.get('meaning', '')}" for w in unit["words"])
    prompt = PROMPT_TMPL.format(card_types=" / ".join(CARD_TYPES),
                                mapping_modes=" / ".join(MAPPING_MODES),
                                word_lines=word_lines)
    last_err = None
    for attempt in range(3):   # 校验失败把原因喂回去重试
        try:
            data = extract_json(chat(key, prompt))
            unit_words = [w["word"] for w in unit["words"]]
            cards = validate(data, unit_words, ipa_map)
            for i, card in enumerate(cards):
                card["card_id"] = card_id(version, grade["id"], unit["id"], i + 1)
                # 释义以词库为准（Stage 01：事实源），不信模型
                m_map = {w["word"]: w.get("meaning", "") for w in unit["words"]}
                for t in card["targets"]:
                    t["meaning"] = m_map[t["word"]]
            out = PLANS_DIR / f"{version}--{grade['id']}--{unit['id']}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({
                "version": version, "grade": grade["id"],
                "gradeName": grade["name"] + (grade.get("semester") or ""),
                "unit": unit["id"], "unitName": unit["name"],
                "cards": cards}, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  ✓ {unit['name']}: {len(cards)} 张卡 → {out.name}")
            return out
        except Exception as e:
            last_err = e
            prompt += f"\n\n【上次输出未通过校验：{e}。请修正后重新输出完整 JSON】"
    raise RuntimeError(f"{unit['name']} 三次校验仍失败: {last_err}")


def card_id(version: str, grade_id: str, unit_id: str, seq: int) -> str:
    vid = {"yilin": "YL", "nce": "NCE"}.get(version, version.upper()[:4])
    gid = re.sub(r"[^A-Za-z0-9]", "", grade_id).upper()
    uid = re.sub(r"[^A-Za-z0-9]", "", unit_id).upper()
    return f"{vid}-{gid}-{uid}-C{seq:02d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version")
    ap.add_argument("--grade")
    ap.add_argument("--unit")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    key = load_env()
    bank = json.loads(WORDBANK.read_text(encoding="utf-8"))
    ipa_map = json.loads(IPA_JSON.read_text(encoding="utf-8"))

    total_cards = 0
    for v in bank["versions"]:
        if args.version and v["id"] != args.version:
            continue
        # 只处理英语学科
        for g in v["grades"]:
            if args.grade and g["id"] != args.grade:
                continue
            eng = next((s for s in g["subjects"] if s["id"] == "english"), None)
            if not eng:
                continue
            for u in eng["units"]:
                if args.unit and u["id"] != args.unit:
                    continue
                out = plan_unit(key, v["id"], g, eng["name"], u, ipa_map)
                total_cards += len(json.loads(out.read_text(encoding="utf-8"))["cards"])
                time.sleep(0.5)  # 限流缓冲
    if not (args.version or args.all):
        sys.exit("请指定 --version/--grade/--unit 或 --all")
    print(f"完成，共生成 {total_cards} 张场景卡方案")
    return 0


if __name__ == "__main__":
    sys.exit(main())
