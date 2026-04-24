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
import smtplib
import urllib.request
import urllib.error
from html import unescape, escape
from urllib.parse import urlparse, quote_plus, urljoin, unquote
from datetime import datetime
from email.message import EmailMessage
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
IMAGE_AGENT_PAGE_ATTEMPTS = int(os.getenv("IMAGE_AGENT_PAGE_ATTEMPTS", "4"))
IMAGE_AGENT_MAX_VALIDATIONS = int(os.getenv("IMAGE_AGENT_MAX_VALIDATIONS", "2"))
IMAGE_AGENT_AI_WEB_SEARCH = os.getenv("IMAGE_AGENT_AI_WEB_SEARCH", "false").lower() == "true"
IMAGE_AGENT_AI_SEARCH_FALLBACK = os.getenv("IMAGE_AGENT_AI_SEARCH_FALLBACK", "false").lower() == "true"
CHECKOUT_NOTIFY_EMAILS = [value.strip() for value in os.getenv("CHECKOUT_NOTIFY_EMAILS", "mgalipeau@uri.edu").split(",") if value.strip()]
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or "587")
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USERNAME).strip()
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() == "true"
ITEM_IMAGES_FILE = "item_images.json"
ITEM_IMAGE_METADATA_FILE = "item_image_metadata.json"
ITEM_IMAGE_CACHE_DIR = os.path.join("static", "item-images")
BIN_COORD_COLUMN_COUNT = 48
BIN_COORD_ROW_COUNT = 36
_help_bot_backend = None
_image_agent_client = None
_item_images_lock = threading.Lock()
_item_image_cancelled = set()

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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS checkout_requests(
                RequestID INTEGER PRIMARY KEY AUTOINCREMENT,
                SessionID INTEGER,
                Email TEXT NOT NULL,
                UriId TEXT NOT NULL DEFAULT '',
                IdentifierID INTEGER NOT NULL UNIQUE,
                UPC INTEGER NOT NULL,
                UnitIdentifier TEXT NOT NULL,
                ItemName TEXT NOT NULL,
                RequestedDate TEXT NOT NULL,
                RequestedTime TEXT NOT NULL,
                IsActive INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (SessionID) REFERENCES user_sessions(SessionID),
                FOREIGN KEY (IdentifierID) REFERENCES item_identifiers(IdentifierID),
                FOREIGN KEY (UPC) REFERENCES items(UPC)
            )
        """)
        cursor.execute("PRAGMA table_info(checkout_requests)")
        existing_columns = {row["name"] for row in cursor.fetchall()}
        if "UriId" not in existing_columns:
            cursor.execute("ALTER TABLE checkout_requests ADD COLUMN UriId TEXT NOT NULL DEFAULT ''")
        if "FirstName" not in existing_columns:
            cursor.execute("ALTER TABLE checkout_requests ADD COLUMN FirstName TEXT NOT NULL DEFAULT ''")
        if "LastName" not in existing_columns:
            cursor.execute("ALTER TABLE checkout_requests ADD COLUMN LastName TEXT NOT NULL DEFAULT ''")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_checkout_requests_session ON checkout_requests (SessionID)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_checkout_requests_email ON checkout_requests (Email)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS checkout_log(
                LogID INTEGER PRIMARY KEY AUTOINCREMENT,
                SessionID INTEGER,
                Email TEXT NOT NULL,
                UriId TEXT NOT NULL DEFAULT '',
                FirstName TEXT NOT NULL DEFAULT '',
                LastName TEXT NOT NULL DEFAULT '',
                IdentifierID INTEGER NOT NULL,
                UPC INTEGER NOT NULL,
                UnitIdentifier TEXT NOT NULL,
                ItemName TEXT NOT NULL,
                CheckOutDate TEXT NOT NULL,
                CheckOutTime TEXT NOT NULL,
                CheckInDate TEXT,
                CheckInTime TEXT,
                IsActive INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (SessionID) REFERENCES user_sessions(SessionID),
                FOREIGN KEY (IdentifierID) REFERENCES item_identifiers(IdentifierID),
                FOREIGN KEY (UPC) REFERENCES items(UPC)
            )
        """)
        cursor.execute("PRAGMA table_info(checkout_log)")
        checkout_log_columns = {row["name"] for row in cursor.fetchall()}
        if "FirstName" not in checkout_log_columns:
            cursor.execute("ALTER TABLE checkout_log ADD COLUMN FirstName TEXT NOT NULL DEFAULT ''")
        if "LastName" not in checkout_log_columns:
            cursor.execute("ALTER TABLE checkout_log ADD COLUMN LastName TEXT NOT NULL DEFAULT ''")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_checkout_log_session ON checkout_log (SessionID)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_checkout_log_email ON checkout_log (Email)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_checkout_log_identifier_active ON checkout_log (IdentifierID, IsActive)")
        db.commit()
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while ensuring tracking tables:", e.args[0])
    finally:
        db.close()

ensure_tracking_tables()

