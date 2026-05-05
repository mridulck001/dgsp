"""
Digital Gram Samadhan Portal (DGSP)
====================================
A comprehensive rural grievance redressal platform for Indian villages.
Single-file Flask application with MongoDB Atlas backend.
"""

import eventlet
eventlet.monkey_patch()

import os
import re
import json
import uuid
import logging
import requests
import tempfile
from io import BytesIO
from datetime import datetime, timedelta
from functools import wraps
from dotenv import load_dotenv

from flask import (
    Flask, render_template, redirect, url_for, flash,
    request, jsonify, session, abort, send_file
)
from flask_pymongo import PyMongo
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from flask_bcrypt import Bcrypt
from flask_socketio import SocketIO, emit, join_room
from flask_mail import Mail, Message
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from bson import ObjectId
from bson.errors import InvalidId

try:
    from pydub import AudioSegment
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False

# ─── Load environment ────────────────────────────────────────────────────────
load_dotenv()

# ─── App factory ─────────────────────────────────────────────────────────────
app = Flask(__name__)

app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-key-change-me"),
    MONGO_URI=os.getenv("MONGO_URI", "mongodb://localhost:27017/dgsp"),
    RATELIMIT_STORAGE_URI=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
    # Mail
    MAIL_SERVER=os.getenv("MAIL_SERVER", "smtp.gmail.com"),
    MAIL_PORT=int(os.getenv("MAIL_PORT", 587)),
    MAIL_USE_TLS=os.getenv("MAIL_USE_TLS", "True") == "True",
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    # Upload limits
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,  # 16 MB
)

# ─── Extensions ───────────────────────────────────────────────────────────────
# Added 500ms timeouts so the app doesn't hang if the DB is offline
mongo   = PyMongo(app, serverSelectionTimeoutMS=500, connectTimeoutMS=500, socketTimeoutMS=500)

# Fix for missing database name in MONGO_URI
if mongo.db is None and mongo.cx is not None:
    mongo.db = mongo.cx['dgsp']

bcrypt  = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")
mail    = Mail(app)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri=app.config["RATELIMIT_STORAGE_URI"],
)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────
ROLES = ("citizen", "officer", "admin")

CATEGORIES = [
    "Roads & Infrastructure",
    "Water Supply",
    "Electricity",
    "Sanitation & Drainage",
    "Education",
    "Health Services",
    "Land & Property",
    "Agriculture Support",
    "Social Welfare",
    "Public Safety",
    "Other",
]

STATUSES = [
    "Submitted",
    "Under Review",
    "In Progress",
    "Resolved",
    "Closed",
    "Rejected",
]

STATUS_COLORS = {
    "Submitted":    "blue",
    "Under Review": "yellow",
    "In Progress":  "orange",
    "Resolved":     "green",
    "Closed":       "gray",
    "Rejected":     "red",
}

LANGUAGES = {
    "en": "English",
    "hi": "हिंदी",
    "pa": "ਪੰਜਾਬੀ",
    "bn": "বাংলা",
    "ta": "தமிழ்",
    "te": "తెలుగు",
    "mr": "मराठी",
    "gu": "ગુજરાતી",
    "kn": "ಕನ್ನಡ",
}

PRIORITIES = ["Low", "Medium", "High", "Urgent"]

APP_URL = os.getenv("APP_URL", "http://localhost:5000")

# ─── User model ───────────────────────────────────────────────────────────────
class User(UserMixin):
    def __init__(self, data: dict):
        self._data = data

    def get_id(self):
        return str(self._data["_id"])

    @property
    def id(self):
        return str(self._data["_id"])

    def __getattr__(self, item):
        try:
            return self._data[item]
        except KeyError:
            raise AttributeError(item)


@login_manager.user_loader
def load_user(user_id):
    try:
        doc = mongo.db.users.find_one({"_id": ObjectId(user_id)})
        return User(doc) if doc else None
    except Exception:
        return None


# ─── Helpers ──────────────────────────────────────────────────────────────────
def role_required(*roles):
    """Decorator that restricts a route to specified roles."""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


def oid(v):
    """Safe ObjectId conversion."""
    try:
        return ObjectId(v)
    except (InvalidId, TypeError):
        return None


def generate_complaint_id():
    """Generate unique readable complaint ID like DGSP-2024-00001."""
    year = datetime.utcnow().year
    count = mongo.db.complaints.count_documents({}) + 1
    return f"DGSP-{year}-{count:05d}"


