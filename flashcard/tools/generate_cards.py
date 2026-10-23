#!/usr/bin/env python3
"""generate_cards.py · 场景闪卡生产管线（Stage 05-11 的最小可运行实现）

每张卡四步：
  1. 提示词编译：场景简报 + 版式负空间指引 + 硬性禁文字条款（prompt_compiler_contract）
  2. Seedream 底图：纯净插图，零文字零标签零箭头（Stage 07 硬要求）
  3. Doubao 视觉锚点：返回各目标物归一化 bbox，锚点取中心
  4. PIL 确定性合成：白底标签卡（英文深蓝粗体/IPA 灰蓝/中文）+ 细连线（design_tokens v2.2.2）

产物：
  flashcard/cards/<version>--<grade>--<unit>/<card_id>.jpg   成品卡（部署）
  flashcard/cards/base/<card_id>.png                         底图（中间产物，不部署）
  flashcard/cards/manifest.json                              前端清单（部署）

用法：
  .venv/bin/python generate_cards.py --plans cards/plans/yilin--g3-up--u1.json
  .venv/bin/python generate_cards.py --all-plans --limit 5
  .venv/bin/python generate_cards.py --dry-run --only YL-G3UP-U1-C01   # 离线测试合成器
"""
import argparse
import base64
import io
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
PLANS_DIR = ROOT / "cards" / "plans"
BASE_DIR = ROOT / "cards" / "base"
CARDS_DIR = ROOT / "cards"
MANIFEST = CARDS_DIR / "manifest.json"

ARK_BASE = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
IMAGE_MODEL = os.environ.get("ARK_IMAGE_MODEL", "doubao-seedream-4-0-250828")
VISION_MODEL = os.environ.get("ARK_VISION_MODEL", "doubao-1-5-vision-pro-32k-250115")
IMAGE_SIZE = os.environ.get("ARK_IMAGE_SIZE", "1536x2048")   # design_tokens 参考画布 3:4

# design_tokens.json flashcard.visual.v2.2.2
EN_COLOR = "#0E2B6D"
IPA_COLOR = "#56677F"
CN_COLOR = "#333333"
LINE_COLOR = (183, 198, 216)        # #B7C6D8
LINE_ALPHA = 0.78
DOT_COLOR = (161, 180, 202)         # #A1B4CA
LABEL_FILL = (255, 255, 255, 250)   # opacity .98
EN_SIZE, EN_MIN = 68, 54
SUB_SIZE, SUB_MIN = 38, 28
SAFE = 48                            # 安全边距

FONT_EN = ("/System/Library/Fonts/Helvetica.ttc", 1)        # Helvetica Bold
FONT_IPA = ("/System/Library/Fonts/Helvetica.ttc", 0)       # Helvetica（IPA 全覆盖实测）
FONT_CN = ("/System/Library/Fonts/Hiragino Sans GB.ttc", 2) # 冬青黑体 W6


# ---------------------------------------------------------------- Ark 客户端

def load_key() -> str:
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


def ark_post(key: str, path: str, payload: dict, timeout: int = 180, retries: int = 3) -> dict:
    last = None
    for attempt in range(retries):
        try:
            r = requests.post(ARK_BASE + path,
                              headers={"Authorization": "Bearer " + key},
                              json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"ark {path} 重试 {retries} 次仍失败: {last}")


# ---------------------------------------------------------------- Stage 06 提示词编译