def ensure_item_identifier_table():
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS item_identifiers(
                IdentifierID INTEGER PRIMARY KEY AUTOINCREMENT,
                UPC INTEGER NOT NULL,
                UnitIdentifier TEXT NOT NULL UNIQUE,
                CanCheckOut INTEGER NOT NULL DEFAULT 0,
                CreatedDate TEXT NOT NULL,
                CreatedTime TEXT NOT NULL,
                FOREIGN KEY (UPC) REFERENCES items(UPC)
            )
        """)
        cursor.execute("PRAGMA table_info(item_identifiers)")
        existing_columns = {row["name"] for row in cursor.fetchall()}
        if "CanCheckOut" not in existing_columns:
            cursor.execute("ALTER TABLE item_identifiers ADD COLUMN CanCheckOut INTEGER NOT NULL DEFAULT 0")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_item_identifiers_upc ON item_identifiers (UPC)")
        db.commit()
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while ensuring item identifier table:", e.args[0])
    finally:
        db.close()

ensure_item_identifier_table()

def ensure_bin_coordinate_column():
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("PRAGMA table_info(bins)")
        existing_columns = {row["name"] for row in cursor.fetchall()}
        if "Coordinates" not in existing_columns:
            cursor.execute("ALTER TABLE bins ADD COLUMN Coordinates TEXT")
            db.commit()
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while ensuring bin coordinates column:", e.args[0])
    finally:
        db.close()

ensure_bin_coordinate_column()

def ensure_bins_room_support():
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("PRAGMA table_info(bins)")
        columns = cursor.fetchall()
        if not columns:
            return

        column_map = {row["name"]: row for row in columns}
        needs_room_column = "RoomID" not in column_map
        wall_requires_value = bool(column_map.get("WallID") and column_map["WallID"]["notnull"])
        needs_coordinates_column = "Coordinates" not in column_map

        if not needs_room_column and not wall_requires_value and not needs_coordinates_column:
            return

        coordinate_expression = "Coordinates" if not needs_coordinates_column else "NULL"
        room_id_expression = "COALESCE(b.RoomID, w.RoomID)" if not needs_room_column else "w.RoomID"

        cursor.execute("PRAGMA foreign_keys = OFF")
        cursor.execute("DROP TABLE IF EXISTS bins_new")
        cursor.execute("""
            CREATE TABLE bins_new(
                BinUPC INTEGER PRIMARY KEY NOT NULL,
                BinID INTEGER NOT NULL,
                BinType TEXT NOT NULL,
                WallID INTEGER,
                RoomID INTEGER,
                Coordinates TEXT,
                FOREIGN KEY (WallID) REFERENCES walls(WallID),
                FOREIGN KEY (RoomID) REFERENCES rooms(RoomID)
            )
        """)
        cursor.execute(f"""
            INSERT INTO bins_new (BinUPC, BinID, BinType, WallID, RoomID, Coordinates)
            SELECT
                b.BinUPC,
                b.BinID,
                b.BinType,
                b.WallID,
                {room_id_expression},
                {coordinate_expression}
            FROM bins b
            LEFT JOIN walls w ON w.WallID = b.WallID
        """)
        cursor.execute("DROP TABLE bins")
        cursor.execute("ALTER TABLE bins_new RENAME TO bins")
        db.commit()
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while ensuring room-level bins:", e.args[0])
    finally:
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
        except sqlite3.Error:
            pass
        db.close()

ensure_bins_room_support()

def ensure_item_bin_room_support():
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("PRAGMA table_info(item_bin)")
        columns = cursor.fetchall()
        if not columns:
            return

        column_map = {row["name"]: row for row in columns}
        needs_room_column = "RoomID" not in column_map
        bin_upc_requires_value = bool(column_map.get("BinUPC") and column_map["BinUPC"]["notnull"])

        if not needs_room_column and not bin_upc_requires_value:
            return

        room_id_expression = "COALESCE(ib.RoomID, r.RoomID)" if not needs_room_column else "r.RoomID"

        cursor.execute("PRAGMA foreign_keys = OFF")
        cursor.execute("DROP TABLE IF EXISTS item_bin_new")
        cursor.execute("""
            CREATE TABLE item_bin_new(
                EntryID INTEGER PRIMARY KEY AUTOINCREMENT,
                UPC INTEGER NOT NULL,
                Name TEXT NOT NULL,
                BinUPC INTEGER,
                RoomID INTEGER,
                Qty INTEGER NOT NULL,
                Date TEXT NOT NULL,
                Time TEXT NOT NULL,
                FOREIGN KEY (UPC) REFERENCES items(UPC),
                FOREIGN KEY (BinUPC) REFERENCES bins(BinUPC),
                FOREIGN KEY (RoomID) REFERENCES rooms(RoomID)
            )
        """)
        cursor.execute(f"""
            INSERT INTO item_bin_new (EntryID, UPC, Name, BinUPC, RoomID, Qty, Date, Time)
            SELECT
                ib.EntryID,
                ib.UPC,
                ib.Name,
                ib.BinUPC,
                {room_id_expression} AS RoomID,
                ib.Qty,
                ib.Date,
                ib.Time
            FROM item_bin ib
            LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
            LEFT JOIN walls w ON w.WallID = b.WallID
            LEFT JOIN rooms r ON r.RoomID = w.RoomID
        """)
        cursor.execute("DROP TABLE item_bin")
        cursor.execute("ALTER TABLE item_bin_new RENAME TO item_bin")
        cursor.execute("CREATE INDEX IF NOT EXISTS nameIndex ON item_bin (Name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS UPCIndex ON item_bin (UPC)")
        db.commit()
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while ensuring room-only item locations:", e.args[0])
    finally:
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
        except sqlite3.Error:
            pass
        db.close()

ensure_item_bin_room_support()
functions.refreshDatabaseConnection()

def build_bin_coord_columns(count):
    labels = []
    for index in range(count):
        label = ""
        number = index
        while True:
            number, remainder = divmod(number, 26)
            label = chr(65 + remainder) + label
            if number == 0:
                break
            number -= 1
        labels.append(label)
    return labels

BIN_COORD_COLUMNS = build_bin_coord_columns(BIN_COORD_COLUMN_COUNT)
BIN_COORD_ROWS = list(range(1, BIN_COORD_ROW_COUNT + 1))

def normalize_bin_coordinates(value):
    raw = (value or "").strip().upper()
    if not raw:
        return ""

    match = re.fullmatch(r"([A-Z]{1,2})\s*-?\s*([1-9]|[1-2][0-9]|3[0-6])", raw)
    if not match:
        return None

    column_label = match.group(1)
    row_label = match.group(2)
    if column_label not in BIN_COORD_COLUMNS:
        return None

    return f"{column_label}{row_label}"

def parse_bin_coordinates(value):
    normalized = normalize_bin_coordinates(value)
    if not normalized:
        return None

    match = re.fullmatch(r"([A-Z]{1,2})([1-9]|[1-2][0-9]|3[0-6])", normalized)
    if not match:
        return None

    column_label = match.group(1)
    row_number = int(match.group(2))
    return {
        "label": normalized,
        "column_label": column_label,
        "column_index": BIN_COORD_COLUMNS.index(column_label),
        "row_number": row_number,
        "row_index": row_number - 1,
    }

def parse_item_bin_coordinate_list(raw_value):
    coordinates = []
    seen = set()

    for part in (raw_value or "").split(","):
        parsed = parse_bin_coordinates(part)
        if not parsed:
            continue
        label = parsed["label"]
        if label in seen:
            continue
        seen.add(label)
        coordinates.append(parsed)

    return coordinates

def coerce_int_list(values):
    coerced = []
    for value in values or []:
        text = str(value).strip()
        if not text:
            continue
        try:
            coerced.append(int(text))
        except (TypeError, ValueError):
            continue
    return coerced

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
                        WHEN COALESCE(r.RoomName, rd.RoomName) IS NOT NULL THEN
                            CASE
                                WHEN ? IS NOT NULL AND COALESCE(r.RoomName, rd.RoomName) = ? AND w.WallName IS NOT NULL THEN w.WallName
                                ELSE COALESCE(r.RoomName, rd.RoomName)
                            END
                    END
                ) AS WallNames,
                GROUP_CONCAT(
                    DISTINCT CASE
                        WHEN ib.BinUPC IS NULL AND rd.RoomName IS NOT NULL THEN
                            rd.RoomName
                        WHEN COALESCE(r.RoomName, br.RoomName) IS NOT NULL AND b.BinType IS NOT NULL AND b.BinID IS NOT NULL AND w.WallName IS NULL THEN
                            COALESCE(r.RoomName, br.RoomName) || ' ' || b.BinType || ' ' || b.BinID
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
            LEFT JOIN rooms br ON br.RoomID = b.RoomID
            LEFT JOIN rooms rd ON rd.RoomID = ib.RoomID
        """
        params = [room_name, room_name]

        if room_name:
            query += """
            WHERE EXISTS (
                SELECT 1
                FROM item_bin ib2
                LEFT JOIN bins b2 ON b2.BinUPC = ib2.BinUPC
                LEFT JOIN walls w2 ON w2.WallID = b2.WallID
                LEFT JOIN rooms r2 ON r2.RoomID = w2.RoomID
                LEFT JOIN rooms rd2 ON rd2.RoomID = ib2.RoomID
                LEFT JOIN rooms br2 ON br2.RoomID = b2.RoomID
                WHERE ib2.UPC = i.UPC AND COALESCE(r2.RoomName, br2.RoomName, rd2.RoomName) = ?
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
                i.TotalQty,
                CASE WHEN ib.BinUPC IS NULL THEN NULL ELSE b.BinID END AS BinID,
                COALESCE(b.BinType, CASE WHEN ib.BinUPC IS NULL THEN 'None' END) AS BinType,
                CASE WHEN ib.BinUPC IS NULL THEN NULL ELSE w.WallName END AS WallName,
                COALESCE(r.RoomName, rd.RoomName) AS RoomName,
                COALESCE(id_counts.IdentifierCount, 0) AS IdentifierCount
            FROM item_bin ib
            LEFT JOIN items i ON i.UPC = ib.UPC
            LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
            LEFT JOIN walls w ON w.WallID = b.WallID
            LEFT JOIN rooms r ON r.RoomID = w.RoomID
            LEFT JOIN rooms rd ON rd.RoomID = ib.RoomID
            LEFT JOIN (
                SELECT UPC, COUNT(*) AS IdentifierCount
                FROM item_identifiers
                GROUP BY UPC
            ) id_counts ON id_counts.UPC = ib.UPC
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

def load_item_identifiers(upc):
    db = get_db()
    cursor = db.cursor()
    identifiers = []
    try:
        cursor.execute("""
            SELECT
                ii.IdentifierID,
                ii.UPC,
                ii.UnitIdentifier,
                ii.CanCheckOut,
                ii.CreatedDate,
                ii.CreatedTime,
                CASE WHEN c.IdentifierID IS NOT NULL THEN 1 ELSE 0 END AS IsCheckedOut
            FROM item_identifiers ii
            LEFT JOIN checkout_log c ON c.IdentifierID = ii.IdentifierID AND c.IsActive = 1
            WHERE ii.UPC = ?
            ORDER BY ii.UnitIdentifier
        """, (upc,))
        identifiers = [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        print("An error occurred while loading item identifiers:", e.args[0])
    finally:
        db.close()

    return identifiers

def load_item_identifier_summary(upc):
    db = get_db()
    cursor = db.cursor()
    summary = None
    try:
        cursor.execute("""
            SELECT i.UPC, i.Name, i.TotalQty, COUNT(ii.IdentifierID) AS IdentifierCount
            FROM items i
            LEFT JOIN item_identifiers ii ON ii.UPC = i.UPC
            WHERE i.UPC = ?
            GROUP BY i.UPC, i.Name, i.TotalQty
        """, (upc,))
        row = cursor.fetchone()
        if row:
            summary = dict(row)
    except sqlite3.Error as e:
        print("An error occurred while loading item identifier summary:", e.args[0])
    finally:
        db.close()

    return summary

def create_item_identifiers(upc):
    db = get_db()
    cursor = db.cursor()
    created = []
    try:
        cursor.execute("SELECT Name, TotalQty FROM items WHERE UPC = ?", (upc,))
        item_row = cursor.fetchone()
        if not item_row:
            return None, "Item not found."

        total_qty = int(item_row["TotalQty"] or 0)
        if total_qty < 1:
            return None, "Item quantity must be at least 1 to create identifiers."

        cursor.execute("SELECT UnitIdentifier FROM item_identifiers WHERE UPC = ?", (upc,))
        existing_identifiers = [row["UnitIdentifier"] for row in cursor.fetchall()]
        existing_count = len(existing_identifiers)
        if existing_count >= total_qty:
            db.commit()
            return [], None

        name_letters = re.sub(r"[^A-Z0-9]", "", (item_row["Name"] or "").upper())
        identifier_prefix = (name_letters[:3] if len(name_letters) >= 3 else name_letters.ljust(3, "X"))
        suffixes = []
        pattern = re.compile(rf"^{re.escape(identifier_prefix)}(\d{{3}})$")
        for identifier in existing_identifiers:
            match = pattern.match(identifier or "")
            if match:
                suffixes.append(int(match.group(1)))
        used_suffixes = set(suffixes)
        next_suffix = 0

        now = datetime.now()
        created_date = now.strftime("%Y-%m-%d")
        created_time = now.strftime("%H:%M:%S")
        for _ in range(total_qty - existing_count):
            while next_suffix in used_suffixes:
                next_suffix += 1
            unit_identifier = f"{identifier_prefix}{next_suffix:03d}"
            cursor.execute("""
                INSERT INTO item_identifiers (UPC, UnitIdentifier, CanCheckOut, CreatedDate, CreatedTime)
                VALUES (?, ?, 0, ?, ?)
            """, (upc, unit_identifier, created_date, created_time))
            created.append(unit_identifier)
            used_suffixes.add(next_suffix)
            next_suffix += 1

        db.commit()
        return created, None
    except sqlite3.IntegrityError as e:
        db.rollback()
        print("An integrity error occurred while creating item identifiers:", e.args[0])
        return None, "Could not create unique identifiers for this item."
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while creating item identifiers:", e.args[0])
        return None, "Failed to create item identifiers."
    finally:
        db.close()

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
                CASE
                    WHEN s.SignOutDate IS NULL OR s.SignOutTime IS NULL THEN 1
                    ELSE 0
                END AS IsActive,
                COUNT(DISTINCT a.AccessID) AS AccessCount,
                GROUP_CONCAT(
                    DISTINCT CASE
                        WHEN a.ItemName IS NOT NULL AND a.ItemName != '' THEN a.ItemName
                    END
                ) AS AccessedItems,
                GROUP_CONCAT(
                    DISTINCT CASE
                        WHEN c.UnitIdentifier IS NOT NULL AND c.UnitIdentifier != '' THEN c.UnitIdentifier
                    END
                ) AS CheckedOut
            FROM user_sessions s
            LEFT JOIN user_item_access a ON a.SessionID = s.SessionID
            LEFT JOIN checkout_log c ON c.SessionID = s.SessionID AND c.IsActive = 1
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

        for session_row in sessions:
            if (session_row.get("AccessCount") or 0) > 0:
                continue

            sign_in_stamp = f"{session_row.get('SignInDate') or ''} {session_row.get('SignInTime') or ''}".strip()
            sign_out_date = session_row.get("SignOutDate")
            sign_out_time = session_row.get("SignOutTime")
            sign_out_stamp = f"{sign_out_date or ''} {sign_out_time or ''}".strip()
            if not sign_in_stamp:
                continue

            cursor.execute("""
                SELECT
                    COUNT(DISTINCT AccessID) AS AccessCount,
                    GROUP_CONCAT(
                        DISTINCT CASE
                            WHEN ItemName IS NOT NULL AND ItemName != '' THEN ItemName
                        END
                    ) AS AccessedItems
                FROM user_item_access
                WHERE Email = ?
                  AND ((AccessDate || ' ' || AccessTime) >= ?)
                  AND (? = '' OR (AccessDate || ' ' || AccessTime) <= ?)
            """, (
                session_row.get("Email") or "",
                sign_in_stamp,
                sign_out_stamp,
                sign_out_stamp,
            ))
            fallback_row = cursor.fetchone()
            if not fallback_row:
                continue

            fallback_access_count = fallback_row["AccessCount"] or 0
            fallback_accessed_items = fallback_row["AccessedItems"]
            if fallback_access_count:
                session_row["AccessCount"] = fallback_access_count
                session_row["AccessedItems"] = fallback_accessed_items
    except sqlite3.Error as e:
        print("An error occurred while loading user tracking:", e.args[0])
    finally:
        db.close()

    return sessions

def load_checkout_history(limit=250):
    db = get_db()
    cursor = db.cursor()
    history = []
    try:
        cursor.execute("""
            SELECT
                ItemName,
                UnitIdentifier,
                UriId,
                Email,
                CheckOutDate,
                CheckOutTime,
                CheckInDate,
                CheckInTime
            FROM checkout_log
            ORDER BY
                datetime(CheckOutDate || ' ' || CheckOutTime) DESC,
                LogID DESC
            LIMIT ?
        """, (limit,))
        history = [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        print("An error occurred while loading checkout history:", e.args[0])
    finally:
        db.close()

    return history

def load_checkout_request_items(current_email=""):
    current_email = (current_email or "").strip().lower()
    db = get_db()
    cursor = db.cursor()
    items_by_upc = {}
    try:
        cursor.execute("""
            SELECT
                i.UPC,
                i.Name,
                i.TotalQty,
                ii.IdentifierID,
                ii.UnitIdentifier,
                ii.CanCheckOut,
                ii.CreatedDate,
                ii.CreatedTime,
                c.Email AS CheckedOutBy,
                c.CheckOutDate AS RequestedDate,
                c.CheckOutTime AS RequestedTime
            FROM items i
            INNER JOIN item_identifiers ii ON ii.UPC = i.UPC
            LEFT JOIN checkout_log c ON c.IdentifierID = ii.IdentifierID AND c.IsActive = 1
            WHERE ii.CanCheckOut = 1
            ORDER BY i.Name COLLATE NOCASE, ii.UnitIdentifier
        """)
        for row in cursor.fetchall():
            record = dict(row)
            upc = record["UPC"]
            item = items_by_upc.setdefault(upc, {
                "UPC": upc,
                "Name": record.get("Name") or "Unknown Item",
                "TotalQty": record.get("TotalQty") or 0,
                "Thumbnail": get_item_image_url(record.get("Name") or "Unknown Item"),
                "Identifiers": [],
            })
            item["Identifiers"].append({
                "IdentifierID": record.get("IdentifierID"),
                "UnitIdentifier": record.get("UnitIdentifier") or "",
                "CanCheckOut": bool(record.get("CanCheckOut")),
                "CreatedDate": record.get("CreatedDate") or "",
                "CreatedTime": record.get("CreatedTime") or "",
                "CheckedOutBy": record.get("CheckedOutBy") or "",
                "RequestedDate": record.get("RequestedDate") or "",
                "RequestedTime": record.get("RequestedTime") or "",
                "IsCheckedOut": bool(record.get("CheckedOutBy")),
                "IsCheckedOutByCurrentUser": bool(record.get("CheckedOutBy")) and (record.get("CheckedOutBy") or "").strip().lower() == current_email,
            })
    except sqlite3.Error as e:
        print("An error occurred while loading checkout request items:", e.args[0])
    finally:
        db.close()

    return list(items_by_upc.values())

def get_item_email_inline_image(item_name):
    image_url = get_item_image_url(item_name)
    if not image_url:
        return None

    try:
        if image_url.startswith("data:image/"):
            header, payload = image_url.split(",", 1)
            mime_type = header[5:].split(";", 1)[0].strip().lower() or "image/png"
            if ";base64" in header.lower():
                image_bytes = base64.b64decode(payload)
            else:
                image_bytes = unquote(payload).encode("utf-8")
            maintype, subtype = (mime_type.split("/", 1) + ["png"])[:2]
            return {
                "bytes": image_bytes,
                "maintype": maintype,
                "subtype": subtype,
            }

        if image_url.startswith("/static/"):
            relative_path = image_url.lstrip("/").replace("/", os.sep)
            file_path = os.path.join(os.getcwd(), relative_path)
            if os.path.exists(file_path):
                mime_type, _ = mimetypes.guess_type(file_path)
                mime_type = (mime_type or "image/png").lower()
                with open(file_path, "rb") as image_file:
                    image_bytes = image_file.read()
                maintype, subtype = (mime_type.split("/", 1) + ["png"])[:2]
                return {
                    "bytes": image_bytes,
                    "maintype": maintype,
                    "subtype": subtype,
                }
    except Exception as exc:
        print(f"Could not load email image for {item_name}: {exc}")

    return None

def send_checkout_notification(action, item_name, unit_identifier, user_email, uri_id="", event_date="", event_time="", first_name="", last_name=""):
    recipient_emails = []
    for value in [*CHECKOUT_NOTIFY_EMAILS, user_email]:
        normalized_email = (value or "").strip().lower()
        if normalized_email and normalized_email not in recipient_emails:
            recipient_emails.append(normalized_email)

    if not recipient_emails:
        return False
    if not SMTP_HOST or not SMTP_FROM_EMAIL:
        print("Checkout email notification skipped: SMTP is not configured.")
        return False

    normalized_action = (action or "").strip().lower()
    action_label = "checked out" if normalized_action == "checkout" else "checked in"
    timestamp_line = " ".join(part for part in [event_date, event_time] if part).strip() or "Unknown time"
    full_name = " ".join(part for part in [(first_name or "").strip(), (last_name or "").strip()] if part).strip() or "Unknown name"

    message = EmailMessage()
    message["Subject"] = f"Item {action_label}: {unit_identifier or 'Unknown ID'}"
    message["From"] = SMTP_FROM_EMAIL
    message["To"] = ", ".join(recipient_emails)
    text_lines = [
        f"An item was {action_label} for {full_name}.",
        "",
        f"Item Name: {item_name or 'Unknown item'}",
        f"Item ID: {unit_identifier or 'Unknown'}",
        f"Email: {user_email}",
        f"URI ID: {uri_id or 'Unknown'}",
    ]
    message.set_content("\n".join(text_lines))

    image_payload = get_item_email_inline_image(item_name)
    html_lines = [
        f"<p>An item was {escape(action_label)} for {escape(full_name or 'Unknown user')}.</p>",
        "<p>",
        f"Item Name: {escape(item_name or 'Unknown item')}<br>",
        f"Item ID: {escape(unit_identifier or 'Unknown')}<br>",
        f"Email: {escape(user_email)}<br>",
        f"URI ID: {escape(uri_id or 'Unknown')}",
        "</p>",
    ]
    if image_payload:
        html_lines.append("<p><img src=\"cid:item-image\" alt=\"Item image\" style=\"max-width: 320px; height: auto;\"></p>")
    message.add_alternative("".join(html_lines), subtype="html")
    if image_payload:
        message.get_payload()[-1].add_related(
            image_payload["bytes"],
            maintype=image_payload["maintype"],
            subtype=image_payload["subtype"],
            cid="<item-image>",
        )

    try:
        if SMTP_USE_SSL:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as server:
                if SMTP_USERNAME:
                    server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(message)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
                if SMTP_USE_TLS:
                    server.starttls()
                if SMTP_USERNAME:
                    server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(message)
        return True
    except Exception as exc:
        print(f"Checkout email notification failed: {exc}")
        return False

def return_checkout_request(identifier_id, email):
    email = (email or "").strip().lower()
    if not email:
        return None, "You must be signed in to return a checkout."

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("""
            SELECT LogID, IdentifierID, UnitIdentifier, Email, ItemName, UriId, FirstName, LastName
            FROM checkout_log
            WHERE IdentifierID = ? AND IsActive = 1
        """, (identifier_id,))
        row = cursor.fetchone()
        if not row:
            return {
                "IdentifierID": identifier_id,
                "CheckedOutBy": "",
                "RequestedDate": "",
                "RequestedTime": "",
                "IsCheckedOutByCurrentUser": False,
            }, "This identifier is not currently checked out."

        record = dict(row)
        checked_out_by = (record.get("Email") or "").strip().lower()
        if checked_out_by != email:
            return {
                "IdentifierID": record["IdentifierID"],
                "UnitIdentifier": record.get("UnitIdentifier") or "",
                "CheckedOutBy": record.get("Email") or "",
                "RequestedDate": "",
                "RequestedTime": "",
                "IsCheckedOutByCurrentUser": False,
            }, "Only the user who checked out this identifier can return it."

        now = datetime.now()
        check_in_date = now.strftime("%Y-%m-%d")
        check_in_time = now.strftime("%H:%M:%S")
        cursor.execute(
            """
            UPDATE checkout_log
            SET IsActive = 0, CheckInDate = ?, CheckInTime = ?
            WHERE LogID = ?
            """,
            (check_in_date, check_in_time, record["LogID"])
        )
        db.commit()
        send_checkout_notification(
            "return",
            record.get("ItemName") or "",
            record.get("UnitIdentifier") or "",
            checked_out_by,
            record.get("UriId") or "",
            check_in_date,
            check_in_time,
            record.get("FirstName") or "",
            record.get("LastName") or "",
        )
        return {
            "IdentifierID": record["IdentifierID"],
            "UnitIdentifier": record.get("UnitIdentifier") or "",
            "CheckedOutBy": "",
            "RequestedDate": "",
            "RequestedTime": "",
            "IsCheckedOutByCurrentUser": False,
        }, None
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while returning checkout request:", e.args[0])
        return None, "Failed to return checkout request."
    finally:
        db.close()

def create_checkout_request(identifier_id, email, session_id, uri_id, first_name, last_name):
    email = (email or "").strip().lower()
    if not email:
        return None, "You must be signed in to request a checkout."
    normalized_uri_id = re.sub(r"\D", "", str(uri_id or ""))
    normalized_first_name = " ".join(str(first_name or "").split()).strip()
    normalized_last_name = " ".join(str(last_name or "").split()).strip()
    if not re.fullmatch(r"\d{9}", normalized_uri_id):
        return None, "Enter a valid 9-digit URI ID number."
    if not normalized_first_name or not normalized_last_name:
        return None, "Enter both a first and last name."

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("""
            SELECT
                ii.IdentifierID,
                ii.UPC,
                ii.UnitIdentifier,
                i.Name,
                c.Email AS CheckedOutBy,
                c.UriId,
                c.CheckOutDate,
                c.CheckOutTime
            FROM item_identifiers ii
            INNER JOIN items i ON i.UPC = ii.UPC
            LEFT JOIN checkout_log c ON c.IdentifierID = ii.IdentifierID AND c.IsActive = 1
            WHERE ii.IdentifierID = ?
        """, (identifier_id,))
        row = cursor.fetchone()
        if not row:
            return None, "Identifier not found."

        record = dict(row)
        if record.get("CheckedOutBy"):
            return {
                "IdentifierID": record["IdentifierID"],
                "UnitIdentifier": record["UnitIdentifier"],
                "CheckedOutBy": record["CheckedOutBy"],
                "UriId": record.get("UriId") or "",
                "RequestedDate": record.get("CheckOutDate") or "",
                "RequestedTime": record.get("CheckOutTime") or "",
                "IsCheckedOutByCurrentUser": (record.get("CheckedOutBy") or "").strip().lower() == email,
            }, "This identifier has already been requested."

        now = datetime.now()
        requested_date = now.strftime("%Y-%m-%d")
        requested_time = now.strftime("%H:%M:%S")
        cursor.execute("""
                INSERT INTO checkout_log (
                SessionID,
                Email,
                UriId,
                FirstName,
                LastName,
                IdentifierID,
                UPC,
                UnitIdentifier,
                ItemName,
                CheckOutDate,
                CheckOutTime,
                IsActive
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            session_id,
            email,
            normalized_uri_id,
            normalized_first_name,
            normalized_last_name,
            record["IdentifierID"],
            record["UPC"],
            record["UnitIdentifier"],
            record["Name"],
            requested_date,
            requested_time,
        ))
        db.commit()
        send_checkout_notification(
            "checkout",
            record.get("Name") or "",
            record.get("UnitIdentifier") or "",
            email,
            normalized_uri_id,
            requested_date,
            requested_time,
            normalized_first_name,
            normalized_last_name,
        )
        return {
            "IdentifierID": record["IdentifierID"],
            "UnitIdentifier": record["UnitIdentifier"],
            "CheckedOutBy": email,
            "UriId": normalized_uri_id,
            "RequestedDate": requested_date,
            "RequestedTime": requested_time,
            "IsCheckedOutByCurrentUser": True,
        }, None
    except sqlite3.IntegrityError:
        db.rollback()
        return None, "This identifier has already been requested."
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while creating checkout request:", e.args[0])
        return None, "Failed to create checkout request."
    finally:
        db.close()

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
                i.TotalQty,
                COALESCE(b.BinType, CASE WHEN ib.BinUPC IS NULL THEN 'None' END) AS BinType,
                CASE WHEN ib.BinUPC IS NULL THEN NULL ELSE b.BinID END AS BinID,
                CASE WHEN ib.BinUPC IS NULL THEN NULL ELSE w.WallName END AS WallName,
                COALESCE(r.RoomName, br.RoomName, rd.RoomName) AS RoomName,
                COALESCE(id_counts.IdentifierCount, 0) AS IdentifierCount
            FROM item_bin ib
            LEFT JOIN items i ON i.UPC = ib.UPC
            LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
            LEFT JOIN walls w ON w.WallID = b.WallID
            LEFT JOIN rooms r ON r.RoomID = w.RoomID
            LEFT JOIN rooms br ON br.RoomID = b.RoomID
            LEFT JOIN rooms rd ON rd.RoomID = ib.RoomID
            LEFT JOIN (
                SELECT UPC, COUNT(*) AS IdentifierCount
                FROM item_identifiers
                GROUP BY UPC
            ) id_counts ON id_counts.UPC = ib.UPC
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
                b.Coordinates,
                w.WallName,
                COALESCE(r.RoomName, br.RoomName) AS RoomName
            FROM bins b
            LEFT JOIN walls w ON w.WallID = b.WallID
            LEFT JOIN rooms r ON r.RoomID = w.RoomID
            LEFT JOIN rooms br ON br.RoomID = b.RoomID
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
                b.Coordinates,
                w.WallName,
                COALESCE(r.RoomName, br.RoomName) AS RoomName
            FROM bins b
            LEFT JOIN walls w ON w.WallID = b.WallID
            LEFT JOIN rooms r ON r.RoomID = w.RoomID
            LEFT JOIN rooms br ON br.RoomID = b.RoomID
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

