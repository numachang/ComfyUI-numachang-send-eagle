"""
Eagle のタグ汚染を除去するクリーンアップスクリプト

過去に send-eagle ノードが TagClassifier 整形済みテキストをそのまま送信したため、
Eagle のタグに以下の汚染が残っているケースがある:

- `//category\n<tag>` — カテゴリ見出しと改行が連結されたタグ
- `//category <tag>` — 同上 (改行なし)
- `#tag` — ハッシュタグプレフィックス付き

このスクリプトは Eagle v2 API を直接叩いて汚染タグを正規化する。
デフォルトは dry-run。`--apply` で実際に更新する。

使い方:
    python clean_eagle_tags.py                # dry-run
    python clean_eagle_tags.py --apply        # 実際に適用
    python clean_eagle_tags.py --limit 100    # 先頭100件のみ処理 (動作確認用)
"""
import argparse
import re
import sys
import time

import requests

BASE_URL = "http://localhost:41595"
PAGE_SIZE = 200


def clean_tag_to_list(tag: str) -> list:
    """汚染タグを正規化し、0 個以上のクリーンなタグ配列を返す。

    1つの汚染タグから複数タグが展開される可能性がある:
      `cheeky panties\\n#black_shirt` → `['cheeky panties', 'black_shirt']`
      `//composition\\n1girl` → `['1girl']`
      `//composition` → `[]`
    """
    if not isinstance(tag, str):
        return []

    results = []
    for line in tag.split("\n"):
        line = line.strip()
        if not line:
            continue
        # カテゴリ見出し行は丸ごと破棄
        if line.startswith("//"):
            continue
        # 行中のインラインコメント ( //... ) を除去
        line = re.sub(r"\s*//[^,\n]*", "", line).strip()
        if not line:
            continue
        # 先頭の # を除去
        line = line.lstrip("#").strip()
        if not line:
            continue
        # 連続空白を 1 つに潰す
        line = re.sub(r"\s+", " ", line)
        results.append(line)
    return results


def clean_tags(tags: list) -> list:
    """タグ配列を正規化。重複も排除。"""
    out = []
    seen = set()
    for t in tags:
        for c in clean_tag_to_list(t):
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out


def needs_clean(tags: list) -> bool:
    if not tags:
        return False
    return clean_tags(tags) != tags


def fetch_page(offset: int, limit: int) -> list:
    r = requests.get(f"{BASE_URL}/api/v2/item/get", params={"offset": offset, "limit": limit}, timeout=30)
    r.raise_for_status()
    payload = r.json()
    # v2 は data.data にアイテム配列が入る
    data = payload.get("data", {})
    if isinstance(data, dict):
        return data.get("data", [])
    return data if isinstance(data, list) else []


def fetch_all_items(max_items: int | None = None) -> list:
    items = []
    offset = 0
    while True:
        limit = PAGE_SIZE
        if max_items is not None:
            remain = max_items - len(items)
            if remain <= 0:
                break
            limit = min(PAGE_SIZE, remain)
        batch = fetch_page(offset, limit)
        if not batch:
            break
        items.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
        print(f"  fetched {len(items)} items...", flush=True)
    return items


def update_item_tags(item_id: str, tags: list) -> bool:
    try:
        r = requests.post(
            f"{BASE_URL}/api/v2/item/update",
            json={"id": item_id, "tags": tags},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        return body.get("status") == "success"
    except requests.RequestException as e:
        print(f"  [FAIL] {item_id}: {e}", flush=True)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="実際に更新する (指定しない場合は dry-run)")
    ap.add_argument("--limit", type=int, default=None, help="処理する最大アイテム数")
    ap.add_argument("--samples", type=int, default=10, help="dry-run 時に表示するサンプル件数")
    args = ap.parse_args()

    print(f"Connecting to Eagle at {BASE_URL}...")
    try:
        r = requests.get(f"{BASE_URL}/api/v2/app/info", timeout=5)
        r.raise_for_status()
        info = r.json().get("data", {})
        print(f"  Eagle {info.get('version', '?')} build {info.get('build', '?')}")
    except requests.RequestException as e:
        print(f"  Eagle に接続できません: {e}")
        sys.exit(1)

    print("Fetching items...")
    items = fetch_all_items(max_items=args.limit)
    print(f"Total items fetched: {len(items)}")

    targets = [it for it in items if needs_clean(it.get("tags", []))]
    print(f"Polluted items: {len(targets)}")

    if not targets:
        print("何も変更するものはありません。")
        return

    # 汚染タグのサンプル集計
    removed_counter = {}
    for it in targets:
        old = set(it.get("tags", []))
        new = set(clean_tags(it.get("tags", [])))
        for t in old - new:
            removed_counter[t] = removed_counter.get(t, 0) + 1
        for t in new - old:
            removed_counter.setdefault(f"+{t}", 0)
            removed_counter[f"+{t}"] += 1

    print(f"\n=== 変更サンプル (先頭 {args.samples} 件) ===")
    for it in targets[: args.samples]:
        old = it.get("tags", [])
        new = clean_tags(old)
        removed = [t for t in old if t not in new]
        added = [t for t in new if t not in old]
        print(f"\n[{it.get('id')}] {it.get('name', '?')[:60]}")
        if removed:
            print(f"  - removed: {removed[:8]}{' ...' if len(removed) > 8 else ''}")
        if added:
            print(f"  + added:   {added[:8]}{' ...' if len(added) > 8 else ''}")

    print(f"\n=== 削除タグ頻度 Top 20 ===")
    top = sorted(
        [(k, v) for k, v in removed_counter.items() if not k.startswith("+")],
        key=lambda kv: -kv[1],
    )[:20]
    for k, v in top:
        print(f"  {v:5d}  {repr(k)}")

    if not args.apply:
        print(f"\n(dry-run) --apply を付けて再実行すると {len(targets)} 件を更新します")
        return

    print(f"\n{len(targets)} 件を更新中...")
    ok = 0
    ng = 0
    t0 = time.time()
    for i, it in enumerate(targets, 1):
        new_tags = clean_tags(it.get("tags", []))
        if update_item_tags(it["id"], new_tags):
            ok += 1
        else:
            ng += 1
        if i % 100 == 0:
            print(f"  {i}/{len(targets)}  ok={ok} ng={ng}  ({time.time()-t0:.1f}s)", flush=True)
    print(f"\nDone. success={ok}, failed={ng}, elapsed={time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
