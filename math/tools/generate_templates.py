#!/usr/bin/env python3
"""generate_templates.py · 审题雷达 AI 出题模板生成器（离线批量入库）

按 catalog 的「年级 → 单元 → 章节」节点调用方舟文本模型生成题模板，
产物先落 math/data/generated/<grade>--<unit>.json，再用 --merge 合并进
problems.json（id 自动顺延，不覆盖手工模板）。合并前用 node 校验
（见 tools/_validate_generated.cjs），node selfcheck.js 做最终全量自检。

用法（在 math/tools 下）：
  python3 generate_templates.py --list                       # 看哪些节点还没模板
  python3 generate_templates.py --grade g4s --unit division  # 只生成某单元
  python3 generate_templates.py --grade g4s                  # 生成整册缺失单元
  python3 generate_templates.py --merge                      # 校验并合并全部生成产物
  python3 generate_templates.py --grade g4s --merge          # 生成 + 合并一条龙

key 读取顺序：ARK_API_KEY 环境变量 > math/tools/.env > flashcard/tools/.env
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent      # math/
TOOLS = ROOT / "tools"
DATA = ROOT / "data"
BANK = DATA / "problems.json"
GEN_DIR = DATA / "generated"

ARK_BASE = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
TEXT_MODEL = os.environ.get("ARK_TEXT_MODEL", "deepseek-v4-1-flash-261017")

# 该年级的题材适配说明，注入 prompt（按 2022 新课标教材章节）
UNIT_HINT = {
    "d-tens": "两、三位数除以整十数（除数 10/20/30/40...90），被除数两三位数，整除；情境：捆书、装盒、平均分",
    "d-two": "两、三位数除以两位数需四舍五入试商（如 96÷24、156÷12），结果整除；情境：装箱、排队、平均分配",
    "d-app": "用除法两步解决实际问题：先求总量/每份再平均分，或够不够；情境贴近购物、装箱、分配",
    "d-prop": "商的变化规律：被除数和除数同时乘或除以相同的数（0除外）商不变；或用连除 480÷12=480÷3÷4 简算；情境：同时扩大包装的运输问题",
    "pa-peri": "长方形/正方形周长 =（长+宽）×2 或边长×4；单位厘米/米/分米；情境：围篱笆、绕操场跑、花边长度",
    "pa-area": "长方形面积=长×宽，正方形面积=边长×边长；单位平方厘米/平方分米/平方米；情境：铺地砖、喷水池、花园面积",
    "pa-conv": "面积单位换算：1 平方分米=100 平方厘米，1 平方米=100 平方分米；情境：换算后比较或计算面积",
    "mr-speed": "速度×时间=路程，路程÷速度=时间，路程÷时间=速度；单位：千米/时、米/分、米/秒；情境：汽车、火车、步行、跑步",
    "mr-price": "单价×数量=总价，总价÷单价=数量，总价÷数量=单价；情境：买文具、买水果、团购",
    "mr-norm": "归一问题：先求一份量（单价/速度/每份数），再求几份；情境：照这样计算",
    "mx2-two": "不带括号的两步混合运算应用：先乘除后加减，如「买 3 盒彩笔每盒 12 元，付 50 元找回多少」",
    "mx2-bracket": "含小括号的两步混合运算应用：先算括号里，如「(50 - 8) ÷ 6」；注意区分 540÷3+6×2 与 540÷(3+6×2) 这类易混式",
    "bn-num": "认识万以上的数：万、十万、百万、千万、亿；读出/写出大数，比较大小；情境：人口、面积、星球距离",
    "bn-place": "数位顺序与十进制计数法：相邻计数单位进率是 10；数位表填空；情境：一个数里包含几个万/几个亿",
    "bn-conv": "把整万数改写成用「万」作单位、整亿数改写成用「亿」作单位；用四舍五入法求近似数；情境：人口约多少万",
    "bc-mul": "三位数乘两位数笔算应用：128×16 这类；情境：路程、总价、产量",
    "bc-est": "大数估算：估算三位数乘两位数或多位数加减；用「大约」表述；情境：预算够不够、大约多少",
    "a-basic": "平均数 = 总数 ÷ 份数；已知每项数据求平均；或已知平均和份数反求总数",
    "a-app": "平均数应用：移多补少、平均身高/平均分变化、总数=平均×份数；情境：考试成绩、身高、产量",
}

PROMPT_TMPL = """你是小学数学应用题命题专家，为「审题训练」工具编写参数化题模板。孩子读题后按「圈条件→列表格→想先算什么→列算式」四步解题，模板会被程序随机换数字批量出题。

【本次任务】苏教版数学 {grade_name}「{unit_name}」单元，为以下章节各出 6 个模板（每章 6 个，level 覆盖 1 基础 / 2 进阶 / 3 挑战 三档，同章不重复情境）：
{chapter_lines}
题材提示：{hint}