def load_floorplan_bin_markers():
    db = get_db()
    cursor = db.cursor()
    markers = []
    try:
        cursor.execute("""
            SELECT
                b.BinUPC,
                b.BinID,
                b.BinType,
                b.Coordinates,
                w.WallName,
                COALESCE(r.RoomName, br.RoomName) AS RoomName
            FROM bins b
            LEFT JOIN walls w ON w.WallID = b.WallID
            LEFT JOIN rooms r ON r.RoomID = w.RoomID
            LEFT JOIN rooms br ON br.RoomID = b.RoomID
            WHERE b.Coordinates IS NOT NULL AND TRIM(b.Coordinates) != ''
            ORDER BY r.RoomName, w.WallName, b.BinType, b.BinID
        """)
        for row in cursor.fetchall():
            marker = dict(row)
            parsed = parse_bin_coordinates(marker.get("Coordinates"))
            if not parsed:
                continue
            marker["Coordinates"] = parsed["label"]
            marker["GridColumn"] = parsed["column_index"]
            marker["GridRow"] = parsed["row_index"]
            markers.append(marker)
    except sqlite3.Error as e:
        print("An error occurred while loading floorplan bin markers:", e.args[0])
    finally:
        db.close()

    return markers

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
                        WHEN ib.BinUPC IS NULL AND rd.RoomName IS NOT NULL THEN
                            rd.RoomName
                        WHEN r.RoomName IS NOT NULL THEN
                            r.RoomName || ' ' || w.WallName || ' ' || b.BinType || ' ' || b.BinID
                    END
                ) AS Locations
            FROM items i
            LEFT JOIN item_bin ib ON ib.UPC = i.UPC
            LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
            LEFT JOIN walls w ON w.WallID = b.WallID
            LEFT JOIN rooms r ON r.RoomID = w.RoomID
            LEFT JOIN rooms rd ON rd.RoomID = ib.RoomID
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

