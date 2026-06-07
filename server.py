"""口コミ監視プラットフォーム - FastAPI サーバー (マルチテナント対応)

環境変数:
  GOOGLE_API_KEY       Google Places API キー
  ANTHROPIC_API_KEY    Anthropic API キー
  SECRET_KEY           セッション署名キー (必須)
  ADMIN_EMAIL          初回起動時に作成される管理者メール
  ADMIN_PASSWORD       初回起動時に作成される管理者パスワード
  PORT                 Railway が自動設定
"""
import os, json
from pathlib import Path
from datetime import datetime
import hashlib

from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
import anthropic

import db
from crawler import run_daily_check

app = FastAPI()
SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-in-production")
COOKIE = "kk_session"

# ---------- Session (signed token: user_id|role|hmac) ----------
def _sign(payload: str) -> str:
    import hmac as _hmac
    return _hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]

def create_session(user_id: int, role: str) -> str:
    payload = f"{user_id}|{role}"
    return f"{payload}|{_sign(payload)}"

def parse_session(token: str):
    try:
        parts = token.rsplit("|", 1)
        if len(parts) != 2: return None
        payload, sig = parts
        if _sign(payload) != sig: return None
        uid_str, role = payload.split("|", 1)
        return {"user_id": int(uid_str), "role": role}
    except Exception:
        return None

def get_current_user(request: Request):
    token = request.cookies.get(COOKIE)
    if not token: raise HTTPException(401)
    session = parse_session(token)
    if not session: raise HTTPException(401)
    user = db.get_user(session["user_id"])
    if not user: raise HTTPException(401)
    return user

def require_admin(request: Request):
    user = get_current_user(request)
    if user["role"] != "admin": raise HTTPException(403, "管理者のみアクセス可能です")
    return user

# ---------- Static HTML ----------
def html(filename): return HTMLResponse((Path(__file__).parent / "static" / filename).read_text("utf-8"))

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    try: get_current_user(request)
    except: return RedirectResponse("/login")
    return html("index.html")

@app.get("/login",  response_class=HTMLResponse) 
def login_page(): return html("login.html")

@app.get("/admin",  response_class=HTMLResponse)
def admin_page(request: Request):
    require_admin(request); return html("admin.html")

# ---------- Auth API ----------
class LoginBody(BaseModel):
    email: str; password: str

@app.post("/api/login")
def login(body: LoginBody, response: Response):
    user = db.authenticate(body.email, body.password)
    if not user: raise HTTPException(401, "メールアドレスまたはパスワードが違います")
    token = create_session(user["id"], user["role"])
    response.set_cookie(COOKIE, token, max_age=60*60*24*30, httponly=True, samesite="lax")
    return {"ok": True, "role": user["role"], "name": user["name"]}

@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE); return {"ok": True}

@app.get("/api/me")
def me(request: Request):
    user = get_current_user(request)
    return {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]}

# ---------- Admin API ----------
class CreateUserBody(BaseModel):
    email: str; password: str; name: str; role: str = "client"

@app.get("/api/admin/users")
def admin_list_users(request: Request):
    require_admin(request); return db.get_all_users()

@app.post("/api/admin/users")
def admin_create_user(body: CreateUserBody, request: Request):
    require_admin(request)
    ok = db.create_user(body.email, body.password, body.name, body.role)
    if not ok: raise HTTPException(400, "このメールアドレスは既に登録されています")
    return {"ok": True}

@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, request: Request):
    admin = require_admin(request)
    if admin["id"] == user_id: raise HTTPException(400, "自分自身は削除できません")
    db.delete_user(user_id); return {"ok": True}

class ResetPwBody(BaseModel):
    new_password: str

@app.post("/api/admin/users/{user_id}/reset-password")
def admin_reset_password(user_id: int, body: ResetPwBody, request: Request):
    require_admin(request)
    db.update_password(user_id, body.new_password); return {"ok": True}

# ---------- Data API (tenant-scoped) ----------
def _parse_reviews(rows):
    for r in rows:
        if r.get("topics"):
            try: r["topics"] = json.loads(r["topics"])
            except: r["topics"] = []
    return rows

@app.get("/api/summary")
def summary(request: Request):
    u = get_current_user(request); return db.get_store_stats(u["id"])

