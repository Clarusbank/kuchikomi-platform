"""Google Places API (New) からレビュー取得。

Docs: https://developers.google.com/maps/documentation/places/web-service/place-details

注意: New APIは1回のリクエストで最新5件まで。FieldMaskでreviewsを指定する。
reviews取得は「Enterprise」課金ティアだが、$200/月の無料枠でカバー可能。
"""
import os
import requests
from typing import List, Dict

API_URL = "https://places.googleapis.com/v1/places/{place_id}"


def fetch_place_reviews(place_id: str) -> List[Dict]:
    """指定 place_id のレビュー(最新5件)を取得。"""
    api_key = os.environ["GOOGLE_API_KEY"]
    url = API_URL.format(place_id=place_id)
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "id,displayName,rating,userRatingCount,reviews",
    }
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    reviews = []
    for r in data.get("reviews", []):
        reviews.append({
            "author": r.get("authorAttribution", {}).get("displayName", "anonymous"),
            "rating": r.get("rating", 0),
            "text": r.get("text", {}).get("text", ""),
            "publish_time": r.get("publishTime", ""),
            "relative_time": r.get("relativePublishTimeDescription", ""),
        })
    return reviews


def fetch_place_summary(place_id: str) -> Dict:
    """店舗全体の評価サマリー(平均評価・件数)も合わせて返す。"""
    api_key = os.environ["GOOGLE_API_KEY"]
    url = API_URL.format(place_id=place_id)
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "rating,userRatingCount,displayName",
    }
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    return {
        "name": data.get("displayName", {}).get("text", ""),
        "rating": data.get("rating", 0),
        "review_count": data.get("userRatingCount", 0),
    }
