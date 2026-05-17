"""
FastAPI application — REST endpoints for AutoSending dashboard.
Multi-user: every resource is scoped to the authenticated user.
"""

import io
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from auth import (
    check_rate_limit,
    create_access_token,
    get_current_user,
    verify_telegram_auth,
)
from config import setup_logging
from database import (
    Account, ActivityLog, Campaign, Channel,
    MessageTemplate, User, get_db, init_db, SessionLocal,
)
import scheduler
import telegram_manager as tm

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Reset campaigns stuck in "running" from a previous crashed/restarted process
    db = SessionLocal()
    try:
        stuck = db.query(Campaign).filter(Campaign.status == "running").all()
        for c in stuck:
            c.status = "stopped"
            if not c.stopped_at:
                c.stopped_at = datetime.utcnow()
        if stuck:
            db.commit()
            logger.info(f"Reset {len(stuck)} stuck campaign(s) to 'stopped' on startup")
    finally:
        db.close()
    logger.info("AutoSending backend started — DB ready")
    yield
    logger.info("AutoSending backend shutting down")


app = FastAPI(title="AutoSending API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# Pydantic schemas
# ══════════════════════════════════════════════════════════════════════════════

class TelegramAuthData(BaseModel):
    id:         int
    first_name: str
    last_name:  Optional[str] = None
    username:   Optional[str] = None
    photo_url:  Optional[str] = None
    auth_date:  int
    hash:       str


class AccountCreate(BaseModel):
    api_id:   int
    api_hash: str
    phone:    str


class VerifyCode(BaseModel):
    code:     str
    password: Optional[str] = None


class ProfileUpdate(BaseModel):
    first_name: Optional[str] = ""
    last_name:  Optional[str] = ""
    bio:        Optional[str] = ""


class ChannelCreate(BaseModel):
    url: str


class MessageCreate(BaseModel):
    content:  str
    category: Optional[str] = None


class MessageUpdate(BaseModel):
    content:   Optional[str]  = None
    category:  Optional[str]  = None
    is_active: Optional[bool] = None


class CampaignCreate(BaseModel):
    name:             str
    account_ids:      List[int]
    channel_ids:      List[int]
    message_ids:      List[int]
    min_delay:        float = 30.0
    max_delay:        float = 120.0
    comment_on_posts: bool  = False


# ══════════════════════════════════════════════════════════════════════════════
# Auth
# ══════════════════════════════════════════════════════════════════════════════

def _user_dict(u: User) -> dict:
    return {
        "id":          u.id,
        "telegram_id": u.telegram_id,
        "username":    u.username,
        "first_name":  u.first_name,
        "last_name":   u.last_name,
        "created_at":  u.created_at,
        "last_login":  u.last_login,
    }


@app.post("/api/auth/telegram")
async def telegram_auth(
    data: TelegramAuthData,
    request: Request,
    db: Session = Depends(get_db),
):
    check_rate_limit(request)

    if not verify_telegram_auth(data.model_dump()):
        raise HTTPException(401, "Telegram auth verification failed")

    now  = datetime.utcnow()
    user = db.query(User).filter(User.telegram_id == data.id).first()
    if user:
        user.first_name = data.first_name
        user.last_name  = data.last_name
        user.username   = data.username
        user.last_login = now
    else:
        user = User(
            telegram_id=data.id,
            username=   data.username,
            first_name= data.first_name,
            last_name=  data.last_name,
            created_at= now,
            last_login= now,
        )
        db.add(user)

    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)
    return {"access_token": token, "token_type": "bearer", "user": _user_dict(user)}


@app.get("/api/auth/me")
async def auth_me(current_user: User = Depends(get_current_user)):
    return _user_dict(current_user)


# ══════════════════════════════════════════════════════════════════════════════
# Accounts
# ══════════════════════════════════════════════════════════════════════════════