@app.get("/api/reviews")
def reviews(request: Request, limit: int=30, own_only: int=0):
    u = get_current_user(request)
    return _parse_reviews(db.get_recent_reviews(u["id"], limit, bool(own_only)))

@app.get("/api/alerts")
def alerts(request: Request):
    u = get_current_user(request)
    return _parse_reviews(db.get_alerts(u["id"]))

@app.get("/api/stores")
def stores(request: Request):
    u = get_current_user(request); return db.get_stores(u["id"])

class StoreBody(BaseModel):
    place_id: str; name: str; is_own: bool=False; line_group_id: str=""

@app.post("/api/stores")
def add_store(body: StoreBody, request: Request):
    u = get_current_user(request)
    db.upsert_store(u["id"], body.place_id, body.name, body.is_own, body.line_group_id or None)
    return {"ok": True}

@app.delete("/api/stores/{place_id}")
def del_store(place_id: str, request: Request):
    u = get_current_user(request)
    db.delete_store(u["id"], place_id); return {"ok": True}

@app.post("/api/refresh")
def refresh(request: Request):
    u = get_current_user(request)
    return {"ok": True, "new_reviews": run_daily_check(target_user_id=u["id"])}

# ---------- AI Chat ----------
def build_system_prompt(stores, recent_reviews, alerts):
    own  = [s for s in stores if s.get("is_own")]
    comp = [s for s in stores if not s.get("is_own")]
    def fmt(s):
        r = s.get("avg_rating") or 0
        return f"・{s['name']}: ★{float(r):.1f}({s.get('total_reviews',0)}件,今日{s.get('new_today',0)}件)"
    rev_str = "\n".join([
        f"[★{r['rating']} {str(r.get('seen_at',''))[:10]}] {str(r.get('text',''))[:180]}"
        for r in recent_reviews[:12]]) or "（データなし）"
    alert_str = "\n".join([
        f"[緊急度{r['urgency']} ★{r['rating']}] {r.get('summary_ja') or str(r.get('text',''))[:80]}"
        for r in alerts[:5]]) or "（現在なし）"
    return f"""あなたは飲食店経営の専門コンサルタントです。
以下のGoogleレビューデータを根拠に、経営者の質問へ日本語で具体的・実践的に回答してください。

【自店データ】\n{chr(10).join(map(fmt,own)) or '（未登録）'}
【競合店データ】\n{chr(10).join(map(fmt,comp)) or '（未登録）'}
【最近の口コミ抜粋（自店）】\n{rev_str}
【要対応アラート】\n{alert_str}"""

class ChatBody(BaseModel):
    message: str; history: list=[]

@app.post("/api/chat")
async def chat(body: ChatBody, request: Request):
    u = get_current_user(request)
    stores_data  = db.get_store_stats(u["id"])
    recent       = db.get_recent_reviews(u["id"], limit=20, own_only=True)
    alert_rv     = db.get_alerts(u["id"], limit=5)
    system       = build_system_prompt(stores_data, recent, alert_rv)
    messages     = [{"role":h["role"],"content":h["content"]} for h in body.history[-8:]]
    messages.append({"role":"user","content":body.message})
    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    async def generate():
        try:
            async with client.messages.stream(
                model="claude-haiku-4-5-20251001", max_tokens=1200,
                system=system, messages=messages) as stream:
                async for text in stream.text_stream:
                    yield f"data: {json.dumps({'text':text},ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error':str(e)},ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# ---------- Startup ----------
def scheduled_job():
    print(f"[SCHEDULER] {datetime.now().isoformat()} 定期取得開始")
    db.init_db(); run_daily_check()

scheduler = BackgroundScheduler(timezone="Asia/Tokyo")
scheduler.add_job(scheduled_job, "cron", hour=7, minute=0)
scheduler.start()
db.init_db()

# 管理者アカウントの初期作成
admin_email = os.environ.get("ADMIN_EMAIL")
admin_pass  = os.environ.get("ADMIN_PASSWORD")
if admin_email and admin_pass:
    created = db.create_user(admin_email, admin_pass, "管理者", "admin")
    if created: print(f"[INIT] 管理者アカウントを作成しました: {admin_email}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.environ.get("PORT",8000)), reload=False)