def compile_prompt(card: dict) -> str:
    """硬约束：一张底图；禁一切教学文字/标签/箭头/连线/锚点；预留标签负空间。"""
    # 只有需要连线的词才要求画面可见；dialogue_phrase 等功能词不进画面，防止诱导出气泡文字
    visible = [t["visual_target"] for t in card["targets"] if t.get("connector") != "none"]
    title = card.get("scene_title_cn") or card.get("scene_title_en")
    req = (f"。画面中必须清晰可见：{'、'.join(visible)}，"
           f"各自占据画面不同位置、互不遮挡、大小适中") if visible else ""
    return (
        f"一幅竖版 3:4 的儿童绘本插画，温馨 2D 故事书风格，柔和质感，圆润造型，"
        f"干净轮廓，饱和度受控，背景层次安静。场景主题：{title}。"
        f"画面内容：{card['scene_brief']}{req}。"
        f"构图要求：主体集中在中部，四周（尤其顶部两个角和底部两侧）留出干净的留白区域，"
        f"便于后期叠加文字标签。"
        f"严格禁止：画面中不得出现任何文字、字母、拼音、音标、汉字、数字、"
        f"标签框、对话框、气泡、箭头、指示线、圆点标记；不得出现水印和 logo。"
    )


# ---------------------------------------------------------------- Stage 07 底图

def gen_base(key: str, card: dict) -> Image.Image:
    data = ark_post(key, "/images/generations", {
        "model": IMAGE_MODEL,
        "prompt": compile_prompt(card),
        "size": IMAGE_SIZE,
        "response_format": "b64_json",
        "watermark": False,
    })
    b64 = data["data"][0]["b64_json"]
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


# ---------------------------------------------------------------- Stage 07.5 视觉锚点

ANCHOR_PROMPT = """这张图是小学英语单词卡的底图。请定位下列目标物在图中的位置，输出 JSON：
{{
  "anchors": [
    {{"word": "{words}", "box": [x1, y1, x2, y2]}}
  ]
}}
要求：坐标为归一化数值（0-1），box 框住目标物的可见主体；找不到的目标填 null。
只输出 JSON。

目标物清单：
{target_lines}"""


def detect_anchors(key: str, img: Image.Image, card: dict) -> dict:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    b64 = base64.b64encode(buf.getvalue()).decode()
    target_lines = "\n".join(f"- {t['word']}：{t['visual_target']}" for t in card["targets"])
    prompt = ANCHOR_PROMPT.replace("{words}", "").format(target_lines=target_lines)
    data = ark_post(key, "/chat/completions", {
        "model": VISION_MODEL,
        "temperature": 0,
        "max_tokens": 16384,   # reasoning 模型会先烧 reasoning_content，预算必须留足
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
        ]}],
    })
    text = data["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", text, re.S)
    out = {}

    def _collect(items):
        for a in items:
            if a.get("box"):
                x1, y1, x2, y2 = a["box"]
                out[a["word"]] = ((x1 + x2) / 2, (y1 + y2) / 2)

    if m:
        try:
            _collect(json.loads(m.group(0)).get("anchors", []))
        except json.JSONDecodeError:
            # 模型偶发输出非法 JSON：降级为逐条正则提取，能救回几个算几个
            for wm, bm in re.findall(
                    r'"word"\s*:\s*"([^"]+)"\s*,\s*"box"\s*:\s*\[([\d.,\s]+)\]', text):
                nums = [float(v) for v in bm.split(",") if v.strip()]
                if len(nums) == 4:
                    out[wm] = ((nums[0] + nums[2]) / 2, (nums[1] + nums[3]) / 2)
    return out


# ---------------------------------------------------------------- Stage 08 确定性合成

