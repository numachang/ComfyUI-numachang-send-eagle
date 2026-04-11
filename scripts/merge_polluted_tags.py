"""
Eagle の汚染タグをグローバルに整理するスクリプト (tag/merge 版)

clean_eagle_tags.py の per-item update 方式は list API の 3689件上限に
引っかかるため、タグ単位で API を叩く tag/merge 方式で全体を処理する。

処理の流れ:
  1. /api/v2/tag/get でタグ全件取得 (ページング)
  2. 汚染タグ (`//` / `#` / 改行を含むもの) を分類:
     - zero:  クリーニング後に空になるもの (丸ごと削除)
     - one:   1タグに正規化されるもの (tag/merge で処理)
     - multi: 複数タグに分離するもの (per-item update で処理)
  3. apply モードで実行

使い方:
    python merge_polluted_tags.py              # dry-run
    python merge_polluted_tags.py --apply      # 実際に適用
"""
import argparse
import json
import re
import sys
import time

import requests

BASE_URL = "http://localhost:41595"


def clean_tag_to_list(tag):
    """汚染タグを 0 個以上のクリーンタグ配列に正規化する。"""
    if not isinstance(tag, str):
        return []
    results = []
    for line in tag.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("//"):
            continue
        line = re.sub(r"\s*//[^,\n]*", "", line).strip()
        if not line:
            continue
        line = line.lstrip("#").strip()
        if not line:
            continue
        line = re.sub(r"\s+", " ", line)
        results.append(line)
    return results


def is_polluted(name):
    return "//" in name or name.startswith("#") or "\n" in name


def fetch_all_tags():
    tags = []
    offset = 0
    while True:
        r = requests.get(f"{BASE_URL}/api/v2/tag/get", params={"offset": offset, "limit": 1000}, timeout=30)
        r.raise_for_status()
        data = r.json().get("data", [])
        if isinstance(data, dict):
            data = data.get("data", data.get("tags", []))
        if not data:
            break
        tags.extend(data)
        if len(data) < 1000:
            break
        offset += 1000
    return tags


def classify_polluted(tags):
    """汚染タグを zero / one / multi に分類する。"""
    zero, one, multi = [], [], []
    for t in tags:
        name = t.get("name", "")
        if not is_polluted(name):
            continue
        cleaned = clean_tag_to_list(name)
        count = t.get("imageCount", 0)
        entry = {"name": name, "cleaned": cleaned, "count": count}
        if len(cleaned) == 0:
            zero.append(entry)
        elif len(cleaned) == 1:
            one.append(entry)
        else:
            multi.append(entry)
    return zero, one, multi


def merge_tag(source, target):
    r = requests.post(
        f"{BASE_URL}/api/v2/tag/merge",
        json={"source": source, "target": target},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    return body.get("status") == "success", body.get("data", {})


def fetch_items_by_tag(tag_name):
    """指定タグを持つアイテムを取得する。"""
    r = requests.post(
        f"{BASE_URL}/api/v2/item/get",
        json={"tags": [tag_name], "limit": 1000},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json().get("data", [])
    if isinstance(data, dict):
        data = data.get("data", [])
    return data


def normalize_item_tags(tags):
    """アイテムの全タグを正規化し、クリーンな配列を返す。"""
    out = []
    seen = set()
    for t in tags:
        for c in clean_tag_to_list(t):
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out


def update_item_tags(item_id, tags):
    r = requests.post(
        f"{BASE_URL}/api/v2/item/update",
        json={"id": item_id, "tags": tags},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("status") == "success"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="実際に更新 (指定しない場合は dry-run)")
    args = ap.parse_args()

    print(f"Connecting to Eagle at {BASE_URL}...")
    print("Fetching all tags...")
    tags = fetch_all_tags()
    print(f"  total tags: {len(tags)}")

    zero, one, multi = classify_polluted(tags)
    print(f"\n=== Polluted tag classification ===")
    print(f"  zero  (drop):   {len(zero):4d} tags, {sum(e['count'] for e in zero):5d} image-refs")
    print(f"  one   (merge):  {len(one):4d} tags, {sum(e['count'] for e in one):5d} image-refs")
    print(f"  multi (split): {len(multi):4d} tags, {sum(e['count'] for e in multi):5d} image-refs")

    if zero:
        print("\n=== zero tags (drop entirely) ===")
        for e in zero:
            print(f"  {e['count']:5d}  {repr(e['name'])}")

    print("\n=== one tags top 10 ===")
    one.sort(key=lambda e: -e["count"])
    for e in one[:10]:
        print(f"  {e['count']:5d}  {repr(e['name'])}  ->  {repr(e['cleaned'][0])}")

    if multi:
        print("\n=== multi tags ===")
        multi.sort(key=lambda e: -e["count"])
        for e in multi:
            print(f"  {e['count']:5d}  {repr(e['name'])}  ->  {e['cleaned']}")

    if not args.apply:
        total = len(zero) + len(one) + len(multi)
        print(f"\n(dry-run) --apply を付けて再実行すると {total} 個の汚染タグを処理します")
        return

    t0 = time.time()

    # Pass 1: one -> tag/merge
    print(f"\n=== Pass 1: merging {len(one)} tags ===")
    ok = ng = 0
    for i, e in enumerate(one, 1):
        try:
            success, data = merge_tag(e["name"], e["cleaned"][0])
            if success:
                ok += 1
            else:
                ng += 1
                print(f"  [FAIL] {repr(e['name'])}: {data}")
        except Exception as ex:
            ng += 1
            print(f"  [FAIL] {repr(e['name'])}: {ex}")
        if i % 50 == 0:
            print(f"  {i}/{len(one)}  ok={ok} ng={ng}")
    print(f"  done: ok={ok} ng={ng}")

    # Pass 2: multi + zero -> per-item update
    to_fix_per_item = multi + zero
    if to_fix_per_item:
        print(f"\n=== Pass 2: per-item update for {len(to_fix_per_item)} tags ===")
        processed_items = set()
        for e in to_fix_per_item:
            try:
                items = fetch_items_by_tag(e["name"])
                print(f"  {repr(e['name'])}: {len(items)} items")
                for it in items:
                    if it["id"] in processed_items:
                        continue
                    new_tags = normalize_item_tags(it.get("tags", []))
                    if update_item_tags(it["id"], new_tags):
                        processed_items.add(it["id"])
                    else:
                        print(f"    [FAIL] {it['id']}")
            except Exception as ex:
                print(f"  [FAIL] {repr(e['name'])}: {ex}")
        print(f"  updated items: {len(processed_items)}")

    print(f"\nDone. elapsed={time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
