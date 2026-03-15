from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pywebpush import webpush, WebPushException
from typing import Optional
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
import pytz
import sqlite3
import json
import os
import uuid

# -----------------------
# App setup
# -----------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

VAPID_PUBLIC_KEY = "BDKhTIxI05AlXXk_zbJxESluEqbGXe25m6k5BuIXHWHQhS4Eh58JajT7IGdR1jwa9bjPZLD_LxM58vrNIiHEaS8"
VAPID_PRIVATE_KEY = "nBBu_wCGpRaX_RZ0Te0RrygMUNQT5AhuQ25MnHP10_I"

# -----------------------
# Database
# -----------------------
DB_FILE = "notifications.db"
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS subscribers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT UNIQUE,
    p256dh TEXT,
    auth TEXT,
    subscribed_at TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS scheduled_notifications (
    id TEXT PRIMARY KEY,
    title TEXT,
    message TEXT,
    url TEXT,
    run_time TIMESTAMP
)
""")

conn.commit()

# -----------------------
# Scheduler (NO SQLAlchemy)
# -----------------------
scheduler = BackgroundScheduler(timezone=pytz.UTC)

@app.on_event("startup")
def start_scheduler():
    if not scheduler.running:
        scheduler.start()

@app.on_event("shutdown")
def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()

# -----------------------
# Helper functions
# -----------------------
def get_subscribers():
    cursor.execute("SELECT endpoint, p256dh, auth, subscribed_at FROM subscribers")
    rows = cursor.fetchall()

    subs = []
    for r in rows:
        subs.append({
            "endpoint": r[0],
            "keys": {"p256dh": r[1], "auth": r[2]},
            "subscribed_at": datetime.fromisoformat(r[3])
        })

    return subs


def send_notification_task(title, message, url=None, job_id=None):

    subs = get_subscribers()
    dead_subs = []

    host = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")
    absolute_url = url if url else host
    icon_url = f"{host}/static/ima1.png"

    for sub in subs:

        try:

            payload = json.dumps({
                "title": title,
                "body": message,
                "url": absolute_url,
                "icon": icon_url
            })

            endpoint = sub["endpoint"]
            aud = endpoint.split("/")[2]

            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={
                    "sub": "mailto:test@test.com",
                    "aud": f"https://{aud}"
                }
            )

        except WebPushException as ex:

            print("Push failed:", ex)

            if ex.response and ex.response.status_code == 410:
                dead_subs.append(sub["endpoint"])

    for ep in dead_subs:
        cursor.execute("DELETE FROM subscribers WHERE endpoint=?", (ep,))

    conn.commit()

# -----------------------
# Admin page
# -----------------------
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "public_key": VAPID_PUBLIC_KEY}
    )

# -----------------------
# Subscribe
# -----------------------
@app.post("/subscribe")
async def subscribe(subscription: dict):

    now = datetime.utcnow().isoformat()

    cursor.execute(
        "INSERT OR IGNORE INTO subscribers (endpoint, p256dh, auth, subscribed_at) VALUES (?, ?, ?, ?)",
        (
            subscription["endpoint"],
            subscription["keys"]["p256dh"],
            subscription["keys"]["auth"],
            now
        )
    )

    conn.commit()

    return {"message": "Subscribed"}

# -----------------------
# Send notification
# -----------------------
@app.post("/send-notification")
async def send_notification(
    title: str = Form(...),
    message: str = Form(...),
    url: Optional[str] = Form(None),
    send_at: Optional[str] = Form(None)
):

    if send_at and send_at.strip():

        try:
            run_time = datetime.fromisoformat(send_at)
        except:
            return {"error": "Invalid datetime format"}

        ist = pytz.timezone("Asia/Kolkata")

        if run_time.tzinfo is None:
            run_time = ist.localize(run_time)

        utc_time = run_time.astimezone(pytz.UTC)

        job_id = str(uuid.uuid4())

        scheduler.add_job(
            send_notification_task,
            "date",
            run_date=utc_time,
            args=[title, message, url, job_id],
            id=job_id
        )

        cursor.execute(
            "INSERT INTO scheduled_notifications (id, title, message, url, run_time) VALUES (?, ?, ?, ?, ?)",
            (job_id, title, message, url, utc_time.isoformat())
        )

        conn.commit()

        return {"status": "Notification Scheduled", "id": job_id}

    send_notification_task(title, message, url)

    return {"status": "Notification Sent"}

# -----------------------
# Home
# -----------------------
@app.get("/")
def home():
    return {"status": "FastAPI running"}