def _account_dict(a: Account) -> dict:
    return {
        "id":           a.id,
        "phone":        a.phone,
        "username":     a.username,
        "first_name":   a.first_name,
        "last_name":    a.last_name,
        "bio":          a.bio,
        "status":       a.status,
        "is_active":    a.is_active,
        "messages_sent":a.messages_sent,
        "errors_count": a.errors_count,
        "created_at":   a.created_at,
        "last_used":    a.last_used,
        "session_file": a.session_file,
    }


@app.get("/api/accounts")
async def list_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return [_account_dict(a) for a in
            db.query(Account).filter(Account.user_id == current_user.id).all()]


@app.post("/api/accounts", status_code=201)
async def add_account(
    data: AccountCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = (db.query(Account)
                .filter(Account.phone == data.phone, Account.user_id == current_user.id)
                .first())
    if existing:
        if existing.status != "error":
            raise HTTPException(400, "Телефон уже добавлен")
        # Session expired — reset the account so user can re-authorize
        existing.api_id   = data.api_id
        existing.api_hash = data.api_hash
        existing.status   = "pending"
        await tm.disconnect_client(existing.id)
        db.commit()
        db.refresh(existing)
        return _account_dict(existing)

    acc = Account(
        api_id=  data.api_id,
        api_hash=data.api_hash,
        phone=   data.phone,
        status=  "pending",
        user_id= current_user.id,
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return _account_dict(acc)


@app.delete("/api/accounts/{account_id}")
async def delete_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    acc = db.query(Account).filter(
        Account.id == account_id, Account.user_id == current_user.id
    ).first()
    if not acc:
        raise HTTPException(404, "Account not found")
    await tm.disconnect_client(account_id)
    tm.delete_session_file(acc.phone)
    db.delete(acc)
    db.commit()
    return {"ok": True}


@app.post("/api/accounts/{account_id}/send-code")
async def send_code(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    acc = db.query(Account).filter(
        Account.id == account_id, Account.user_id == current_user.id
    ).first()
    if not acc:
        raise HTTPException(404, "Account not found")
    try:
        result = await tm.send_auth_code(acc.api_id, acc.api_hash, acc.phone)
        if result["status"] == "already_authorized":
            acc.status = "online"
            db.commit()
        return result
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/accounts/{account_id}/verify-code")
async def verify_code(
    account_id: int,
    data: VerifyCode,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    acc = db.query(Account).filter(
        Account.id == account_id, Account.user_id == current_user.id
    ).first()
    if not acc:
        raise HTTPException(404, "Account not found")
    try:
        info = await tm.verify_auth_code(acc.phone, data.code, data.password)
        acc.username     = info.get("username")
        acc.first_name   = info.get("first_name", "")
        acc.last_name    = info.get("last_name", "")
        acc.session_file = info.get("session_file")
        acc.status       = "online"
        db.commit()
        return _account_dict(acc)
    except ValueError as e:
        msg = str(e)
        if "2FA_REQUIRED" in msg:
            raise HTTPException(422, "2FA_REQUIRED")
        raise HTTPException(400, msg)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.patch("/api/accounts/{account_id}/profile")
async def update_profile(
    account_id: int,
    data: ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    acc = db.query(Account).filter(
        Account.id == account_id, Account.user_id == current_user.id
    ).first()
    if not acc:
        raise HTTPException(404, "Account not found")
    try:
        await tm.update_profile(acc, data.first_name or "", data.last_name or "", data.bio or "")
        acc.first_name = data.first_name
        acc.last_name  = data.last_name
        acc.bio        = data.bio
        db.commit()
        return _account_dict(acc)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/accounts/{account_id}/photo")
async def update_photo(
    account_id: int,
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    acc = db.query(Account).filter(
        Account.id == account_id, Account.user_id == current_user.id
    ).first()
    if not acc:
        raise HTTPException(404, "Account not found")
    client = await tm.get_client(acc)
    if not client:
        raise HTTPException(400, "Account not authorized")
    data = await file.read()
    await tm.set_profile_photo(acc, io.BytesIO(data))
    return {"ok": True}


@app.get("/api/accounts/{account_id}/sync")
async def sync_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    acc = db.query(Account).filter(
        Account.id == account_id, Account.user_id == current_user.id
    ).first()
    if not acc:
        raise HTTPException(404, "Account not found")
    info = await tm.fetch_me(acc)
    if info["status"] == "online":
        acc.username   = info.get("username")
        acc.first_name = info.get("first_name", "")
        acc.last_name  = info.get("last_name", "")
        acc.status     = "online"
        db.commit()
    return info


# ══════════════════════════════════════════════════════════════════════════════
# Channels
# ══════════════════════════════════════════════════════════════════════════════

def _channel_dict(c: Channel) -> dict:
    return {
        "id":            c.id,
        "url":           c.url,
        "title":         c.title,
        "channel_tg_id": c.channel_tg_id,
        "is_active":     c.is_active,
        "created_at":    c.created_at,
    }


@app.get("/api/channels")
async def list_channels(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return [_channel_dict(c) for c in
            db.query(Channel).filter(Channel.user_id == current_user.id).all()]


@app.post("/api/channels", status_code=201)
async def add_channel(
    data: ChannelCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    url = data.url.strip()
    if db.query(Channel).filter(
        Channel.url == url, Channel.user_id == current_user.id
    ).first():
        raise HTTPException(400, "Канал уже добавлен")

    ch = Channel(url=url, user_id=current_user.id)

    online = db.query(Account).filter(
        Account.user_id == current_user.id, Account.status == "online"
    ).first()
    if online:
        try:
            client = await tm.get_client(online)
            if client:
                info = await tm.resolve_channel_info(client, url)
                ch.title         = info.get("title")
                ch.channel_tg_id = info.get("tg_id")
        except Exception:
            pass

    db.add(ch)
    db.commit()
    db.refresh(ch)
    return _channel_dict(ch)


@app.patch("/api/channels/{channel_id}")
async def toggle_channel(
    channel_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ch = db.query(Channel).filter(
        Channel.id == channel_id, Channel.user_id == current_user.id
    ).first()
    if not ch:
        raise HTTPException(404, "Channel not found")
    ch.is_active = not ch.is_active
    db.commit()
    return _channel_dict(ch)


@app.delete("/api/channels/{channel_id}")
async def delete_channel(
    channel_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ch = db.query(Channel).filter(
        Channel.id == channel_id, Channel.user_id == current_user.id
    ).first()
    if not ch:
        raise HTTPException(404, "Channel not found")
    db.delete(ch)
    db.commit()
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════════════
# Messages
# ══════════════════════════════════════════════════════════════════════════════

def _msg_dict(m: MessageTemplate) -> dict:
    return {
        "id":         m.id,
        "content":    m.content,
        "category":   m.category,
        "use_count":  m.use_count,
        "is_active":  m.is_active,
        "created_at": m.created_at,
    }


@app.get("/api/messages")
async def list_messages(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return [_msg_dict(m) for m in
            db.query(MessageTemplate).filter(MessageTemplate.user_id == current_user.id).all()]


@app.post("/api/messages", status_code=201)
async def add_message(
    data: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    msg = MessageTemplate(
        content= data.content.strip(),
        category=data.category,
        user_id= current_user.id,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return _msg_dict(msg)


@app.patch("/api/messages/{message_id}")
async def update_message(
    message_id: int,
    data: MessageUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    msg = db.query(MessageTemplate).filter(
        MessageTemplate.id == message_id, MessageTemplate.user_id == current_user.id
    ).first()
    if not msg:
        raise HTTPException(404, "Message not found")
    if data.content   is not None: msg.content   = data.content.strip()
    if data.category  is not None: msg.category  = data.category
    if data.is_active is not None: msg.is_active = data.is_active
    db.commit()
    return _msg_dict(msg)


@app.delete("/api/messages/{message_id}")
async def delete_message(
    message_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    msg = db.query(MessageTemplate).filter(
        MessageTemplate.id == message_id, MessageTemplate.user_id == current_user.id
    ).first()
    if not msg:
        raise HTTPException(404, "Message not found")
    db.delete(msg)
    db.commit()
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════════════
# Campaigns
# ══════════════════════════════════════════════════════════════════════════════

_PERM_SKIP_KEYWORDS = [
    "expired", "approval_required", "private", "not_channel",
    "banned in channel", "write forbidden", "admin-only",
]


def _campaign_dict(c: Campaign, db: Session) -> dict:
    is_running = scheduler.is_running(c.id)
    round_stats = scheduler.get_round_stats(c.id) if is_running else {}
    errors_count = db.query(func.count(ActivityLog.id)).filter(
        ActivityLog.campaign_id == c.id,
        ActivityLog.status == "error",
    ).scalar() or 0

    if is_running:
        skipped_channels = round_stats.get("skip_forever_urls", [])
    else:
        # Query ActivityLog for channels that hit permanent failures in this campaign
        from sqlalchemy import or_
        perm_rows = (
            db.query(ActivityLog.channel_url)
            .filter(
                ActivityLog.campaign_id == c.id,
                ActivityLog.status == "error",
                ActivityLog.channel_url != None,
                or_(*[ActivityLog.error_message.ilike(f"%{kw}%") for kw in _PERM_SKIP_KEYWORDS])
            )
            .distinct()
            .all()
        )
        skipped_channels = [r.channel_url for r in perm_rows if r.channel_url]

    return {
        "id":               c.id,
        "name":             c.name,
        "min_delay":        c.min_delay,
        "max_delay":        c.max_delay,
        "comment_on_posts": c.comment_on_posts,
        "status":           c.status,
        "created_at":       c.created_at,
        "started_at":       c.started_at,
        "stopped_at":       c.stopped_at,
        "total_sent":       c.total_sent,
        "errors_count":     errors_count,
        "is_running":       is_running,
        "round_stats":      round_stats,
        "skipped_channels": skipped_channels,
        "account_ids":      [a.id  for a in c.accounts],
        "channel_ids":      [ch.id for ch in c.channels],
        "message_ids":      [m.id  for m in c.messages],
    }


@app.get("/api/campaigns")
async def list_campaigns(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return [_campaign_dict(c, db) for c in
            db.query(Campaign).filter(Campaign.user_id == current_user.id).all()]


@app.post("/api/campaigns", status_code=201)
async def create_campaign(
    data: CampaignCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    accounts = db.query(Account).filter(
        Account.id.in_(data.account_ids), Account.user_id == current_user.id
    ).all()
    channels = db.query(Channel).filter(
        Channel.id.in_(data.channel_ids), Channel.user_id == current_user.id
    ).all()
    messages = db.query(MessageTemplate).filter(
        MessageTemplate.id.in_(data.message_ids), MessageTemplate.user_id == current_user.id
    ).all()

    if not accounts: raise HTTPException(400, "No valid accounts selected")
    if not channels: raise HTTPException(400, "No valid channels selected")
    if not messages: raise HTTPException(400, "No valid messages selected")

    camp = Campaign(
        name=            data.name,
        min_delay=       data.min_delay,
        max_delay=       data.max_delay,
        comment_on_posts=data.comment_on_posts,
        user_id=         current_user.id,
        accounts=        accounts,
        channels=        channels,
        messages=        messages,
    )
    db.add(camp)
    db.commit()
    db.refresh(camp)
    return _campaign_dict(camp, db)


@app.post("/api/campaigns/{campaign_id}/start")
async def start_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    camp = db.query(Campaign).filter(
        Campaign.id == campaign_id, Campaign.user_id == current_user.id
    ).first()
    if not camp:
        raise HTTPException(404, "Campaign not found")
    if scheduler.is_running(campaign_id):
        raise HTTPException(400, "Campaign already running")
    scheduler.start_campaign(campaign_id)
    return {"ok": True, "status": "running"}


@app.post("/api/campaigns/{campaign_id}/stop")
async def stop_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    camp = db.query(Campaign).filter(
        Campaign.id == campaign_id, Campaign.user_id == current_user.id
    ).first()
    if not camp:
        raise HTTPException(404, "Campaign not found")
    scheduler.stop_campaign(campaign_id)
    # Update DB immediately so the next poll sees "stopped" without waiting for workers to drain
    if camp.status == "running":
        camp.status = "stopped"
        camp.stopped_at = datetime.utcnow()
        db.commit()
    return {"ok": True, "status": "stopped"}


@app.delete("/api/campaigns/{campaign_id}")
async def delete_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    camp = db.query(Campaign).filter(
        Campaign.id == campaign_id, Campaign.user_id == current_user.id
    ).first()
    if not camp:
        raise HTTPException(404, "Campaign not found")
    scheduler.stop_campaign(campaign_id)
    db.delete(camp)
    db.commit()
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════════════
# Activity Logs
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/logs")
async def get_logs(
    limit: int = 100,
    campaign_id: Optional[int] = None,
    account_id:  Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = (db.query(ActivityLog)
         .options(joinedload(ActivityLog.account))
         .filter(ActivityLog.user_id == current_user.id)
         .order_by(ActivityLog.timestamp.desc()))
    if campaign_id: q = q.filter(ActivityLog.campaign_id == campaign_id)
    if account_id:  q = q.filter(ActivityLog.account_id  == account_id)

    return [
        {
            "id":            l.id,
            "account_id":    l.account_id,
            "account_phone": l.account.phone if l.account else None,
            "campaign_id":   l.campaign_id,
            "channel_url":   l.channel_url,
            "message":       l.message,
            "action_type":   l.action_type,
            "status":        l.status,
            "error_message": l.error_message,
            "timestamp":     l.timestamp,
        }
        for l in q.limit(limit).all()
    ]


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard stats
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/stats")
async def get_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    uid = current_user.id

    account_stats = db.query(
        func.count(Account.id).label("total"),
        func.count(Account.id).filter(Account.status.in_(["online", "working"])).label("online"),
    ).filter(Account.user_id == uid).one()

    campaign_stats = db.query(
        func.count(Campaign.id).label("total"),
        func.count(Campaign.id).filter(Campaign.status == "running").label("running"),
        func.coalesce(func.sum(Campaign.total_sent), 0).label("messages_sent"),
    ).filter(Campaign.user_id == uid).one()

    total_channels = db.query(func.count(Channel.id)).filter(Channel.user_id == uid).scalar()
    total_messages = db.query(func.count(MessageTemplate.id)).filter(MessageTemplate.user_id == uid).scalar()

    # Today's activity (UTC midnight boundary)
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = db.query(func.count(ActivityLog.id)).filter(
        ActivityLog.user_id == uid,
        ActivityLog.action_type == "send_message",
        ActivityLog.status == "success",
        ActivityLog.timestamp >= today_start,
    ).scalar() or 0
    errors_today = db.query(func.count(ActivityLog.id)).filter(
        ActivityLog.user_id == uid,
        ActivityLog.status == "error",
        ActivityLog.timestamp >= today_start,
    ).scalar() or 0

    # Per-running-campaign live stats
    running_details = []
    for camp in db.query(Campaign).filter(Campaign.user_id == uid, Campaign.status == "running").all():
        rs = scheduler.get_round_stats(camp.id)
        running_details.append({
            "id":         camp.id,
            "name":       camp.name,
            "total_sent": camp.total_sent,
            "round":      rs.get("round", 0),
            "cooldown":   rs.get("cooldown", 0),
            "active":     rs.get("active", 0),
            "skip_forever": rs.get("skip_forever", 0),
        })

    return {
        "total_accounts":    account_stats.total,
        "online_accounts":   account_stats.online,
        "total_channels":    total_channels,
        "total_messages":    total_messages,
        "total_campaigns":   campaign_stats.total,
        "running_campaigns": campaign_stats.running,
        "messages_sent":     campaign_stats.messages_sent,
        "sent_today":        sent_today,
        "errors_today":      errors_today,
        "running_details":   running_details,
    }