【模板 JSON 规范】（严格遵守，程序按此执行）
每个模板字段：
- id：字符串，先填 "NEW"（入库时统一编号）
- unit："{unit_id}"；chapter：对应章节 id（上面给出）
- type：standard 普通题 / distractor 含多余条件 / supply 缺条件需补充
- skill：从 condition relation hidden distractor supply bridge unit steps 中选 1-3 个
- level：1/2/3
- story：题干，数字一律用 {{n1}} {{n2}} 占位（如 "每箱{{n2}}盒"）
- question：问句（不含数字占位也可含）
- params：每个变量 [最小, 最大, 步长]，整数闭区间，如 "n1": [3, 9, 1]
- calc：计算式，按声明顺序求值，后面可引用前面。可用：+ - * / 和函数 div(a,b)整除取商 mod(a,b)取余 min max。除法结果需整数时用 div 并配约束 mod(...)==0
- constraints：约束数组，每条求值非 0 才保留这次随机（如 "mod({{total}}, {{n3}}) == 0"、"{{ans}} >= 10"）。必须保证随机 100 次至少 60 次能满足，否则出题失败
- segments：把 story 按语义切段，数组，每段 {{"t": "文本（含占位）", "type": "condition|distractor|plain"}}。**所有段 t 拼接必须恰好等于 story**（标点也要一致）
- relations：题干或问句里的关键词（如 "平均分装"、"每箱"），数组，可空
- table：{{"known": ["每箱 {{n2}} 盒", ...], "ask": "每筐 ？ 个"}}；supply 型 known 只放题面已有条件，另加 "supplyRow": "（补上条件的表格行）"
- bridge：两步及以上题给 {{"ask": "要求……先要算出", "choices": ["正确项", "干扰1", "干扰2"], "answer": 0}}；一步题 bridge 为 null
- steps：算式数组，如 "{{n1}} × {{n2}} = {{total}}（盒）"，运算符号用 × ÷，单位用全角括号
- answer：如 "{{ans}} 盒"
- tip：一句话审题锦囊，点出关键关系词或易错点
supply 型另加 supply 字段：{{"choices": ["正确补充条件（含 {{n0}}）", "干扰项", "干扰项"], "answer": 0, "why": "缺什么条件的说明"}}；{{n0}} 参与 calc。
distractor 型：多余条件的参数不要与有效参数撞值（加约束如 "{{n0}} != {{n1}}"）。

【范例模板】（照此风格与粒度）
{examples}