class Composer:
    def __init__(self, img: Image.Image):
        self.img = img
        self.W, self.H = img.size
        s = self.W / 1536  # 以 1536 宽为基准等比缩放
        self.s = s
        self.pad_x, self.pad_y = round(0.024 * self.W), round(0.016 * self.W)
        self.radius = round(0.018 * self.W)
        self.safe = round(SAFE * s)
        self.en = self._font(FONT_EN, round(EN_SIZE * s))
        self.ipa = self._font(FONT_IPA, round(SUB_SIZE * s))
        self.cn = self._font(FONT_CN, round(SUB_SIZE * s))
        self.placed: list[tuple[float, float, float, float]] = []   # 已放标签 bbox
        self.anchor_pts: list[tuple[float, float]] = []

    @staticmethod
    def _font(spec, size):
        path, idx = spec
        return ImageFont.truetype(path, size, index=idx)

    def _measure(self, word: str, ipa: str, meaning: str):
        """标签内尺寸；长词先缩字号（下限 EN_MIN），短语允许换行撑大卡。"""
        d = ImageDraw.Draw(self.img)
        s = self.s
        en_size = EN_SIZE
        en = self.en
        max_w = 0.34 * self.W
        while en_size > EN_MIN and d.textlength(word, font=en) > max_w:
            en_size -= 2
            en = self._font(FONT_EN, round(en_size * s))
        w_en = d.textlength(word, font=en)
        w_ipa = d.textlength(ipa, font=self.ipa) if ipa else 0
        w_cn = d.textlength(meaning, font=self.cn) if meaning else 0
        line_h = [round(en_size * s * 1.25), round(SUB_SIZE * s * 1.45), round(SUB_SIZE * s * 1.45)]
        w = max(w_en, w_ipa, w_cn) + 2 * self.pad_x
        h = line_h[0] + (line_h[1] if ipa else 0) + (line_h[2] if meaning else 0) + 2 * self.pad_y
        return en, w, h, line_h

    @staticmethod
    def _overlap(a, b) -> float:
        ox = max(0, min(a[2], b[2]) - max(a[0], b[0]))
        oy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        return ox * oy

    def _candidates(self, ax: float, ay: float, w: float, h: float):
        """锚点四周 8 个候选位（像素坐标）：上下正位、左右正位、四个斜位。"""
        gap = 0.06 * self.W
        spots = [
            (ax - w / 2, ay - gap - h),        # 上
            (ax - w / 2, ay + gap),            # 下
            (ax - gap - w, ay - h / 2),        # 左
            (ax + gap, ay - h / 2),            # 右
            (ax - gap - w * 0.75, ay - gap - h * 0.75 - h / 4),  # 左上
            (ax + gap - w * 0.25, ay - gap - h * 0.75 - h / 4),  # 右上
            (ax - gap - w * 0.75, ay + gap - h / 4),             # 左下
            (ax + gap - w * 0.25, ay + gap - h / 4),             # 右下
        ]
        out = []
        for x, y in spots:
            x = min(max(x, self.safe), self.W - self.safe - w)
            y = min(max(y, self.safe), self.H - self.safe - h)
            out.append((x, y))
        return out

    def place_label(self, word: str, ipa: str, meaning: str, anchor_norm):
        en, w, h, line_h = self._measure(word, ipa, meaning)
        W, H = self.W, self.H
        if anchor_norm:
            ax, ay = anchor_norm[0] * W, anchor_norm[1] * H
        else:
            ax, ay = W / 2, H * 0.55
        best, best_score = None, None
        anchor_r = 0.05 * self.W   # 目标物占位圈半径：标签不得压住（含自己的目标）
        for x, y in self._candidates(ax, ay, w, h):
            box = (x, y, x + w, y + h)
            score = math.hypot(x + w / 2 - ax, y + h / 2 - ay)          # 连线越短越好
            for p in self.placed:
                ov = self._overlap(box, p)
                if ov:
                    score += ov * 10 + 1e6                              # 标签重叠重罚
            for px, py in self.anchor_pts + [(ax, ay)]:                 # 压住任何锚点都罚
                zone = (px - anchor_r, py - anchor_r, px + anchor_r, py + anchor_r)
                if self._overlap(box, zone) > 0:
                    score += 8e5
            edge = min(x, y, W - (x + w), H - (y + h))                  # 贴边略加分（留白区多在边上）
            score -= edge * 0.05
            if best_score is None or score < best_score:
                best, best_score = (x, y), score
        if best_score is not None and best_score >= 8e5:
            # 锚点邻域放不下：全画布网格扫描找无冲突空位，取离锚点最近者
            step = round(0.05 * self.W)
            fallback, fb_score = None, None
            gy = self.safe
            while gy + h <= self.H - self.safe:
                gx = self.safe
                while gx + w <= self.W - self.safe:
                    box = (gx, gy, gx + w, gy + h)
                    conflict = any(self._overlap(box, p) > 0 for p in self.placed)
                    if not conflict:
                        for px, py in self.anchor_pts + [(ax, ay)]:
                            zone = (px - anchor_r, py - anchor_r, px + anchor_r, py + anchor_r)
                            if self._overlap(box, zone) > 0:
                                conflict = True
                                break
                    if not conflict:
                        sc = math.hypot(gx + w / 2 - ax, gy + h / 2 - ay)
                        if fb_score is None or sc < fb_score:
                            fallback, fb_score = (gx, gy), sc
                    gx += step
                gy += step
            if fallback:
                best = fallback
        x, y = best
        self.placed.append((x, y, x + w, y + h))
        if anchor_norm:
            self.anchor_pts.append((ax, ay))
        return {"box": [x, y, w, h], "en_font": en, "line_h": line_h}

    def compose(self, targets: list[dict], anchors: dict) -> tuple[Image.Image, list[dict]]:
        W, H = self.W, self.H
        s = self.s
        # 0. 无锚词（dialogue_phrase 等 connector=none）分配到顶部留白带的展开伪锚点，
        #    避免全部挤向画面中心压住主体；伪锚点不画连线
        floating = [t for t in targets if t.get("connector") == "none" and t["word"] not in anchors]
        for i, t in enumerate(floating):
            fx = 0.5 if len(floating) == 1 else 0.18 + 0.64 * i / (len(floating) - 1)
            anchors[t["word"]] = (fx, 0.14)
        # 1. 先摆位（全部算完再统一绘制，连线压在标签下）
        layouts = []
        for t in targets:
            anchor = anchors.get(t["word"])
            lay = self.place_label(t["word"], t.get("ipa", ""), t.get("meaning", ""), anchor)
            lay["target"] = t
            lay["anchor"] = anchor
            layouts.append(lay)

        canvas = self.img.convert("RGBA")

        # 2. 连线层（connector.v3.light：短线、无投影、端点小圆点）
        line_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(line_layer)
        for lay in layouts:
            if not lay["anchor"] or lay["target"].get("connector") == "none":
                continue
            x, y, w, h = lay["box"]
            ax, ay = lay["anchor"][0] * W, lay["anchor"][1] * H
            cx, cy = x + w / 2, y + h / 2
            # 从标签离锚点最近的一条边中点出发
            if abs(ax - cx) > abs(ay - cy):
                sx = x if ax < cx else x + w
                sy = min(max(ay, y), y + h)
            else:
                sx = min(max(ax, x), x + w)
                sy = y if ay < cy else y + h
            # 若连线会穿过别的标签，本卡 QA 里报 warning（重排由人工/迭代处理）
            rgba = LINE_COLOR + (round(255 * LINE_ALPHA),)
            ld.line([(sx, sy), (ax, ay)], fill=rgba, width=max(2, round(3 * s)))
            r = 4 * s
            ld.ellipse([ax - r, ay - r, ax + r, ay + r], fill=DOT_COLOR + (235,))
            r2 = r + 1.2 * s
            ld.ellipse([ax - r2, ay - r2, ax + r2, ay + r2], outline=(255, 255, 255, 200), width=max(1, round(1 * s)))
        canvas.alpha_composite(line_layer)

        # 3. 标签卡层：柔和投影 + 白底圆角 + 三行文字
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        for lay in layouts:
            x, y, w, h = lay["box"]
            sd.rounded_rectangle([x, y + 3 * s, x + w, y + h + 3 * s],
                                 radius=self.radius, fill=(31, 35, 40, 42))
        shadow = shadow.filter(ImageFilter.GaussianBlur(6 * s))
        canvas.alpha_composite(shadow)

        d = ImageDraw.Draw(canvas)
        for lay in layouts:
            x, y, w, h = lay["box"]
            t = lay["target"]
            d.rounded_rectangle([x, y, x + w, y + h], radius=self.radius, fill=LABEL_FILL)
            cy = y + self.pad_y
            lines = [(t["word"], lay["en_font"], EN_COLOR, lay["line_h"][0])]
            if t.get("ipa"):
                lines.append((t["ipa"], self.ipa, IPA_COLOR, lay["line_h"][1]))
            if t.get("meaning"):
                lines.append((t["meaning"], self.cn, CN_COLOR, lay["line_h"][2]))
            for text, font, color, lh in lines:
                tw = d.textlength(text, font=font)
                d.text((x + (w - tw) / 2, cy), text, font=font, fill=color)
                cy += lh
            # 写回归一化坐标供前端点读热区
            lay["label_norm"] = [round(x / W, 4), round(y / H, 4),
                                 round(w / W, 4), round(h / H, 4)]

        return canvas.convert("RGB"), layouts

    def qa(self, layouts: list[dict]) -> list[str]:
        """Stage 09 确定性检查：重叠 / 越界 / 超长连线 / 缺锚。"""
        issues = []
        boxes = [((l["box"][0], l["box"][1],
                  l["box"][0] + l["box"][2], l["box"][1] + l["box"][3]),
                  l["target"]["word"]) for l in layouts]   # [x,y,w,h] → (x1,y1,x2,y2)
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if self._overlap(boxes[i][0], boxes[j][0]) > 0:
                    issues.append(f"标签重叠: {boxes[i][1]} × {boxes[j][1]}")
        diag = math.hypot(self.W, self.H)
        for l in layouts:
            t = l["target"]
            x, y, w, h = l["box"]
            if x < 0 or y < 0 or x + w > self.W or y + h > self.H:
                issues.append(f"标签越界: {t['word']}")
            if l.get("anchor") and t.get("connector") != "none":
                ax, ay = l["anchor"][0] * self.W, l["anchor"][1] * self.H
                dist = math.hypot(x + w / 2 - ax, y + h / 2 - ay)
                if dist > 0.45 * diag:
                    issues.append(f"连线过长: {t['word']} ({dist / diag:.2f} diag)")
                anchor_r = 0.05 * self.W
                zone = (ax - anchor_r, ay - anchor_r, ax + anchor_r, ay + anchor_r)
                if self._overlap((x, y, x + w, y + h), zone) > 0:
                    issues.append(f"标签压住目标物: {t['word']}")
            if t.get("connector") == "required" and not l.get("anchor"):
                issues.append(f"缺锚点: {t['word']}")
        return issues