def iter_item_image_key_variants(item_name):
    normalized_name = normalize_item_image_key(item_name)
    if not normalized_name:
        return []

    variants = []
    seen = set()

    def add_variant(value):
        cleaned = " ".join((value or "").split()).strip()
        if not cleaned:
            return
        lowered = cleaned.lower()
        if lowered in seen:
            return
        seen.add(lowered)
        variants.append(cleaned)

    add_variant(normalized_name)
    add_variant(re.sub(r"\s*\([^)]*\)", "", normalized_name))
    add_variant(re.sub(r"\s*[-–]\s*[^-–]+$", "", normalized_name))

    return variants

def find_item_image_map_match(image_map, item_name):
    for variant in iter_item_image_key_variants(item_name):
        if variant in image_map:
            return variant, image_map[variant]

    lowered_map = {
        " ".join((key or "").split()).strip().lower(): (key, value)
        for key, value in image_map.items()
    }
    for variant in iter_item_image_key_variants(item_name):
        match = lowered_map.get(variant.lower())
        if match:
            return match

    return None, None

def find_item_image_metadata_match(metadata_map, item_name):
    for variant in iter_item_image_key_variants(item_name):
        metadata = metadata_map.get(variant)
        if isinstance(metadata, dict):
            return variant, metadata

    lowered_metadata = {
        " ".join((key or "").split()).strip().lower(): (key, value)
        for key, value in metadata_map.items()
        if isinstance(value, dict)
    }
    for variant in iter_item_image_key_variants(item_name):
        match = lowered_metadata.get(variant.lower())
        if match:
            return match

    return None, None

def get_item_image_status(item_name):
    normalized_name = normalize_item_image_key(item_name)
    if not normalized_name:
        return IMAGE_STATUS_FAILED

    metadata_map = load_item_image_metadata()
    _, metadata = find_item_image_metadata_match(metadata_map, normalized_name)
    if isinstance(metadata, dict):
        status = (metadata.get("status") or "").strip().lower()
        if status == IMAGE_STATUS_PENDING:
            return IMAGE_STATUS_PENDING

    image_map = load_item_image_map()
    matched_key, _ = find_item_image_map_match(image_map, normalized_name)
    if matched_key:
        return IMAGE_STATUS_SUCCESS

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

def clear_item_image_cancel(item_name):
    normalized_name = normalize_item_image_key(item_name)
    if not normalized_name:
        return
    with _item_images_lock:
        _item_image_cancelled.discard(normalized_name)

def cancel_item_image_lookup(item_name):
    normalized_name = normalize_item_image_key(item_name)
    if not normalized_name:
        return
    with _item_images_lock:
        _item_image_cancelled.add(normalized_name)

def is_item_image_lookup_cancelled(item_name):
    normalized_name = normalize_item_image_key(item_name)
    if not normalized_name:
        return False
    with _item_images_lock:
        return normalized_name in _item_image_cancelled

def save_item_image_metadata(metadata_map):
    payload = dict(metadata_map)
    try:
        with open(ITEM_IMAGE_METADATA_FILE, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, sort_keys=True)
    except OSError as exc:
        print("Could not save item image metadata:", exc)

def remove_exact_item_image_state(item_name, delete_cached_file=True):
    normalized_name = normalize_item_image_key(item_name)
    if not normalized_name:
        return

    with _item_images_lock:
        image_map = load_item_image_map()
        metadata_map = load_item_image_metadata()

        image_url = image_map.pop(normalized_name, None)
        metadata_map.pop(normalized_name, None)

        save_item_image_map(image_map)
        save_item_image_metadata(metadata_map)

    if not delete_cached_file or not isinstance(image_url, str):
        return

    if not image_url.startswith("/static/item-images/"):
        return

    remaining_map = load_item_image_map()
    if image_url in remaining_map.values():
        return

    relative_path = image_url.lstrip("/").replace("/", os.sep)
    file_path = os.path.join(app.root_path, relative_path)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except OSError as exc:
        print(f"Could not remove cached image for {normalized_name}: {exc}")

