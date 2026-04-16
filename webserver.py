import os
import os
import math
import random
import sqlite3
import re
import json
import threading
import importlib
import hashlib
import mimetypes
import base64
import socket
import ssl
import urllib.request
import urllib.error
from html import unescape
from urllib.parse import urlparse, quote_plus
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import functions

FLOORPLAN_GAME_ROOMS = ("110", "110A", "110B", "110C")
FLOORPLAN_ROOM_PATTERN = re.compile(r"\b(?:110A|110B|110C|110)\b", re.IGNORECASE)

def load_environment_file():
    try:
        dotenv_module = importlib.import_module("dotenv")
        dotenv_module.load_dotenv()
        return
    except ImportError:
        pass
    except Exception as exc:
        print("Could not load environment with dotenv:", exc)

    if os.path.exists(".env"):
        try:
            with open(".env", "r", encoding="utf-8") as env_file:
                for line in env_file:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError as exc:
            print("Could not load .env file:", exc)

load_environment_file()
app = Flask(__name__)
app.secret_key = "bme-inventory-editor-lock"

# Database setup: Create an SQLite database and a table if it doesn't exist
DATABASE = 'bmeInventory.db'
EDITOR_PASSWORD = "MattIsTech!"
HELP_BOT_BACKEND = os.getenv("HELP_BOT_BACKEND", "openai").strip().lower()
HELP_BOT_MODEL = os.getenv("HELP_BOT_MODEL", "gpt-4o-mini")
HELP_BOT_OLLAMA_BASE_URL = os.getenv("HELP_BOT_OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
HELP_BOT_EMBED_MODEL = os.getenv("HELP_BOT_EMBED_MODEL", "mxbai-embed-large")
IMAGE_AGENT_MODEL = os.getenv("IMAGE_AGENT_MODEL", "gpt-4o-mini")
IMAGE_AGENT_PAGE_ATTEMPTS = int(os.getenv("IMAGE_AGENT_PAGE_ATTEMPTS", "2"))
IMAGE_AGENT_MAX_VALIDATIONS = int(os.getenv("IMAGE_AGENT_MAX_VALIDATIONS", "2"))
IMAGE_AGENT_AI_SEARCH_FALLBACK = os.getenv("IMAGE_AGENT_AI_SEARCH_FALLBACK", "false").lower() == "true"
ITEM_IMAGES_FILE = "item_images.json"
ITEM_IMAGE_METADATA_FILE = "item_image_metadata.json"
ITEM_IMAGE_CACHE_DIR = os.path.join("static", "item-images")
_help_bot_backend = None
_image_agent_client = None
_item_images_lock = threading.Lock()

IMAGE_STATUS_PENDING = "pending"
IMAGE_STATUS_SUCCESS = "success"
IMAGE_STATUS_FAILED = "failed"

PUBLIC_ENDPOINTS = {
    "sign_in_page",
    "sign_in",
    "static",
}

def get_db():
    """Open a new database connection."""
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row  # This make the DB return rows as dictionaries
    return db

def ensure_tracking_tables():
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions(
                SessionID INTEGER PRIMARY KEY AUTOINCREMENT,
                Email TEXT NOT NULL,
                SignInDate TEXT NOT NULL,
                SignInTime TEXT NOT NULL,
                SignOutDate TEXT,
                SignOutTime TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_item_access(
                AccessID INTEGER PRIMARY KEY AUTOINCREMENT,
                SessionID INTEGER NOT NULL,
                Email TEXT NOT NULL,
                UPC INTEGER,
                ItemName TEXT NOT NULL,
                AccessDate TEXT NOT NULL,
                AccessTime TEXT NOT NULL,
                FOREIGN KEY (SessionID) REFERENCES user_sessions(SessionID)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_floorplan_stats(
                Email TEXT PRIMARY KEY,
                Score INTEGER NOT NULL DEFAULT 0,
                Attempts INTEGER NOT NULL DEFAULT 0,
                UpdatedAt TEXT NOT NULL
            )
        """)
        db.commit()
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while ensuring tracking tables:", e.args[0])
    finally:
        db.close()

ensure_tracking_tables()

def is_signed_in():
    return bool((session.get("signed_in_email") or "").strip())

@app.before_request
def require_sign_in():
    endpoint = request.endpoint or ""
    if endpoint in PUBLIC_ENDPOINTS:
        return None

    if is_signed_in():
        return None

    wants_json = request.path.startswith("/search") or request.path.startswith("/help-chat") or request.is_json
    if wants_json:
        return jsonify({"error": "Sign-in required", "redirect": url_for("sign_in_page")}), 401

    return redirect(url_for("sign_in_page"))

def load_inventory_items(room_name=None):
    db = get_db()
    cursor = db.cursor()
    items = []
    try:
        query = """
            SELECT
                i.UPC,
                i.Name,
                i.TotalQty,
                GROUP_CONCAT(
                    DISTINCT CASE
                        WHEN r.RoomName IS NOT NULL THEN
                            CASE
                                WHEN ? IS NOT NULL AND r.RoomName = ? THEN w.WallName
                                ELSE r.RoomName
                            END
                    END
                ) AS WallNames,
                GROUP_CONCAT(
                    DISTINCT CASE
                        WHEN r.RoomName IS NOT NULL THEN
                            r.RoomName || ' ' || w.WallName || ' ' || b.BinType || ' ' || b.BinID
                    END
                ) AS LocationDetails,
                MAX(datetime(ib.Date || ' ' || ib.Time)) AS LastAdded
            FROM items i
            LEFT JOIN item_bin ib ON ib.UPC = i.UPC
            LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
            LEFT JOIN walls w ON w.WallID = b.WallID
            LEFT JOIN rooms r ON r.RoomID = w.RoomID
        """
        params = [room_name, room_name]

        if room_name:
            query += """
            WHERE EXISTS (
                SELECT 1
                FROM item_bin ib2
                JOIN bins b2 ON b2.BinUPC = ib2.BinUPC
                JOIN walls w2 ON w2.WallID = b2.WallID
                JOIN rooms r2 ON r2.RoomID = w2.RoomID
                WHERE ib2.UPC = i.UPC AND r2.RoomName = ?
            )
            """
            params.append(room_name)

        query += """
            GROUP BY i.UPC, i.Name, i.TotalQty
            ORDER BY
                LastAdded IS NULL,
                LastAdded DESC,
                i.UPC DESC
        """

        cursor.execute(query, params)
        rows = cursor.fetchall()
        items = [dict(row) for row in rows]
    except sqlite3.Error as e:
        print("An error occurred while loading inventory items:", e.args[0])
    finally:
        db.close()

    return items

def load_database_entries():
    db = get_db()
    cursor = db.cursor()
    entries = []
    try:
        cursor.execute("""
            SELECT
                ib.EntryID,
                ib.UPC,
                ib.Name,
                ib.BinUPC,
                ib.Qty,
                ib.Date,
                ib.Time,
                b.BinID,
                b.BinType,
                w.WallName,
                r.RoomName
            FROM item_bin ib
            LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
            LEFT JOIN walls w ON w.WallID = b.WallID
            LEFT JOIN rooms r ON r.RoomID = w.RoomID
            ORDER BY
                datetime(ib.Date || ' ' || ib.Time) DESC,
                ib.EntryID DESC
        """)
        rows = cursor.fetchall()
        entries = [dict(row) for row in rows]
    except sqlite3.Error as e:
        print("An error occurred while loading database entries:", e.args[0])
    finally:
        db.close()

    for entry in entries:
        entry["ImageStatus"] = get_item_image_status(entry.get("Name"))

    return entries

def load_user_tracking(limit=100):
    db = get_db()
    cursor = db.cursor()
    sessions = []
    try:
        cursor.execute("""
            SELECT
                s.SessionID,
                s.Email,
                s.SignInDate,
                s.SignInTime,
                s.SignOutDate,
                s.SignOutTime,
                COUNT(a.AccessID) AS AccessCount,
                GROUP_CONCAT(
                    DISTINCT CASE
                        WHEN a.ItemName IS NOT NULL AND a.ItemName != '' THEN a.ItemName
                    END
                ) AS AccessedItems
            FROM user_sessions s
            LEFT JOIN user_item_access a ON a.SessionID = s.SessionID
            GROUP BY
                s.SessionID,
                s.Email,
                s.SignInDate,
                s.SignInTime,
                s.SignOutDate,
                s.SignOutTime
            ORDER BY
                s.SessionID DESC
            LIMIT ?
        """, (limit,))
        sessions = [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        print("An error occurred while loading user tracking:", e.args[0])
    finally:
        db.close()

    return sessions

def log_user_sign_in(email):
    email = (email or "").strip()
    if not email:
        return None

    now = datetime.now()
    sign_in_date = now.strftime("%Y-%m-%d")
    sign_in_time = now.strftime("%H:%M:%S")

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute(
            "INSERT INTO user_sessions (Email, SignInDate, SignInTime) VALUES (?, ?, ?)",
            (email, sign_in_date, sign_in_time)
        )
        db.commit()
        return cursor.lastrowid
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while logging sign in:", e.args[0])
        return None
    finally:
        db.close()

def log_user_sign_out(session_id):
    if not session_id:
        return

    now = datetime.now()
    sign_out_date = now.strftime("%Y-%m-%d")
    sign_out_time = now.strftime("%H:%M:%S")

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute(
            """
            UPDATE user_sessions
            SET SignOutDate = ?, SignOutTime = ?
            WHERE SessionID = ? AND (SignOutDate IS NULL OR SignOutTime IS NULL)
            """,
            (sign_out_date, sign_out_time, session_id)
        )
        db.commit()
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while logging sign out:", e.args[0])
    finally:
        db.close()

def log_item_access(session_id, email, upc, item_name):
    email = (email or "").strip()
    item_name = (item_name or "").strip()
    if not session_id or not email or not item_name:
        return False

    now = datetime.now()
    access_date = now.strftime("%Y-%m-%d")
    access_time = now.strftime("%H:%M:%S")

    try:
        upc_value = int(upc) if str(upc).strip() else None
    except (TypeError, ValueError):
        upc_value = None

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO user_item_access (SessionID, Email, UPC, ItemName, AccessDate, AccessTime)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, email, upc_value, item_name, access_date, access_time)
        )
        db.commit()
        return True
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while logging item access:", e.args[0])
        return False
    finally:
        db.close()

def load_database_entry(entry_id):
    db = get_db()
    cursor = db.cursor()
    entry = None
    try:
        cursor.execute("""
            SELECT
                ib.EntryID,
                ib.UPC,
                ib.Name,
                ib.Qty,
                ib.Date,
                ib.Time,
                b.BinType,
                b.BinID,
                w.WallName,
                r.RoomName
            FROM item_bin ib
            LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
            LEFT JOIN walls w ON w.WallID = b.WallID
            LEFT JOIN rooms r ON r.RoomID = w.RoomID
            WHERE ib.EntryID = ?
        """, (entry_id,))
        row = cursor.fetchone()
        if row:
            entry = dict(row)
    except sqlite3.Error as e:
        print("An error occurred while loading database entry:", e.args[0])
    finally:
        db.close()

    if entry:
        entry["ImageStatus"] = get_item_image_status(entry.get("Name"))

    return entry

def load_bins_directory():
    db = get_db()
    cursor = db.cursor()
    bins = []
    try:
        cursor.execute("""
            SELECT
                b.BinUPC,
                b.BinID,
                b.BinType,
                w.WallName,
                r.RoomName
            FROM bins b
            LEFT JOIN walls w ON w.WallID = b.WallID
            LEFT JOIN rooms r ON r.RoomID = w.RoomID
            ORDER BY
                r.RoomName,
                w.WallName,
                b.BinType,
                b.BinID
        """)
        rows = cursor.fetchall()
        bins = [dict(row) for row in rows]
    except sqlite3.Error as e:
        print("An error occurred while loading bins directory:", e.args[0])
    finally:
        db.close()

    return bins

def load_bin_row(bin_upc):
    db = get_db()
    cursor = db.cursor()
    bin_row = None
    try:
        cursor.execute("""
            SELECT
                b.BinUPC,
                b.BinID,
                b.BinType,
                w.WallName,
                r.RoomName
            FROM bins b
            LEFT JOIN walls w ON w.WallID = b.WallID
            LEFT JOIN rooms r ON r.RoomID = w.RoomID
            WHERE b.BinUPC = ?
        """, (bin_upc,))
        row = cursor.fetchone()
        if row:
            bin_row = dict(row)
    except sqlite3.Error as e:
        print("An error occurred while loading bin row:", e.args[0])
    finally:
        db.close()

    return bin_row

def load_help_inventory_snapshot():
    db = get_db()
    cursor = db.cursor()
    items = []
    try:
        cursor.execute("""
            SELECT
                i.UPC,
                i.Name,
                i.TotalQty,
                GROUP_CONCAT(
                    DISTINCT CASE
                        WHEN r.RoomName IS NOT NULL THEN
                            r.RoomName || ' ' || w.WallName || ' ' || b.BinType || ' ' || b.BinID
                    END
                ) AS Locations
            FROM items i
            LEFT JOIN item_bin ib ON ib.UPC = i.UPC
            LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
            LEFT JOIN walls w ON w.WallID = b.WallID
            LEFT JOIN rooms r ON r.RoomID = w.RoomID
            GROUP BY i.UPC, i.Name, i.TotalQty
            ORDER BY i.Name
        """)
        items = [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        print("An error occurred while loading help inventory snapshot:", e.args[0])
    finally:
        db.close()

    return items

def build_item_thumbnail_data_uri(item_name):
    safe_name = (item_name or "Item").strip()
    initials = "".join(part[0] for part in safe_name.split()[:2]).upper() or "IT"
    accent = "#70ffa7"
    bg = "#274134"
    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="320" height="220" viewBox="0 0 320 220">
        <defs>
            <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="{bg}" />
                <stop offset="100%" stop-color="#3c3c3c" />
            </linearGradient>
        </defs>
        <rect width="320" height="220" rx="28" fill="url(#g)" />
        <circle cx="160" cy="92" r="46" fill="rgba(255,255,255,0.10)" />
        <text x="160" y="107" text-anchor="middle" font-family="Arial, sans-serif" font-size="38" font-weight="700" fill="{accent}">{initials}</text>
        <text x="160" y="178" text-anchor="middle" font-family="Arial, sans-serif" font-size="20" fill="#ffffff">Inventory Item</text>
    </svg>
    """.strip()
    encoded = re.sub(r"\s+", " ", svg).replace("#", "%23").replace("<", "%3C").replace(">", "%3E").replace('"', "'")
    return f"data:image/svg+xml,{encoded}"

def load_item_image_map():
    if not os.path.exists(ITEM_IMAGES_FILE):
        return {}

    try:
        with open(ITEM_IMAGES_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError) as exc:
        print("Could not load item image map:", exc)

    return {}

def save_item_image_map(image_map):
    payload = dict(image_map)
    try:
        with open(ITEM_IMAGES_FILE, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, sort_keys=True)
    except OSError as exc:
        print("Could not save item image map:", exc)

def load_item_image_metadata():
    if not os.path.exists(ITEM_IMAGE_METADATA_FILE):
        return {}

    try:
        with open(ITEM_IMAGE_METADATA_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError) as exc:
        print("Could not load item image metadata:", exc)

    return {}

def normalize_item_image_key(item_name):
    return (item_name or "").strip()

def get_item_image_status(item_name):
    normalized_name = normalize_item_image_key(item_name)
    if not normalized_name:
        return IMAGE_STATUS_FAILED

    image_map = load_item_image_map()
    if normalized_name in image_map:
        return IMAGE_STATUS_SUCCESS

    for key in image_map.keys():
        if key.strip() == normalized_name:
            return IMAGE_STATUS_SUCCESS

    metadata_map = load_item_image_metadata()
    metadata = metadata_map.get(normalized_name)
    if not metadata:
        for key, value in metadata_map.items():
            if key.strip() == normalized_name:
                metadata = value
                break

    if isinstance(metadata, dict):
        status = (metadata.get("status") or "").strip().lower()
        if status in {IMAGE_STATUS_PENDING, IMAGE_STATUS_SUCCESS, IMAGE_STATUS_FAILED}:
            return status

    return IMAGE_STATUS_FAILED

def set_item_image_status(item_name, status, **extra_fields):
    normalized_name = normalize_item_image_key(item_name)
    if not normalized_name:
        return

    with _item_images_lock:
        metadata_map = load_item_image_metadata()
        existing = {}
        if normalized_name in metadata_map and isinstance(metadata_map[normalized_name], dict):
            existing = dict(metadata_map[normalized_name])
        else:
            for key, value in metadata_map.items():
                if key.strip() == normalized_name and isinstance(value, dict):
                    existing = dict(value)
                    break

        existing.update(extra_fields)
        existing["status"] = status
        existing["updated_at"] = datetime.now().isoformat(timespec="seconds")
        metadata_map[normalized_name] = existing
        save_item_image_metadata(metadata_map)

def save_item_image_metadata(metadata_map):
    payload = dict(metadata_map)
    try:
        with open(ITEM_IMAGE_METADATA_FILE, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, sort_keys=True)
    except OSError as exc:
        print("Could not save item image metadata:", exc)

def get_item_image_url(item_name):
    image_map = load_item_image_map()
    normalized_name = (item_name or "").strip()
    if normalized_name in image_map:
        return image_map[normalized_name]

    for key, value in image_map.items():
        if key.strip() == normalized_name:
            return value

    return build_item_thumbnail_data_uri(item_name)

def load_recently_changed_items(limit=5):
    db = get_db()
    cursor = db.cursor()
    items = []
    try:
        cursor.execute("""
            SELECT
                i.UPC,
                i.Name,
                i.TotalQty,
                MAX(datetime(ib.Date || ' ' || ib.Time)) AS LastChanged,
                GROUP_CONCAT(
                    DISTINCT CASE
                        WHEN r.RoomName IS NOT NULL THEN r.RoomName
                    END
                ) AS Rooms
            FROM items i
            JOIN item_bin ib ON ib.UPC = i.UPC
            LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
            LEFT JOIN walls w ON w.WallID = b.WallID
            LEFT JOIN rooms r ON r.RoomID = w.RoomID
            GROUP BY i.UPC, i.Name, i.TotalQty
            ORDER BY LastChanged DESC, i.Name
            LIMIT ?
        """, (limit,))
        items = [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        print("An error occurred while loading recently changed items:", e.args[0])
    finally:
        db.close()

    for item in items:
        item["Thumbnail"] = get_item_image_url(item["Name"])

    return items

def load_room_inventory(room_name):
    db = get_db()
    cursor = db.cursor()
    items = []
    try:
        cursor.execute("""
            SELECT
                i.UPC,
                i.Name,
                i.TotalQty,
                w.WallName,
                b.BinType,
                b.BinID
            FROM items i
            JOIN item_bin ib ON ib.UPC = i.UPC
            JOIN bins b ON b.BinUPC = ib.BinUPC
            JOIN walls w ON w.WallID = b.WallID
            JOIN rooms r ON r.RoomID = w.RoomID
            WHERE r.RoomName = ?
            ORDER BY
                i.Name,
                w.WallName,
                b.BinType,
                b.BinID
        """, (room_name,))
        items = [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        print("An error occurred while loading room inventory:", e.args[0])
    finally:
        db.close()

    return items

def extract_floorplan_rooms(*texts):
    rooms = []
    seen = set()

    for text in texts:
        for match in FLOORPLAN_ROOM_PATTERN.findall((text or "").upper()):
            room = match.upper()
            if room in seen:
                continue
            seen.add(room)
            rooms.append(room)

    return rooms

def load_floorplan_game_candidates():
    candidates = []

    for item in load_search_cards():
        rooms = extract_floorplan_rooms(item.get("Rooms"), item.get("LocationDetails"))
        if not rooms:
            continue
        candidates.append({
            "upc": int(item["UPC"]),
            "name": item["Name"],
            "rooms": rooms,
        })

    return candidates

def get_default_floorplan_game_state():
    return {
        "score": 0,
        "attempts": 0,
        "target_upc": None,
        "feedback": "Click the room where you think the item is stored.",
        "last_guess": "",
    }

def load_floorplan_stats(email):
    normalized_email = (email or "").strip().lower()
    if not normalized_email:
        return {"score": 0, "attempts": 0}

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("""
            SELECT Score, Attempts
            FROM user_floorplan_stats
            WHERE Email = ?
        """, (normalized_email,))
        row = cursor.fetchone()
        if not row:
            return {"score": 0, "attempts": 0}
        return {
            "score": int(row["Score"] or 0),
            "attempts": int(row["Attempts"] or 0),
        }
    except sqlite3.Error as e:
        print("An error occurred while loading floorplan stats:", e.args[0])
        return {"score": 0, "attempts": 0}
    finally:
        db.close()

def save_floorplan_stats(email, score, attempts):
    normalized_email = (email or "").strip().lower()
    if not normalized_email:
        return

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("""
            INSERT INTO user_floorplan_stats (Email, Score, Attempts, UpdatedAt)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(Email) DO UPDATE SET
                Score = excluded.Score,
                Attempts = excluded.Attempts,
                UpdatedAt = excluded.UpdatedAt
        """, (
            normalized_email,
            max(0, int(score or 0)),
            max(0, int(attempts or 0)),
            datetime.now().isoformat(timespec="seconds"),
        ))
        db.commit()
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while saving floorplan stats:", e.args[0])
    finally:
        db.close()

def get_persistent_floorplan_game_state(email):
    state = get_default_floorplan_game_state()
    stats = load_floorplan_stats(email)
    state["score"] = stats["score"]
    state["attempts"] = stats["attempts"]
    return state

def assign_floorplan_target(state, candidates, exclude_upc=None):
    if not candidates:
        state["target_upc"] = None
        return

    pool = [candidate for candidate in candidates if candidate["upc"] != exclude_upc]
    if not pool:
        pool = candidates

    state["target_upc"] = random.choice(pool)["upc"]

def ensure_floorplan_game_state():
    state = session.get("floorplan_game")
    if not isinstance(state, dict):
        state = get_persistent_floorplan_game_state(session.get("signed_in_email"))

    candidates = load_floorplan_game_candidates()
    candidate_upcs = {candidate["upc"] for candidate in candidates}

    if state.get("target_upc") not in candidate_upcs:
        assign_floorplan_target(state, candidates)

    session["floorplan_game"] = state
    return state, candidates

def get_floorplan_target_candidate(candidates, target_upc):
    for candidate in candidates:
        if candidate["upc"] == target_upc:
            return candidate
    return None

def load_search_cards(search_query="", upcs=None):
    db = get_db()
    cursor = db.cursor()
    results = []
    upc_values = [int(upc) for upc in (upcs or []) if str(upc).strip()]
    try:
        if upc_values:
            placeholders = ",".join("?" for _ in upc_values)
            cursor.execute(
                f"""
                SELECT
                    i.UPC,
                    i.Name,
                    i.TotalQty,
                    COUNT(DISTINCT ib.BinUPC) AS LocationCount,
                    GROUP_CONCAT(
                        DISTINCT CASE
                            WHEN r.RoomName IS NOT NULL THEN r.RoomName
                        END
                    ) AS Rooms,
                    GROUP_CONCAT(
                        DISTINCT CASE
                            WHEN r.RoomName IS NOT NULL AND w.WallName IS NOT NULL THEN r.RoomName || ' ' || w.WallName
                        END
                    ) AS WallNames,
                    GROUP_CONCAT(
                        DISTINCT CASE
                            WHEN r.RoomName IS NOT NULL AND w.WallName IS NOT NULL AND b.BinType IS NOT NULL AND b.BinID IS NOT NULL THEN
                                r.RoomName || ' ' || w.WallName || ' ' || b.BinType || ' ' || b.BinID
                        END
                    ) AS LocationDetails,
                    MAX(datetime(ib.Date || ' ' || ib.Time)) AS LastChanged
                FROM items i
                LEFT JOIN item_bin ib ON ib.UPC = i.UPC
                LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
                LEFT JOIN walls w ON w.WallID = b.WallID
                LEFT JOIN rooms r ON r.RoomID = w.RoomID
                WHERE i.UPC IN ({placeholders})
                GROUP BY i.UPC, i.Name, i.TotalQty
                ORDER BY LastChanged DESC, i.Name
                """,
                upc_values,
            )
        else:
            cursor.execute(
                """
                SELECT
                    i.UPC,
                    i.Name,
                    i.TotalQty,
                    COUNT(DISTINCT ib.BinUPC) AS LocationCount,
                    GROUP_CONCAT(
                        DISTINCT CASE
                            WHEN r.RoomName IS NOT NULL THEN r.RoomName
                        END
                    ) AS Rooms,
                    GROUP_CONCAT(
                        DISTINCT CASE
                            WHEN r.RoomName IS NOT NULL AND w.WallName IS NOT NULL THEN r.RoomName || ' ' || w.WallName
                        END
                    ) AS WallNames,
                    GROUP_CONCAT(
                        DISTINCT CASE
                            WHEN r.RoomName IS NOT NULL AND w.WallName IS NOT NULL AND b.BinType IS NOT NULL AND b.BinID IS NOT NULL THEN
                                r.RoomName || ' ' || w.WallName || ' ' || b.BinType || ' ' || b.BinID
                        END
                    ) AS LocationDetails,
                    MAX(datetime(ib.Date || ' ' || ib.Time)) AS LastChanged
                FROM items i
                LEFT JOIN item_bin ib ON ib.UPC = i.UPC
                LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
                LEFT JOIN walls w ON w.WallID = b.WallID
                LEFT JOIN rooms r ON r.RoomID = w.RoomID
                WHERE CAST(i.UPC AS TEXT) LIKE ? OR i.Name LIKE ?
                GROUP BY i.UPC, i.Name, i.TotalQty
                ORDER BY
                    CASE
                        WHEN i.Name = ? THEN 0
                        WHEN i.Name LIKE ? THEN 1
                        WHEN CAST(i.UPC AS TEXT) = ? THEN 2
                        ELSE 3
                    END,
                    LastChanged DESC,
                    i.Name
                """,
                (
                    '%' + search_query + '%',
                    '%' + search_query + '%',
                    search_query,
                    search_query + '%',
                    search_query,
                )
            )

        results = [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        print("An error occurred while loading search cards:", e.args[0])
    finally:
        db.close()

    for item in results:
        item["Thumbnail"] = get_item_image_url(item.get("Name"))

    return results

def find_item_records(query_text):
    db = get_db()
    cursor = db.cursor()
    normalized = (query_text or "").strip()
    results = []

    try:
        if normalized.isdigit():
            cursor.execute("""
                SELECT
                    i.UPC,
                    i.Name,
                    i.TotalQty,
                    GROUP_CONCAT(
                        DISTINCT r.RoomName || ' ' || w.WallName || ' ' || b.BinType || ' ' || b.BinID
                    ) AS Locations
                FROM items i
                LEFT JOIN item_bin ib ON ib.UPC = i.UPC
                LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
                LEFT JOIN walls w ON w.WallID = b.WallID
                LEFT JOIN rooms r ON r.RoomID = w.RoomID
                WHERE CAST(i.UPC AS TEXT) = ?
                GROUP BY i.UPC, i.Name, i.TotalQty
                ORDER BY i.Name
            """, (normalized,))
        else:
            wildcard = f"%{normalized}%"
            cursor.execute("""
                SELECT
                    i.UPC,
                    i.Name,
                    i.TotalQty,
                    GROUP_CONCAT(
                        DISTINCT r.RoomName || ' ' || w.WallName || ' ' || b.BinType || ' ' || b.BinID
                    ) AS Locations
                FROM items i
                LEFT JOIN item_bin ib ON ib.UPC = i.UPC
                LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
                LEFT JOIN walls w ON w.WallID = b.WallID
                LEFT JOIN rooms r ON r.RoomID = w.RoomID
                WHERE i.Name LIKE ? OR CAST(i.UPC AS TEXT) LIKE ?
                GROUP BY i.UPC, i.Name, i.TotalQty
                ORDER BY
                    CASE WHEN LOWER(i.Name) = LOWER(?) THEN 0 ELSE 1 END,
                    CASE WHEN LOWER(i.Name) LIKE LOWER(?) THEN 0 ELSE 1 END,
                    i.Name
                LIMIT 8
            """, (wildcard, wildcard, normalized, normalized + '%'))

        results = [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        print("An error occurred while finding item records:", e.args[0])
    finally:
        db.close()

    return results

def extract_room_name(message):
    match = re.search(r"\b(110A|110B|110C|110)\b", message.upper())
    if match:
        return match.group(1)
    return None

def extract_item_query(message):
    message = (message or "").strip()
    if not message:
        return ""

    quoted = re.findall(r'"([^"]+)"', message)
    if quoted:
        return quoted[0].strip()

    lowered = message.lower()
    lowered = re.sub(r"\b(110a|110b|110c|110)\b", " ", lowered)
    fillers = [
        "where is", "where are", "find", "locate", "location of", "show me", "tell me about",
        "what is", "what are", "do you have", "how many", "can you find", "item", "the", "please",
        "inventory", "for", "in the database", "in database", "room", "located", "at",
        "do we have", "we have", "there", "any", "many", "quantity of"
    ]
    for filler in fillers:
        lowered = lowered.replace(filler, " ")

    lowered = re.sub(r"\s+", " ", lowered).strip(" ?.!,'")
    return lowered

def get_inventory_intent_matches(message, catalog):
    lowered = (message or "").lower()
    if not lowered:
        return []

    # Handle subtype requests before broad equipment categories.
    if any(keyword in lowered for keyword in ("resin printer", "resin printers", "sla printer", "sla printers", "msla", "photopolymer")):
        resin_matches = [
            item for item in catalog
            if any(token in item["Name"].lower() for token in ("formlabs", "resin", "sla", "msla"))
        ]
        fallback_matches = [
            item for item in catalog
            if any(token in item["Name"].lower() for token in ("prusa", "bambu", "raise3d"))
        ]
        return [("Resin 3D printers", resin_matches, "FDM 3D printers", fallback_matches)]

    if any(keyword in lowered for keyword in ("fdm printer", "fdm printers", "filament printer", "filament printers")):
        fdm_matches = [
            item for item in catalog
            if any(token in item["Name"].lower() for token in ("prusa", "bambu", "raise3d"))
        ]
        return [("FDM 3D printers", fdm_matches, None, [])]

    intent_rules = [
        {
            "keywords": ("keyboard", "keyboards", "mechanical keyboard", "wireless keyboard", "typing keyboard"),
            "matches": ("keyboard", "royal kludge", "rk royal kludge", "rk "),
            "label": "Keyboards"
        },
        {
            "keywords": ("power tool", "power tools", "shop tool", "shop tools", "band saw", "bandsaw", "cnc"),
            "matches": ("band saw", "shopmaster", "cnc", "othermill"),
            "label": "Power tools"
        },
        {
            "keywords": ("pcb", "pcbs", "circuit board", "printed circuit", "board milling", "pcb milling", "pcb printer"),
            "matches": ("voltera", "othermill"),
            "label": "PCB-related machines"
        },
        {
            "keywords": ("3d printer", "3d printers", "3d print", "3d printing", "printer"),
            "matches": ("prusa", "bambu", "raise3d"),
            "label": "3D printing machines"
        },
        {
            "keywords": ("laser cutter", "laser cutting", "engraver", "engraving", "glowforge"),
            "matches": ("glowforge",),
            "label": "Laser cutting equipment"
        },
        {
            "keywords": ("solder", "soldering", "rework", "hot air"),
            "matches": ("solder station", "995d"),
            "label": "Soldering equipment"
        },
        {
            "keywords": ("dryer", "dry filament", "filament dryer", "filadryer"),
            "matches": ("filadryer", "dryer"),
            "label": "Filament drying equipment"
        },
        {
            "keywords": ("microscope", "inspect", "inspection", "magnify", "magnification"),
            "matches": ("microscope",),
            "label": "Inspection equipment"
        },
    ]

    for rule in intent_rules:
        if any(keyword in lowered for keyword in rule["keywords"]):
            matches = [
                item for item in catalog
                if any(token in item["Name"].lower() for token in rule["matches"])
            ]
            if matches:
                return [(rule["label"], matches, None, [])]

    return []

def dedupe_upcs(upcs):
    unique_upcs = []
    seen = set()
    for upc in upcs:
        if not upc or upc in seen:
            continue
        seen.add(upc)
        unique_upcs.append(upc)
    return unique_upcs

def build_help_embedding_text(item):
    name = (item.get("Name") or "").strip()
    locations = (item.get("Locations") or "").strip()
    qty = item.get("TotalQty")
    parts = [name]

    lowered_name = name.lower()
    semantic_tags = []
    if "royal kludge" in lowered_name or lowered_name.startswith("rk "):
        semantic_tags.extend(["keyboard", "mechanical keyboard", "wireless keyboard"])

    if semantic_tags:
        parts.append("related terms " + ", ".join(semantic_tags))

    if locations:
        parts.append(f"stored at {locations}")
    if qty is not None:
        parts.append(f"quantity {qty}")
    return ". ".join(part for part in parts if part)

def find_semantic_help_matches(message, catalog, backend, limit=4, min_similarity=0.38):
    if not message or not catalog or backend is None:
        return []

    try:
        texts = [message] + [build_help_embedding_text(item) for item in catalog]
        embeddings = backend.embed_texts(texts)
    except Exception as exc:
        print("Semantic help matching failed:", exc)
        return []

    if len(embeddings) != len(texts):
        return []

    query_embedding = embeddings[0]
    scored_items = []
    for item, item_embedding in zip(catalog, embeddings[1:]):
        similarity = cosine_similarity(query_embedding, item_embedding)
        if similarity >= min_similarity:
            scored_items.append((similarity, item))

    scored_items.sort(key=lambda entry: entry[0], reverse=True)
    return [item for _, item in scored_items[:limit]]

def resolve_help_bot_request(message, backend=None):
    text = (message or "").strip()
    if not text:
        return {
            "reply": "I can help find items, rooms, walls, storage types, and bin locations in the inventory.",
            "upcs": [],
            "use_local_reply": True,
        }

    lowered = text.lower()
    room_name = extract_room_name(text)
    catalog = load_help_inventory_snapshot()
    intent_matches = get_inventory_intent_matches(text, catalog)

    if intent_matches:
        label, matches, fallback_label, fallback_matches = intent_matches[0]
        if not matches:
            if fallback_matches:
                fallback_summaries = [
                    f"{item['Name']} ({item['Locations'] or 'location unknown'})"
                    for item in fallback_matches
                ]
                return {
                    "reply": (
                        f"I could not find any logged {label.lower()} in the inventory. "
                        f"The closest related equipment I found is {fallback_label.lower()}: "
                        + "; ".join(fallback_summaries) + "."
                    ),
                    "upcs": dedupe_upcs(item["UPC"] for item in fallback_matches if item.get("UPC")),
                    "use_local_reply": True,
                }
            return {
                "reply": f"I could not find any logged {label.lower()} in the inventory.",
                "upcs": [],
                "use_local_reply": True,
            }
        summaries = [
            f"{item['Name']} ({item['Locations'] or 'location unknown'})"
            for item in matches
        ]
        return {
            "reply": f"The {label.lower()} I found are: " + "; ".join(summaries) + ".",
            "upcs": dedupe_upcs(item["UPC"] for item in matches if item.get("UPC")),
            "use_local_reply": True,
        }

    if "3d printers" in lowered or "3d printer" in lowered:
        printer_keywords = ("prusa", "bambu", "raise3d")
        printers = [item for item in catalog if any(keyword in item["Name"].lower() for keyword in printer_keywords)]
        if printers:
            printer_summaries = [
                f"{item['Name']} ({item['Locations'] or 'location unknown'})"
                for item in printers
            ]
            return {
                "reply": "The logged 3D printers I found are: " + "; ".join(printer_summaries) + ".",
                "upcs": dedupe_upcs(item["UPC"] for item in printers if item.get("UPC")),
                "use_local_reply": True,
            }

    if "3d print" in lowered or "3d printing" in lowered:
        printer_keywords = ("prusa", "bambu", "raise3d")
        printers = [item for item in catalog if any(keyword in item["Name"].lower() for keyword in printer_keywords)]
        support_items = [item for item in catalog if "filadryer" in item["Name"].lower() or "dryer" in item["Name"].lower()]

        parts = []
        if printers:
            parts.append(
                "For 3D printing, the main printers I found are " +
                "; ".join(f"{item['Name']} ({item['Locations'] or 'location unknown'})" for item in printers)
            )
        if support_items:
            parts.append(
                "Related supporting equipment includes " +
                "; ".join(f"{item['Name']} ({item['Locations'] or 'location unknown'})" for item in support_items)
            )

        if parts:
            related_items = printers + support_items
            return {
                "reply": ". ".join(parts) + ".",
                "upcs": dedupe_upcs(item["UPC"] for item in related_items if item.get("UPC")),
                "use_local_reply": True,
            }

    if any(word in lowered for word in ["hello", "hi", "hey", "help"]) and len(lowered.split()) <= 6:
        return {
            "reply": (
                "I can help you find where an item is stored, check what is in a room, or summarize item quantities. "
                "Try asking something like 'Where is Microscope?' or 'What is in room 110A?'"
            ),
            "upcs": [],
            "use_local_reply": True,
        }

    if room_name and any(phrase in lowered for phrase in ["what is in", "what's in", "show", "list", "items in", "in room"]):
        room_items = load_room_inventory(room_name)
        if not room_items:
            return {
                "reply": f"I could not find any logged items in room {room_name}.",
                "upcs": [],
                "use_local_reply": True,
            }

        preview = []
        for item in room_items[:8]:
            preview.append(f"{item['Name']} ({item['WallName']} {item['BinType']} {item['BinID']})")

        extra_count = max(0, len(room_items) - len(preview))
        suffix = f" There are {extra_count} more item entries in that room." if extra_count else ""
        return {
            "reply": f"Room {room_name} currently has: " + "; ".join(preview) + "." + suffix,
            "upcs": dedupe_upcs(item["UPC"] for item in room_items if item.get("UPC")),
            "use_local_reply": True,
        }

    item_query = extract_item_query(text)
    if room_name and not item_query:
        room_items = load_room_inventory(room_name)
        if not room_items:
            return {
                "reply": f"I could not find any logged items in room {room_name}.",
                "upcs": [],
                "use_local_reply": True,
            }
        return {
            "reply": (
                f"Room {room_name} has {len(room_items)} logged item entries. "
                "Ask me to list the items in that room if you want the details."
            ),
            "upcs": dedupe_upcs(item["UPC"] for item in room_items if item.get("UPC")),
            "use_local_reply": True,
        }

    matches = find_item_records(item_query or text)
    if not matches:
        semantic_matches = find_semantic_help_matches(text, catalog, backend)
        if semantic_matches:
            summaries = [
                f"{item['Name']} ({item['Locations'] or 'location unknown'})"
                for item in semantic_matches
            ]
            return {
                "reply": "The closest related inventory items I found are: " + "; ".join(summaries) + ".",
                "upcs": dedupe_upcs(item["UPC"] for item in semantic_matches if item.get("UPC")),
                "use_local_reply": True,
            }
        return {
            "reply": "I could not find a matching item. Try the exact item name, part of the name, a UPC, or ask about a room like 110A.",
            "upcs": [],
            "use_local_reply": False,
        }

    if len(matches) > 1 and (item_query or text) and not (item_query or text).isdigit():
        names = [f"{item['Name']} (UPC {item['UPC']})" for item in matches[:5]]
        return {
            "reply": "I found multiple possible matches: " + "; ".join(names) + ". Tell me which one you want and I can give its location.",
            "upcs": dedupe_upcs(item["UPC"] for item in matches if item.get("UPC")),
            "use_local_reply": True,
        }

    item = matches[0]
    if any(phrase in lowered for phrase in ["how many", "quantity", "qty", "count"]):
        return {
            "reply": f"{item['Name']} has a total logged quantity of {item['TotalQty']}.",
            "upcs": [item["UPC"]] if item.get("UPC") else [],
            "use_local_reply": True,
        }

    if item.get("Locations"):
        return {
            "reply": f"{item['Name']} (UPC {item['UPC']}) has total quantity {item['TotalQty']} and is located at {item['Locations']}.",
            "upcs": [item["UPC"]] if item.get("UPC") else [],
            "use_local_reply": True,
        }

    return {
        "reply": f"{item['Name']} (UPC {item['UPC']}) has total quantity {item['TotalQty']}, but I could not find a logged location for it.",
        "upcs": [item["UPC"]] if item.get("UPC") else [],
        "use_local_reply": True,
    }

def build_help_bot_reply(message):
    return resolve_help_bot_request(message, backend=get_help_bot_backend())["reply"]

def get_help_bot_related_cards(message):
    upcs = resolve_help_bot_request(message, backend=get_help_bot_backend())["upcs"]
    if not upcs:
        return []
    return load_search_cards(upcs=upcs)

def post_json(url, payload, timeout=60):
    request_body = json.dumps(payload).encode("utf-8")
    request_obj = urllib.request.Request(
        url,
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request_obj, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def cosine_similarity(vector_a, vector_b):
    if not vector_a or not vector_b or len(vector_a) != len(vector_b):
        return 0.0

    dot_product = sum(a * b for a, b in zip(vector_a, vector_b))
    norm_a = math.sqrt(sum(a * a for a in vector_a))
    norm_b = math.sqrt(sum(b * b for b in vector_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)

class HelpBotBackendAdapter:
    mode = "local"

    def generate_response(self, message, history, resolution, related_cards):
        raise NotImplementedError

    def embed_texts(self, texts):
        return []

class OpenAIHelpBotBackend(HelpBotBackendAdapter):
    mode = "openai"

    def __init__(self, client):
        self.client = client

    def generate_response(self, message, history, resolution, related_cards):
        prompt = build_help_bot_prompt(message, history, resolution=resolution, related_cards=related_cards)
        response = self.client.responses.create(
            model=HELP_BOT_MODEL,
            instructions=build_help_bot_instructions(),
            tools=[{"type": "web_search"}],
            input=prompt,
        )
        parsed = parse_json_object_from_text(response.output_text)
        return {
            "reply": str((parsed or {}).get("reply") or "").strip() or resolution["reply"],
            "mentioned_upcs": normalize_help_bot_upcs((parsed or {}).get("mentioned_upcs"), resolution["upcs"]),
            "sources": extract_response_sources(response),
            "mode": self.mode,
        }

class OllamaHelpBotBackend(HelpBotBackendAdapter):
    mode = "ollama"

    def generate_response(self, message, history, resolution, related_cards):
        prompt = build_help_bot_prompt(message, history, resolution=resolution, related_cards=related_cards)
        response = post_json(
            f"{HELP_BOT_OLLAMA_BASE_URL}/api/chat",
            {
                "model": HELP_BOT_MODEL,
                "messages": [
                    {"role": "system", "content": build_help_bot_instructions()},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "format": {
                    "type": "object",
                    "properties": {
                        "reply": {"type": "string"},
                        "mentioned_upcs": {
                            "type": "array",
                            "items": {"type": "integer"},
                        },
                    },
                    "required": ["reply", "mentioned_upcs"],
                },
                "options": {
                    "temperature": 0,
                },
            },
        )
        content = (((response or {}).get("message") or {}).get("content") or "").strip()
        parsed = parse_json_object_from_text(content)
        return {
            "reply": str((parsed or {}).get("reply") or "").strip() or resolution["reply"],
            "mentioned_upcs": normalize_help_bot_upcs((parsed or {}).get("mentioned_upcs"), resolution["upcs"]),
            "sources": [],
            "mode": self.mode,
        }

    def embed_texts(self, texts):
        if not texts:
            return []
        response = post_json(
            f"{HELP_BOT_OLLAMA_BASE_URL}/api/embed",
            {
                "model": HELP_BOT_EMBED_MODEL,
                "input": texts,
                "truncate": True,
            },
        )
        embeddings = response.get("embeddings")
        return embeddings if isinstance(embeddings, list) else []

def get_openai_client():
    global _image_agent_client

    if _image_agent_client is not None:
        return _image_agent_client

    if not os.getenv("OPENAI_API_KEY"):
        return None

    try:
        openai_module = importlib.import_module("openai")
        _image_agent_client = openai_module.OpenAI()
    except ImportError:
        return None
    except Exception as exc:
        print("Could not initialize OpenAI client:", exc)
        _image_agent_client = None

    return _image_agent_client

def get_help_bot_backend():
    global _help_bot_backend

    if _help_bot_backend is not None:
        return _help_bot_backend

    if HELP_BOT_BACKEND == "openai":
        client = get_openai_client()
        if client is not None:
            _help_bot_backend = OpenAIHelpBotBackend(client)
            return _help_bot_backend
        print("OpenAI help backend requested but OPENAI_API_KEY is unavailable; falling back to local help resolution.")
        return None

    if HELP_BOT_BACKEND == "ollama":
        _help_bot_backend = OllamaHelpBotBackend()
        return _help_bot_backend

    print(f"Unknown HELP_BOT_BACKEND '{HELP_BOT_BACKEND}', falling back to local help resolution.")
    return None

def get_image_agent_client():
    return get_openai_client()

def read_annotation_field(value, field_name, default=None):
    if isinstance(value, dict):
        return value.get(field_name, default)
    return getattr(value, field_name, default)

def extract_response_sources(response):
    sources = []
    seen = set()

    for output_item in getattr(response, "output", []) or []:
        if read_annotation_field(output_item, "type") != "message":
            continue

        for content_item in read_annotation_field(output_item, "content", []) or []:
            annotations = read_annotation_field(content_item, "annotations", []) or []
            for annotation in annotations:
                if read_annotation_field(annotation, "type") != "url_citation":
                    continue

                url = read_annotation_field(annotation, "url")
                title = read_annotation_field(annotation, "title", url)
                if not url or url in seen:
                    continue

                seen.add(url)
                sources.append({
                    "title": title or url,
                    "url": url,
                })

    return sources

def build_help_bot_prompt(message, history, resolution=None, related_cards=None):
    inventory_rows = load_help_inventory_snapshot()
    inventory_context = "\n".join(
        f"- {item['Name']} | UPC {item['UPC']} | Qty {item['TotalQty']} | Locations: {item['Locations'] or 'Unknown'}"
        for item in inventory_rows
    )

    resolution = resolution or {}
    related_cards = related_cards or []

    resolved_context = "\n".join(
        f"- {item.get('Name', 'Unknown')} | UPC {item.get('UPC', 'Unknown')} | Qty {item.get('TotalQty', 'Unknown')} | Locations: {item.get('LocationDetails') or 'Unknown'}"
        for item in related_cards
    ) or "No resolved inventory matches."

    local_reply = resolution.get("reply", "").strip() or "No local inventory summary available."

    history_lines = []
    for turn in history[-8:]:
        role = turn.get("role", "user").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        history_lines.append(f"{role.title()}: {content}")

    history_text = "\n".join(history_lines) if history_lines else "No prior conversation."

    return f"""
Inventory data the bot is allowed to rely on for stock, item names, quantities, and locations:
{inventory_context}

Locally resolved inventory matches for this request. Keep any specific inventory items you mention consistent with this set so the chat reply matches the UI cards:
{resolved_context}

Local inventory summary for this request:
{local_reply}

Recent conversation:
{history_text}

    Current user message:
    {message}
""".strip()

def build_image_agent_instructions():
    return (
        "Find one good product image source for the item. "
        "Prefer official product/store pages, otherwise retailer pages. "
        "Avoid support/docs/manual/spec/PDF pages and avoid Wikimedia/forums/editorial photos. "
        "Return JSON only."
    )

def parse_json_object_from_text(text):
    if isinstance(text, (dict, list)):
        return text

    if text is None:
        return None

    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")

    text = str(text)
    text = text.strip()
    if not text:
        return None

    # Remove invisible control characters that can break JSON decoding.
    sanitized = "".join(
        ch if ch in "\t\n\r" or ord(ch) >= 0x20 else " "
        for ch in text
    ).strip()

    try:
        return json.loads(sanitized)
    except json.JSONDecodeError:
        pass

    # Extract the first balanced JSON object from the text.
    start = sanitized.find("{")
    if start != -1:
        depth = 0
        for idx in range(start, len(sanitized)):
            ch = sanitized[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    snippet = sanitized[start:idx + 1]
                    try:
                        return json.loads(snippet)
                    except json.JSONDecodeError:
                        break

    print(f"Failed to parse JSON from response: {repr(text)}")
    return None

def extract_urls_from_text(text):
    if text is None:
        return []

    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")

    text = str(text)
    urls = re.findall(r'https?://[^\s\)\]\}>"\']+', text, flags=re.I)
    unique_urls = []
    seen = set()
    for url in urls:
        cleaned = url.rstrip(".,;:!?")
        if cleaned in seen:
            continue
        seen.add(cleaned)
        unique_urls.append(cleaned)
    return unique_urls

def build_image_validation_instructions():
    return (
        "Decide if an image is a good product photo for the named item. "
        "Approve only if it matches the item or a very close product-family match and looks like a clean product listing photo. "
        "Return JSON only with keys approved, confidence, reason."
    )

def validate_item_image_via_ai(item_name, image_url, image_bytes=None, content_type=None):
    client = get_image_agent_client()
    if client is None or (not image_url and not image_bytes):
        return None

    image_input = image_url
    if image_bytes and content_type:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        image_input = f"data:{content_type.split(';')[0]};base64,{encoded}"

    response = client.responses.create(
        model=IMAGE_AGENT_MODEL,
        instructions=build_image_validation_instructions(),
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f'Is this a good product image for {json.dumps(item_name)}? '
                            "Check whether it actually looks like the item and whether it matches the desired clean retailer/manufacturer product-photo style."
                        ),
                    },
                    {
                        "type": "input_image",
                        "image_url": image_input,
                    },
                ],
            }
        ],
    )

    parsed = parse_json_object_from_text(response.output_text)
    if not parsed:
        print(f"Image validator returned non-JSON for {item_name}: {response.output_text}")
        return None

    return {
        "approved": bool(parsed.get("approved")),
        "confidence": parsed.get("confidence", ""),
        "reason": parsed.get("reason", ""),
    }

def is_live_image_url(image_url):
    if not image_url:
        return False, "Missing image URL"

    request = urllib.request.Request(
        image_url,
        headers={
            "User-Agent": "Mozilla/5.0 BME-Inventory-Image-Checker",
            "Accept": "image/*,*/*;q=0.8",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            content_type = response.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                return False, f"URL did not return an image content type ({content_type})"
            return True, content_type
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return False, f"URL error: {exc.reason}"
    except Exception as exc:
        return False, f"Unexpected error: {exc}"

def make_item_image_slug(item_name):
    slug = re.sub(r"[^a-z0-9]+", "-", (item_name or "").strip().lower()).strip("-")
    return slug or "item"

def download_image_bytes(image_url, source_url=""):
    if not image_url:
        return None, None

    headers = {
        "User-Agent": "Mozilla/5.0 BME-Inventory-Image-Downloader",
        "Accept": "image/*,*/*;q=0.8",
    }
    if source_url:
        headers["Referer"] = source_url

    request = urllib.request.Request(image_url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        content_type = response.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            return None, None
        return response.read(), content_type

def cache_item_image_locally(item_name, image_url, source_url=""):
    try:
        image_bytes, content_type = download_image_bytes(image_url, source_url)
    except Exception as exc:
        print(f"Could not download image for {item_name}: {exc}")
        return image_url

    if not image_bytes or not content_type:
        return image_url

    os.makedirs(ITEM_IMAGE_CACHE_DIR, exist_ok=True)
    slug = make_item_image_slug(item_name)
    digest = hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:10]
    extension = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".jpg"
    if extension == ".jpe":
        extension = ".jpg"
    filename = f"{slug}-{digest}{extension}"
    file_path = os.path.join(ITEM_IMAGE_CACHE_DIR, filename)

    try:
        with open(file_path, "wb") as image_file:
            image_file.write(image_bytes)
    except OSError as exc:
        print(f"Could not cache image for {item_name}: {exc}")
        return image_url

    return f"/static/item-images/{filename}"

def extract_image_candidates_from_page(page_url):
    if not page_url:
        return []

    parsed = urlparse(page_url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    if (
        host.startswith("support.")
        or host.startswith("docs.")
        or host.startswith("help.")
        or path.endswith(".pdf")
        or any(part in path for part in ("/support", "/docs", "/manual", "/manuals", "/spec", "/specs", "/datasheet"))
    ):
        return []

    request = urllib.request.Request(
        page_url,
        headers={
            "User-Agent": "Mozilla/5.0 BME-Inventory-Image-Extractor",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type:
                return []
            html = response.read().decode("utf-8", errors="ignore")
    except socket.timeout:
        return []
    except ssl.SSLCertVerificationError:
        return []
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 404, 429}:
            return []
        print(f"Could not inspect image source page {page_url}: HTTP {exc.code}")
        return []
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", "") or "")
        if "CERTIFICATE_VERIFY_FAILED" in reason or "self-signed certificate" in reason:
            return []
        print(f"Could not inspect image source page {page_url}: {exc}")
        return []
    except Exception as exc:
        print(f"Could not inspect image source page {page_url}: {exc}")
        return []

    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'https?://[^"\'\s>]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\'\s>]*)?',
    ]

    candidates = []
    seen = set()
    for pattern in patterns:
        for match in re.findall(pattern, html, flags=re.I):
            candidate = unescape(match if isinstance(match, str) else match[0]).strip()
            if not candidate.startswith("http"):
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)

    return candidates

def build_item_search_tokens(item_name):
    cleaned = re.sub(r"[^a-z0-9]+", " ", (item_name or "").lower())
    return [token for token in cleaned.split() if len(token) >= 2]

def score_image_candidate(item_name, candidate_url):
    url = (candidate_url or "").lower()
    tokens = build_item_search_tokens(item_name)
    score = 0

    for token in tokens:
        if token in url:
            score += 4

    for marker in ("product", "front", "main", "hero", "angle", "printer", "device"):
        if marker in url:
            score += 1

    for marker in (
        "favicon", "icon", "logo", "banner", "manual", "spec", "datasheet",
        "thumb", "preview", "nav-menu", "compare", "group_", "group-", "cropped", "pdf"
    ):
        if marker in url:
            score -= 5

    return score

def rank_image_candidates(item_name, candidates):
    unique_candidates = []
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        unique_candidates.append(candidate)

    return sorted(
        unique_candidates,
        key=lambda candidate: score_image_candidate(item_name, candidate),
        reverse=True,
    )

def try_image_candidates(item_name, source_url, candidates):
    validations_used = 0
    for candidate_url in rank_image_candidates(item_name, candidates):
        image_bytes = None
        content_type = None
        live_ok, _ = is_live_image_url(candidate_url)
        if not live_ok:
            try:
                image_bytes, content_type = download_image_bytes(candidate_url, source_url)
                live_ok = bool(image_bytes and content_type and content_type.startswith("image/"))
            except Exception:
                live_ok = False
            if not live_ok:
                continue

        if validations_used >= IMAGE_AGENT_MAX_VALIDATIONS:
            break

        validation = validate_item_image_via_ai(
            item_name,
            candidate_url,
            image_bytes=image_bytes,
            content_type=content_type,
        )
        validations_used += 1
        if validation and validation.get("approved"):
            return {
                "image_url": candidate_url,
                "source_url": source_url or "",
                "note": "Resolved from source page image candidates.",
                "validation": validation,
            }

    return None

def get_url_host(url):
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""

def is_unusable_source_page(url):
    if not url:
        return True

    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()

    if not host:
        return True

    blocked_host_parts = (
        "bing.com", "google.com", "duckduckgo.com", "yahoo.com", "youtube.com",
        "facebook.com", "instagram.com", "pinterest.com", "reddit.com"
    )
    if any(part in host for part in blocked_host_parts):
        return True

    if (
        host.startswith("support.")
        or host.startswith("docs.")
        or host.startswith("help.")
        or path.endswith(".pdf")
        or any(part in path for part in ("/support", "/docs", "/manual", "/manuals", "/spec", "/specs", "/datasheet"))
    ):
        return True

    return False

def score_source_page(item_name, url, retailer_only=False):
    url_text = (url or "").lower()
    score = 0

    for token in build_item_search_tokens(item_name):
        if token in url_text:
            score += 3

    preferred_markers = ("product", "products", "shop", "store", "buy", "item", "printer")
    for marker in preferred_markers:
        if marker in url_text:
            score += 1

    if retailer_only and any(marker in url_text for marker in ("shop", "store", "buy", "walmart", "staples", "office", "bestbuy", "target", "bhphotovideo", "adorama")):
        score += 2

    bad_markers = ("support", "manual", "datasheet", "spec", "pdf", "forum", "community", "article", "blog")
    for marker in bad_markers:
        if marker in url_text:
            score -= 5

    return score

def search_product_pages(item_name, retailer_only=False, excluded_hosts=None, limit=8):
    excluded_hosts = {host for host in (excluded_hosts or []) if host}
    queries = [
        f'"{item_name}" product',
        f'"{item_name}" {"retailer" if retailer_only else "official"}',
    ]
    if retailer_only:
        queries.append(f'"{item_name}" buy')
        queries.extend([
            f'site:amazon.com "{item_name}"',
            f'site:walmart.com "{item_name}"',
            f'site:target.com "{item_name}"',
            f'site:staples.com "{item_name}"',
            f'site:officedepot.com "{item_name}"',
            f'site:bestbuy.com "{item_name}"',
            f'site:bhphotovideo.com "{item_name}"',
            f'site:adorama.com "{item_name}"',
            f'site:cvs.com "{item_name}"',
            f'site:walgreens.com "{item_name}"',
        ])
    else:
        queries.extend([
            f'"{item_name}" store',
            f'"{item_name}" shop',
        ])

    found_urls = []
    seen = set()

    for query in queries:
        search_url = f"https://www.bing.com/search?q={quote_plus(query)}"
        request = urllib.request.Request(
            search_url,
            headers={
                "User-Agent": "Mozilla/5.0 BME-Inventory-Page-Search",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                html = response.read().decode("utf-8", errors="ignore")
        except Exception:
            continue

        candidates = re.findall(r'href=["\'](https?://[^"\']+)["\']', html, flags=re.I)
        for candidate in candidates:
            host = get_url_host(candidate)
            if candidate in seen or host in excluded_hosts or is_unusable_source_page(candidate):
                continue
            seen.add(candidate)
            found_urls.append(candidate)

    ranked = sorted(
        found_urls,
        key=lambda candidate: score_source_page(item_name, candidate, retailer_only=retailer_only),
        reverse=True,
    )
    return ranked[:limit]

def search_image_candidates_from_web(item_name, limit=12):
    queries = [
        f'"{item_name}" product',
        f'"{item_name}"',
    ]

    found_urls = []
    seen = set()

    for query in queries:
        search_url = f"https://www.bing.com/images/search?q={quote_plus(query)}"
        request = urllib.request.Request(
            search_url,
            headers={
                "User-Agent": "Mozilla/5.0 BME-Inventory-Image-Search",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                html = response.read().decode("utf-8", errors="ignore")
        except Exception:
            continue

        patterns = [
            r'"murl":"(https?://[^"]+)"',
            r'murl&quot;:&quot;(https?://[^"&]+)',
            r'https?://[^"\'>\s]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\'>\s]*)?',
        ]

        for pattern in patterns:
            for candidate in re.findall(pattern, html, flags=re.I):
                candidate = unescape(candidate).replace("\\/", "/").strip()
                if not candidate.startswith("http"):
                    continue
                if candidate in seen:
                    continue
                seen.add(candidate)
                found_urls.append(candidate)

    ranked = rank_image_candidates(item_name, found_urls)
    return ranked[:limit]

def find_product_page_via_ai(item_name, retailer_only=False, feedback="", excluded_hosts=None):
    client = get_image_agent_client()
    if client is None:
        return None

    excluded_hosts = [host for host in (excluded_hosts or []) if host]
    excluded_clause = ""
    if excluded_hosts:
        excluded_clause = "Avoid these blocked or unreliable hosts: " + ", ".join(sorted(set(excluded_hosts))) + ". "

    retailer_clause = (
        "Do not use the manufacturer marketing site. Prefer a retailer or official storefront product page with accessible product images. "
        if retailer_only else
        "Prefer an official product page, but if those images are blocked or unreliable, prefer a retailer or official storefront product page with accessible product images. "
    )

    prompt = (
        f'Find the best product page URL for the inventory item {json.dumps(item_name)}. '
        f"{retailer_clause}"
        f"{excluded_clause}"
        "Return JSON only with keys source_url and note. "
        f"{feedback}"
    )

    response = client.responses.create(
        model=IMAGE_AGENT_MODEL,
        instructions=(
            "Return one likely product/store page for the item. "
            "Prefer pages with accessible product images. "
            "Avoid support/manual/spec/PDF pages. "
            "Return JSON only with keys source_url and note."
        ),
        tools=[{"type": "web_search"}],
        input=prompt,
    )

    parsed = parse_json_object_from_text(response.output_text)
    if not parsed:
        fallback_urls = extract_urls_from_text(response.output_text)
        for candidate in fallback_urls:
            if candidate.startswith("http") and not is_unusable_source_page(candidate):
                return {
                    "source_url": candidate,
                    "note": "Recovered from non-JSON AI response.",
                }
        return None

    return {
        "source_url": parsed.get("source_url", ""),
        "note": parsed.get("note", ""),
    }

def find_direct_image_via_ai(item_name, feedback="", excluded_hosts=None):
    client = get_image_agent_client()
    if client is None:
        return None

    excluded_hosts = [host for host in (excluded_hosts or []) if host]
    excluded_clause = ""
    if excluded_hosts:
        excluded_clause = "Avoid these blocked or unreliable hosts: " + ", ".join(sorted(set(excluded_hosts))) + ". "

    prompt = (
        f'Find one direct product image URL for the inventory item {json.dumps(item_name)}. '
        "Prefer retailer or official storefront product images on a plain or simple background. "
        "Return a direct image file URL only, not a product page URL. "
        "Avoid support/docs/manual/spec/PDF pages and avoid Wikimedia/forums/editorial photos. "
        f"{excluded_clause}"
        "Return JSON only with keys image_url and note. "
        f"{feedback}"
    )

    response = client.responses.create(
        model=IMAGE_AGENT_MODEL,
        instructions=(
            "Return one likely direct product image URL for the item. "
            "Return JSON only with keys image_url and note."
        ),
        tools=[{"type": "web_search"}],
        input=prompt,
    )

    parsed = parse_json_object_from_text(response.output_text)
    if not parsed:
        fallback_urls = extract_urls_from_text(response.output_text)
        for candidate in fallback_urls:
            if not candidate.startswith("http"):
                continue
            if re.search(r'\.(?:jpg|jpeg|png|webp)(?:\?|$)', candidate, flags=re.I):
                return {
                    "image_url": candidate,
                    "note": "Recovered direct image URL from non-JSON AI response.",
                }
            if not is_unusable_source_page(candidate):
                return {
                    "source_url": candidate,
                    "note": "Recovered product page URL from non-JSON AI response.",
                }
        return None

    image_url = (parsed.get("image_url") or "").strip()
    if image_url.startswith("http"):
        return {
            "image_url": image_url,
            "note": parsed.get("note", ""),
        }

    source_url = (parsed.get("source_url") or "").strip()
    if source_url.startswith("http") and not is_unusable_source_page(source_url):
        return {
            "source_url": source_url,
            "note": parsed.get("note", ""),
        }

    return None

def find_item_image_via_ai(item_name):
    rejected_hosts = set()
    code_search_found_any_sources = False

    web_image_candidates = search_image_candidates_from_web(
        item_name,
        limit=max(IMAGE_AGENT_MAX_VALIDATIONS * 3, 8),
    )
    if web_image_candidates:
        resolved = try_image_candidates(item_name, "", web_image_candidates)
        if resolved:
            resolved["note"] = resolved.get("note", "") or "Resolved from web image search."
            return resolved

    for retailer_only in (False, True):
        source_urls = search_product_pages(
            item_name,
            retailer_only=retailer_only,
            excluded_hosts=rejected_hosts,
            limit=max(IMAGE_AGENT_PAGE_ATTEMPTS * 3, 6),
        )
        if source_urls:
            code_search_found_any_sources = True

        for source_url in source_urls:
            source_candidates = extract_image_candidates_from_page(source_url)
            if not source_candidates:
                rejected_hosts.add(get_url_host(source_url))
                continue

            resolved = try_image_candidates(item_name, source_url, source_candidates)
            if resolved:
                resolved["note"] = resolved.get("note", "") or "Resolved from product page search."
                return resolved

            rejected_hosts.add(get_url_host(source_url))

        if not retailer_only:
            retailer_source_urls = search_product_pages(
                item_name,
                retailer_only=True,
                excluded_hosts=rejected_hosts,
                limit=max(IMAGE_AGENT_PAGE_ATTEMPTS * 4, 10),
            )
            if retailer_source_urls:
                code_search_found_any_sources = True

            for source_url in retailer_source_urls:
                source_candidates = extract_image_candidates_from_page(source_url)
                if not source_candidates:
                    rejected_hosts.add(get_url_host(source_url))
                    continue

                resolved = try_image_candidates(item_name, source_url, source_candidates)
                if resolved:
                    resolved["note"] = resolved.get("note", "") or "Resolved from retailer product page search."
                    return resolved

                rejected_hosts.add(get_url_host(source_url))

        should_use_ai_fallback = IMAGE_AGENT_AI_SEARCH_FALLBACK or not code_search_found_any_sources
        if should_use_ai_fallback:
            feedback = ""
            fallback_attempts = 1 if not code_search_found_any_sources else IMAGE_AGENT_PAGE_ATTEMPTS
            for _ in range(fallback_attempts):
                page_result = find_product_page_via_ai(
                    item_name,
                    retailer_only=retailer_only,
                    feedback=feedback,
                    excluded_hosts=rejected_hosts,
                )
                source_url = (page_result or {}).get("source_url", "")
                if not source_url:
                    feedback = "Return a single product or storefront page URL."
                    continue

                source_candidates = extract_image_candidates_from_page(source_url)
                if not source_candidates:
                    direct_result = find_direct_image_via_ai(
                        item_name,
                        feedback=(
                            f"The product page {source_url} did not expose usable image candidates. "
                            "Return a direct image file URL from a retailer or official storefront instead."
                        ),
                        excluded_hosts=rejected_hosts,
                    )
                    if direct_result:
                        if direct_result.get("image_url"):
                            resolved = try_image_candidates(
                                item_name,
                                source_url,
                                [direct_result["image_url"]],
                            )
                            if resolved:
                                resolved["note"] = direct_result.get("note", "") or "Resolved from direct image search."
                                return resolved
                        elif direct_result.get("source_url"):
                            direct_candidates = extract_image_candidates_from_page(direct_result["source_url"])
                            if direct_candidates:
                                resolved = try_image_candidates(
                                    item_name,
                                    direct_result["source_url"],
                                    direct_candidates,
                                )
                                if resolved:
                                    resolved["note"] = direct_result.get("note", "") or "Resolved from recovered product page search."
                                    return resolved

                    rejected_hosts.add(get_url_host(source_url))
                    feedback = (
                        "That source did not expose usable image candidates. "
                        "Return a different retailer or official storefront product page."
                    )
                    continue

                resolved = try_image_candidates(item_name, source_url, source_candidates)
                if resolved:
                    resolved["note"] = (
                        page_result.get("note", "") if page_result else resolved.get("note", "")
                    ) or "Resolved from product page fallback."
                    return resolved

                rejected_hosts.add(get_url_host(source_url))
                feedback = (
                    "That source produced unusable or incorrect images. "
                    "Return a different retailer or official storefront product page."
                )

    return None

def store_item_image(item_name, image_url):
    with _item_images_lock:
        image_map = load_item_image_map()
        image_map[item_name] = image_url
        save_item_image_map(image_map)

def store_item_image_result(item_name, result, trigger="manual"):
    if not item_name or not result or not result.get("image_url"):
        return

    image_url = cache_item_image_locally(
        item_name,
        result["image_url"],
        result.get("source_url", ""),
    )
    with _item_images_lock:
        image_map = load_item_image_map()
        image_map[item_name] = image_url
        save_item_image_map(image_map)

        metadata_map = load_item_image_metadata()
        metadata_map[item_name] = {
            "image_url": image_url,
            "source_url": result.get("source_url", ""),
            "note": result.get("note", ""),
            "validation": result.get("validation", {}),
            "trigger": trigger,
            "status": IMAGE_STATUS_SUCCESS,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        save_item_image_metadata(metadata_map)

def localize_existing_item_images():
    with _item_images_lock:
        image_map = load_item_image_map()
        metadata_map = load_item_image_metadata()

    updated_map = dict(image_map)
    updated_metadata = dict(metadata_map)

    for item_name, image_url in image_map.items():
        if isinstance(image_url, str) and image_url.startswith("/static/item-images/"):
            continue

        source_url = metadata_map.get(item_name, {}).get("source_url", "")
        local_url = cache_item_image_locally(item_name, image_url, source_url)

        if local_url == image_url and image_url.startswith("http"):
            try:
                refreshed = find_item_image_via_ai(item_name)
            except Exception as exc:
                print(f"Could not refresh image while localizing {item_name}: {exc}")
                refreshed = None

            if refreshed and refreshed.get("image_url"):
                local_url = cache_item_image_locally(
                    item_name,
                    refreshed["image_url"],
                    refreshed.get("source_url", ""),
                )
                updated_metadata[item_name] = {
                    "image_url": local_url,
                    "source_url": refreshed.get("source_url", ""),
                    "note": refreshed.get("note", ""),
                    "validation": refreshed.get("validation", {}),
                    "trigger": "localize_existing_images_refresh",
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }

        updated_map[item_name] = local_url
        existing_meta = updated_metadata.get(item_name, {})
        updated_metadata[item_name] = {
            "image_url": local_url,
            "source_url": existing_meta.get("source_url", source_url),
            "note": existing_meta.get("note", ""),
            "validation": existing_meta.get("validation", {}),
            "trigger": existing_meta.get("trigger", "localize_existing_images"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    with _item_images_lock:
        save_item_image_map(updated_map)
        save_item_image_metadata(updated_metadata)

    return updated_map

def ensure_item_image(item_name, trigger="new_item_create"):
    item_name = (item_name or "").strip()
    if not item_name:
        return False

    with _item_images_lock:
        current_map = load_item_image_map()
        if item_name in current_map:
            return True

    try:
        result = find_item_image_via_ai(item_name)
        if result and result.get("image_url"):
            store_item_image_result(item_name, result, trigger=trigger)
            return True
    except Exception as exc:
        print(f"Immediate image lookup failed for {item_name}: {exc}")

    queue_item_image_lookup(item_name, trigger=f"{trigger}_fallback")
    return False

def queue_item_image_lookup(item_name, trigger="new_item_create"):
    item_name = (item_name or "").strip()
    if not item_name:
        return

    with _item_images_lock:
        current_map = load_item_image_map()
        if item_name in current_map:
            return

    set_item_image_status(
        item_name,
        IMAGE_STATUS_PENDING,
        trigger=trigger,
        note="Searching for a product image.",
    )

    def worker():
        try:
            had_openai_client = get_image_agent_client() is not None
            result = find_item_image_via_ai(item_name)
            if result and result.get("image_url"):
                store_item_image_result(item_name, result, trigger=trigger)
            else:
                failure_note = "No valid image source was found."
                if not had_openai_client:
                    failure_note = "OpenAI image agent unavailable in the current runtime."
                set_item_image_status(
                    item_name,
                    IMAGE_STATUS_FAILED,
                    trigger=trigger,
                    note=failure_note,
                )
        except Exception as exc:
            print(f"Image lookup failed for {item_name}: {exc}")
            set_item_image_status(
                item_name,
                IMAGE_STATUS_FAILED,
                trigger=trigger,
                note=str(exc),
            )

    threading.Thread(target=worker, daemon=True).start()

def refresh_current_item_images():
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT Name FROM items ORDER BY Name")
        item_names = [row["Name"] for row in cursor.fetchall()]
    finally:
        db.close()

    refreshed = {}
    for item_name in item_names:
        try:
            result = find_item_image_via_ai(item_name)
            if result and result.get("image_url"):
                refreshed[item_name] = result["image_url"]
        except Exception as exc:
            print(f"Image refresh failed for {item_name}: {exc}")

    if refreshed:
        metadata_map = load_item_image_metadata()
        with _item_images_lock:
            image_map = load_item_image_map()
            image_map.update(refreshed)
            save_item_image_map(image_map)

            for item_name, image_url in refreshed.items():
                existing_meta = metadata_map.get(item_name, {})
                metadata_map[item_name] = {
                    "image_url": image_url,
                    "source_url": existing_meta.get("source_url", ""),
                    "note": existing_meta.get("note", ""),
                    "validation": existing_meta.get("validation", {}),
                    "trigger": "refresh_current_items",
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            save_item_image_metadata(metadata_map)

    return refreshed

def revalidate_existing_item_images():
    with _item_images_lock:
        current_map = load_item_image_map()

    approved = {}
    for item_name, image_url in current_map.items():
        try:
            live_ok, _ = is_live_image_url(image_url)
            if not live_ok:
                continue

            validation = validate_item_image_via_ai(item_name, image_url)
            if validation and validation.get("approved"):
                approved[item_name] = image_url
        except Exception as exc:
            print(f"Image revalidation failed for {item_name}: {exc}")

    if approved:
        with _item_images_lock:
            save_item_image_map(approved)

            metadata_map = load_item_image_metadata()
            approved_metadata = {}
            for item_name, image_url in approved.items():
                existing_meta = metadata_map.get(item_name, {})
                approved_metadata[item_name] = {
                    "image_url": image_url,
                    "source_url": existing_meta.get("source_url", ""),
                    "note": existing_meta.get("note", ""),
                    "validation": existing_meta.get("validation", {}),
                    "trigger": existing_meta.get("trigger", "revalidated_existing_images"),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            save_item_image_metadata(approved_metadata)

    return approved

def build_help_bot_instructions():
    return (
        "You are BMEnventory Help, a concise support chatbot for a lab inventory website. "
        "You can use web search for general educational guidance, such as explaining what equipment is typically needed for a task. "
        "You must treat the provided inventory data as the only source of truth for what this lab actually has, where it is located, and how many are logged. "
        "Never invent inventory items, quantities, or locations that are not present in the provided data. "
        "When the prompt includes a locally resolved inventory match set, keep any specific inventory item names you mention consistent with that set so the response stays aligned with the UI results. "
        "If the user asks what they need for a task, explain the typical needs briefly and then map those needs to the lab items that actually fit, if any. "
        "If the user asks generally about a type of machine or workflow, infer the likely category and return the matching inventory items even when the exact item names were not mentioned. "
        "Examples of this behavior include mapping PCB making or PCB machines to relevant PCB equipment in the inventory, and mapping 3D printing to the printers and supporting equipment in the inventory. "
        "If the inventory does not contain a needed item, say that clearly. "
        "When citing web-derived guidance, keep the answer concise and factual. "
        "Do not claim to change data, edit data, reserve equipment, or perform actions. "
        "Return JSON only with two keys: reply and mentioned_upcs. "
        "reply must be a user-facing string. "
        "mentioned_upcs must be an array of numeric UPCs for the specific inventory items you actually mention in reply. "
        "If you do not mention any inventory items, return an empty mentioned_upcs array."
    )

def normalize_help_bot_upcs(raw_upcs, allowed_upcs):
    allowed = set(allowed_upcs or [])
    normalized = []
    seen = set()

    for raw_upc in raw_upcs or []:
        try:
            upc = int(raw_upc)
        except (TypeError, ValueError):
            continue

        if upc in seen:
            continue

        if allowed and upc not in allowed:
            continue

        seen.add(upc)
        normalized.append(upc)

    return normalized

def generate_help_bot_response(message, history):
    backend = get_help_bot_backend()
    resolution = resolve_help_bot_request(message, backend=backend)
    resolved_cards = load_search_cards(upcs=resolution["upcs"]) if resolution["upcs"] else []

    if backend is None:
        return {
            "reply": resolution["reply"],
            "sources": [],
            "mode": "local-fallback",
            "items": resolved_cards,
        }

    try:
        backend_result = backend.generate_response(message, history, resolution, resolved_cards)
    except Exception as exc:
        print(f"Help bot {getattr(backend, 'mode', 'backend')} call failed, using local fallback:", exc)
        return {
            "reply": resolution["reply"],
            "sources": [],
            "mode": "local-fallback",
            "items": resolved_cards,
        }
    reply_text = backend_result.get("reply") or resolution["reply"]
    mentioned_upcs = normalize_help_bot_upcs(backend_result.get("mentioned_upcs"), resolution["upcs"])

    display_cards = load_search_cards(upcs=mentioned_upcs) if mentioned_upcs else resolved_cards

    return {
        "reply": reply_text,
        "sources": backend_result.get("sources", []),
        "mode": backend_result.get("mode", "local"),
        "items": display_cards,
    }

def get_default_helpbot_history():
    return [
        {
            "role": "assistant",
            "content": "I can help locate items, summarize quantities, tell you what is in a room, and suggest which inventory items fit a task like 3D printing."
        }
    ]

def get_helpbot_history():
    history = session.get("helpbot_history")
    if isinstance(history, list) and history:
        return history
    return get_default_helpbot_history()

@app.context_processor
def inject_editor_auth():
    return {
        "signed_in": is_signed_in(),
        "signed_in_email": session.get("signed_in_email", ""),
        "editor_authenticated": session.get("editor_authenticated", False),
        "database_authenticated": session.get("database_authenticated", False),
    }

@app.route('/sign-in', methods=['GET'])
def sign_in_page():
    if is_signed_in():
        return redirect(url_for('home'))
    return render_template('sign_in.html')

@app.route('/sign-in', methods=['POST'])
def sign_in():
    data = request.get_json(silent=True) or request.form or {}
    email = (data.get('email') or '').strip()
    normalized_email = email.lower()

    if not normalized_email or not normalized_email.endswith('@uri.edu'):
        return jsonify({"success": False, "error": "Enter a valid @uri.edu email address."}), 400

    log_user_sign_out(session.get("tracked_session_id"))
    session.clear()
    session["signed_in_email"] = normalized_email
    session["helpbot_history"] = get_default_helpbot_history()
    session["floorplan_game"] = get_persistent_floorplan_game_state(normalized_email)
    tracked_session_id = log_user_sign_in(normalized_email)
    if tracked_session_id:
        session["tracked_session_id"] = tracked_session_id
    return jsonify({"success": True, "redirect": url_for('home')}), 200

@app.route('/sign-out', methods=['POST'])
def sign_out():
    log_user_sign_out(session.get("tracked_session_id"))
    session.clear()
    return redirect(url_for('sign_in_page'))

@app.route('/', methods=['GET', 'POST'])
def home():
    recently_changed_items = load_recently_changed_items()
    return render_template('search.html', recently_changed_items=recently_changed_items, helpbot_history=get_helpbot_history())

@app.route('/edit', methods=['GET', 'POST'])
def edit():
    if not session.get("editor_authenticated"):
        return redirect(url_for('home'))
    session.pop("editor_authenticated", None)

    return render_template('edit.html')

@app.route('/editor-auth', methods=['POST'])
def editor_auth():
    data = request.get_json(silent=True) or {}
    password = data.get('password', '')
    if password == EDITOR_PASSWORD:
        session["editor_authenticated"] = True
        return jsonify({"success": True, "redirect": url_for('edit')}), 200
    return jsonify({"success": False, "error": "Incorrect password"}), 401

@app.route('/db', methods=['GET', 'POST'])
def db():
    if not session.get("database_authenticated"):
        return redirect(url_for('home'))
    entries = load_database_entries()
    bin_rows = load_bins_directory()
    user_tracking = load_user_tracking()
    return render_template('db.html', entries=entries, bins=bin_rows, user_tracking=user_tracking)

@app.route('/database-auth', methods=['POST'])
def database_auth():
    data = request.get_json(silent=True) or {}
    password = data.get('password', '')
    if password == EDITOR_PASSWORD:
        session["database_authenticated"] = True
        return jsonify({"success": True, "redirect": url_for('db')}), 200
    return jsonify({"success": False, "error": "Incorrect password"}), 401

@app.route('/floorplan', methods=['GET'])
def floorplan():
    game_state, candidates = ensure_floorplan_game_state()
    current_item = get_floorplan_target_candidate(candidates, game_state.get("target_upc"))
    return render_template('floorplan.html', game_state=game_state, current_item=current_item)

@app.route('/floorplan-guess', methods=['POST'])
def floorplan_guess():
    data = request.get_json(silent=True) or {}
    guessed_room = (data.get('room') or '').strip().upper()
    if guessed_room not in FLOORPLAN_GAME_ROOMS:
        return jsonify({"success": False, "error": "Select a valid room."}), 400

    game_state, candidates = ensure_floorplan_game_state()
    current_item = get_floorplan_target_candidate(candidates, game_state.get("target_upc"))
    if not current_item:
        return jsonify({"success": False, "error": "No floorplan game items are available yet."}), 400

    game_state["attempts"] = int(game_state.get("attempts", 0)) + 1
    game_state["last_guess"] = guessed_room
    is_correct = guessed_room in current_item["rooms"]

    if is_correct:
        game_state["score"] = int(game_state.get("score", 0)) + 1
        solved_item_name = current_item["name"]
        solved_item_rooms = current_item["rooms"]
        game_state["feedback"] = f"Correct. {solved_item_name} is stored in room {', '.join(solved_item_rooms)}."
        assign_floorplan_target(game_state, candidates, exclude_upc=current_item["upc"])
        current_item = get_floorplan_target_candidate(candidates, game_state.get("target_upc"))
    else:
        game_state["feedback"] = f"Not there. {current_item['name']} is not stored in room {guessed_room}. Try again."

    session["floorplan_game"] = game_state
    save_floorplan_stats(
        session.get("signed_in_email"),
        game_state.get("score", 0),
        game_state.get("attempts", 0),
    )
    return jsonify({
        "success": True,
        "correct": is_correct,
        "score": int(game_state.get("score", 0)),
        "attempts": int(game_state.get("attempts", 0)),
        "feedback": game_state.get("feedback", ""),
        "current_item": current_item["name"] if current_item else "No item available",
    })

@app.route('/search', methods=['GET', 'POST'])
def search():
    # Get the JSON passed by javascript
    data = request.get_json(silent=True) or {}
    search_query = data.get('search_query', '')

    # Initialize cursor to search database
    results = load_search_cards(search_query=search_query)
    return jsonify(results)

@app.route('/help-chat', methods=['POST'])
def help_chat():
    data = request.get_json(silent=True) or {}
    message = data.get('message', '')
    history = get_helpbot_history()
    result = generate_help_bot_response(message, history)
    updated_history = list(history)
    if message:
        updated_history.append({"role": "user", "content": message})
    updated_history.append({"role": "assistant", "content": result.get("reply", "")})
    session["helpbot_history"] = updated_history[-20:]
    result["history"] = session["helpbot_history"]
    return jsonify(result)

@app.route('/track-item-access', methods=['POST'])
def track_item_access():
    if not is_signed_in():
        return jsonify({"error": "Sign-in required", "redirect": url_for("sign_in_page")}), 401

    data = request.get_json(silent=True) or {}
    success = log_item_access(
        session.get("tracked_session_id"),
        session.get("signed_in_email"),
        data.get("upc"),
        data.get("item_name"),
    )
    if not success:
        return jsonify({"error": "Could not track item access"}), 400

    return jsonify({"success": True}), 200

@app.route('/update-item', methods=['POST'])
def update_item():
    data = request.get_json(silent=True) or {}
    original_upc = data.get('original_upc')
    new_upc = data.get('upc')
    new_name = data.get('name', '').strip()
    new_total_qty = data.get('total_qty')

    if not original_upc or not new_upc or not new_name or new_total_qty in (None, ''):
        return jsonify({"error": "Missing required fields"}), 400

    try:
        original_upc = int(original_upc)
        new_upc = int(new_upc)
        new_total_qty = int(new_total_qty)
    except (TypeError, ValueError):
        return jsonify({"error": "UPC and quantity must be numbers"}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT UPC FROM items WHERE UPC = ?", (original_upc,))
        existing_item = cursor.fetchone()
        if not existing_item:
            return jsonify({"error": "Item not found"}), 404

        if new_upc != original_upc:
            cursor.execute("SELECT UPC FROM items WHERE UPC = ?", (new_upc,))
            upc_conflict = cursor.fetchone()
            if upc_conflict:
                return jsonify({"error": "That UPC is already in use"}), 409

        cursor.execute(
            "UPDATE items SET UPC = ?, Name = ?, TotalQty = ? WHERE UPC = ?",
            (new_upc, new_name, new_total_qty, original_upc)
        )
        cursor.execute(
            "UPDATE item_bin SET UPC = ?, Name = ? WHERE UPC = ?",
            (new_upc, new_name, original_upc)
        )
        db.commit()
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while updating item:", e.args[0])
        return jsonify({"error": "Failed to update item"}), 500
    finally:
        db.close()

    return jsonify({"success": True}), 200

@app.route('/update-entry', methods=['POST'])
def update_entry():
    data = request.get_json(silent=True) or {}
    entry_id = data.get('entry_id')
    name = data.get('name', '').strip()
    qty = data.get('qty')
    room = data.get('room', '').strip()
    wall = data.get('wall', '').strip()
    storage_type = data.get('storage_type', '').strip()
    bin_number = data.get('bin_number')

    if not entry_id or not name or qty in (None, '') or not room or not wall or not storage_type or not bin_number:
        return jsonify({"error": "Missing required fields"}), 400

    try:
        entry_id = int(entry_id)
        qty = int(qty)
    except (TypeError, ValueError):
        return jsonify({"error": "Entry ID and quantity must be numbers"}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT UPC, Qty FROM item_bin WHERE EntryID = ?", (entry_id,))
        existing = cursor.fetchone()
        if not existing:
            return jsonify({"error": "Entry not found"}), 404

        upc = existing["UPC"]
        old_qty = existing["Qty"]

        wall_id = functions.wallDecider(wall, room)
        cursor.execute(
            "SELECT BinUPC FROM bins WHERE BinType = ? AND BinID = ? AND WallID = ?",
            (storage_type, bin_number, wall_id)
        )
        bin_row = cursor.fetchone()

        if bin_row:
            bin_upc = bin_row["BinUPC"]
        else:
            try:
                desired_bin = int(bin_number)
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid bin number"}), 400

            if desired_bin != 1:
                return jsonify({"error": "That bin does not exist. Create bins sequentially."}), 400

            _, bin_upc = functions.createBin(storage_type, wall, room)

        now = datetime.now()
        current_date = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M:%S")

        cursor.execute(
            "UPDATE item_bin SET Name = ?, Qty = ?, BinUPC = ?, Date = ?, Time = ? WHERE EntryID = ?",
            (name, qty, bin_upc, current_date, current_time, entry_id)
        )

        qty_diff = qty - old_qty
        cursor.execute("UPDATE items SET Name = ?, TotalQty = TotalQty + ? WHERE UPC = ?", (name, qty_diff, upc))
        db.commit()
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while updating entry:", e.args[0])
        return jsonify({"error": "Failed to update entry"}), 500
    finally:
        db.close()

    return jsonify({"success": True}), 200

@app.route('/delete-entry', methods=['POST'])
def delete_entry():
    data = request.get_json(silent=True) or {}
    entry_id = data.get('entry_id')

    if not entry_id:
        return jsonify({"error": "Missing entry ID"}), 400

    try:
        entry_id = int(entry_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Entry ID must be a number"}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT UPC, Qty FROM item_bin WHERE EntryID = ?", (entry_id,))
        existing = cursor.fetchone()
        if not existing:
            return jsonify({"error": "Entry not found"}), 404

        upc = existing["UPC"]
        qty = existing["Qty"] or 0

        cursor.execute("DELETE FROM item_bin WHERE EntryID = ?", (entry_id,))
        cursor.execute(
            "UPDATE items SET TotalQty = CASE WHEN TotalQty - ? < 0 THEN 0 ELSE TotalQty - ? END WHERE UPC = ?",
            (qty, qty, upc)
        )
        cursor.execute("SELECT COUNT(*) AS RemainingEntries FROM item_bin WHERE UPC = ?", (upc,))
        remaining_entries = cursor.fetchone()["RemainingEntries"]
        if remaining_entries == 0:
            cursor.execute("DELETE FROM items WHERE UPC = ?", (upc,))

        db.commit()
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while deleting entry:", e.args[0])
        return jsonify({"error": "Failed to delete entry"}), 500
    finally:
        db.close()

    return jsonify({"success": True, "deleted_entry_id": entry_id}), 200

@app.route('/print-item-label', methods=['POST'])
def print_item_label():
    data = request.get_json(silent=True) or {}
    upc = data.get('upc')

    if not upc:
        return jsonify({"error": "Missing UPC"}), 400

    try:
        upc = int(upc)
    except (TypeError, ValueError):
        return jsonify({"error": "UPC must be a number"}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT UPC FROM items WHERE UPC = ?", (upc,))
        item = cursor.fetchone()
        if not item:
            return jsonify({"error": "Item not found"}), 404
    finally:
        db.close()

    try:
        functions.printItemUPC(upc)
    except Exception as exc:
        print("An error occurred while printing item label:", exc)
        return jsonify({"error": "Failed to print item label"}), 500

    return jsonify({"success": True}), 200

@app.route('/print-bin-label', methods=['POST'])
def print_bin_label():
    data = request.get_json(silent=True) or {}
    bin_upc = data.get('bin_upc')

    if not bin_upc:
        return jsonify({"error": "Missing bin identifier"}), 400

    try:
        bin_upc = int(bin_upc)
    except (TypeError, ValueError):
        return jsonify({"error": "Bin identifier must be numeric"}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute(
            "SELECT BinUPC, BinType, BinID FROM bins WHERE BinUPC = ?",
            (bin_upc,)
        )
        bin_row = cursor.fetchone()
        if not bin_row:
            return jsonify({"error": "Bin not found"}), 404
    finally:
        db.close()

    try:
        functions.printBinUPC(bin_row["BinUPC"], bin_row["BinType"], bin_row["BinID"])
    except Exception as exc:
        print("An error occurred while printing bin label:", exc)
        return jsonify({"error": "Failed to print bin label"}), 500

    return jsonify({"success": True}), 200

@app.route('/create-bin', methods=['POST'])
def create_bin():
    data = request.get_json(silent=True) or {}
    room = (data.get('room') or '').strip()
    wall = (data.get('wall') or '').strip()
    storage_type = (data.get('storage_type') or '').strip()

    if not room or not wall or not storage_type:
        return jsonify({"error": "Missing required fields"}), 400

    valid_rooms = {"110", "110A", "110B", "110C"}
    valid_walls = {"North", "South", "East", "West"}
    valid_storage_types = {"Bin", "Shelf", "Drawer", "Cabinet", "Tabletop", "Overhead", "Other"}

    if room not in valid_rooms or wall not in valid_walls or storage_type not in valid_storage_types:
        return jsonify({"error": "Invalid bin details"}), 400

    try:
        bin_id, bin_upc = functions.createBin(storage_type, wall, room)
    except Exception as exc:
        print("An error occurred while creating bin:", exc)
        return jsonify({"error": "Failed to create bin"}), 500

    created_bin = load_bin_row(bin_upc)
    return jsonify({"success": True, "bin": created_bin, "bin_id": bin_id, "bin_upc": bin_upc}), 200

@app.route('/delete-bin', methods=['POST'])
def delete_bin():
    data = request.get_json(silent=True) or {}
    bin_upc = data.get('bin_upc')

    if not bin_upc:
        return jsonify({"error": "Missing bin identifier"}), 400

    try:
        bin_upc = int(bin_upc)
    except (TypeError, ValueError):
        return jsonify({"error": "Bin identifier must be numeric"}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT BinUPC FROM bins WHERE BinUPC = ?", (bin_upc,))
        existing_bin = cursor.fetchone()
        if not existing_bin:
            return jsonify({"error": "Bin not found"}), 404

        cursor.execute("SELECT COUNT(*) AS ItemCount FROM item_bin WHERE BinUPC = ?", (bin_upc,))
        item_count = cursor.fetchone()["ItemCount"]
        if item_count:
            return jsonify({"error": "Cannot delete a bin that still contains item entries."}), 409

        cursor.execute("DELETE FROM bins WHERE BinUPC = ?", (bin_upc,))
        db.commit()
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while deleting bin:", e.args[0])
        return jsonify({"error": "Failed to delete bin"}), 500
    finally:
        db.close()

    return jsonify({"success": True, "deleted_bin_upc": bin_upc}), 200

@app.route('/create', methods=['POST'])
def create_item():
    data = request.get_json()
    if data:
        room = data.get('rooms', '')
        wall = data.get('walls', '')
        storage_type = data.get('bin-type', '')
        bin_number = data.get('bin', '')
        item_name = data.get('item_name', '')
        quantity = data.get('quantity', '')
        # if any of the required fields are missing, return an error
        if not room or not wall or not storage_type or not bin_number or not item_name or not quantity:
            return jsonify({"error": "Missing required fields"}), 300
        else:
            functions.createItemLocator(item_name, bin_number, quantity, storage_type, wall, room)
            queue_item_image_lookup(item_name, trigger="new_item_create")
            db = get_db()
            cursor = db.cursor()
            entry_id = None
            try:
                cursor.execute(
                    """
                    SELECT EntryID
                    FROM item_bin
                    WHERE Name = ?
                    ORDER BY EntryID DESC
                    LIMIT 1
                    """,
                    (item_name,)
                )
                row = cursor.fetchone()
                if row:
                    entry_id = row["EntryID"]
            finally:
                db.close()

            created_entry = load_database_entry(entry_id) if entry_id else None
            return jsonify({"success": True, "entry": created_entry}), 200
    else:
        print("No data received")
        return jsonify({"error": "No data received"}), 400

@app.route('/get-bins', methods=['POST'])
def get_bins():
    data = request.json
    print(data)
    room = data.get('room')
    wall = data.get('wall')
    storage_type = data.get('storageType')

    # Query the database for bins based on the selected room, wall, and storage type
    bins = query_bins_from_database(room, wall, storage_type)

    # Return the bins as JSON
    return jsonify(bins)

@app.route('/item-image-status')
def item_image_status():
    item_name = request.args.get('item_name', '')
    normalized_name = normalize_item_image_key(item_name)
    if not normalized_name:
        return jsonify({"error": "Missing item name"}), 400

    return jsonify({
        "item_name": normalized_name,
        "status": get_item_image_status(normalized_name),
        "image_url": get_item_image_url(normalized_name),
    }), 200

def query_bins_from_database(room, wall, storage_type):
    WallID = functions.wallDecider(wall,room)
    theList = functions.returnBinList(WallID, storage_type)
    print(theList)
    return theList


if __name__ == '__main__':
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.run(debug=True)