def send_status_sms(phone: str, complaint_id: str, status: str):
    """Send SMS via Twilio (gracefully skips if not configured)."""
    sid   = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_ = os.getenv("TWILIO_PHONE_NUMBER")
    if not (sid and token and from_):
        return
    try:
        from twilio.rest import Client
        Client(sid, token).messages.create(
            body=f"[DGSP] Your complaint {complaint_id} status: {status}. Track at {APP_URL}",
            from_=from_,
            to=phone,
        )
    except Exception as e:
        logger.warning("SMS failed: %s", e)


def send_status_email(to: str, complaint_id: str, status: str, name: str):
    """Send email notification (gracefully skips if not configured)."""
    if not app.config.get("MAIL_USERNAME"):
        return
    try:
        msg = Message(
            subject=f"[DGSP] Complaint {complaint_id} – {status}",
            recipients=[to],
            sender=app.config["MAIL_USERNAME"],
            html=render_template(
                "email_status.html",
                name=name,
                complaint_id=complaint_id,
                status=status,
                url=f"{APP_URL}/complaint/{complaint_id}",
            ),
        )
        mail.send(msg)
    except Exception as e:
        logger.warning("Email failed: %s", e)


def upload_to_cloudinary(file_obj, folder="dgsp"):
    """Upload file to Cloudinary; returns secure_url or None."""
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    api_key    = os.getenv("CLOUDINARY_API_KEY")
    api_secret = os.getenv("CLOUDINARY_API_SECRET")
    if not (cloud_name and api_key and api_secret):
        return None
    try:
        import cloudinary
        import cloudinary.uploader
        cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret)
        result = cloudinary.uploader.upload(file_obj, folder=folder, resource_type="auto")
        return result.get("secure_url")
    except Exception as e:
        logger.warning("Cloudinary upload failed: %s", e)
        return None


def auto_categorize(text: str) -> str:
    """Simple keyword-based NLP categorization."""
    text_lower = text.lower()
    mapping = {
        "Roads & Infrastructure": ["road", "bridge", "pothole", "street", "footpath", "sadak", "pul"],
        "Water Supply": ["water", "pipe", "tap", "pani", "well", "bore", "supply"],
        "Electricity": ["light", "electricity", "power", "bijli", "transformer", "wire", "current"],
        "Sanitation & Drainage": ["drain", "sewer", "garbage", "waste", "toilet", "swachh", "naali"],
        "Education": ["school", "teacher", "book", "vidya", "shiksha", "education", "student"],
        "Health Services": ["hospital", "doctor", "medicine", "health", "swasthya", "clinic", "nurse"],
        "Agriculture Support": ["farm", "crop", "kisan", "krishi", "seed", "fertilizer", "agriculture"],
        "Social Welfare": ["pension", "ration", "welfare", "scheme", "yojana", "subsidy", "card"],
        "Land & Property": ["land", "plot", "zameen", "property", "registry", "boundary"],
        "Public Safety": ["crime", "theft", "safety", "police", "suraksha", "violence", "illegal"],
    }
    scores = {cat: 0 for cat in mapping}
    for cat, keywords in mapping.items():
        for kw in keywords:
            if kw in text_lower:
                scores[cat] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Other"