def get_item_image_url(item_name):
    normalized_name = normalize_item_image_key(item_name)
    metadata_map = load_item_image_metadata()
    exact_metadata = metadata_map.get(normalized_name)
    if isinstance(exact_metadata, dict):
        status = (exact_metadata.get("status") or "").strip().lower()
        if status == IMAGE_STATUS_PENDING:
            return build_item_thumbnail_data_uri(item_name)

    image_map = load_item_image_map()
    _, matched_url = find_item_image_map_match(image_map, normalized_name)
    if matched_url:
        return matched_url

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
                        WHEN COALESCE(r.RoomName, br.RoomName, rd.RoomName) IS NOT NULL THEN COALESCE(r.RoomName, br.RoomName, rd.RoomName)
                    END
                ) AS Rooms,
                GROUP_CONCAT(
                    DISTINCT CASE
                        WHEN ib.BinUPC IS NULL AND rd.RoomName IS NOT NULL THEN
                            rd.RoomName
                        WHEN COALESCE(r.RoomName, br.RoomName) IS NOT NULL AND b.BinType IS NOT NULL AND b.BinID IS NOT NULL AND w.WallName IS NULL THEN
                            COALESCE(r.RoomName, br.RoomName) || ' ' || b.BinType || ' ' || b.BinID
                        WHEN r.RoomName IS NOT NULL AND w.WallName IS NOT NULL AND b.BinType IS NOT NULL AND b.BinID IS NOT NULL THEN
                            r.RoomName || ' ' || w.WallName || ' ' || b.BinType || ' ' || b.BinID
                    END
                ) AS LocationDetails,
                GROUP_CONCAT(
                    DISTINCT CASE
                        WHEN b.Coordinates IS NOT NULL AND TRIM(b.Coordinates) != '' THEN b.Coordinates
                    END
                ) AS BinCoordinates
            FROM items i
            JOIN item_bin ib ON ib.UPC = i.UPC
            LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
            LEFT JOIN walls w ON w.WallID = b.WallID
            LEFT JOIN rooms r ON r.RoomID = w.RoomID
            LEFT JOIN rooms br ON br.RoomID = b.RoomID
            LEFT JOIN rooms rd ON rd.RoomID = ib.RoomID
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
        parsed_coordinates = parse_item_bin_coordinate_list(item.get("BinCoordinates"))
        primary_coordinate = parsed_coordinates[0] if parsed_coordinates else None
        item["PrimaryCoordinate"] = primary_coordinate["label"] if primary_coordinate else ""
        item["BinCoordinates"] = ", ".join(coord["label"] for coord in parsed_coordinates)

    return items