【输出】只输出 JSON（不要任何解释文字）：{{"templates": [ ... ]}}，共 {total} 个模板。
"""


def load_env() -> str:
    key = os.environ.get("ARK_API_KEY", "").strip()
    for env_file in (TOOLS / ".env", ROOT.parent / "flashcard" / "tools" / ".env"):
        if not env_file.exists():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(ARK_API_KEY|ARK_TEXT_MODEL|ARK_BASE_URL)\s*=\s*(.+)", line)
            if not m:
                continue
            name, val = m.group(1), m.group(2).strip().strip('"').strip("'")
            if name == "ARK_API_KEY" and not key:
                key = val
            elif name == "ARK_TEXT_MODEL" and not os.environ.get("ARK_TEXT_MODEL"):
                globals()["TEXT_MODEL"] = val
            elif name == "ARK_BASE_URL" and not os.environ.get("ARK_BASE_URL"):
                globals()["ARK_BASE"] = val
    if not key:
        sys.exit("缺少 ARK_API_KEY：写入环境变量或 math/tools/.env")
    return key


def chat(key: str, prompt: str, retries: int = 3) -> str:
    last_raw = ""
    for attempt in range(retries):
        try:
            r = requests.post(
                ARK_BASE + "/chat/completions",
                headers={"Authorization": "Bearer " + key},
                json={"model": TEXT_MODEL, "temperature": 0.7, "max_tokens": 32000,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=300)
            r.raise_for_status()
            payload = r.json()
            choice = payload["choices"][0]
            content = choice["message"]["content"]
            last_raw = content
            if choice.get("finish_reason") == "length":
                raise ValueError("输出被 max_tokens 截断（finish_reason=length）")
            if not content.strip():
                raise ValueError("返回为空（可能全部为 reasoning）")
            return content
        except Exception as e:
            if attempt == retries - 1:
                if os.environ.get("DEBUG_RAW") and last_raw:
                    print("---- 原始返回末尾 500 字 ----", file=sys.stderr)
                    print(last_raw[-500:], file=sys.stderr)
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


def bank_data() -> dict:
    return json.loads(BANK.read_text(encoding="utf-8"))


def unit_of(data: dict, grade_id: str, unit_id: str) -> dict | None:
    return next((u for u in data["meta"]["catalog"]["units"]
                 if u["gradeId"] == grade_id and u["id"] == unit_id), None)


def grade_name(data: dict, grade_id: str) -> str:
    return next(g["name"] for g in data["meta"]["catalog"]["grades"] if g["id"] == grade_id)


def pending_units(data: dict, grade_id: str) -> list[dict]:
    """该年级下「有章节但还没有任何模板」的单元"""
    out = []
    for u in data["meta"]["catalog"]["units"]:
        if u["gradeId"] != grade_id or not u.get("chapters"):
            continue
        has = any(t["unit"] == u["id"] for t in data["templates"])
        if not has:
            out.append(u)
    return out


def gen_unit(key: str, data: dict, grade_id: str, unit: dict, per_chapter: int,
             only_chapters: list[str] | None = None) -> Path:
    chapters = [c for c in unit["chapters"] if not only_chapters or c["id"] in only_chapters]
    chapter_lines = "\n".join(f"- {c['id']}：{c['name']}" for c in chapters)
    hint = "；".join(UNIT_HINT.get(c["id"], c["name"]) for c in chapters)
    examples = json.dumps([t for t in data["templates"] if t["id"] in ("T001", "T007")],
                          ensure_ascii=False, indent=1)
    prompt = PROMPT_TMPL.format(
        grade_name=grade_name(data, grade_id), unit_name=unit["name"], unit_id=unit["id"],
        chapter_lines=chapter_lines, hint=hint, examples=examples,
        total=len(chapters) * per_chapter)
    last_err = None
    for _ in range(3):
        try:
            tpls = extract_json(chat(key, prompt))["templates"]
            assert isinstance(tpls, list) and tpls, "templates 为空"
            want = {c["id"]: per_chapter for c in chapters}
            for t in tpls:
                t["unit"] = unit["id"]
                assert t.get("chapter") in want, f"chapter 不在本单元: {t.get('chapter')}"
                want[t["chapter"]] = want.get(t["chapter"], 0) - 1
            out = GEN_DIR / f"{grade_id}--{unit['id']}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"gradeId": grade_id, "unitId": unit["id"],
                                       "templates": tpls}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            print(f"  ✓ {unit['name']}: {len(tpls)} 个模板 → generated/{out.name}")
            return out
        except Exception as e:
            last_err = e
            print(f"  校验失败（{e}），重试…", file=sys.stderr)
            prompt += f"\n\n【上次输出未通过校验：{e}。请修正后重新输出完整 JSON】"
    raise RuntimeError(f"{unit['name']} 三次仍失败: {last_err}")


def validate_with_node(files: list[Path]) -> None:
    r = subprocess.run(["node", str(TOOLS / "_validate_generated.cjs")] + [str(f) for f in files],
                       capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    if r.returncode != 0:
        sys.exit("生成产物校验未通过，未合并")


def merge(files: list[Path]) -> None:
    data = bank_data()
    existing = {t["id"] for t in data["templates"]}
    seq = max([int(m.group(1)) for t in data["templates"]
               for m in [re.match(r"T(\d+)", t["id"])] if m], default=0)
    added = 0
    for f in files:
        gen = json.loads(f.read_text(encoding="utf-8"))
        unit = unit_of(data, gen["gradeId"], gen["unitId"])
        assert unit, f"{f.name}: 单元已不在 catalog"
        for t in gen["templates"]:
            while True:
                seq += 1
                tid = f"T{seq:03d}"
                if tid not in existing:
                    break
            t["id"] = tid
            t["unit"] = gen["unitId"]
            existing.add(tid)
            data["templates"].append(t)
            added += 1
        print(f"  合并 {f.name}")
    BANK.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"合并完成：新增 {added} 个模板，题库共 {len(data['templates'])} 个")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grade", default="g4s")
    ap.add_argument("--unit")
    ap.add_argument("--chapters", help="只生成这些章节 id，逗号分隔（如 d-adv,d-quot）")
    ap.add_argument("--per-chapter", type=int, default=6)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--merge-only", action="store_true")
    args = ap.parse_args()

    data = bank_data()
    if args.list:
        for g in data["meta"]["catalog"]["grades"]:
            pend = pending_units(data, g["id"])
            print(f"{g['name']}：待生成 {len(pend)} 个单元")
            for u in pend:
                print(f"  - {u['id']} {u['name']}（{len(u['chapters'])} 章）")
        return 0

    if args.merge_only:
        files = sorted(GEN_DIR.glob("*.json"))
        if not files:
            sys.exit("generated/ 下没有产物")
        validate_with_node(files)
        merge(files)
        return 0

    if args.unit:
        u = unit_of(data, args.grade, args.unit)
        if not u:
            sys.exit(f"catalog 里找不到 {args.grade}/{args.unit}")
        units = [u]
    else:
        units = pending_units(data, args.grade)
        if not units:
            print("该年级没有待生成的单元")
            return 0

    key = load_env()
    only = args.chapters.split(",") if args.chapters else None
    files = []
    for u in units:
        chapters = [c for c in u["chapters"] if not only or c["id"] in only]
        print(f"生成 {u['name']}（{len(chapters)} 章 × {args.per_chapter}）…")
        files.append(gen_unit(key, data, args.grade, u, args.per_chapter, only))
        time.sleep(0.5)
    if args.merge:
        validate_with_node(files)
        merge(files)
    else:
        print("已生成。检查后用 --merge-only 校验并入库")
    return 0


if __name__ == "__main__":
    sys.exit(main())