def paginate(collection_query, page: int, per_page: int = 10):
    """Return (items, total, pages) for a pymongo cursor."""
    total = collection_query.count_documents({}) if hasattr(collection_query, "count_documents") else 0
    items = list(collection_query.skip((page - 1) * per_page).limit(per_page))
    pages = max(1, (total + per_page - 1) // per_page)
    return items, total, pages


# ─── Context processors ───────────────────────────────────────────────────────
@app.context_processor
def inject_globals():
    return dict(
        categories=CATEGORIES,
        statuses=STATUSES,
        status_colors=STATUS_COLORS,
        languages=LANGUAGES,
        priorities=PRIORITIES,
        now=datetime.utcnow(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTH ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    stats = {
        "total":    mongo.db.complaints.count_documents({}),
        "resolved": mongo.db.complaints.count_documents({"status": "Resolved"}),
        "citizens": mongo.db.users.count_documents({"role": "citizen"}),
        "villages": mongo.db.users.distinct("village"),
    }
    stats["villages"] = len(stats["villages"])
    recent = list(mongo.db.complaints.find(
        {"status": "Resolved"},
        {"title": 1, "category": 1, "village": 1, "resolved_at": 1}
    ).sort("resolved_at", -1).limit(6))
    return render_template("index.html", stats=stats, recent=recent)


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name     = request.form.get("name", "").strip()
        phone    = request.form.get("phone", "").strip()
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        village  = request.form.get("village", "").strip()
        district = request.form.get("district", "").strip()
        state    = request.form.get("state", "").strip()
        lang     = request.form.get("language", "hi")
        role     = request.form.get("role", "citizen")

        # Basic validation
        errors = []
        if not name:   errors.append("Name is required.")
        if not phone:  errors.append("Phone number is required.")
        if not village: errors.append("Village name is required.")
        if len(password) < 6: errors.append("Password must be at least 6 characters.")
        if role not in ROLES:  role = "citizen"

        # Uniqueness checks
        if mongo.db.users.find_one({"phone": phone}):
            errors.append("Phone number already registered.")
        if email and mongo.db.users.find_one({"email": email}):
            errors.append("Email already registered.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("register.html")

        pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        user_doc = {
            "name": name,
            "phone": phone,
            "email": email,
            "password": pw_hash,
            "village": village,
            "district": district,
            "state": state,
            "language": lang,
            "role": role,
            "is_active": True,
            "created_at": datetime.utcnow(),
            "last_login": None,
        }
        result = mongo.db.users.insert_one(user_doc)
        user_doc["_id"] = result.inserted_id
        user = User(user_doc)
        login_user(user)
        flash(f"Welcome, {name}! Your account has been created.", "success")
        return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("20 per hour")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password   = request.form.get("password", "")
        remember   = request.form.get("remember") == "on"

        # Allow login by phone or email
        user_doc = mongo.db.users.find_one(
            {"$or": [{"phone": identifier}, {"email": identifier.lower()}]}
        )

        if user_doc and bcrypt.check_password_hash(user_doc["password"], password):
            if not user_doc.get("is_active", True):
                flash("Your account has been deactivated. Contact admin.", "danger")
                return render_template("login.html")
            mongo.db.users.update_one(
                {"_id": user_doc["_id"]},
                {"$set": {"last_login": datetime.utcnow()}}
            )
            user = User(user_doc)
            login_user(user, remember=remember)
            next_page = request.args.get("next")
            flash(f"Welcome back, {user_doc['name']}!", "success")
            return redirect(next_page or url_for("dashboard"))
        else:
            flash("Invalid phone/email or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


# ═══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/dashboard")
@login_required
def dashboard():
    uid = oid(current_user.id)

    if current_user.role == "citizen":
        complaints = list(mongo.db.complaints.find({"user_id": uid}).sort("created_at", -1).limit(10))
        counts = {
            s: mongo.db.complaints.count_documents({"user_id": uid, "status": s})
            for s in STATUSES
        }
        return render_template("dashboard_citizen.html", complaints=complaints, counts=counts)

    elif current_user.role == "officer":
        village  = current_user._data.get("assigned_village", current_user._data.get("village"))
        district = current_user._data.get("district")
        query = {"$or": [{"village": village}, {"district": district}]}
        complaints = list(mongo.db.complaints.find(query).sort("created_at", -1).limit(20))
        counts = {s: mongo.db.complaints.count_documents({**query, "status": s}) for s in STATUSES}
        return render_template("dashboard_officer.html", complaints=complaints, counts=counts)

    else:  # admin
        total = mongo.db.complaints.count_documents({})
        counts = {s: mongo.db.complaints.count_documents({"status": s}) for s in STATUSES}
        by_cat = {
            cat: mongo.db.complaints.count_documents({"category": cat})
            for cat in CATEGORIES
        }
        recent_users = list(mongo.db.users.find().sort("created_at", -1).limit(5))
        recent_complaints = list(mongo.db.complaints.find().sort("created_at", -1).limit(10))
        return render_template(
            "dashboard_admin.html",
            total=total, counts=counts, by_cat=by_cat,
            recent_users=recent_users, recent_complaints=recent_complaints,
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  COMPLAINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/complaint/new", methods=["GET", "POST"])
@login_required
@role_required("citizen")
def new_complaint():
    if request.method == "POST":
        title       = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category    = request.form.get("category", "Other")
        priority    = request.form.get("priority", "Medium")
        village     = request.form.get("village", current_user._data.get("village", "")).strip()
        district    = request.form.get("district", current_user._data.get("district", "")).strip()
        state       = request.form.get("state", current_user._data.get("state", "")).strip()
        lang        = request.form.get("language", current_user._data.get("language", "hi"))
        location    = request.form.get("location", "")

        if not title:
            flash("Title is required.", "danger")
            return render_template("complaint_new.html")
        if not description:
            flash("Description is required.", "danger")
            return render_template("complaint_new.html")

        # Auto-categorize if default
        if category == "Other" and description:
            category = auto_categorize(description)

        # Handle file uploads
        attachments = []
        files = request.files.getlist("attachments")
        for f in files:
            if f and f.filename:
                url = upload_to_cloudinary(f.stream, folder="dgsp/complaints")
                if url:
                    attachments.append({"name": f.filename, "url": url})

        complaint_doc = {
            "complaint_id": generate_complaint_id(),
            "user_id": oid(current_user.id),
            "user_name": current_user._data["name"],
            "user_phone": current_user._data.get("phone"),
            "user_email": current_user._data.get("email"),
            "title": title,
            "description": description,
            "category": category,
            "priority": priority,
            "status": "Submitted",
            "village": village,
            "district": district,
            "state": state,
            "language": lang,
            "location": location,
            "attachments": attachments,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "resolved_at": None,
            "assigned_to": None,
            "timeline": [
                {
                    "status": "Submitted",
                    "note": "Complaint submitted by citizen.",
                    "actor": current_user._data["name"],
                    "timestamp": datetime.utcnow(),
                }
            ],
            "comments": [],
            "rating": None,
            "feedback": None,
        }

        result = mongo.db.complaints.insert_one(complaint_doc)
        cid = complaint_doc["complaint_id"]

        # Notify via SocketIO
        socketio.emit("new_complaint", {
            "complaint_id": cid,
            "title": title,
            "village": village,
            "category": category,
        }, room="officers")

        # Send email confirmation
        if current_user._data.get("email"):
            send_status_email(
                current_user._data["email"], cid, "Submitted", current_user._data["name"]
            )

        flash(f"Complaint {cid} submitted successfully!", "success")
        return redirect(url_for("view_complaint", complaint_id=cid))

    return render_template("complaint_new.html")


@app.route("/complaint/<complaint_id>")
@login_required
def view_complaint(complaint_id):
    doc = mongo.db.complaints.find_one({"complaint_id": complaint_id})
    if not doc:
        abort(404)

    # Citizens can only see their own complaints
    if current_user.role == "citizen" and str(doc["user_id"]) != current_user.id:
        abort(403)

    officers = []
    if current_user.role in ("officer", "admin"):
        officers = list(mongo.db.users.find(
            {"role": "officer"},
            {"name": 1, "phone": 1, "village": 1}
        ))

    return render_template("complaint_view.html", c=doc, officers=officers, status_colors=STATUS_COLORS)


@app.route("/complaint/<complaint_id>/update", methods=["POST"])
@login_required
@role_required("officer", "admin")
def update_complaint(complaint_id):
    doc = mongo.db.complaints.find_one({"complaint_id": complaint_id})
    if not doc:
        abort(404)

    new_status  = request.form.get("status", doc["status"])
    note        = request.form.get("note", "").strip()
    assigned_to = request.form.get("assigned_to")
    priority    = request.form.get("priority", doc.get("priority", "Medium"))

    update_fields = {
        "status": new_status,
        "priority": priority,
        "updated_at": datetime.utcnow(),
    }

    if assigned_to:
        officer = mongo.db.users.find_one({"_id": oid(assigned_to)})
        if officer:
            update_fields["assigned_to"] = {"id": str(officer["_id"]), "name": officer["name"]}

    if new_status == "Resolved":
        update_fields["resolved_at"] = datetime.utcnow()

    timeline_entry = {
        "status": new_status,
        "note": note or f"Status updated to {new_status}.",
        "actor": current_user._data["name"],
        "timestamp": datetime.utcnow(),
    }

    mongo.db.complaints.update_one(
        {"complaint_id": complaint_id},
        {
            "$set": update_fields,
            "$push": {"timeline": timeline_entry},
        }
    )

    # Notify via SocketIO
    socketio.emit("complaint_update", {
        "complaint_id": complaint_id,
        "status": new_status,
        "note": note,
    }, room=f"complaint_{complaint_id}")

    # Notify complainant
    phone = doc.get("user_phone")
    email = doc.get("user_email")
    name  = doc.get("user_name", "Citizen")
    if phone:
        send_status_sms(phone, complaint_id, new_status)
    if email:
        send_status_email(email, complaint_id, new_status, name)

    flash(f"Complaint updated to '{new_status}'.", "success")
    return redirect(url_for("view_complaint", complaint_id=complaint_id))


@app.route("/complaint/<complaint_id>/comment", methods=["POST"])
@login_required
def add_comment(complaint_id):
    doc = mongo.db.complaints.find_one({"complaint_id": complaint_id})
    if not doc:
        abort(404)

    text = request.form.get("comment", "").strip()
    if not text:
        flash("Comment cannot be empty.", "warning")
        return redirect(url_for("view_complaint", complaint_id=complaint_id))

    comment = {
        "id": str(uuid.uuid4()),
        "text": text,
        "author": current_user._data["name"],
        "role": current_user._data["role"],
        "timestamp": datetime.utcnow(),
    }
    mongo.db.complaints.update_one(
        {"complaint_id": complaint_id},
        {"$push": {"comments": comment}}
    )

    socketio.emit("new_comment", {
        "complaint_id": complaint_id,
        "comment": {**comment, "timestamp": comment["timestamp"].isoformat()},
    }, room=f"complaint_{complaint_id}")

    flash("Comment added.", "success")
    return redirect(url_for("view_complaint", complaint_id=complaint_id))


@app.route("/complaint/<complaint_id>/rate", methods=["POST"])
@login_required
@role_required("citizen")
def rate_complaint(complaint_id):
    doc = mongo.db.complaints.find_one({"complaint_id": complaint_id})
    if not doc or str(doc.get("user_id")) != current_user.id:
        abort(403)
    if doc.get("status") != "Resolved":
        flash("You can only rate resolved complaints.", "warning")
        return redirect(url_for("view_complaint", complaint_id=complaint_id))

    rating   = int(request.form.get("rating", 0))
    feedback = request.form.get("feedback", "").strip()
    if 1 <= rating <= 5:
        mongo.db.complaints.update_one(
            {"complaint_id": complaint_id},
            {"$set": {"rating": rating, "feedback": feedback}}
        )
        flash("Thank you for your feedback!", "success")
    return redirect(url_for("view_complaint", complaint_id=complaint_id))


@app.route("/complaints")
@login_required
def complaints_list():
    page     = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 15))
    q        = request.args.get("q", "").strip()
    status   = request.args.get("status", "")
    category = request.args.get("category", "")
    priority = request.args.get("priority", "")
    village  = request.args.get("village", "").strip()
    sort_by  = request.args.get("sort", "newest")

    query = {}

    # Role-based filtering
    if current_user.role == "citizen":
        query["user_id"] = oid(current_user.id)
    elif current_user.role == "officer":
        v = current_user._data.get("assigned_village", current_user._data.get("village"))
        d = current_user._data.get("district")
        query["$or"] = [{"village": v}, {"district": d}]

    # Filters
    if status:   query["status"]   = status
    if category: query["category"] = category
    if priority: query["priority"] = priority
    if village and current_user.role == "admin":
        query["village"] = {"$regex": village, "$options": "i"}
    if q:
        query["$or"] = [
            {"title":       {"$regex": q, "$options": "i"}},
            {"description": {"$regex": q, "$options": "i"}},
            {"complaint_id":{"$regex": q, "$options": "i"}},
        ]

    sort_map = {
        "newest":   [("created_at", -1)],
        "oldest":   [("created_at",  1)],
        "priority": [("priority",   -1)],
        "status":   [("status",      1)],
    }
    sort = sort_map.get(sort_by, [("created_at", -1)])

    cursor = mongo.db.complaints.find(query).sort(sort)
    total  = mongo.db.complaints.count_documents(query)
    items  = list(cursor.skip((page - 1) * per_page).limit(per_page))
    pages  = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "complaints_list.html",
        complaints=items, total=total, page=page, pages=pages,
        per_page=per_page, q=q, status=status, category=category,
        priority=priority, village=village, sort_by=sort_by,
    )


@app.route("/complaint/voice", methods=["POST"])
@login_required
@role_required("citizen")
def voice_complaint():
    """Accept audio file, chunk if >30s, transcribe via Sarvam AI, return JSON."""
    sarvam_key = os.getenv("SARVAM_API_KEY")
    language = request.form.get('lang', 'hi-IN')

    if not sarvam_key:
        return jsonify({'error': 'Sarvam API key is required. Provide it in the UI or set SARVAM_API_KEY env var.'}), 401

    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided in request.'}), 400

    file = request.files['audio']
    if file.filename == '':
        return jsonify({'error': 'No file selected.'}), 400

    SARVAM_API_URL = "https://api.sarvam.ai/speech-to-text"

    try:
        # Ephemeral file handling
        with tempfile.NamedTemporaryFile(delete=False, suffix='.webm') as temp_audio:
            file.save(temp_audio.name)
            temp_filepath = temp_audio.name

        # --- CHUNKING LOGIC FOR AUDIO > 30s ---
        if HAS_PYDUB:
            try:
                audio = AudioSegment.from_file(temp_filepath)
                # 29 seconds (safe margin below 30s)
                chunk_length_ms = 29 * 1000

                # If audio is longer than 29s, process in chunks
                if len(audio) > chunk_length_ms:
                    chunks = [audio[i:i + chunk_length_ms]
                              for i in range(0, len(audio), chunk_length_ms)]
                    full_transcript = []

                    for i, chunk in enumerate(chunks):
                        # FIX: Skip chunks smaller than 500ms
                        if len(chunk) < 500:
                            logger.info(
                                f"Skipping chunk {i} - too short ({len(chunk)}ms)")
                            continue

                        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as chunk_file:
                            chunk.export(chunk_file.name, format="wav")
                            with open(chunk_file.name, 'rb') as f:
                                files = {
                                    'file': (f"chunk_{i}.wav", f, 'audio/wav')}
                                data = {
                                    'language_code': language,
                                    'model': 'saarika:v2.5',
                                    'with_timestamps': 'false'
                                }
                                headers = {'api-subscription-key': sarvam_key}
                                response = requests.post(
                                    SARVAM_API_URL,
                                    headers=headers,
                                    files=files,
                                    data=data,
                                    timeout=60
                                )
                        os.remove(chunk_file.name)

                        if response.status_code == 200:
                            result = response.json()
                            transcript = result.get(
                                'transcript') or result.get('text') or ''
                            if transcript:
                                full_transcript.append(transcript.strip())
                        else:
                            # Clean up and bubble error if any chunk fails
                            os.remove(temp_filepath)
                            try:
                                err_msg = response.json().get('message', response.text)
                            except ValueError:
                                err_msg = response.text
                            return jsonify({'error': f'Sarvam API error on chunk {i+1}: {err_msg}'}), response.status_code

                    os.remove(temp_filepath)
                    merged_transcript = " ".join(full_transcript)
                    category = auto_categorize(merged_transcript)
                    return jsonify({'success': True, 'transcript': merged_transcript, 'suggested_category': category})

            except Exception as e:
                logger.warning(
                    f"Pydub chunking failed (ffmpeg likely missing): {e}. Falling back to direct upload.")
                pass  # Proceed to direct upload fallback

        # DIRECT UPLOAD FALLBACK (For <30s files or missing ffmpeg)
        with open(temp_filepath, 'rb') as f:
            files = {'file': (file.filename, f, file.mimetype or 'audio/webm')}
            data = {
                'language_code': language,
                'model': 'saarika:v2.5',
                'with_timestamps': 'false'
            }
            headers = {'api-subscription-key': sarvam_key}
            response = requests.post(
                SARVAM_API_URL,
                headers=headers,
                files=files,
                data=data,
                timeout=60
            )

        os.remove(temp_filepath)

        if response.status_code == 200:
            result = response.json()
            transcript = result.get('transcript') or result.get('text') or ''
            category = auto_categorize(transcript)
            return jsonify({'success': True, 'transcript': transcript, 'suggested_category': category})
        else:
            try:
                err_msg = response.json().get('message', response.text)
            except ValueError:
                err_msg = response.text

            # Intercept the specific 30s limit error to guide the user to the fix
            if "duration exceeds" in err_msg.lower() or "30 seconds" in err_msg.lower():
                return jsonify({'error': 'Audio > 30s. To enable automatic chunking, run: "pip install pydub" AND install ffmpeg on your server.'}), 400

            return jsonify({'error': f'Sarvam API error: {err_msg}'}), response.status_code

    except Exception as e:
        if 'temp_filepath' in locals() and os.path.exists(temp_filepath):
            os.remove(temp_filepath)
        return jsonify({'error': f'Transcription processing failed: {str(e)}'}), 500


# ═══════════════════════════════════════════════════════════════════════════════
#  PROFILE
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        name     = request.form.get("name", "").strip()
        email    = request.form.get("email", "").strip().lower()
        village  = request.form.get("village", "").strip()
        district = request.form.get("district", "").strip()
        state    = request.form.get("state", "").strip()
        lang     = request.form.get("language", "hi")
        password = request.form.get("new_password", "").strip()

        updates = {
            "name": name, "email": email, "village": village,
            "district": district, "state": state, "language": lang,
        }
        if password and len(password) >= 6:
            updates["password"] = bcrypt.generate_password_hash(password).decode("utf-8")

        mongo.db.users.update_one({"_id": oid(current_user.id)}, {"$set": updates})
        flash("Profile updated successfully.", "success")
        return redirect(url_for("profile"))

    user_doc = mongo.db.users.find_one({"_id": oid(current_user.id)})
    complaints = list(mongo.db.complaints.find(
        {"user_id": oid(current_user.id)},
        {"complaint_id": 1, "title": 1, "status": 1, "created_at": 1}
    ).sort("created_at", -1).limit(5))
    return render_template("profile.html", user=user_doc, complaints=complaints)


# ═══════════════════════════════════════════════════════════════════════════════
#  ADMIN ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/admin/users")
@role_required("admin")
def admin_users():
    page     = int(request.args.get("page", 1))
    per_page = 20
    role_f   = request.args.get("role", "")
    q        = request.args.get("q", "").strip()

    query = {}
    if role_f: query["role"] = role_f
    if q:
        query["$or"] = [
            {"name":    {"$regex": q, "$options": "i"}},
            {"phone":   {"$regex": q, "$options": "i"}},
            {"village": {"$regex": q, "$options": "i"}},
        ]

    total = mongo.db.users.count_documents(query)
    users = list(mongo.db.users.find(query).sort("created_at", -1).skip((page-1)*per_page).limit(per_page))
    pages = max(1, (total + per_page - 1) // per_page)

    return render_template("admin_users.html", users=users, total=total, page=page, pages=pages, q=q, role_f=role_f)


@app.route("/admin/user/<user_id>/toggle", methods=["POST"])
@role_required("admin")
def toggle_user(user_id):
    user = mongo.db.users.find_one({"_id": oid(user_id)})
    if not user:
        abort(404)
    new_status = not user.get("is_active", True)
    mongo.db.users.update_one({"_id": oid(user_id)}, {"$set": {"is_active": new_status}})
    action = "activated" if new_status else "deactivated"
    flash(f"User {user['name']} has been {action}.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/user/<user_id>/role", methods=["POST"])
@role_required("admin")
def change_user_role(user_id):
    new_role = request.form.get("role")
    if new_role not in ROLES:
        flash("Invalid role.", "danger")
        return redirect(url_for("admin_users"))
    mongo.db.users.update_one({"_id": oid(user_id)}, {"$set": {"role": new_role}})
    flash("Role updated.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/analytics")
@role_required("admin")
def admin_analytics():
    # By status
    by_status = {s: mongo.db.complaints.count_documents({"status": s}) for s in STATUSES}

    # By category
    by_cat = {c: mongo.db.complaints.count_documents({"category": c}) for c in CATEGORIES}

    # By priority
    by_priority = {p: mongo.db.complaints.count_documents({"priority": p}) for p in PRIORITIES}

    # Monthly trend (last 6 months)
    monthly = []
    for i in range(5, -1, -1):
        d = datetime.utcnow().replace(day=1) - timedelta(days=i * 30)
        start = d.replace(hour=0, minute=0, second=0, microsecond=0)
        end   = (start + timedelta(days=32)).replace(day=1)
        count = mongo.db.complaints.count_documents({"created_at": {"$gte": start, "$lt": end}})
        monthly.append({"month": start.strftime("%b %Y"), "count": count})

    # Top villages
    pipeline = [
        {"$group": {"_id": "$village", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ]
    top_villages = list(mongo.db.complaints.aggregate(pipeline))

    # Average resolution time
    resolved = list(mongo.db.complaints.find(
        {"status": "Resolved", "resolved_at": {"$exists": True, "$ne": None}},
        {"created_at": 1, "resolved_at": 1}
    ))
    if resolved:
        times = [(r["resolved_at"] - r["created_at"]).total_seconds() / 3600 for r in resolved]
        avg_resolution_hrs = round(sum(times) / len(times), 1)
    else:
        avg_resolution_hrs = 0

    # Rating distribution
    ratings = {str(i): mongo.db.complaints.count_documents({"rating": i}) for i in range(1, 6)}

    return render_template(
        "admin_analytics.html",
        by_status=by_status, by_cat=by_cat, by_priority=by_priority,
        monthly=monthly, top_villages=top_villages,
        avg_resolution_hrs=avg_resolution_hrs, ratings=ratings,
    )


@app.route("/admin/settings", methods=["GET", "POST"])
@role_required("admin")
def admin_settings():
    if request.method == "POST":
        flash("Settings saved (demo mode – no persistence).", "info")
        return redirect(url_for("admin_settings"))
    return render_template("admin_settings.html")


@app.route("/admin/export")
@role_required("admin")
def export_complaints():
    """Export all complaints as JSON."""
    complaints = list(mongo.db.complaints.find({}, {"_id": 0, "user_id": 0}))
    for c in complaints:
        for key in ("created_at", "updated_at", "resolved_at"):
            if isinstance(c.get(key), datetime):
                c[key] = c[key].isoformat()
        for t in c.get("timeline", []):
            if isinstance(t.get("timestamp"), datetime):
                t["timestamp"] = t["timestamp"].isoformat()
    buf = BytesIO(json.dumps(complaints, ensure_ascii=False, indent=2).encode("utf-8"))
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/json",
        as_attachment=True,
        download_name=f"dgsp_complaints_{datetime.utcnow().strftime('%Y%m%d')}.json",
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  API ENDPOINTS  (JSON)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/complaints/stats")
@login_required
def api_stats():
    uid = oid(current_user.id)
    if current_user.role == "citizen":
        base = {"user_id": uid}
    elif current_user.role == "officer":
        v = current_user._data.get("assigned_village", current_user._data.get("village"))
        base = {"village": v}
    else:
        base = {}

    return jsonify({
        s: mongo.db.complaints.count_documents({**base, "status": s})
        for s in STATUSES
    })


@app.route("/api/complaint/<complaint_id>/timeline")
@login_required
def api_timeline(complaint_id):
    doc = mongo.db.complaints.find_one({"complaint_id": complaint_id}, {"timeline": 1})
    if not doc:
        return jsonify([])
    timeline = doc.get("timeline", [])
    for t in timeline:
        if isinstance(t.get("timestamp"), datetime):
            t["timestamp"] = t["timestamp"].isoformat()
    return jsonify(timeline)


@app.route("/api/villages")
def api_villages():
    """Autocomplete endpoint for village names."""
    q = request.args.get("q", "")
    if len(q) < 2:
        return jsonify([])
    villages = mongo.db.users.distinct("village", {"village": {"$regex": q, "$options": "i"}})
    return jsonify(villages[:20])


# ═══════════════════════════════════════════════════════════════════════════════
#  SOCKET.IO EVENTS
# ═══════════════════════════════════════════════════════════════════════════════

@socketio.on("join")
def on_join(data):
    room = data.get("room")
    if room:
        join_room(room)
        emit("joined", {"room": room})


@socketio.on("join_complaint")
def on_join_complaint(data):
    complaint_id = data.get("complaint_id")
    if complaint_id:
        join_room(f"complaint_{complaint_id}")


@socketio.on("join_officers")
def on_join_officers():
    join_room("officers")


# ═══════════════════════════════════════════════════════════════════════════════
#  ERROR HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, message="Access Denied",
                           detail="You don't have permission to access this page."), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page Not Found",
                           detail="The page you're looking for doesn't exist."), 404


@app.errorhandler(429)
def rate_limited(e):
    return render_template("error.html", code=429, message="Too Many Requests",
                           detail="You're making requests too quickly. Please slow down."), 429


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500, message="Server Error",
                           detail="Something went wrong on our end. Please try again later."), 500


# ═══════════════════════════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════════════════════════

def create_indexes():
    """Create MongoDB indexes for performance."""
    try:
        # Force a quick connection check
        mongo.cx.server_info() 
        
        mongo.db.complaints.create_index([("complaint_id", 1)], unique=True)
        mongo.db.complaints.create_index([("user_id", 1)])
        mongo.db.complaints.create_index([("status", 1)])
        mongo.db.complaints.create_index([("village", 1)])
        mongo.db.complaints.create_index([("created_at", -1)])
        mongo.db.users.create_index([("phone", 1)], unique=True, sparse=True)
        mongo.db.users.create_index([("email", 1)], sparse=True)
        logger.info("MongoDB indexes created.")
    except Exception as e:
        logger.debug("MongoDB unavailable for index creation (will retry on request): %s", e)


def seed_admin():
    """Create a default admin user if none exists."""
    try:
        if not mongo.db.users.find_one({"role": "admin"}):
            pw = bcrypt.generate_password_hash("Admin@123").decode("utf-8")
            mongo.db.users.insert_one({
                "name": "Administrator",
                "phone": "9000000000",
                "email": "admin@dgsp.gov.in",
                "password": pw,
                "village": "Headquarters",
                "district": "Central",
                "state": "India",
                "language": "en",
                "role": "admin",
                "is_active": True,
                "created_at": datetime.utcnow(),
                "last_login": None,
            })
            logger.info("Default admin created: phone=9000000000 password=Admin@123")
    except Exception as e:
        logger.debug("MongoDB unavailable for admin seeding (will retry on request): %s", e)


with app.app_context():
    create_indexes()
    seed_admin()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    socketio.run(app, debug=os.getenv("FLASK_DEBUG", "True") == "True", host="0.0.0.0", port=port)