# ---------------------------------------------------------------- 主流程

def placeholder_base(card: dict) -> tuple[Image.Image, dict]:
    """--dry-run：合成假底图（米色底 + 目标物占位圆），锚点固定，专测合成器。"""
    W, H = 1536, 2048
    img = Image.new("RGB", (W, H), (246, 241, 231))
    d = ImageDraw.Draw(img)
    n = len(card["targets"])
    anchors = {}
    for i, t in enumerate(card["targets"]):
        cx = 0.25 + 0.5 * (i % 3) / 2 * 2 if n > 1 else 0.5
        cx = 0.22 + (0.56 * (i % 3)) if n > 1 else 0.5
        cy = 0.30 + 0.25 * (i // 3)
        anchors[t["word"]] = (min(cx, 0.85), min(cy, 0.8))
        r = 90
        x, y = anchors[t["word"]][0] * W, anchors[t["word"]][1] * H
        d.ellipse([x - r, y - r, x + r, y + r], fill=(150 + (i * 40) % 90, 170, 190))
    return img, anchors


def process_card(card: dict, key: str | None, dry_run: bool) -> dict:
    cid = card["card_id"]
    base_path = BASE_DIR / f"{cid}.png"
    if dry_run:
        img, anchors = placeholder_base(card)
    else:
        if base_path.exists():                      # 底图可复用：合成返工不重复生图
            img = Image.open(base_path).convert("RGB")
        else:
            img = gen_base(key, card)
            base_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(base_path)
        anchors_path = BASE_DIR / f"{cid}.anchors.json"
        if anchors_path.exists():
            anchors = {k: tuple(v) for k, v in json.loads(anchors_path.read_text()).items()}
        else:
            anchors = detect_anchors(key, img, card)
            anchors_path.write_text(json.dumps(anchors))
        # 缺锚重试一次视觉定位
        missing = [t["word"] for t in card["targets"]
                   if t.get("connector") != "none" and t["word"] not in anchors]
        if missing:
            anchors2 = detect_anchors(key, img, card)
            anchors.update({k: v for k, v in anchors2.items() if k in missing})
            anchors_path.write_text(json.dumps(anchors))

    composer = Composer(img)
    final, layouts = composer.compose(card["targets"], anchors)
    issues = composer.qa(layouts)

    unit_dir = CARDS_DIR / card["unit_dir"]
    unit_dir.mkdir(parents=True, exist_ok=True)
    out_path = unit_dir / f"{cid}.jpg"
    final.save(out_path, "JPEG", quality=88)

    return {
        "id": cid,
        "unitKey": card["unit_key"],
        "unitName": card["unit_name"],
        "sceneTitle": card.get("scene_title_cn") or card.get("scene_title_en"),
        "image": f"cards/{card['unit_dir']}/{cid}.jpg",
        "words": [{
            "word": l["target"]["word"],
            "ipa": l["target"].get("ipa", ""),
            "meaning": l["target"].get("meaning", ""),
            "label": l.get("label_norm"),
            "anchor": [round(a, 4) for a in l["anchor"]] if l.get("anchor") else None,
        } for l in layouts],
        "qa": issues,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="*", help="plan json 路径（可多个）")
    ap.add_argument("--all-plans", action="store_true")
    ap.add_argument("--only", nargs="*", help="只跑这些 card_id")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true", help="不调 API，用占位底图测合成器")
    args = ap.parse_args()

    plan_files = []
    if args.all_plans:
        plan_files = sorted(PLANS_DIR.glob("*.json"))
    for p in args.plans or []:
        plan_files.append(Path(p))
    if not plan_files:
        sys.exit("请指定 --plans 或 --all-plans")

    cards = []
    for pf in plan_files:
        data = json.loads(pf.read_text(encoding="utf-8"))
        for c in data["cards"]:
            c["unit_dir"] = f"{data['version']}--{data['grade']}--{data['unit']}"
            c["unit_key"] = f"{data['version']}/{data['grade']}/{data['unit']}"
            c["unit_name"] = data["unitName"]
            cards.append(c)
    if args.only:
        want = set(args.only)
        cards = [c for c in cards if c["card_id"] in want]
    if args.limit:
        cards = cards[: args.limit]
    if not cards:
        sys.exit("没有匹配的卡")

    key = None if args.dry_run else load_key()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {"cards": []}
    by_id = {c["id"]: c for c in manifest["cards"]}

    qa_fail = 0
    for i, card in enumerate(cards):
        cid = card["card_id"]
        try:
            entry = process_card(card, key, args.dry_run)
            by_id[cid] = entry
            status = "QA⚠ " + "; ".join(entry["qa"]) if entry["qa"] else "OK"
            if entry["qa"]:
                qa_fail += 1
            print(f"[{i + 1}/{len(cards)}] {cid} → {entry['image']}  {status}")
        except Exception as e:
            print(f"[{i + 1}/{len(cards)}] {cid} 失败: {e}", file=sys.stderr)
        if not args.dry_run:
            time.sleep(1)

    manifest["cards"] = sorted(by_id.values(), key=lambda c: c["id"])
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"manifest 更新：{len(manifest['cards'])} 张卡；QA 告警 {qa_fail} 张")
    return 0


if __name__ == "__main__":
    sys.exit(main())
