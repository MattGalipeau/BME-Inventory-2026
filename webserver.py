import os
import sqlite3
import re
import json
import threading
import importlib
import hashlib
import mimetypes
import base64
import socket
import urllib.request
import urllib.error
from html import unescape
from urllib.parse import urlparse
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import functions

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
EDITOR_PASSWORD = "BMETech"
HELP_BOT_MODEL = os.getenv("HELP_BOT_MODEL", "gpt-4.1-mini")
IMAGE_AGENT_MODEL = os.getenv("IMAGE_AGENT_MODEL", "gpt-4.1-mini")
ITEM_IMAGES_FILE = "item_images.json"
ITEM_IMAGE_METADATA_FILE = "item_image_metadata.json"
ITEM_IMAGE_CACHE_DIR = os.path.join("static", "item-images")
_help_bot_client = None
_item_images_lock = threading.Lock()
DEFAULT_ITEM_IMAGE_URLS = {
    "Glowforge Plus": "https://shop.glowforge.com/cdn/shop/files/GF_Plus-HD-angle_1.png?v=1716220130&width=1445",
    "Bambu P1S": "https://store.bblcdn.com/s7/default/7f6a9319e420463baa7281eb4e26622a/2_bb5ca5ee-11f8-466c-8c39-ace73c014be3.jpg",
    "Microscope": "https://www.adorama.com/images/Large/cnmsls20.jpg",
    "Prusa MK3": "https://www.printedsolid.com/cdn/shop/products/3325.jpg?v=1644874414&width=1946",
    "Prusa MK3S": "https://www.printedsolid.com/cdn/shop/products/3325.jpg?v=1644874414&width=1946",
    "Raise3D Pro2": "https://img.matterhackers.com/g/Z3M6Ly9taC1wcm9kdWN0LWltYWdlcy9wcm9kLzQ1NjEzMDQxLTI0ZjUtNDEwOS1hOWZmLTcwZmJmMzRlNzhmMA=w1200-h630-c0xffffff-rj-pd",
    "SUNLU FilaDryer S4": "https://cdn03.plentymarkets.com/ioseuwg7moqp/item/images/30436/full/SUNLU-FilaDryer-S4-30436.jpg",
    "Othermill Desktop CNC": "https://cdn-shop.adafruit.com/970x728/2323-01.jpg",
    "Voltera V-One": "https://www.voltera.io/images/vone/vone_frontView.webp",
    "995D+ Solder Station": "https://www.millevolt.it/pimages/FCKeditorFiles/Image/yihua-995d-1.jpg",
}

