#!/usr/bin/env python3
"""Python Insta Info — Telegram Bot (stdlib only, no pip install).

SETUP (2 minutes):
  1. Open Telegram, search @BotFather -> /newbot -> copy the token.
  2. Set the new token in the BOT_TOKEN environment variable.
  3. Send /start to your bot, copy your numeric ID, paste it in ADMIN_IDS.
  4. REQUIRED_CHANNELS mein 4 channels ke @username + links daalo.
  5. Bot ko un 4 channels mein Admin banao (warna membership check fail hoga).

HOW TO RUN:
  Windows      : double-click START.bat
  VS Code      : open this folder -> press F5 (or Run > Start Debugging)
  cPanel/Linux : bash run.sh   (keeps running, logs to bot.log)
                 bash stop.sh   (stop it)
                 tail -f bot.log (watch live log)

FEATURES:
  /start  -> force join 4 channels → verify → full user profile card + inline menu
  /admin  -> admin panel (broadcast to ALL users, user count, stats)
  Broadcast flow: Panel > Broadcast > send message > Confirm > delivered.
  Every new user auto-saves to tg_users.json for broadcasts.
  Demo +91 numbers are saved by username in fake_numbers.json.
  Colored buttons (primary/success/danger) supported (Bot API 9.4+).
  Fast response with membership + users cache.
"""
import json
import os
import re
import secrets
import time
import threading
import urllib.parse
import urllib.request
import html as ihtml

try:
    from insta_api import get_profile as _ig_lookup
    _IG_OK = True
except Exception:
    _ig_lookup = None
    _IG_OK = False


# ============ INSTA LOOKUP (self-contained, no other file needed) ============
DESK_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
MOB_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
_HERE = os.path.dirname(os.path.abspath(__file__))
_FALLBACK_DIRS = [_HERE, os.path.dirname(_HERE),
                  r"C:\Users\PRINCE YADAV\OneDrive\Desktop\python"]


def _ig_token():
    for d in _FALLBACK_DIRS:
        for name in ("chocodata_token.txt", "scraperapi_token.txt"):
            p = os.path.join(d, name)
            if os.path.exists(p):
                try:
                    t = open(p, encoding="utf-8").read().strip().split()[0]
                    if len(t) >= 8:
                        return t
                except Exception:
                    pass
    return ""


def _ig_num(x):
    try:
        if isinstance(x, dict):
            x = x.get("count", 0)
        s = str(x).replace(",", "").strip()
        if s.lower().endswith("k"):
            return int(float(s[:-1]) * 1000)
        if s.lower().endswith("m"):
            return int(float(s[:-1]) * 1000000)
        return int(float(s))
    except Exception:
        return 0


def _ig_pick(o, *keys, default=""):
    for k in keys:
        if isinstance(o, dict) and o.get(k) not in (None, ""):
            return o.get(k)
    return default


def _ig_clean(u):
    u = (u or "").strip().split("?")[0].split("#")[0].rstrip("/")
    if "/" in u:
        parts = [p for p in u.split("/") if p and p not in (
            "http:", "https:", "www.instagram.com", "instagram.com",
            "p", "reel", "reels", "stories")]
        u = parts[-1] if parts else ""
    return re.sub(r"[^A-Za-z0-9._]", "", u.lstrip("@"))[:30].lower()


def _ig_extract(raw):
    key = 'contextJSON":"'
    i = raw.find(key)
    if i < 0:
        return None
    i += len(key)
    out = []
    while i < len(raw):
        c = raw[i]
        if c == "\\":
            out.append(raw[i:i + 2])
            i += 2
            continue
        if c == '"':
            break
        out.append(c)
        i += 1
    try:
        return json.loads('"' + "".join(out) + '"')
    except Exception:
        return None


