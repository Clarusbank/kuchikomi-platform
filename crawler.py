"""口コミ取得・解析ロジック。全ユーザー分を横断して実行。"""
import os
from db import init_db, get_all_users, get_stores, review_hash, is_seen, mark_seen
from fetch_reviews import fetch_place_reviews
from analyze import analyze_review

URGENCY_THRESHOLD = int(os.environ.get("URGENCY_THRESHOLD","70"))

def run_daily_check(target_user_id=None):
    init_db()
    users = get_all_users()
    if target_user_id:
        users = [u for u in users if u["id"] == target_user_id]
    total_new = 0
    for user in users:
        uid = user["id"]
        stores = get_stores(uid)
        for store in stores:
            try:
                reviews = fetch_place_reviews(store["place_id"])
            except Exception as e:
                print(f"[CRAWLER ERROR] {store['name']}: {e}"); continue
            for r in reviews:
                h = review_hash(store["place_id"], r["author"], r["publish_time"])
                if is_seen(uid, h): continue
                analysis = {}
                if store["is_own"]:
                    try: analysis = analyze_review(r["text"], r["rating"], store["name"])
                    except Exception as e: print(f"[ANALYZE ERROR] {e}")
                mark_seen(uid, h, store["place_id"], store["name"],
                          r["author"], r["rating"], r["text"], r["publish_time"], analysis)
                total_new += 1
        print(f"[CRAWLER] user_id={uid} ({user['name']}) 完了")
    return total_new
