
import os, json, requests
from dotenv import load_dotenv
load_dotenv()
PLACE_ID = "ChIJTbHwCBLYVDURa-dv_jzE4pY"
api_key = os.environ.get("GOOGLE_API_KEY")
url = f"https://places.googleapis.com/v1/places/{PLACE_ID}"
headers = {"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": "displayName,rating,userRatingCount,reviews"}
resp = requests.get(url, headers=headers, timeout=20)
data = resp.json()
print("店舗名:", data.get("displayName",{}).get("text",""))
print("評価:", data.get("rating"))
print("件数:", data.get("userRatingCount"))
reviews = data.get("reviews",[])
print("取得件数:", len(reviews))
for i,r in enumerate(reviews):
    print(f"[{i+1}] 星{r.get(chr(114)+'ating','?')} {r.get('text',{}).get('text','')[:100]}")
print("完了")