def get_db():
    """Open a new database connection."""
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row  # This make the DB return rows as dictionaries
    return db

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

    return entries

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
        return dict(DEFAULT_ITEM_IMAGE_URLS)

    try:
        with open(ITEM_IMAGES_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            if isinstance(data, dict):
                return {**DEFAULT_ITEM_IMAGE_URLS, **data}
    except (OSError, json.JSONDecodeError) as exc:
        print("Could not load item image map:", exc)

    return dict(DEFAULT_ITEM_IMAGE_URLS)

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

def save_item_image_metadata(metadata_map):
    payload = dict(metadata_map)
    try:
        with open(ITEM_IMAGE_METADATA_FILE, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, sort_keys=True)
    except OSError as exc:
        print("Could not save item image metadata:", exc)

def get_item_image_url(item_name):
    image_map = load_item_image_map()
    return image_map.get(item_name) or build_item_thumbnail_data_uri(item_name)

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

def build_help_bot_reply(message):
    text = (message or "").strip()
    if not text:
        return "I can help find items, rooms, walls, storage types, and bin locations in the inventory."

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
                return (
                    f"I could not find any logged {label.lower()} in the inventory. "
                    f"The closest related equipment I found is {fallback_label.lower()}: "
                    + "; ".join(fallback_summaries) + "."
                )
            return f"I could not find any logged {label.lower()} in the inventory."
        summaries = [
            f"{item['Name']} ({item['Locations'] or 'location unknown'})"
            for item in matches
        ]
        return f"The {label.lower()} I found are: " + "; ".join(summaries) + "."

    if "3d printers" in lowered or "3d printer" in lowered:
        printer_keywords = ("prusa", "bambu", "raise3d")
        printers = [item for item in catalog if any(keyword in item["Name"].lower() for keyword in printer_keywords)]
        if printers:
            printer_summaries = [
                f"{item['Name']} ({item['Locations'] or 'location unknown'})"
                for item in printers
            ]
            return "The logged 3D printers I found are: " + "; ".join(printer_summaries) + "."

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
            return ". ".join(parts) + "."

    if any(word in lowered for word in ["hello", "hi", "hey", "help"]) and len(lowered.split()) <= 6:
        return (
            "I can help you find where an item is stored, check what is in a room, or summarize item quantities. "
            "Try asking something like 'Where is Microscope?' or 'What is in room 110A?'"
        )

    if room_name and any(phrase in lowered for phrase in ["what is in", "what's in", "show", "list", "items in", "in room"]):
        room_items = load_room_inventory(room_name)
        if not room_items:
            return f"I could not find any logged items in room {room_name}."

        preview = []
        for item in room_items[:8]:
            preview.append(f"{item['Name']} ({item['WallName']} {item['BinType']} {item['BinID']})")

        extra_count = max(0, len(room_items) - len(preview))
        suffix = f" There are {extra_count} more item entries in that room." if extra_count else ""
        return f"Room {room_name} currently has: " + "; ".join(preview) + "." + suffix

    item_query = extract_item_query(text)
    if room_name and not item_query:
        room_items = load_room_inventory(room_name)
        if not room_items:
            return f"I could not find any logged items in room {room_name}."
        return f"Room {room_name} has {len(room_items)} logged item entries. Ask me to list the items in that room if you want the details."

    matches = find_item_records(item_query or text)
    if not matches:
        return "I could not find a matching item. Try the exact item name, part of the name, a UPC, or ask about a room like 110A."

    if len(matches) > 1 and (item_query or text) and not (item_query or text).isdigit():
        names = [f"{item['Name']} (UPC {item['UPC']})" for item in matches[:5]]
        return "I found multiple possible matches: " + "; ".join(names) + ". Tell me which one you want and I can give its location."

    item = matches[0]
    if any(phrase in lowered for phrase in ["how many", "quantity", "qty", "count"]):
        return f"{item['Name']} has a total logged quantity of {item['TotalQty']}."

    if item.get("Locations"):
        return f"{item['Name']} (UPC {item['UPC']}) has total quantity {item['TotalQty']} and is located at {item['Locations']}."

    return f"{item['Name']} (UPC {item['UPC']}) has total quantity {item['TotalQty']}, but I could not find a logged location for it."

def get_help_bot_related_cards(message):
    text = (message or "").strip()
    if not text:
        return []

    room_name = extract_room_name(text)
    catalog = load_help_inventory_snapshot()
    intent_matches = get_inventory_intent_matches(text, catalog)
    upcs = []

    if intent_matches:
        label, matches, _, fallback_matches = intent_matches[0]
        chosen_matches = matches or fallback_matches
        upcs = [item["UPC"] for item in chosen_matches if item.get("UPC")]
    elif room_name and any(phrase in text.lower() for phrase in ["what is in", "what's in", "show", "list", "items in", "in room"]):
        room_items = load_room_inventory(room_name)
        upcs = [item["UPC"] for item in room_items if item.get("UPC")]
    else:
        item_query = extract_item_query(text)
        matches = find_item_records(item_query or text)
        upcs = [item["UPC"] for item in matches if item.get("UPC")]

    if not upcs:
        return []

    unique_upcs = []
    seen = set()
    for upc in upcs:
        if upc in seen:
            continue
        seen.add(upc)
        unique_upcs.append(upc)

    return load_search_cards(upcs=unique_upcs)

def get_help_bot_client():
    global _help_bot_client

    if _help_bot_client is not None:
        return _help_bot_client

    if not os.getenv("OPENAI_API_KEY"):
        return None

    try:
        openai_module = importlib.import_module("openai")
        _help_bot_client = openai_module.OpenAI()
    except ImportError:
        return None
    except Exception as exc:
        print("Could not initialize help bot client:", exc)
        _help_bot_client = None

    return _help_bot_client

def get_image_agent_client():
    return get_help_bot_client()

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

def build_help_bot_prompt(message, history):
    inventory_rows = load_help_inventory_snapshot()
    inventory_context = "\n".join(
        f"- {item['Name']} | UPC {item['UPC']} | Qty {item['TotalQty']} | Locations: {item['Locations'] or 'Unknown'}"
        for item in inventory_rows
    )

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

Recent conversation:
{history_text}

    Current user message:
    {message}
""".strip()

def build_image_agent_instructions():
    return (
        "You are the inventory image agent for a lab inventory system. "
        "Your job is to find the best product image for a newly created or existing inventory item. "
        "Prefer official manufacturer or company product pages first, and otherwise use a retailer product page. "
        "Choose a stock-style product image with a clean white or plain background whenever possible. "
        "Prefer images that show the product by itself, centered, clearly lit, and suitable for a catalog or storefront. "
        "Return a direct image file URL, not an HTML product page URL. "
        "Do not use support pages, help pages, spec sheets, manuals, documentation pages, or PDF files as the source page. "
        "Do not use Wikimedia, Flickr, maker wikis, forum posts, or editorial/news photos. "
        "Do not use blocked or gated image hosts if a cleaner retailer or manufacturer image is available. "
        "If the item is generic, choose a simple retailer stock-style image with a white background. "
        "Return JSON only with keys image_url, source_url, and note. "
        "Do not return markdown fences or extra text."
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

def build_image_validation_instructions():
    return (
        "You verify whether a candidate image is a good product image for a named inventory item in a lab inventory system. "
        "Approve only if the image likely depicts the named item or a very close product-family match, "
        "and the image looks like a clean retailer/manufacturer product photo rather than a lifestyle, news, or workshop scene. "
        "Prefer plain or white backgrounds and centered product-style shots. "
        "Reject images that are blocked, generic when the item is specific, or clearly not the item. "
        "Return JSON only with keys approved, confidence, and reason. "
        "Do not return markdown fences or extra text."
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
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 404, 429}:
            return []
        print(f"Could not inspect image source page {page_url}: HTTP {exc.code}")
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

def try_image_candidates(item_name, source_url, candidates):
    for candidate_url in candidates:
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

        validation = validate_item_image_via_ai(
            item_name,
            candidate_url,
            image_bytes=image_bytes,
            content_type=content_type,
        )
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
            "You find a single best product page for an inventory item. "
            "Prefer pages that are likely to expose accessible product images that can be hotlinked directly. "
            "Do not use support pages, PDF spec sheets, manuals, or documentation pages. "
            "Return JSON only with keys source_url and note."
        ),
        tools=[{"type": "web_search"}],
        input=prompt,
    )

    parsed = parse_json_object_from_text(response.output_text)
    if not parsed:
        return None

    return {
        "source_url": parsed.get("source_url", ""),
        "note": parsed.get("note", ""),
    }

def find_item_image_via_ai(item_name):
    client = get_image_agent_client()
    if client is None:
        return None

    feedback = ""
    rejected_hosts = set()
    for attempt in range(3):
        excluded_clause = ""
        if rejected_hosts:
            excluded_clause = "Avoid these blocked or unreliable hosts: " + ", ".join(sorted(rejected_hosts)) + ". "
        prompt = (
            f'Find the best product image URL for the inventory item {json.dumps(item_name)}. '
            "Use web search. Prefer an official company/manufacturer website. "
            "If no official image is available, use the cleanest stable stock-style image you can find from a retailer. "
            "The image should ideally look like a marketing/product listing image on a plain or white background. "
            f"{excluded_clause}"
            f"This is attempt {attempt + 1} of 3, so if earlier choices were questionable, try a different source. "
            f"{feedback}"
        )

        response = client.responses.create(
            model=IMAGE_AGENT_MODEL,
            instructions=build_image_agent_instructions(),
            tools=[{"type": "web_search"}],
            input=prompt,
        )

        parsed = parse_json_object_from_text(response.output_text)
        if not parsed:
            print(f"Image agent returned non-JSON for {item_name}: {response.output_text}")
            continue

        image_url = parsed.get("image_url")
        source_url = parsed.get("source_url", "")
        if source_url:
            rejected_hosts.add(get_url_host(source_url)) if not extract_image_candidates_from_page(source_url) else None
        if not image_url:
            source_candidates = extract_image_candidates_from_page(source_url)
            resolved = try_image_candidates(item_name, source_url, source_candidates)
            if resolved:
                return resolved

            feedback = "The previous response did not include a usable direct image URL."
            continue

        live_ok, live_reason = is_live_image_url(image_url)
        if not live_ok:
            rejected_hosts.add(get_url_host(image_url))
            rejected_hosts.add(get_url_host(source_url))
            source_candidates = extract_image_candidates_from_page(source_url)
            resolved = try_image_candidates(item_name, source_url, source_candidates)
            if resolved:
                return resolved

            feedback = (
                f"The previous candidate URL failed because: {live_reason}. "
                "Return a different direct image URL that resolves successfully."
            )
            continue

        validation = validate_item_image_via_ai(item_name, image_url)
        if validation and validation.get("approved"):
            return {
                "image_url": image_url,
                "source_url": source_url,
                "note": parsed.get("note", ""),
                "validation": validation,
            }

        source_candidates = extract_image_candidates_from_page(source_url)
        resolved = try_image_candidates(item_name, source_url, source_candidates)
        if resolved:
            return resolved

        feedback = (
            f"The previous candidate was rejected because: "
            f"{validation.get('reason') if validation else 'image validation failed'}. "
            "Return a different direct image URL."
        )
        print(
            f"Image candidate rejected for {item_name}: "
            f"{validation.get('reason') if validation else 'validation failed'}"
        )

    for retailer_only in (False, True):
        page_result = find_product_page_via_ai(
            item_name,
            retailer_only=retailer_only,
            feedback="The earlier direct image lookups failed, so return a product page whose image assets are likely to be directly accessible.",
            excluded_hosts=rejected_hosts,
        )
        source_url = (page_result or {}).get("source_url", "")
        source_candidates = extract_image_candidates_from_page(source_url)
        if not source_candidates and source_url:
            rejected_hosts.add(get_url_host(source_url))
        resolved = try_image_candidates(item_name, source_url, source_candidates)
        if resolved:
            resolved["note"] = (
                page_result.get("note", "") if page_result else resolved.get("note", "")
            ) or "Resolved from product page fallback."
            return resolved

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

    def worker():
        try:
            result = find_item_image_via_ai(item_name)
            if result and result.get("image_url"):
                store_item_image_result(item_name, result, trigger=trigger)
        except Exception as exc:
            print(f"Image lookup failed for {item_name}: {exc}")

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
        "You are BME Inventory Help, a concise support chatbot for a lab inventory website. "
        "You can use web search for general educational guidance, such as explaining what equipment is typically needed for a task. "
        "You must treat the provided inventory data as the only source of truth for what this lab actually has, where it is located, and how many are logged. "
        "Never invent inventory items, quantities, or locations that are not present in the provided data. "
        "If the user asks what they need for a task, explain the typical needs briefly and then map those needs to the lab items that actually fit, if any. "
        "If the user asks generally about a type of machine or workflow, infer the likely category and return the matching inventory items even when the exact item names were not mentioned. "
        "Examples of this behavior include mapping PCB making or PCB machines to relevant PCB equipment in the inventory, and mapping 3D printing to the printers and supporting equipment in the inventory. "
        "If the inventory does not contain a needed item, say that clearly. "
        "When citing web-derived guidance, keep the answer concise and factual. "
        "Do not claim to change data, edit data, reserve equipment, or perform actions."
    )

def generate_help_bot_response(message, history):
    client = get_help_bot_client()
    related_cards = get_help_bot_related_cards(message)
    if client is None:
        return {
            "reply": build_help_bot_reply(message),
            "sources": [],
            "mode": "local",
            "items": related_cards,
        }

    try:
        prompt = build_help_bot_prompt(message, history)
        response = client.responses.create(
            model=HELP_BOT_MODEL,
            instructions=build_help_bot_instructions(),
            tools=[{"type": "web_search"}],
            input=prompt,
        )
    except Exception as exc:
        print("Help bot OpenAI call failed, using local fallback:", exc)
        return {
            "reply": build_help_bot_reply(message),
            "sources": [],
            "mode": "local-fallback",
            "items": related_cards,
        }

    return {
        "reply": response.output_text,
        "sources": extract_response_sources(response),
        "mode": "openai",
        "items": related_cards,
    }

@app.context_processor
def inject_editor_auth():
    return {
        "editor_authenticated": session.get("editor_authenticated", False),
        "database_authenticated": session.get("database_authenticated", False),
    }

@app.route('/', methods=['GET', 'POST'])
def home():
    recently_changed_items = load_recently_changed_items()
    return render_template('search.html', recently_changed_items=recently_changed_items)

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
    session.pop("database_authenticated", None)
    entries = load_database_entries()
    bin_rows = load_bins_directory()
    return render_template('db.html', entries=entries, bins=bin_rows)

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
    selected_room = request.args.get('room', '').strip()
    valid_rooms = {"110", "110A", "110B", "110C"}
    if selected_room not in valid_rooms:
        selected_room = None

    items = load_inventory_items(selected_room)
    return render_template('floorplan.html', items=items, selected_room=selected_room)

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
    history = data.get('history', [])
    result = generate_help_bot_response(message, history)
    return jsonify(result)

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

def query_bins_from_database(room, wall, storage_type):
    WallID = functions.wallDecider(wall,room)
    theList = functions.returnBinList(WallID, storage_type)
    print(theList)
    return theList


if __name__ == '__main__':
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.run(debug=True)
