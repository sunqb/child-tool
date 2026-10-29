# 审题雷达 · 生成管线

`generate_templates.py`：按 catalog 的「年级 → 单元 → 章节」调用方舟文本模型批量生成题模板，校验后合并入 `data/problems.json`。

## 用法

```bash
# 环境：python3 + requests（可复用 flashcard/tools/.venv）
PY=../..  # 示例：用 flashcard 的 venv
PY=/Volumes/samsungssd/github/child-tool/flashcard/tools/.venv/bin/python

$PY generate_templates.py --list                      # 看哪些单元还没模板
$PY generate_templates.py --grade g4s                 # 生成整册缺失单元（不合并）
$PY generate_templates.py --grade g4s --unit chance   # 只生成某单元
$PY generate_templates.py --grade g4s --unit division --chapters d-adv,d-period  # 指定章节
$PY generate_templates.py --merge-only                # 校验 generated/ 全部产物并合并入库
$PY generate_templates.py --grade g4s --merge         # 生成 + 合并一条龙
```

## 密钥

`ARK_API_KEY` 读取顺序：环境变量 → `math/tools/.env` → `flashcard/tools/.env`。
`.env` 同时支持 `ARK_TEXT_MODEL` / `ARK_BASE_URL` 覆盖默认模型与端点。

## 校验流程

1. 生成产物先落 `data/generated/<grade>--<unit>.json`（中间产物，可审查）
2. `--merge` 时先跑 `_validate_generated.cjs`：真实实例化 60 次/模板，检查 segments 拼接、约束通过率 ≥50%、分支生效等
3. 合并后跑 `node selfcheck.js` 全量自检（含 relations 词必须在题干/问句中）

注意：合并后 `generated/` 产物**不会自动清空**，再次 `--merge-only` 会重复入库。
如某单元要重新生成，先删对应 `generated/*.json` 再跑。

## 已知教训

- AI 生成的「周期规律」题倾向用 `constraints` 把余数锁成固定值保证答案唯一——训练价值低。入库前检查：`d-period` 等章节应改用 `answers` 条件分支让答案随数字变化（见 T050-T055 写法）。
- AI 生成的 `relations` 常含「比……多」这类省略号写法，selfcheck 会报「关系词不在题干或问题中」，需改成文中实有词或删除。
