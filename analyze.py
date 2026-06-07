"""Claude Haiku によるレビュー解析。コストを最小化するためHaikuを使用。"""
import os
import json
import anthropic

MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """あなたは飲食店経営コンサルタントです。Google レビューを分析して、
経営者が即座に判断できる形で要約します。常に以下のJSON形式のみで回答してください。

{
  "sentiment": "positive" | "neutral" | "negative",
  "urgency": 0-100の整数,
  "topics": ["料理", "接客", "価格", "待ち時間", "清潔感", "その他"のいずれか1-3個],
  "summary_ja": "30字以内の要約",
  "reply_draft_ja": "店舗から返信する文面のドラフト(150字程度、丁寧語)"
}

urgency判定基準:
- 90+: 衛生問題、食中毒、重大クレーム、SNS拡散リスク
- 70-89: 接客トラブル、明確な不満
- 40-69: 軽度の不満、改善要望
- 0-39: 中立〜好意的
"""


def analyze_review(review_text: str, rating: int, place_name: str) -> dict:
    """1件のレビューを解析。"""
    if not review_text.strip():
        return {
            "sentiment": "neutral",
            "urgency": 0,
            "topics": ["その他"],
            "summary_ja": "本文なし",
            "reply_draft_ja": "",
        }

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    user_msg = f"""店舗: {place_name}
評価: {rating}/5
本文: {review_text}

上記レビューを分析してJSONで返してください。"""

    resp = client.messages.create(
        model=MODEL,
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = resp.content[0].text.strip()
    # JSON以外の前後テキストを除去
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "sentiment": "neutral",
            "urgency": 50,
            "topics": ["その他"],
            "summary_ja": "解析失敗",
            "reply_draft_ja": raw[:200],
        }
