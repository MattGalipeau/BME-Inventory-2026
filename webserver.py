import os
import sqlite3
import re
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import functions

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

if load_dotenv is not None:
    load_dotenv()

app = Flask(__name__)
app.secret_key = "bme-inventory-editor-lock"

# Database setup: Create an SQLite database and a table if it doesn't exist
DATABASE = 'bmeInventory.db'
EDITOR_PASSWORD = "BMETech"
HELP_BOT_MODEL = os.getenv("HELP_BOT_MODEL", "gpt-4.1-mini")
_help_bot_client = None

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

def build_help_bot_reply(message):
    text = (message or "").strip()
    if not text:
        return "I can help find items, rooms, walls, storage types, and bin locations in the inventory."

    lowered = text.lower()
    room_name = extract_room_name(text)
    catalog = load_help_inventory_snapshot()

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

def get_help_bot_client():
    global _help_bot_client

    if _help_bot_client is not None:
        return _help_bot_client

    if OpenAI is None or not os.getenv("OPENAI_API_KEY"):
        return None

    try:
        _help_bot_client = OpenAI()
    except Exception as exc:
        print("Could not initialize help bot client:", exc)
        _help_bot_client = None

    return _help_bot_client

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

def build_help_bot_instructions():
    return (
        "You are BME Inventory Help, a concise support chatbot for a lab inventory website. "
        "You can use web search for general educational guidance, such as explaining what equipment is typically needed for a task. "
        "You must treat the provided inventory data as the only source of truth for what this lab actually has, where it is located, and how many are logged. "
        "Never invent inventory items, quantities, or locations that are not present in the provided data. "
        "If the user asks what they need for a task, explain the typical needs briefly and then map those needs to the lab items that actually fit, if any. "
        "If the inventory does not contain a needed item, say that clearly. "
        "When citing web-derived guidance, keep the answer concise and factual. "
        "Do not claim to change data, edit data, reserve equipment, or perform actions."
    )

def generate_help_bot_response(message, history):
    client = get_help_bot_client()
    if client is None:
        return {
            "reply": build_help_bot_reply(message),
            "sources": [],
            "mode": "local",
        }

    prompt = build_help_bot_prompt(message, history)
    response = client.responses.create(
        model=HELP_BOT_MODEL,
        instructions=build_help_bot_instructions(),
        tools=[{"type": "web_search"}],
        input=prompt,
    )

    return {
        "reply": response.output_text,
        "sources": extract_response_sources(response),
        "mode": "openai",
    }

@app.context_processor
def inject_editor_auth():
    return {
        "editor_authenticated": session.get("editor_authenticated", False),
        "database_authenticated": session.get("database_authenticated", False),
    }

@app.route('/', methods=['GET', 'POST'])
def home():

    return render_template('search.html')

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
    data = request.get_json()
    search_query = data.get('search_query', '')

    # Initialize cursor to search database
    db = get_db()
    cursor = db.cursor()
    results = []
    try:
        # Perform the search query
        cursor.execute(
            "SELECT * FROM items WHERE UPC LIKE ? OR Name LIKE ?",
            ('%' + search_query + '%', '%' + search_query + '%')
        )
        rows = cursor.fetchall()
        # Convert the results to a list of dictionaries
        results = [dict(row) for row in rows]

        # Query the `item_bin` table to count locations for each item
        for item in results:
            upc = item.get("UPC")
            if upc:
                cursor.execute(
                    "SELECT COUNT(*) FROM item_bin WHERE UPC = ?",
                    (upc,)
                )
                location_count = cursor.fetchone()[0]  # Get the count from the query
                item["LocationCount"] = location_count  # Add the count to the item dictionary
        print(results)
    except sqlite3.Error as e:
        print("An error occurred:", e.args[0])

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

        cursor.execute(
            "UPDATE item_bin SET Name = ?, Qty = ?, BinUPC = ? WHERE EntryID = ?",
            (name, qty, bin_upc, entry_id)
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
            return jsonify({"success": True}), 200
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