def _ig_embed_posts(username):
    url = "https://www.instagram.com/" + urllib.parse.quote(username) + "/embed/"
    req = urllib.request.Request(url, headers={
        "User-Agent": MOB_UA, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"})
    raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
    if "may be broken" in raw or "Sorry, this page" in raw:
        raise ValueError("Username '%s' not found." % username)
    c = _ig_extract(raw)
    if not c:
        return [], ""
    ctx = (json.loads(c).get("context") or {})
    posts = []
    for m in (ctx.get("graphql_media") or [])[:12]:
        sm = m.get("shortcode_media") or {}
        cap = ""
        try:
            cap = (((sm.get("edge_media_to_caption") or {}).get("edges") or [{}])[0]
                   .get("node") or {}).get("text", "")
        except Exception:
            cap = ""
        img = sm.get("display_url", "")
        if not img:
            try:
                img = (sm.get("display_resources") or [{}])[-1].get("src", "")
            except Exception:
                img = ""
        code = sm.get("shortcode", "")
        posts.append({"img": img,
                      "likes": _ig_num((sm.get("edge_liked_by") or {}).get("count", 0)),
                      "comments": _ig_num((sm.get("edge_media_to_comment") or {}).get("count", 0)),
                      "caption": (cap or "")[:500], "code": code,
                      "link": ("https://www.instagram.com/p/" + code + "/") if code else "",
                      "is_video": bool(sm.get("is_video"))})
    return posts, ctx.get("profile_pic_url", "")


def _ig_meta(username):
    """Profile HTML se counts padhta hai — og:description mein
    '271M Followers, 279 Following, 1,058 Posts - See Instagram...' aata hai.
    ye page API tarah rate-limited nahi, plain GET par chal jaata hai."""
    url = "https://www.instagram.com/" + urllib.parse.quote(username) + "/"
    req = urllib.request.Request(url, headers={
        "User-Agent": MOB_UA, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"})
    raw = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
    if "Page Not Found" in raw or "Sorry, this page isn't available" in raw:
        raise ValueError("Username '%s' not found." % username)
    m = re.search(r'og:description"\s+content="([^"]{0,400})"', raw)
    if not m:
        raise RuntimeError("Instagram is busy right now. Wait a minute and retry.")
    desc = ihtml.unescape(m.group(1))
    fo = re.search(r"([\d.,]+[KkMm]?)\s+Followers", desc)
    gi = re.search(r"([\d.,]+[KkMm]?)\s+Following", desc)
    po = re.search(r"([\d.,]+[KkMm]?)\s+Posts", desc)
    if not fo:
        raise RuntimeError("Instagram is busy right now. Wait a minute and retry.")
    full = username
    m = re.search(r'og:title"\s+content="([^"]{0,300})"', raw)
    if m:
        full = ihtml.unescape(m.group(1)).split(" (@")[0].split(" • ")[0].strip()
    bio = ""
    m = re.search(r'"biography":"((?:[^"\\]|\\.){0,500})"', raw)
    if m:
        try:
            bio = json.loads('"' + m.group(1) + '"')
        except Exception:
            bio = ""
    return {
        "full_name": full,
        "bio": bio,
        "followers": _ig_num(fo.group(1)),
        "following": _ig_num(gi.group(1)) if gi else 0,
        "posts_count": _ig_num(po.group(1)) if po else 0,
        "is_private": '"is_private":true' in raw,
        "is_verified": '"is_verified":true' in raw,
    }


def _ig_lookup(username):
    u = _ig_clean(username)
    if not u:
        raise ValueError("Empty username. Send @username or profile link.")
    tok = _ig_token()
    data = None
    if tok:
        try:
            url = ("https://api.chocodata.com/api/v1/instagram/profile?api_key="
                   + urllib.parse.quote(tok) + "&username=" + urllib.parse.quote(u))
            req = urllib.request.Request(url, headers={"User-Agent": DESK_UA})
            with urllib.request.urlopen(req, timeout=45) as r:
                o = json.loads(r.read().decode("utf-8", "ignore"))
            if isinstance(o, dict) and isinstance(o.get("data"), dict):
                o = o["data"]
            if isinstance(o, dict) and o.get("username"):
                data = {
                    "username": o.get("username", u),
                    "full_name": _ig_pick(o, "full_name", "fullName"),
                    "bio": _ig_pick(o, "biography", "bio"),
                    "pic": _ig_pick(o, "profile_pic_url", "profile_pic_url_hd", "profilePicUrl"),
                    "followers": _ig_num(_ig_pick(o, "follower_count", "followersCount", default=0)),
                    "following": _ig_num(_ig_pick(o, "following_count", "followsCount", default=0)),
                    "posts_count": _ig_num(_ig_pick(o, "posts_count", "media_count", default=0)),
                    "is_private": bool(_ig_pick(o, "is_private", "isPrivate", default=False)),
                    "is_verified": bool(_ig_pick(o, "is_verified", "isVerified", default=False)),
                    "external": _ig_pick(o, "external_url", "externalUrl"),
                    "posts": []}
        except Exception as e:
            if "401" in str(e) or "403" in str(e):
                raise RuntimeError("API token invalid.")
            data = None
    try:
        posts, pic = _ig_embed_posts(u)
    except ValueError:
        raise
    except Exception:
        posts, pic = [], ""
    if data is None:
        meta = None
        try:
            meta = _ig_meta(u)
        except ValueError:
            raise
        except Exception:
            meta = None
        if not meta and not posts and not pic:
            raise RuntimeError("Instagram is busy right now. Wait a minute and retry.")
        m = meta or {}
        return {"username": u, "full_name": m.get("full_name", ""), "bio": m.get("bio", ""),
                "pic": pic, "followers": m.get("followers", 0),
                "following": m.get("following", 0), "posts_count": m.get("posts_count", 0),
                "is_private": m.get("is_private", False),
                "is_verified": m.get("is_verified", False),
                "external": "", "posts": posts}
    if posts:
        data["posts"] = posts
    if not data.get("pic") and pic:
        data["pic"] = pic
    return data


_IG_OK = True

# ================== CONFIG ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")  # Render > Environment se aata hai
ADMIN_IDS = []          # <-- your Telegram numeric IDs, e.g. [123456789]

REQUIRED_CHANNELS = []  # <-- JAB CHAHNA: {"id": "@YourChannel", "link": "https://t.me/YourChannel", "name": "My Channel"} add karo. Blank = join gate OFF.
# ===============================================================================

BASE = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(BASE, "tg_users.json")
FAKE_NUMBERS_FILE = os.path.join(BASE, "fake_numbers.json")
API = "https://api.telegram.org/bot%s/" % BOT_TOKEN if BOT_TOKEN else ""

_state = {}
_ig_cache = {}
_fake_numbers_lock = threading.Lock()

# ===== PERFORMANCE CACHES =====
_member_cache = {}          # uid -> (expire_ts, all_joined, missing_list)
MEMBER_CACHE_TTL = 300      # 5 minutes

_users = {}                 # in-memory users
_users_dirty = False
_users_lock = threading.Lock()
_last_users_save = 0
USERS_SAVE_INTERVAL = 15    # seconds


def fmt_num(n):
    try:
        n = int(n or 0)
    except Exception:
        return "0"
    if n >= 1000000:
        s = "%.1fM" % (n / 1000000)
    elif n >= 1000:
        s = "%.1fK" % (n / 1000)
    else:
        return str(n)
    return s.rstrip("0").rstrip(".") if "." in s else s


def esc(s):
    return ihtml.escape(str(s or ""), quote=False)


# ---------- USERS (fast in-memory) ----------
def _load_users_from_disk():
    global _users
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _users = data
    except Exception:
        _users = {}


def _save_users_to_disk():
    global _users_dirty, _last_users_save
    with _users_lock:
        if not _users_dirty:
            return
        try:
            tmp = USERS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_users, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, USERS_FILE)
            _users_dirty = False
            _last_users_save = time.time()
        except Exception:
            pass


def load_users():
    return _users


def track_user(uid, first_name="", username=""):
    global _users_dirty
    key = str(uid)
    with _users_lock:
        if key not in _users:
            _users[key] = {"first": first_name, "joined": int(time.time()), "user": username}
            _users_dirty = True
        else:
            changed = False
            if first_name and _users[key].get("first") != first_name:
                _users[key]["first"] = first_name
                changed = True
            if username and _users[key].get("user") != username:
                _users[key]["user"] = username
                changed = True
            if changed:
                _users_dirty = True

    # delayed save
    if _users_dirty and (time.time() - _last_users_save) > USERS_SAVE_INTERVAL:
        threading.Thread(target=_save_users_to_disk, daemon=True).start()


# ---------- FAKE CONTACT ----------
def fake_contact_for(username):
    normalized = _ig_clean(username).lower()
    if not normalized:
        raise ValueError("Please provide a valid username.")

    with _fake_numbers_lock:
        try:
            with open(FAKE_NUMBERS_FILE, encoding="utf-8") as f:
                numbers = json.load(f)
            if not isinstance(numbers, dict):
                numbers = {}
        except (OSError, ValueError, TypeError):
            numbers = {}

        existing = numbers.get(normalized)
        old_number = existing if isinstance(existing, str) else ""
        if isinstance(existing, dict):
            old_number = existing.get("number", "")
        number_ok = isinstance(old_number, str) and re.fullmatch(
            r"\+91[6-9]\d{9}", old_number or "")

        used = set()
        for value in numbers.values():
            if isinstance(value, str):
                used.add(value)
            elif isinstance(value, dict) and isinstance(value.get("number"), str):
                used.add(value["number"])
        if number_ok:
            number = old_number
        else:
            while True:
                number = "+91" + secrets.choice("6789") + "%09d" % secrets.randbelow(1_000_000_000)
                if number not in used:
                    break

        local = re.sub(r"[^A-Za-z0-9]", "", normalized).lower() or "user"
        prefix = local[:5] if len(local) >= 5 else local[:4]
        email = existing.get("email", "") if isinstance(existing, dict) else ""
        email_version = existing.get("email_version") if isinstance(existing, dict) else None
        email_re = r"[a-z]{%d}@gmail\.com" % (len(prefix) + 4)
        if email_version != 2 or not isinstance(email, str) or not re.fullmatch(email_re, email):
            extra = "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(4))
            mixed = "".join(
                letter + (extra[i] if i < len(extra) else "")
                for i, letter in enumerate(prefix)
            )
            email = "%s@gmail.com" % mixed

        numbers[normalized] = {
            "number": number, "email": email, "email_version": 2}

        tmp_path = FAKE_NUMBERS_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(numbers, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, FAKE_NUMBERS_FILE)
        return {"number": number, "email": email}


def fake_number_for(username):
    return fake_contact_for(username)["number"]


# ---------- TELEGRAM API ----------
def api(method, params=None, timeout=25):
    data = json.dumps(params or {}).encode("utf-8")
    req = urllib.request.Request(API + method, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def send(chat, text, kb=None, parse="HTML", rk=None, rm=False):
    p = {"chat_id": chat, "text": text, "parse_mode": parse,
         "disable_web_page_preview": True}
    if kb:
        p["reply_markup"] = {"inline_keyboard": kb}
    elif rk:
        p["reply_markup"] = {"keyboard": rk, "resize_keyboard": True,
                             "is_persistent": True}
    elif rm:
        p["reply_markup"] = {"remove_keyboard": True}
    try:
        return api("sendMessage", p, timeout=20)
    except Exception:
        return None


def bottom_menu(admin=False):
    rows = [
        [
            {"text": "🔍 Insta Info", "style": "primary"},
            {"text": "👤 My Profile", "style": "primary"}
        ],
        [{"text": "❓ Help"}]
    ]
    if admin:
        rows.append([{"text": "🛠 Admin Panel", "style": "danger"}])
    return rows


def answer(cb_id, text=""):
    try:
        api("answerCallbackQuery", {"callback_query_id": cb_id, "text": text[:190]}, timeout=10)
    except Exception:
        pass


def is_admin(uid):
    return uid in ADMIN_IDS


def profile_card(u):
    return (
        "👤 <b>Your Profile</b>\n"
        "━━━━━━━━━━━━━━\n"
        "🆔 ID: <code>%s</code>\n"
        "📛 First name: %s\n"
        "📛 Last name: %s\n"
        "🔗 Username: %s\n"
        "🌍 Language: %s\n"
        "🤖 Bot contact: %s\n"
        "━━━━━━━━━━━━━━\n"
        "Choose an option below 👇"
    ) % (u.get("id"), esc(u.get("first_name") or "-"),
         esc(u.get("last_name") or "-"),
         ("@" + esc(u.get("username")) if u.get("username") else "-"),
         esc(u.get("language_code") or "-"),
         "Yes" if not u.get("is_bot") else "Bot")


def welcome_kb():
    return [
        [
            {"text": "🔍 Insta Info", "callback_data": "m_ig", "style": "danger"},
            {"text": "👤 My Profile", "callback_data": "m_profile", "style": "danger"}
        ],
        [
            {"text": "❓ Help", "callback_data": "m_help", "style": "danger"},
            {"text": "📊 Bot Status", "callback_data": "m_status", "style": "danger"}
        ]
    ]


def mark_welcomed(uid):
    global _users_dirty
    with _users_lock:
        key = str(uid)
        if key in _users:
            _users[key]["welcomed"] = True
            _users_dirty = True
    threading.Thread(target=_save_users_to_disk, daemon=True).start()


def _balloon_burst(chat):
    """Full-screen emoji burst (effect) — bubble 2.5s me clean ho jata hai."""
    try:
        r = api("sendMessage", {"chat_id": chat, "text": "🔥",
                                "message_effect_id": "5104841245755180586"}, timeout=10)
        mid = ((r.get("result") or {}).get("message_id"))
        if mid:
            def _del():
                time.sleep(2.5)
                try:
                    api("deleteMessage", {"chat_id": chat, "message_id": mid}, timeout=10)
                except Exception:
                    pass
            threading.Thread(target=_del, daemon=True).start()
    except Exception:
        pass


def _react(chat, mid, emoji):
    """Animated reaction badge on message (Insta-like animation, no premium needed)."""
    try:
        api("setMessageReaction", {"chat_id": chat, "message_id": mid,
                                   "reaction": [{"type": "emoji", "emoji": emoji}]}, timeout=10)
    except Exception:
        pass


def send_welcome(chat, frm):
    uid = frm.get("id")
    name = esc(frm.get("first_name") or "Dost")
    _balloon_burst(chat)
    photo_id = ""
    try:
        r = api("getUserProfilePhotos", {"user_id": uid, "limit": 1}, timeout=10)
        ph = ((r.get("result") or {}).get("photos") or [])
        if ph:
            photo_id = ph[0][-1].get("file_id", "")
    except Exception:
        photo_id = ""
    cap = (
        "🔥🔥🔥 <b>V I P  W E L C O M E</b> 🔥🔥🔥\n\n"
        "<blockquote>👑 <b>%s</b> — swagat hai!\n"
        "⛔ <b>DANGER ZONE UNLOCKED</b>\n"
        "🔓 VIP access aapke liye ready hai</blockquote>\n\n"
        "▸ 🔍 <b>Insta Info</b> — kisi bhi ID ka pura data\n"
        "▸ 📸 Posts • 👥 Followers • 📝 Bio\n"
        "▸ ⚡ <b>100%% FREE</b> — no login • no limits\n\n"
        "🆔 <code>%s</code>\n\n"
        "━━━━━━━━━━━━━━━\n"
        "👇 <b>NICHE BUTTONS DABAO</b> 👇"
    ) % (name, uid)
    kb = welcome_kb()
    if photo_id:
        for eff in ("5159385139981059251", None):
            try:
                p = {"chat_id": chat, "photo": photo_id, "caption": cap[:1000],
                     "parse_mode": "HTML", "reply_markup": {"inline_keyboard": kb}}
                if eff:
                    p["message_effect_id"] = eff
                sent = api("sendPhoto", p, timeout=20)
                mid = (sent.get("result") or {}).get("message_id")
                if mid:
                    _react(chat, mid, "❤")  # Insta-like animated heart on the card
                return
            except Exception:
                continue
    sent = send(chat, cap, kb)
    if isinstance(sent, dict):
        mid = (sent.get("result") or {}).get("message_id")
        if mid:
            _react(chat, mid, "❤")


def user_dict(frm):
    return {"id": frm.get("id"), "first_name": frm.get("first_name"),
            "last_name": frm.get("last_name"), "username": frm.get("username"),
            "language_code": frm.get("language_code"), "is_bot": frm.get("is_bot")}


def admin_panel_text():
    return "🛠 <b>Admin Panel</b>\n\nBroadcast goes to <b>%d</b> users.\nTotal users: <b>%d</b>" % (
        len(_users), len(_users))


def main_menu():
    return [
        [
            {"text": "👤 My Profile", "callback_data": "m_profile", "style": "primary"},
            {"text": "🔍 Insta Info", "callback_data": "m_ig", "style": "primary"}
        ],
        [
            {"text": "❓ Help", "callback_data": "m_help"},
            {"text": "📊 Bot Status", "callback_data": "m_status", "style": "success"}
        ]
    ]


def admin_menu():
    return [
        [{"text": "📢 Broadcast Message", "callback_data": "a_bc", "style": "primary"}],
        [
            {"text": "👥 User Count", "callback_data": "a_users"},
            {"text": "📊 Stats", "callback_data": "a_stats", "style": "success"}
        ],
        [{"text": "🏠 Main Menu", "callback_data": "m_menu"}]
    ]


def ig_menu(u):
    return [
        [{"text": "📸 Top 3 Posts", "callback_data": "ig_posts", "style": "primary"}],
        [
            {"text": "🔍 New Search", "callback_data": "m_ig", "style": "success"},
            {"text": "🏠 Main Menu", "callback_data": "m_menu"}
        ]
    ]


def ig_caption(p):
    v = " ✔" if p.get("is_verified") else ""
    bio = (p.get("bio") or "-")[:100]
    return (
        "📸 <b>%s</b>%s\n@%s\n\n📝 %s\n\n"
        "📰 Posts: <b>%s</b> \n👥 Followers: <b>%s</b> \n➕ Following: <b>%s</b>%s\n\n"
        "📱 Number: <code>%s</code>\n"
        "✉️ Gmail: <code>%s</code>"
    ) % (esc(p.get("full_name") or p.get("username")), v, esc(p.get("username")),
         esc(bio), fmt_num(p.get("posts_count")), fmt_num(p.get("followers")),
         fmt_num(p.get("following")) if p.get("following") else "—",
         ("\n🔒 Private account" if p.get("is_private") else ""),
         p.get("fake_number", "—"), esc(p.get("fake_email", "—")))


# ================== FORCE JOIN + CACHE ==================
def check_membership(user_id, force=False):
    """Fast cached membership check. Returns (all_joined, missing_list)"""
    if not REQUIRED_CHANNELS:
        return True, []

    now = time.time()
    cached = _member_cache.get(user_id)

    if not force and cached and cached[0] > now:
        return cached[1], cached[2]

    missing = []
    for ch in REQUIRED_CHANNELS:
        try:
            r = api("getChatMember", {"chat_id": ch["id"], "user_id": user_id}, timeout=12)
            status = (r.get("result") or {}).get("status", "")
            if status not in ("member", "administrator", "creator", "restricted"):
                missing.append(ch)
        except Exception:
            missing.append(ch)

    all_joined = len(missing) == 0
    _member_cache[user_id] = (now + MEMBER_CACHE_TTL, all_joined, missing)
    return all_joined, missing


def force_join_keyboard(missing=None):
    channels = missing if missing is not None else REQUIRED_CHANNELS
    rows = []
    for ch in channels:
        rows.append([{"text": "📢 Join %s" % ch["name"], "url": ch["link"], "style": "primary"}])
    rows.append([{"text": "✅ I Have Joined — Verify", "callback_data": "verify_join", "style": "success"}])
    return rows


def send_force_join(chat, uid):
    all_ok, missing = check_membership(uid)
    if all_ok:
        return True
    text = (
        "🔒 <b>Access Locked</b>\n\n"
        "Bot use karne se pehle neeche diye gaye <b>4 channels</b> join karo,\n"
        "phir <b>✅ Verify</b> button dabao.\n\n"
        "Channels join nahi kiye to profile nahi dikhega."
    )
    send(chat, text, force_join_keyboard(missing))
    return False


def run_ig_search(chat, uid, username):
    normalized = _ig_clean(username)
    if not normalized:
        send(chat, "❌ Please provide a valid Instagram username.")
        return

    send(chat, "🔍 Searching <b>@%s</b>…" % esc(normalized))

    if not _IG_OK:
        send(chat, "❌ Insta lookup is offline right now.")
        return

    try:
        contact = fake_contact_for(normalized)
    except ValueError as e:
        send(chat, "❌ %s" % esc(str(e)))
        return
    except Exception:
        send(chat, "❌ Could not save the demo number. Check folder permissions and try again.")
        return

    try:
        p = _ig_lookup(normalized)
    except ValueError as e:
        send(chat, "❌ %s" % esc(str(e)))
        return
    except RuntimeError as e:
        send(chat, "⏳ %s" % esc(str(e)))
        return
    except Exception:
        send(chat, "❌ Lookup failed. Try again in a minute.")
        return

    p["fake_number"] = contact["number"]
    p["fake_email"] = contact["email"]
    _ig_cache[uid] = p
    cap = ig_caption(p)
    sent = False
    if p.get("pic"):
        try:
            api("sendPhoto", {"chat_id": chat, "photo": p["pic"],
                              "caption": cap[:950], "parse_mode": "HTML"}, timeout=25)
            sent = True
        except Exception:
            sent = False
    if not sent:
        send(chat, cap, ig_menu(p.get("username")))
        return
    send(chat, "What next? 👇", ig_menu(p.get("username")))


def send_top_posts(chat, uid):
    p = _ig_cache.get(uid)
    if not p or not p.get("posts"):
        send(chat, "No saved result. Search a username first. 👇", main_menu())
        return
    media = []
    for m in p["posts"][:3]:
        if m.get("img"):
            media.append({"type": "photo", "media": m["img"],
                          "caption": ("❤ %s 💬 %s — %s" % (
                              fmt_num(m.get("likes")), fmt_num(m.get("comments")),
                              (m.get("caption") or "")[:150]))[:900]})
    if not media:
        send(chat, "No post images in this result.")
        return
    try:
        api("sendMediaGroup", {"chat_id": chat, "media": media}, timeout=35)
    except Exception:
        for m in media:
            try:
                api("sendPhoto", {"chat_id": chat, "photo": m["media"],
                                  "caption": m.get("caption", "")[:900]}, timeout=20)
            except Exception:
                pass


def do_broadcast(draft, admin_id):
    users = load_users()
    ok = fail = 0
    total = len(users)
    send(admin_id, "🚀 Broadcast started to <b>%d</b> users…" % total)
    for uid in list(users.keys()):
        try:
            if draft.get("type") == "text":
                api("sendMessage", {"chat_id": int(uid), "text": draft["text"],
                                    "parse_mode": "HTML",
                                    "disable_web_page_preview": True}, timeout=15)
            else:
                api("copyMessage", {"chat_id": int(uid),
                                    "from_chat_id": draft["chat"],
                                    "message_id": draft["mid"]}, timeout=15)
            ok += 1
        except Exception:
            fail += 1
        time.sleep(0.04)
    send(admin_id, "✅ Broadcast finished.\nDelivered: <b>%d</b>\nFailed: <b>%d</b>" % (ok, fail),
         admin_menu())


def handle_message(m):
    mid = m.get("message_id")
    chat = (m.get("chat") or {}).get("id")
    frm = m.get("from") or {}
    uid = frm.get("id")
    text = (m.get("text") or "").strip()

    # fast in-memory tracking
    track_user(uid, frm.get("first_name", ""), frm.get("username", ""))

    # --- instagram username await ---
    st = _state.get(uid)
    if st and st.get("step") == "await_ig":
        if text == "/cancel":
            _state.pop(uid, None)
            send(chat, "Cancelled.", main_menu())
            return
        if text and text.startswith("/") and not text.startswith("/insta"):
            pass
        elif text:
            _state.pop(uid, None)
            threading.Thread(target=run_ig_search,
                             args=(chat, uid, text), daemon=True).start()
            return

    # --- admin broadcast steps ---
    st = _state.get(uid)
    if st and is_admin(uid):
        if st["step"] == "await_msg":
            if text == "/cancel":
                _state.pop(uid, None)
                send(chat, "❌ Broadcast cancelled.", admin_menu())
                return
            if text:
                draft = {"type": "text", "text": text}
            elif m.get("photo") or m.get("video") or m.get("document") or m.get("sticker"):
                draft = {"type": "copy", "chat": chat, "mid": mid}
            else:
                send(chat, "Send text, photo, video or /cancel.")
                return
            st["step"] = "confirm"
            st["draft"] = draft
            kb = [
                [
                    {"text": "✅ Yes, Send to All", "callback_data": "a_confirm", "style": "success"},
                    {"text": "❌ Cancel", "callback_data": "a_cancel", "style": "danger"}
                ]
            ]
            preview = "📝 <b>Preview:</b>\n%s\n\nSend to <b>%d</b> users?" % (
                esc(text[:500]) if text else "(media message)", len(_users))
            send(chat, preview, kb)
            return

    # --- bottom-menu buttons ---
    if text in ("🔍 Insta Info", "👤 My Profile", "❓ Help", "🛠 Admin Panel", "📊 Bot Status"):
        if not check_membership(uid)[0]:
            send_force_join(chat, uid)
            return

        adm = is_admin(uid)
        if text == "🔍 Insta Info":
            _state[uid] = {"step": "await_ig"}
            send(chat, "🔍 Send the Instagram <b>username</b>.\nExample: <code>virat.kohli</code>\n\nOr /cancel to abort.", rk=bottom_menu(adm))
            return
        if text == "👤 My Profile":
            send(chat, profile_card(user_dict(frm)), main_menu(), rk=bottom_menu(adm))
            return
        if text == "❓ Help":
            send(chat, "❓ <b>Help</b>\n\n/start — show your profile\n🔍 Insta Info — any Instagram profile by username\n/admin — admin panel (admins only)", main_menu(), rk=bottom_menu(adm))
            return
        if text == "📊 Bot Status":
            send(chat, "📊 <b>Bot Status</b>\n\nStatus: <b>Online ✅</b>\nTracked users: <b>%d</b>" % len(_users), main_menu(), rk=bottom_menu(adm))
            return
        if text == "🛠 Admin Panel":
            if not ADMIN_IDS:
                send(chat, "⚙️ <b>Admin not set.</b>\n\nPut your numeric ID in <code>ADMIN_IDS</code> inside the bot file and restart.", rk=bottom_menu(False))
            elif not adm:
                send(chat, "⛔ Admins only. Your ID: <code>%s</code>" % uid, rk=bottom_menu(False))
            else:
                send(chat, admin_panel_text(), admin_menu(), rk=bottom_menu(True))
            return

    if text.startswith("/start"):
        all_ok, missing = check_membership(uid)
        if not all_ok:
            send_force_join(chat, uid)
            return
        send_welcome(chat, frm)
        mark_welcomed(uid)
        return

    elif text.startswith("/help"):
        if not check_membership(uid)[0]:
            send_force_join(chat, uid)
            return
        send(chat, "❓ <b>Help</b>\n\n/start — show your profile\n/insta <i>username</i> — Instagram profile info\n/help — this help\n\nUse the buttons below to navigate. Admins: /admin opens the control panel.", main_menu())

    elif text.startswith("/insta"):
        if not check_membership(uid)[0]:
            send_force_join(chat, uid)
            return
        arg = text[6:].strip()
        if not arg:
            _state[uid] = {"step": "await_ig"}
            send(chat, "🔍 Send the Instagram <b>username</b> (or /cancel).")
        else:
            threading.Thread(target=run_ig_search,
                             args=(chat, uid, arg), daemon=True).start()

    elif text.startswith("/admin"):
        if not ADMIN_IDS:
            send(chat, "⚙️ <b>Admin not set.</b>\n\n1. Send /start to the bot.\n2. Copy your numeric ID.\n3. Put it in <code>ADMIN_IDS</code> inside the bot file\n4. Restart the bot.", rk=bottom_menu(False))
        elif not is_admin(uid):
            send(chat, "⛔ Admins only. Your ID: <code>%s</code>" % uid, rk=bottom_menu(False))
        else:
            send(chat, admin_panel_text(), admin_menu(), rk=bottom_menu(True))

    elif text.startswith("/users") and is_admin(uid):
        send(chat, "👥 Total users: <b>%d</b>" % len(_users), admin_menu())

    elif text.startswith("/cancel"):
        _state.pop(uid, None)
        send(chat, "Cancelled.", main_menu())

    else:
        if not check_membership(uid)[0]:
            send_force_join(chat, uid)
            return
        send(chat, "Send /start to open the menu.", main_menu())


def handle_callback(cb):
    data = cb.get("data", "")
    msg = cb.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    frm = cb.get("from") or {}
    uid = frm.get("id")
    answer(cb.get("id"))

    # ===== VERIFY JOIN (force re-check) =====
    if data == "verify_join":
        all_ok, missing = check_membership(uid, force=True)
        if all_ok:
            send_welcome(chat, frm)
            mark_welcomed(uid)
        else:
            send(chat,
                 "❌ Abhi bhi kuch channels join nahi kiye.\n"
                 "Neeche join karke phir Verify dabao.",
                 force_join_keyboard(missing))
        return

    # Fast cache check for other callbacks
    if not check_membership(uid)[0]:
        send_force_join(chat, uid)
        return

    if data == "m_profile":
        u = {"id": uid, "first_name": frm.get("first_name"),
             "last_name": frm.get("last_name"), "username": frm.get("username"),
             "language_code": frm.get("language_code"), "is_bot": frm.get("is_bot")}
        send(chat, profile_card(u), main_menu())
    elif data == "m_help":
        send(chat, "❓ <b>Help</b>\n\n/start — your profile\n🔍 Insta Info — any Instagram profile by username\n/insta <i>username</i> — direct lookup\n/admin — admin panel (admins only)", main_menu())
    elif data == "m_ig":
        _state[uid] = {"step": "await_ig"}
        send(chat, "🔍 Send the Instagram <b>username</b>.\nExample: <code>virat.kohli</code>\n\nOr /cancel to abort.")
    elif data == "ig_posts":
        threading.Thread(target=send_top_posts, args=(chat, uid), daemon=True).start()
    elif data == "m_status":
        send(chat, "📊 <b>Bot Status</b>\n\nStatus: <b>Online ✅</b>\nTracked users: <b>%d</b>" % len(_users), main_menu())
    elif data == "m_menu":
        send(chat, "🏠 <b>Main Menu</b>", main_menu())
    elif data == "a_users" and is_admin(uid):
        send(chat, "👥 Total users: <b>%d</b>" % len(_users), admin_menu())
    elif data == "a_stats" and is_admin(uid):
        send(chat, "📊 Users: <b>%d</b>\nMode: <b>Polling ⚡</b>" % len(_users), admin_menu())
    elif data == "a_bc" and is_admin(uid):
        _state[uid] = {"step": "await_msg"}
        send(chat, "📢 <b>Broadcast mode.</b>\n\nNow send the message (text / photo / video).\nOr /cancel to abort.")
    elif data == "a_confirm" and is_admin(uid):
        st = _state.pop(uid, None)
        if not st or st.get("step") != "confirm":
            send(chat, "Nothing to send.", admin_menu())
            return
        threading.Thread(target=do_broadcast, args=(st["draft"], uid), daemon=True).start()
    elif data == "a_cancel" and is_admin(uid):
        _state.pop(uid, None)
        send(chat, "❌ Broadcast cancelled.", admin_menu())


def main():
    if not BOT_TOKEN:
        print("=" * 52)
        print("  BOT TOKEN IS BLANK.")
        print("  1. Telegram me @BotFather kholo -> /newbot")
        print("  2. Set BOT_TOKEN as an environment variable, then restart")
        print("  3. ADMIN_IDS me apni numeric ID dalo, fir bot restart karo")
        print("=" * 52)
        try:
            input("Press Enter to exit...")
        except Exception:
            pass
        return

    # load users once at start
    _load_users_from_disk()

    try:
        me = api("getMe", {}, timeout=15)
        print("  Bot online: @%s" % (me.get("result") or {}).get("username", "?"))
    except Exception as e:
        print("  Token/galat network. getMe failed: %s" % str(e)[:120])
        return

    print("  Polling started. CTRL+C to stop. Admins: %s" % (ADMIN_IDS or "NONE SET"))
    print("  Users loaded: %d | Membership cache: %ds" % (len(_users), MEMBER_CACHE_TTL))

    offset = 0
    while True:
        try:
            res = api("getUpdates", {"offset": offset, "timeout": 25,
                                     "allowed_updates": ["message", "callback_query"]},
                      timeout=35)
            for up in res.get("result", []):
                offset = up.get("update_id", offset) + 1
                if up.get("message"):
                    handle_message(up["message"])
                elif up.get("callback_query"):
                    handle_callback(up["callback_query"])
        except KeyboardInterrupt:
            print("\n  Saving users & stopping...")
            _save_users_to_disk()
            break
        except Exception as e:
            print("  poll error (retry): %s" % str(e)[:100])
            time.sleep(2)


if __name__ == "__main__":
    main()