def load_sign_in_showcase_items(limit=18):
    image_map = load_item_image_map()
    if not image_map:
        return []

    db = get_db()
    cursor = db.cursor()
    items = []
    try:
        cursor.execute("""
            SELECT
                i.Name,
                i.TotalQty,
                MAX(datetime(ib.Date || ' ' || ib.Time)) AS LastChanged
            FROM items i
            LEFT JOIN item_bin ib ON ib.UPC = i.UPC
            GROUP BY i.UPC, i.Name, i.TotalQty
            ORDER BY LastChanged DESC, i.Name
        """)

        for row in cursor.fetchall():
            image_url = get_item_image_url(row["Name"])
            if not image_url or image_url.startswith("data:image/svg+xml"):
                continue
            items.append({
                "Name": row["Name"],
                "TotalQty": row["TotalQty"],
                "ImageUrl": image_url,
            })
    except sqlite3.Error as e:
        print("An error occurred while loading sign-in showcase items:", e.args[0])
    finally:
        db.close()

    if len(items) > limit:
        items = random.sample(items, limit)

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
    upc_values = coerce_int_list(upcs)
    try:
        if upc_values:
            placeholders = ",".join("?" for _ in upc_values)
            cursor.execute(
                f"""
                SELECT
                    i.UPC,
                    i.Name,
                    i.TotalQty,
                    COUNT(DISTINCT CASE
                        WHEN ib.BinUPC IS NOT NULL THEN 'B:' || CAST(ib.BinUPC AS TEXT)
                        WHEN ib.RoomID IS NOT NULL THEN 'R:' || CAST(ib.RoomID AS TEXT)
                    END) AS LocationCount,
                    GROUP_CONCAT(
                        DISTINCT CASE
                            WHEN COALESCE(r.RoomName, br.RoomName, rd.RoomName) IS NOT NULL THEN COALESCE(r.RoomName, br.RoomName, rd.RoomName)
                        END
                    ) AS Rooms,
                    GROUP_CONCAT(
                        DISTINCT CASE
                            WHEN r.RoomName IS NOT NULL AND w.WallName IS NOT NULL THEN r.RoomName || ' ' || w.WallName
                        END
                    ) AS WallNames,
                    GROUP_CONCAT(
                        DISTINCT CASE
                            WHEN ib.BinUPC IS NULL AND rd.RoomName IS NOT NULL THEN rd.RoomName
                            WHEN COALESCE(r.RoomName, br.RoomName) IS NOT NULL AND b.BinType IS NOT NULL AND b.BinID IS NOT NULL AND w.WallName IS NULL THEN
                                COALESCE(r.RoomName, br.RoomName) || ' ' || b.BinType || ' ' || b.BinID
                            WHEN r.RoomName IS NOT NULL AND w.WallName IS NOT NULL AND b.BinType IS NOT NULL AND b.BinID IS NOT NULL THEN
                                r.RoomName || ' ' || w.WallName || ' ' || b.BinType || ' ' || b.BinID
                        END
                    ) AS LocationDetails,
                    GROUP_CONCAT(
                        DISTINCT CASE
                            WHEN b.Coordinates IS NOT NULL AND TRIM(b.Coordinates) != '' THEN b.Coordinates
                        END
                    ) AS BinCoordinates,
                    MAX(datetime(ib.Date || ' ' || ib.Time)) AS LastChanged
                FROM items i
                LEFT JOIN item_bin ib ON ib.UPC = i.UPC
                LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
                LEFT JOIN walls w ON w.WallID = b.WallID
                LEFT JOIN rooms r ON r.RoomID = w.RoomID
                LEFT JOIN rooms br ON br.RoomID = b.RoomID
                LEFT JOIN rooms rd ON rd.RoomID = ib.RoomID
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
                    COUNT(DISTINCT CASE
                        WHEN ib.BinUPC IS NOT NULL THEN 'B:' || CAST(ib.BinUPC AS TEXT)
                        WHEN ib.RoomID IS NOT NULL THEN 'R:' || CAST(ib.RoomID AS TEXT)
                    END) AS LocationCount,
                    GROUP_CONCAT(
                        DISTINCT CASE
                            WHEN COALESCE(r.RoomName, br.RoomName, rd.RoomName) IS NOT NULL THEN COALESCE(r.RoomName, br.RoomName, rd.RoomName)
                        END
                    ) AS Rooms,
                    GROUP_CONCAT(
                        DISTINCT CASE
                            WHEN r.RoomName IS NOT NULL AND w.WallName IS NOT NULL THEN r.RoomName || ' ' || w.WallName
                        END
                    ) AS WallNames,
                    GROUP_CONCAT(
                        DISTINCT CASE
                            WHEN ib.BinUPC IS NULL AND rd.RoomName IS NOT NULL THEN rd.RoomName
                            WHEN COALESCE(r.RoomName, br.RoomName) IS NOT NULL AND b.BinType IS NOT NULL AND b.BinID IS NOT NULL AND w.WallName IS NULL THEN
                                COALESCE(r.RoomName, br.RoomName) || ' ' || b.BinType || ' ' || b.BinID
                            WHEN r.RoomName IS NOT NULL AND w.WallName IS NOT NULL AND b.BinType IS NOT NULL AND b.BinID IS NOT NULL THEN
                                r.RoomName || ' ' || w.WallName || ' ' || b.BinType || ' ' || b.BinID
                        END
                    ) AS LocationDetails,
                    GROUP_CONCAT(
                        DISTINCT CASE
                            WHEN b.Coordinates IS NOT NULL AND TRIM(b.Coordinates) != '' THEN b.Coordinates
                        END
                    ) AS BinCoordinates,
                    MAX(datetime(ib.Date || ' ' || ib.Time)) AS LastChanged
                FROM items i
                LEFT JOIN item_bin ib ON ib.UPC = i.UPC
                LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
                LEFT JOIN walls w ON w.WallID = b.WallID
                LEFT JOIN rooms r ON r.RoomID = w.RoomID
                LEFT JOIN rooms br ON br.RoomID = b.RoomID
                LEFT JOIN rooms rd ON rd.RoomID = ib.RoomID
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
        parsed_coordinates = parse_item_bin_coordinate_list(item.get("BinCoordinates"))
        primary_coordinate = parsed_coordinates[0] if parsed_coordinates else None
        item["PrimaryCoordinate"] = primary_coordinate["label"] if primary_coordinate else ""
        item["BinCoordinates"] = ", ".join(coord["label"] for coord in parsed_coordinates)

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
                        DISTINCT CASE
                            WHEN ib.BinUPC IS NULL AND rd.RoomName IS NOT NULL THEN rd.RoomName
                            WHEN r.RoomName IS NOT NULL THEN r.RoomName || ' ' || w.WallName || ' ' || b.BinType || ' ' || b.BinID
                        END
                    ) AS Locations
                FROM items i
                LEFT JOIN item_bin ib ON ib.UPC = i.UPC
                LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
                LEFT JOIN walls w ON w.WallID = b.WallID
                LEFT JOIN rooms r ON r.RoomID = w.RoomID
                LEFT JOIN rooms rd ON rd.RoomID = ib.RoomID
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
                        DISTINCT CASE
                            WHEN ib.BinUPC IS NULL AND rd.RoomName IS NOT NULL THEN rd.RoomName
                            WHEN r.RoomName IS NOT NULL THEN r.RoomName || ' ' || w.WallName || ' ' || b.BinType || ' ' || b.BinID
                        END
                    ) AS Locations
                FROM items i
                LEFT JOIN item_bin ib ON ib.UPC = i.UPC
                LEFT JOIN bins b ON b.BinUPC = ib.BinUPC
                LEFT JOIN walls w ON w.WallID = b.WallID
                LEFT JOIN rooms r ON r.RoomID = w.RoomID
                LEFT JOIN rooms rd ON rd.RoomID = ib.RoomID
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
        "Approve only if it clearly matches the item or a very close product-family match and looks like a clean product listing photo. "
        "Reject logos, icons, banners, website branding, screenshots, memes, diagrams, text-only graphics, unrelated objects, and pages where the product is not the main subject. "
        "Return JSON only with keys approved, confidence, reason."
    )

def is_blocked_image_candidate_url(image_url):
    lowered = (image_url or "").strip().lower()
    if not lowered:
        return True

    blocked_markers = (
        "storage.live.com/users/0x{0}/myprofile/expressionprofile/profilephoto",
        "schemas.live.com/web/",
        "profilephoto:usertile",
        "avatar",
        "gravatar",
    )
    return any(marker in lowered for marker in blocked_markers)

def is_low_value_image_candidate_url(image_url):
    lowered = (image_url or "").strip().lower()
    if not lowered:
        return True

    low_value_markers = (
        "favicon",
        "site-logo",
        "logo",
        "icon",
        "banner",
        "brandmark",
        "wordmark",
        "header",
        "footer",
        "nav",
        "sprite",
        "placeholder",
        "blank.gif",
        "1x1",
    )
    return any(marker in lowered for marker in low_value_markers)

def is_sane_public_http_url(candidate_url):
    candidate_url = (candidate_url or "").strip()
    if not candidate_url.startswith(("http://", "https://")):
        return False

    try:
        parsed = urlparse(candidate_url)
    except Exception:
        return False

    host = (parsed.netloc or "").lower()
    if not host or "{" in host or "}" in host:
        return False
    if "." not in host:
        return False
    if host.endswith("-") or host.startswith("-"):
        return False
    if re.fullmatch(r"[a-z]{24,}", host):
        return False

    return True

def validate_item_image_via_ai(item_name, image_url, image_bytes=None, content_type=None):
    client = get_image_agent_client()
    if client is None or (not image_url and not image_bytes):
        return None

    if content_type:
        normalized_type = content_type.split(";")[0].strip().lower()
        if normalized_type not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
            return None

    image_input = image_url
    if image_bytes and content_type:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        image_input = f"data:{content_type.split(';')[0]};base64,{encoded}"

    try:
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
    except Exception as exc:
        print(f"Image validator failed for {item_name}: {exc}")
        return None

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
    if is_blocked_image_candidate_url(image_url):
        return False, "Blocked non-product image URL"
    if is_low_value_image_candidate_url(image_url):
        return False, "Low-value image candidate URL"
    if not is_sane_public_http_url(image_url):
        return False, "Invalid candidate URL"

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
    if is_blocked_image_candidate_url(image_url):
        return None, None
    if is_low_value_image_candidate_url(image_url):
        return None, None
    if not is_sane_public_http_url(image_url):
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

    try:
        parsed = urlparse(page_url)
    except Exception:
        return []

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

    candidates = []
    seen = set()

    def add_candidate(raw_value):
        candidate = unescape((raw_value or "").strip())
        if not candidate:
            return

        if "," in candidate and " " in candidate:
            for srcset_part in candidate.split(","):
                add_candidate(srcset_part.strip().split(" ")[0])
            return

        if candidate.startswith("//"):
            candidate = f"{parsed.scheme or 'https'}:{candidate}"
        elif candidate.startswith("/"):
            candidate = urljoin(page_url, candidate)

        if not candidate.startswith("http"):
            return
        if is_blocked_image_candidate_url(candidate):
            return
        if is_low_value_image_candidate_url(candidate):
            return
        if not is_sane_public_http_url(candidate):
            return

        lowered = candidate.lower()
        if any(marker in lowered for marker in ("sprite", "spacer", "placeholder", "blank.gif", "1x1")):
            return

        if candidate in seen:
            return
        seen.add(candidate)
        candidates.append(candidate)

    patterns = [
        r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<img[^>]+src=["\']([^"\']+)["\']',
        r'<img[^>]+data-src=["\']([^"\']+)["\']',
        r'<img[^>]+data-lazy-src=["\']([^"\']+)["\']',
        r'<img[^>]+data-image=["\']([^"\']+)["\']',
        r'<img[^>]+srcset=["\']([^"\']+)["\']',
        r'"image"\s*:\s*"([^"]+)"',
        r'"image_url"\s*:\s*"([^"]+)"',
        r'"primaryImage"\s*:\s*"([^"]+)"',
        r'"largeImage"\s*:\s*"([^"]+)"',
        r'https?://[^"\'\s>]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\'\s>]*)?',
        r'//[^"\'\s>]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\'\s>]*)?',
        r'/[^"\'\s>]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\'\s>]*)?',
    ]

    for pattern in patterns:
        for match in re.findall(pattern, html, flags=re.I):
            if isinstance(match, tuple):
                for value in match:
                    add_candidate(value)
            else:
                add_candidate(match)

    return candidates

def build_item_search_tokens(item_name):
    cleaned = re.sub(r"[^a-z0-9]+", " ", (item_name or "").lower())
    return [token for token in cleaned.split() if len(token) >= 2]

def is_component_like_item(item_name):
    text = (item_name or "").lower()
    component_markers = (
        "resistor", "capacitor", "inductor", "diode", "transistor", "op amp", "ic",
        "integrated circuit", "breadboard", "jumper", "potentiometer", "relay",
        "mosfet", "led", "switch", "sensor", "connector", "header", "wire"
    )
    return any(marker in text for marker in component_markers)

def is_supply_like_item(item_name):
    text = (item_name or "").lower()
    supply_markers = ("label", "tape", "cassette", "sharpie", "marker", "bin", "drawer")
    return any(marker in text for marker in supply_markers)

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
    first_viable_result = None
    validations_used = 0
    has_image_agent = get_image_agent_client() is not None
    for candidate_url in rank_image_candidates(item_name, candidates):
        image_bytes = None
        content_type = None
        live_ok, _ = is_live_image_url(candidate_url)
        try:
            image_bytes, content_type = download_image_bytes(candidate_url, source_url)
            live_ok = bool(image_bytes and content_type and content_type.startswith("image/"))
        except Exception:
            live_ok = False
            image_bytes = None
            content_type = None

        if not live_ok:
            continue

        normalized_type = (content_type or "").split(";")[0].strip().lower()
        if source_url and first_viable_result is None:
            first_viable_result = {
                "image_url": candidate_url,
                "source_url": source_url or "",
                "note": "Used the first real downloadable product-page image candidate.",
                "validation": {
                    "approved": True,
                    "confidence": 0.25,
                    "reason": "Fallback acceptance: product-page candidate downloaded successfully as an image.",
                },
            }

        if normalized_type not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
            continue

        if validations_used >= IMAGE_AGENT_MAX_VALIDATIONS:
            continue

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

    if not has_image_agent:
        return first_viable_result

    return None

def find_first_shopping_page_image(item_name, excluded_hosts=None):
    retailer_source_urls = search_product_pages(
        item_name,
        retailer_only=True,
        excluded_hosts=excluded_hosts,
        limit=max(IMAGE_AGENT_PAGE_ATTEMPTS * 4, 10),
    )

    for source_url in retailer_source_urls:
        source_candidates = extract_image_candidates_from_page(source_url)
        if not source_candidates:
            continue

        for candidate_url in rank_image_candidates(item_name, source_candidates):
            live_ok, _ = is_live_image_url(candidate_url)
            if not live_ok:
                try:
                    image_bytes, content_type = download_image_bytes(candidate_url, source_url)
                    live_ok = bool(image_bytes and content_type and content_type.startswith("image/"))
                except Exception:
                    live_ok = False

            if live_ok:
                return {
                    "image_url": candidate_url,
                    "source_url": source_url,
                    "note": "Used the first live image from a retailer product page as a final fallback.",
                    "validation": {
                        "approved": True,
                        "confidence": 0.3,
                        "reason": "Final fallback: first live retailer product image used before failing.",
                    },
                }

    return None

def find_first_live_web_image(item_name, excluded_hosts=None):
    excluded_hosts = {host for host in (excluded_hosts or []) if host}
    web_image_candidates = search_image_candidates_from_web(
        item_name,
        limit=max(IMAGE_AGENT_MAX_VALIDATIONS * 4, 16),
    )

    for candidate_url in rank_image_candidates(item_name, web_image_candidates):
        host = get_url_host(candidate_url)
        if host in excluded_hosts:
            continue

        live_ok, _ = is_live_image_url(candidate_url)
        if not live_ok:
            try:
                image_bytes, content_type = download_image_bytes(candidate_url, "")
                live_ok = bool(image_bytes and content_type and content_type.startswith("image/"))
            except Exception:
                live_ok = False

        if live_ok:
            return {
                "image_url": candidate_url,
                "source_url": "",
                "note": "Used the first live direct web image result as the final fallback.",
                "validation": {
                    "approved": True,
                    "confidence": 0.2,
                    "reason": "Final fallback: first live direct web image used before failing.",
                },
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

    try:
        parsed = urlparse(url)
    except Exception:
        return True

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

    if "uline.com" in url_text and is_supply_like_item(item_name):
        score += 8

    component_hosts = ("digikey.", "mouser.", "newark.", "arrow.", "sparkfun.", "adafruit.", "rs-online.", "masterelectronics.")
    if any(host in url_text for host in component_hosts) and is_component_like_item(item_name):
        score += 10

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
        if is_supply_like_item(item_name):
            queries.extend([
                f'site:uline.com "{item_name}"',
                f'site:quill.com "{item_name}"',
            ])
        if is_component_like_item(item_name):
            queries.extend([
                f'site:digikey.com "{item_name}"',
                f'site:mouser.com "{item_name}"',
                f'site:newark.com "{item_name}"',
                f'site:arrow.com "{item_name}"',
                f'site:sparkfun.com "{item_name}"',
                f'site:adafruit.com "{item_name}"',
            ])
    else:
        queries.extend([
            f'"{item_name}" store',
            f'"{item_name}" shop',
        ])

    found_urls = []
    seen = set()

    def add_candidate_url(candidate):
        candidate = unescape((candidate or "").strip())
        if not candidate:
            return

        if "://" not in candidate and "%2f" in candidate.lower():
            candidate = unquote(candidate)

        if candidate.startswith("//"):
            candidate = "https:" + candidate

        if not candidate.startswith("http"):
            return
        if not is_sane_public_http_url(candidate):
            return

        host = get_url_host(candidate)
        if candidate in seen or host in excluded_hosts or is_unusable_source_page(candidate):
            return

        seen.add(candidate)
        found_urls.append(candidate)

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

        patterns = [
            r'href=["\'](https?://[^"\']+)["\']',
            r'"url"\s*:\s*"(https?:\\/\\/[^"]+)"',
            r'"contentUrl"\s*:\s*"(https?:\\/\\/[^"]+)"',
            r'((?:https?:)?//[^"\'\s<>]+)',
            r'(https?%3A%2F%2F[^"\'\s<>]+)',
        ]

        for pattern in patterns:
            for candidate in re.findall(pattern, html, flags=re.I):
                if isinstance(candidate, tuple):
                    for value in candidate:
                        add_candidate_url(value.replace("\\/", "/"))
                else:
                    add_candidate_url(candidate.replace("\\/", "/"))

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
                if is_blocked_image_candidate_url(candidate):
                    continue
                if not is_sane_public_http_url(candidate):
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

def find_item_image_via_ai(item_name, excluded_hosts=None):
    rejected_hosts = set(excluded_hosts or [])
    code_search_found_any_sources = False
    image_agent_available = get_image_agent_client() is not None
    image_ai_web_search_enabled = image_agent_available and IMAGE_AGENT_AI_WEB_SEARCH

    if image_ai_web_search_enabled:
        ai_attempts = max(IMAGE_AGENT_PAGE_ATTEMPTS + 1, 4)
        for retailer_only in (True, False):
            direct_feedback = ""
            page_feedback = ""
            for _ in range(ai_attempts):
                direct_result = find_direct_image_via_ai(
                    item_name,
                    feedback=direct_feedback,
                    excluded_hosts=rejected_hosts,
                )
                if direct_result and direct_result.get("image_url"):
                    resolved = try_image_candidates(
                        item_name,
                        direct_result.get("source_url", ""),
                        [direct_result["image_url"]],
                    )
                    if resolved:
                        resolved["note"] = direct_result.get("note", "") or "Resolved from AI direct image search."
                        return resolved
                    direct_feedback = (
                        "That image was wrong, low quality, or not a usable product image. "
                        "Return a different direct image URL from a retailer or distributor product listing."
                    )

                page_result = find_product_page_via_ai(
                    item_name,
                    retailer_only=retailer_only,
                    feedback=page_feedback,
                    excluded_hosts=rejected_hosts,
                )
                source_url = (page_result or {}).get("source_url", "")
                if source_url:
                    source_candidates = extract_image_candidates_from_page(source_url)
                    if source_candidates:
                        resolved = try_image_candidates(item_name, source_url, source_candidates)
                        if resolved:
                            resolved["note"] = (
                                page_result.get("note", "") if page_result else resolved.get("note", "")
                            ) or "Resolved from AI product page search."
                            return resolved
                    rejected_hosts.add(get_url_host(source_url))

                page_feedback = (
                    "The previous page was wrong or did not expose a usable product image. "
                    "Return a different retailer, distributor, or storefront product page with a visible product image."
                )

    web_image_candidates = search_image_candidates_from_web(
        item_name,
        limit=max(IMAGE_AGENT_MAX_VALIDATIONS * 3, 8),
    )
    web_image_candidates = [
        candidate for candidate in web_image_candidates
        if get_url_host(candidate) not in rejected_hosts
    ]
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

        should_use_ai_fallback = (
            image_ai_web_search_enabled and
            (IMAGE_AGENT_AI_SEARCH_FALLBACK or not code_search_found_any_sources)
        )
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

    final_fallback = find_first_shopping_page_image(item_name, excluded_hosts=rejected_hosts)
    if final_fallback:
        return final_fallback

    direct_web_fallback = find_first_live_web_image(item_name, excluded_hosts=rejected_hosts)
    if direct_web_fallback:
        return direct_web_fallback

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

def queue_item_image_lookup(item_name, trigger="new_item_create", force=False, excluded_hosts=None):
    item_name = (item_name or "").strip()
    if not item_name:
        return

    if not force:
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
    clear_item_image_cancel(item_name)

    def worker():
        try:
            had_openai_client = get_image_agent_client() is not None
            result = find_item_image_via_ai(item_name, excluded_hosts=excluded_hosts)
            if is_item_image_lookup_cancelled(item_name):
                return
            if result and result.get("image_url"):
                store_item_image_result(item_name, result, trigger=trigger)
            else:
                current_map = load_item_image_map()
                _, existing_image_url = find_item_image_map_match(current_map, item_name)
                if existing_image_url:
                    set_item_image_status(
                        item_name,
                        IMAGE_STATUS_SUCCESS,
                        trigger=trigger,
                        image_url=existing_image_url,
                        note="Could not find a replacement image. Kept the current saved image.",
                    )
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
            if is_item_image_lookup_cancelled(item_name):
                return
            current_map = load_item_image_map()
            _, existing_image_url = find_item_image_map_match(current_map, item_name)
            if existing_image_url:
                set_item_image_status(
                    item_name,
                    IMAGE_STATUS_SUCCESS,
                    trigger=trigger,
                    image_url=existing_image_url,
                    note="Image regeneration failed unexpectedly. Kept the current saved image.",
                )
            else:
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
    return render_template('sign_in.html', showcase_items=load_sign_in_showcase_items())

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
    if not is_signed_in():
        return redirect(url_for('sign_in_page'))
    if not session.get("database_authenticated"):
        return render_template(
            'db.html',
        entries=[],
        bins=[],
        user_tracking=[],
        checkout_history=[],
        prompt_database_auth=True,
        current_tracked_session_id=session.get("tracked_session_id"),
    )
    entries = load_database_entries()
    bin_rows = load_bins_directory()
    user_tracking = load_user_tracking()
    checkout_history = load_checkout_history()
    return render_template(
        'db.html',
        entries=entries,
        bins=bin_rows,
        user_tracking=user_tracking,
        checkout_history=checkout_history,
        prompt_database_auth=False,
        current_tracked_session_id=session.get("tracked_session_id"),
    )

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
    return render_template(
        'floorplan.html',
        game_state=game_state,
        current_item=current_item,
        bin_markers=load_floorplan_bin_markers(),
        bin_coord_columns=BIN_COORD_COLUMNS,
        bin_coord_rows=BIN_COORD_ROWS,
    )

@app.route('/checkout-request', methods=['GET'])
def checkout_request():
    return render_template(
        'checkout_request.html',
        checkout_items=load_checkout_request_items(session.get("signed_in_email", "")),
    )

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

@app.route('/end-user-session', methods=['POST'])
def end_user_session():
    if not is_signed_in():
        return jsonify({"error": "Sign-in required", "redirect": url_for("sign_in_page")}), 401
    if not session.get("database_authenticated"):
        return jsonify({"error": "Database access required"}), 403

    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Session ID must be numeric"}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("""
            SELECT SessionID, Email, SignOutDate, SignOutTime
            FROM user_sessions
            WHERE SessionID = ?
        """, (session_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Session not found"}), 404
        if row["SignOutDate"] is not None and row["SignOutTime"] is not None:
            return jsonify({"error": "Session is already ended"}), 400
    finally:
        db.close()

    log_user_sign_out(session_id)

    is_current_user_session = session_id == session.get("tracked_session_id")
    if is_current_user_session:
        session.clear()
        return jsonify({
            "success": True,
            "ended_current_session": True,
            "redirect": url_for("sign_in_page"),
        }), 200

    return jsonify({
        "success": True,
        "ended_current_session": False,
        "session_id": session_id,
    }), 200

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
    room_only_location = storage_type == 'None' and not str(bin_number or '').strip()

    if (
        not entry_id
        or not name
        or qty in (None, '')
        or not room
        or not storage_type
        or (not room_only_location and (not wall or not bin_number))
    ):
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

        room_id = functions.roomIDDecider(room)
        if not room_id:
            return jsonify({"error": "Invalid room"}), 400

        if room_only_location:
            bin_upc = None
        else:
            if storage_type == 'None' and not wall:
                cursor.execute(
                    "SELECT BinUPC FROM bins WHERE BinType = ? AND BinID = ? AND RoomID = ?",
                    (storage_type, bin_number, room_id)
                )
            else:
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
            "UPDATE item_bin SET Name = ?, Qty = ?, BinUPC = ?, RoomID = ?, Date = ?, Time = ? WHERE EntryID = ?",
            (name, qty, bin_upc, room_id, current_date, current_time, entry_id)
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

@app.route('/print-item-identifier-label', methods=['POST'])
def print_item_identifier_label():
    data = request.get_json(silent=True) or {}
    identifier_id = data.get('identifier_id')

    if not identifier_id:
        return jsonify({"error": "Missing identifier ID"}), 400

    try:
        identifier_id = int(identifier_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Identifier ID must be numeric"}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute(
            "SELECT UnitIdentifier FROM item_identifiers WHERE IdentifierID = ?",
            (identifier_id,)
        )
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Identifier not found"}), 404
        unit_identifier = row["UnitIdentifier"]
    finally:
        db.close()

    try:
        functions.printItemUPC(unit_identifier)
    except Exception as exc:
        print("An error occurred while printing item identifier label:", exc)
        return jsonify({"error": "Failed to print item identifier label"}), 500

    return jsonify({"success": True}), 200

@app.route('/checkout-request-item', methods=['POST'])
def checkout_request_item():
    data = request.get_json(silent=True) or {}
    identifier_id = data.get('identifier_id')
    uri_id = data.get('uri_id', '')
    first_name = data.get('first_name', '')
    last_name = data.get('last_name', '')

    if not identifier_id:
        return jsonify({"error": "Missing identifier ID"}), 400

    try:
        identifier_id = int(identifier_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Identifier ID must be numeric"}), 400

    email = (session.get("signed_in_email") or "").strip().lower()
    if not email:
        return jsonify({"error": "You must be signed in to request a checkout."}), 401

    tracked_session_id = session.get("tracked_session_id")
    if not tracked_session_id:
        tracked_session_id = log_user_sign_in(email)
        if tracked_session_id:
            session["tracked_session_id"] = tracked_session_id

    result, error = create_checkout_request(identifier_id, email, tracked_session_id, uri_id, first_name, last_name)
    if error:
        status_code = 409 if result else 400
        return jsonify({
            "error": error,
            "identifier": result or {},
        }), status_code

    return jsonify({
        "success": True,
        "identifier": result,
    }), 200

@app.route('/return-checkout-item', methods=['POST'])
def return_checkout_item():
    data = request.get_json(silent=True) or {}
    identifier_id = data.get('identifier_id')

    if not identifier_id:
        return jsonify({"error": "Missing identifier ID"}), 400

    try:
        identifier_id = int(identifier_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Identifier ID must be numeric"}), 400

    email = (session.get("signed_in_email") or "").strip().lower()
    if not email:
        return jsonify({"error": "You must be signed in to return a checkout."}), 401

    result, error = return_checkout_request(identifier_id, email)
    if error:
        status_code = 403 if result and result.get("CheckedOutBy") else 400
        return jsonify({
            "error": error,
            "identifier": result or {},
        }), status_code

    return jsonify({
        "success": True,
        "identifier": result,
    }), 200

@app.route('/item-identifiers/<int:upc>', methods=['GET'])
def get_item_identifiers(upc):
    summary = load_item_identifier_summary(upc)
    if not summary:
        return jsonify({"error": "Item not found"}), 404

    return jsonify({
        "item": summary,
        "identifiers": load_item_identifiers(upc),
    }), 200

@app.route('/create-item-identifiers', methods=['POST'])
def create_item_identifiers_route():
    data = request.get_json(silent=True) or {}
    upc = data.get('upc')

    if not upc:
        return jsonify({"error": "Missing UPC"}), 400

    try:
        upc = int(upc)
    except (TypeError, ValueError):
        return jsonify({"error": "UPC must be numeric"}), 400

    created, error = create_item_identifiers(upc)
    if error:
        return jsonify({"error": error}), 400

    summary = load_item_identifier_summary(upc)
    return jsonify({
        "success": True,
        "created_count": len(created or []),
        "item": summary,
        "identifiers": load_item_identifiers(upc),
    }), 200

@app.route('/delete-item-identifier', methods=['POST'])
def delete_item_identifier_route():
    data = request.get_json(silent=True) or {}
    identifier_id = data.get('identifier_id')

    if not identifier_id:
        return jsonify({"error": "Missing identifier ID"}), 400

    try:
        identifier_id = int(identifier_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Identifier ID must be numeric"}), 400

    db = get_db()
    cursor = db.cursor()
    deleted_upc = None
    try:
        cursor.execute("SELECT UPC FROM item_identifiers WHERE IdentifierID = ?", (identifier_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Identifier not found"}), 404

        deleted_upc = row["UPC"]
        cursor.execute("DELETE FROM item_identifiers WHERE IdentifierID = ?", (identifier_id,))
        db.commit()
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while deleting item identifier:", e.args[0])
        return jsonify({"error": "Failed to delete item identifier"}), 500
    finally:
        db.close()

    summary = load_item_identifier_summary(deleted_upc)
    return jsonify({
        "success": True,
        "item": summary,
        "identifiers": load_item_identifiers(deleted_upc),
    }), 200

@app.route('/update-item-identifier-checkout', methods=['POST'])
def update_item_identifier_checkout_route():
    data = request.get_json(silent=True) or {}
    identifier_id = data.get('identifier_id')
    can_check_out = data.get('can_check_out')

    if identifier_id in (None, ''):
        return jsonify({"error": "Missing identifier ID"}), 400

    try:
        identifier_id = int(identifier_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Identifier ID must be numeric"}), 400

    normalized_checkout_flag = 1 if bool(can_check_out) else 0

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("""
            SELECT
                ii.UPC,
                CASE WHEN c.IdentifierID IS NOT NULL THEN 1 ELSE 0 END AS IsCheckedOut
            FROM item_identifiers ii
            LEFT JOIN checkout_log c ON c.IdentifierID = ii.IdentifierID AND c.IsActive = 1
            WHERE ii.IdentifierID = ?
        """, (identifier_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Identifier not found"}), 404

        if not normalized_checkout_flag and int(row["IsCheckedOut"] or 0):
            return jsonify({"error": "Return this identifier before disabling checkout."}), 409

        cursor.execute(
            "UPDATE item_identifiers SET CanCheckOut = ? WHERE IdentifierID = ?",
            (normalized_checkout_flag, identifier_id)
        )
        db.commit()
        updated_upc = row["UPC"]
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while updating identifier checkout flag:", e.args[0])
        return jsonify({"error": "Failed to update checkout setting"}), 500
    finally:
        db.close()

    summary = load_item_identifier_summary(updated_upc)
    return jsonify({
        "success": True,
        "item": summary,
        "identifiers": load_item_identifiers(updated_upc),
    }), 200

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
        if bin_row["BinType"] == "None":
            return jsonify({"error": "None bins do not have printable labels"}), 400
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
    quantity = data.get('quantity', 1)
    coordinates = normalize_bin_coordinates(data.get('coordinates', ''))

    room_level_none_bin = storage_type == "None" and not wall

    if not room or not storage_type or (not room_level_none_bin and not wall):
        return jsonify({"error": "Missing required fields"}), 400

    valid_rooms = {"110", "110A", "110B", "110C"}
    valid_walls = {"North", "South", "East", "West"}
    valid_storage_types = {"Bin", "Shelf", "Drawer", "Cabinet", "Tabletop", "Overhead", "Other", "None"}

    if room not in valid_rooms or storage_type not in valid_storage_types or (wall and wall not in valid_walls):
        return jsonify({"error": "Invalid bin details"}), 400

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return jsonify({"error": "Quantity must be a whole number"}), 400

    if quantity < 1 or quantity > 100:
        return jsonify({"error": "Quantity must be between 1 and 100"}), 400
    if data.get('coordinates') and coordinates is None:
        return jsonify({"error": "Coordinates must use A1 through AV36 format."}), 400
    if coordinates and quantity != 1:
        return jsonify({"error": "Coordinates can only be set during single-bin creation."}), 400

    try:
        created_pairs = functions.createBins(storage_type, wall, room, quantity=quantity, coordinates=coordinates or None)
    except Exception as exc:
        print("An error occurred while creating bin:", exc)
        return jsonify({"error": "Failed to create bin"}), 500

    created_bins = [load_bin_row(bin_upc) for _, bin_upc in created_pairs]
    created_bins = [bin_row for bin_row in created_bins if bin_row]
    if len(created_bins) != len(created_pairs):
        return jsonify({"error": "Bin creation did not complete successfully"}), 500

    first_bin_id, first_bin_upc = created_pairs[0]
    return jsonify({
        "success": True,
        "bin": created_bins[0],
        "bin_id": first_bin_id,
        "bin_upc": first_bin_upc,
        "bins": created_bins,
        "quantity": len(created_bins),
    }), 200

@app.route('/update-bin-coordinates', methods=['POST'])
def update_bin_coordinates():
    data = request.get_json(silent=True) or {}
    bin_upc = data.get('bin_upc')
    normalized_coordinates = normalize_bin_coordinates(data.get('coordinates', ''))

    if not bin_upc:
        return jsonify({"error": "Missing bin identifier"}), 400

    try:
        bin_upc = int(bin_upc)
    except (TypeError, ValueError):
        return jsonify({"error": "Bin identifier must be numeric"}), 400

    if data.get('coordinates') and normalized_coordinates is None:
        return jsonify({"error": "Coordinates must use A1 through AV36 format."}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT BinUPC FROM bins WHERE BinUPC = ?", (bin_upc,))
        existing_bin = cursor.fetchone()
        if not existing_bin:
            return jsonify({"error": "Bin not found"}), 404

        cursor.execute(
            "UPDATE bins SET Coordinates = ? WHERE BinUPC = ?",
            (normalized_coordinates or None, bin_upc)
        )
        db.commit()
    except sqlite3.Error as e:
        db.rollback()
        print("An error occurred while updating bin coordinates:", e.args[0])
        return jsonify({"error": "Failed to update bin coordinates"}), 500
    finally:
        db.close()

    updated_bin = load_bin_row(bin_upc)
    return jsonify({"success": True, "bin": updated_bin}), 200

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
        room_only_location = storage_type == 'None' and not str(bin_number or '').strip()
        # if any of the required fields are missing, return an error
        if (
            not room
            or not storage_type
            or not item_name
            or not quantity
            or (not room_only_location and (not wall or not bin_number))
        ):
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

    response = jsonify({
        "item_name": normalized_name,
        "status": get_item_image_status(normalized_name),
        "image_url": get_item_image_url(normalized_name),
    })
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response, 200

@app.route('/retry-item-image', methods=['POST'])
def retry_item_image():
    data = request.get_json(silent=True) or {}
    item_name = data.get('item_name', '')
    normalized_name = normalize_item_image_key(item_name)
    if not normalized_name:
        return jsonify({"error": "Missing item name"}), 400

    current_status = get_item_image_status(normalized_name)
    if current_status == IMAGE_STATUS_PENDING:
        return jsonify({"success": True, "status": IMAGE_STATUS_PENDING}), 200

    metadata_map = load_item_image_metadata()
    _, metadata = find_item_image_metadata_match(metadata_map, normalized_name)
    excluded_hosts = []
    if isinstance(metadata, dict):
        source_url = (metadata.get("source_url") or "").strip()
        if source_url:
            source_host = get_url_host(source_url)
            if source_host:
                excluded_hosts.append(source_host)

    remove_exact_item_image_state(normalized_name)

    queue_item_image_lookup(
        normalized_name,
        trigger="manual_retry",
        force=True,
        excluded_hosts=excluded_hosts,
    )
    return jsonify({"success": True, "status": IMAGE_STATUS_PENDING}), 200

@app.route('/cancel-item-image', methods=['POST'])
def cancel_item_image():
    data = request.get_json(silent=True) or {}
    item_name = data.get('item_name', '')
    normalized_name = normalize_item_image_key(item_name)
    if not normalized_name:
        return jsonify({"error": "Missing item name"}), 400

    cancel_item_image_lookup(normalized_name)
    set_item_image_status(
        normalized_name,
        IMAGE_STATUS_FAILED,
        trigger="manual_cancel",
        note="Image search canceled.",
    )
    return jsonify({"success": True, "status": IMAGE_STATUS_FAILED}), 200

def query_bins_from_database(room, wall, storage_type):
    if not room:
        return []
    wall_id = functions.wallDecider(wall, room) if wall else None
    room_id = functions.roomIDDecider(room)
    theList = functions.returnBinList(wall_id, storage_type, room_id if storage_type == "None" and not wall else None)
    print(theList)
    return theList


if __name__ == '__main__':
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.run(host='0.0.0.0', port=5000, debug=True)
