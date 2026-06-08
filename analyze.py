"""
analyzer.py — PDCA自動分析・スコア配点最適化エンジン
勝率データからスコア配点を自動調整してconfig.pyを更新する
"""

import json
import os
from datetime import datetime, timezone

CONFIG_PATH = os.path.expanduser("~/insider_bot/config.py")
SIGNALS_PATH = os.path.expanduser("~/insider_bot/pending_signals.json")
ANALYSIS_PATH = os.path.expanduser("~/insider_bot/pdca_analysis.json")
MIN_SIGNALS_FOR_PDCA = 10   # PDCA実行に必要な最低シグナル数


def run_pdca_analysis() -> dict:
    """
    全シグナルの勝率を分析してPDCA結果を返す
    各スコア項目と勝率の相関を計算する
    """
    try:
        with open(SIGNALS_PATH, "r") as f:
            signals = json.load(f)
    except Exception:
        return {"status": "データ不足", "signals": 0}

    completed = [
        s for s in signals.values()
        if s.get("check_24h") and s["data"].get("勝敗24h") in ("勝", "負")
    ]

    if len(completed) < MIN_SIGNALS_FOR_PDCA:
        return {
            "status": f"データ不足 ({len(completed)}/{MIN_SIGNALS_FOR_PDCA}件)",
            "signals": len(completed),
        }

    # 全体勝率
    wins = sum(1 for s in completed if s["data"]["勝敗24h"] == "勝")
    win_rate = wins / len(completed)

    # スコア別勝率分析
    score_analysis = {}
    for threshold in [70, 75, 80, 85, 90]:
        high_score = [
            s for s in completed
            if float(s["data"].get("スコア", 0)) >= threshold
        ]
        if high_score:
            wins_high = sum(1 for s in high_score if s["data"]["勝敗24h"] == "勝")
            score_analysis[f"score_{threshold}+"] = {
                "count": len(high_score),
                "win_rate": wins_high / len(high_score),
            }

    # 項目別分析
    feature_analysis = _analyze_features(completed)

    result = {
        "status": "分析完了",
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "signals": len(completed),
        "overall_win_rate": round(win_rate, 3),
        "score_analysis": score_analysis,
        "feature_analysis": feature_analysis,
        "recommendations": _generate_recommendations(win_rate, feature_analysis),
    }

    with open(ANALYSIS_PATH, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result


def _analyze_features(completed: list) -> dict:
    """各スコア項目が勝率に与える影響を分析"""
    features = {}

    # 新規ウォレット（年齢<7日）の勝率
    new_wallet = [s for s in completed if float(s["data"].get("ウォレット年齢日") or 99) <= 7]
    old_wallet = [s for s in completed if float(s["data"].get("ウォレット年齢日") or 0) > 7]
    if new_wallet:
        w = sum(1 for s in new_wallet if s["data"]["勝敗24h"] == "勝")
        features["new_wallet_win_rate"] = round(w / len(new_wallet), 3)
    if old_wallet:
        w = sum(1 for s in old_wallet if s["data"]["勝敗24h"] == "勝")
        features["old_wallet_win_rate"] = round(w / len(old_wallet), 3)

    # 直前入金ありの勝率
    with_deposit = [s for s in completed if s["data"].get("入金タイミングh") and float(s["data"]["入金タイミングh"]) <= 24]
    no_deposit = [s for s in completed if not s["data"].get("入金タイミングh") or float(s["data"].get("入金タイミングh", 99)) > 24]
    if with_deposit:
        w = sum(1 for s in with_deposit if s["data"]["勝敗24h"] == "勝")
        features["deposit_24h_win_rate"] = round(w / len(with_deposit), 3)
    if no_deposit:
        w = sum(1 for s in no_deposit if s["data"]["勝敗24h"] == "勝")
        features["no_deposit_win_rate"] = round(w / len(no_deposit), 3)

    # ヘッジ判定別
    pure_spec = [s for s in completed if s["data"].get("ヘッジ判定") == "純投機"]
    hedged = [s for s in completed if s["data"].get("ヘッジ判定") == "ヘッジ"]
    if pure_spec:
        w = sum(1 for s in pure_spec if s["data"]["勝敗24h"] == "勝")
        features["pure_speculation_win_rate"] = round(w / len(pure_spec), 3)
    if hedged:
        w = sum(1 for s in hedged if s["data"]["勝敗24h"] == "勝")
        features["hedge_win_rate"] = round(w / len(hedged), 3)

    return features


def _generate_recommendations(win_rate: float, features: dict) -> list:
    """勝率分析から改善提案を生成"""
    recs = []

    if win_rate < 0.50:
        recs.append("⚠️ 全体勝率50%未満 → スコア閾値を75点以上に引き上げ推奨")
    elif win_rate >= 0.65:
        recs.append("✅ 勝率良好 → スコア閾値を65点に下げて機会を増やすことを検討")

    new_wr = features.get("new_wallet_win_rate", 0)
    old_wr = features.get("old_wallet_win_rate", 0)
    if new_wr > old_wr + 0.15:
        recs.append(f"📈 新規ウォレット勝率({new_wr:.0%}) > 既存({old_wr:.0%}) → ウォレット年齢スコアの配点増加を推奨")
    elif old_wr > new_wr + 0.15:
        recs.append(f"📉 既存ウォレット勝率({old_wr:.0%}) > 新規({new_wr:.0%}) → ウォレット年齢スコアの配点減少を推奨")

    dep_wr = features.get("deposit_24h_win_rate", 0)
    no_dep_wr = features.get("no_deposit_win_rate", 0)
    if dep_wr > no_dep_wr + 0.15:
        recs.append(f"💰 直前入金あり勝率({dep_wr:.0%}) >> なし({no_dep_wr:.0%}) → 入金タイミングスコアの配点増加を強く推奨")

    spec_wr = features.get("pure_speculation_win_rate", 0)
    hedge_wr = features.get("hedge_win_rate", 0)
    if hedge_wr > spec_wr + 0.10:
        recs.append("⚠️ ヘッジポジションの勝率が高い → ヘッジ減点ロジックの見直しを推奨")

    if not recs:
        recs.append("✅ 現在のパラメータは適切です")

    return recs


def auto_adjust_thresholds(analysis: dict) -> bool:
    """
    分析結果に基づいてconfig.pyの閾値を自動調整する
    大きな変更は行わず、小幅な調整のみ
    """
    win_rate = analysis.get("overall_win_rate", 0)
    recs = analysis.get("recommendations", [])
    signals = analysis.get("signals", 0)

    if signals < MIN_SIGNALS_FOR_PDCA:
        return False

    try:
        content = open(CONFIG_PATH).read()
        original = content

        # 勝率が40%未満なら閾値を上げる
        if win_rate < 0.40:
            content = content.replace(
                "INSIDER_SCORE_THRESHOLD = 70",
                "INSIDER_SCORE_THRESHOLD = 75"
            )
            print("[Analyzer] スコア閾値を75点に引き上げ（勝率低下対応）")

        # 勝率が70%以上なら閾値を下げる（機会増加）
        elif win_rate >= 0.70:
            content = content.replace(
                "INSIDER_SCORE_THRESHOLD = 75",
                "INSIDER_SCORE_THRESHOLD = 70"
            )
            content = content.replace(
                "INSIDER_SCORE_THRESHOLD = 70",
                "INSIDER_SCORE_THRESHOLD = 65"
            )
            print("[Analyzer] スコア閾値を65点に引き下げ（勝率良好）")

        if content != original:
            open(CONFIG_PATH, "w").write(content)
            return True

    except Exception as e:
        print(f"[Analyzer] config更新エラー: {e}")

    return False


def generate_pdca_report(analysis: dict) -> str:
    """Telegram送信用のPDCAレポートを生成"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if analysis.get("status") != "分析完了":
        return (
            f"📊 PDCA分析レポート\n"
            f"時刻: {now}\n"
            f"状態: {analysis.get('status', '不明')}\n"
            f"シグナル数: {analysis.get('signals', 0)}件\n"
            f"（{MIN_SIGNALS_FOR_PDCA}件以上で分析開始）"
        )

    win_rate = analysis.get("overall_win_rate", 0)
    signals = analysis.get("signals", 0)
    recs = analysis.get("recommendations", [])
    features = analysis.get("feature_analysis", {})

    lines = [
        f"📊 PDCA分析レポート",
        f"時刻: {now}",
        f"{'─'*30}",
        f"分析シグナル: {signals}件",
        f"全体勝率(24h): {win_rate:.0%}",
        f"{'─'*30}",
        f"特徴別勝率:",
    ]

    if "new_wallet_win_rate" in features:
        lines.append(f"  新規ウォレット: {features['new_wallet_win_rate']:.0%}")
    if "deposit_24h_win_rate" in features:
        lines.append(f"  直前入金あり: {features['deposit_24h_win_rate']:.0%}")
    if "pure_speculation_win_rate" in features:
        lines.append(f"  純投機: {features['pure_speculation_win_rate']:.0%}")

    lines.append(f"{'─'*30}")
    lines.append("改善提案:")
    for rec in recs:
        lines.append(f"  {rec}")

    return "\n".join(lines)
