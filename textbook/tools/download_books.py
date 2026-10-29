#!/usr/bin/env python3
"""download_books.py · 批量下载教材（封面 + 正文）

数据源：tools/books.tsv（TSV，四列：id、书名、封面图 URL、正文 URL）。
正文两种形态自动识别：
  - 直链 PDF（…….pdf）→ 直接下载为 <书名>.pdf
  - 名编辑翻页电子书（……/index.html）→ 解析 mobile/javascript/config.js 的
    totalPageCount，按 files/mobile/{N}.jpg 逐页下载为 pages/001.jpg…

产物：books/<书名>/cover.jpg + book.pdf | pages/*.jpg + manifest.json
断点续跑：已存在且非空的文件自动跳过；失败重试 3 次。

用法：
  python3 tools/download_books.py                 # 全部
  python3 tools/download_books.py --only 137 149  # 只下指定 id
  python3 tools/download_books.py --dry-run       # 只打印计划不下载
依赖：仅标准库（python3 直接可跑）。
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TSV = ROOT / "tools" / "books.tsv"
OUT = ROOT / "books"

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def fetch(url: str, dest: Path, retries: int = 3) -> bool:
    """下载到 dest（已存在且非空则跳过）。返回是否新下载。"""
    if dest.exists() and dest.stat().st_size > 0:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
            tmp.rename(dest)
            return True
        except Exception as e:
            if attempt == retries - 1:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"{url} 下载失败: {e}")
            time.sleep(3 * (attempt + 1))
    return False


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")


def parse_rows() -> list[dict]:
    rows = []
    for line in TSV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = [f.strip() for f in line.split("\t") if f.strip()]
        rid = next((f for f in fields if re.fullmatch(r"\d+", f)), None)
        name = next((f for f in fields if "·" in f and not f.startswith("http")), None)
        urls = [f for f in fields if f.startswith("http") and "10.2.128.25" not in f]
        cover = next((u for u in urls if u.lower().endswith(".jpg")), None)
        body = next((u for u in urls if u.lower().endswith(".pdf")
                     or u.endswith("index.html")), None)
        if not (rid and name and body):
            print(f"  跳过无法解析的行: {line[:60]}…", file=sys.stderr)
            continue
        rows.append({"id": rid, "name": name, "cover": cover, "body": body})
    return rows


def download_flipbook(base: str, dest_dir: Path, dry_run: bool) -> int:
    """名编辑翻页书：返回页数。base 为 index.html 所在目录 URL。"""
    cfg_url = base + "mobile/javascript/config.js"
    cfg = fetch_text(cfg_url)
    m = re.search(r"totalPageCount\s*=\s*(\d+)", cfg)
    if not m:
        raise RuntimeError(f"config.js 未找到 totalPageCount: {cfg_url}")
    total = int(m.group(1))
    if dry_run:
        return total

    pages_dir = dest_dir / "pages"
    def one(n: int):
        return fetch(f"{base}files/mobile/{n}.jpg",
                     pages_dir / f"{n:03d}.jpg")
    got = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        for i, new in enumerate(ex.map(one, range(1, total + 1)), 1):
            got += 1
            if new and got % 20 == 0:
                print(f"    … {got}/{total} 页", flush=True)
    return total


def process(row: dict, dry_run: bool) -> str:
    name, body = row["name"], row["body"]
    dest_dir = OUT / name
    kind = "pdf" if body.lower().endswith(".pdf") else "flipbook"
    if dry_run:
        if kind == "pdf":
            return f"[预览] {name}（{row['id']}）PDF ← {body}"
        total = download_flipbook(body.rsplit("index.html", 1)[0], dest_dir, True)
        return f"[预览] {name}（{row['id']}）翻页书 {total} 页"

    if row["cover"]:
        fetch(row["cover"], dest_dir / "cover.jpg")
    if kind == "pdf":
        fetch(body, dest_dir / "book.pdf")
        pages = None
    else:
        base = body.rsplit("index.html", 1)[0]
        pages = download_flipbook(base, dest_dir, False)

    manifest = {"id": row["id"], "name": name, "type": kind,
                "pages": pages, "source": body,
                "downloadedAt": time.strftime("%Y-%m-%d %H:%M:%S")}
    (dest_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return f"✓ {name}（{row['id']}）" + (f" {pages} 页" if pages else " PDF")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="只处理这些 id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = parse_rows()
    if args.only:
        want = set(args.only)
        rows = [r for r in rows if r["id"] in want]
    if not rows:
        sys.exit("没有匹配的行（检查 books.tsv 或 --only 参数）")

    print(f"共 {len(rows)} 本，输出目录 {OUT}")
    fail = 0
    for i, row in enumerate(rows, 1):
        try:
            print(f"[{i}/{len(rows)}] {process(row, args.dry_run)}", flush=True)
        except Exception as e:
            fail += 1
            print(f"[{i}/{len(rows)}] ✗ {row['name']} 失败: {e}", file=sys.stderr)
    print(f"完成：成功 {len(rows) - fail}，失败 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
