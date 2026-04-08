import sqlite3
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import functions

app = Flask(__name__)
app.secret_key = "bme-inventory-editor-lock"

# Database setup: Create an SQLite database and a table if it doesn't exist
DATABASE = 'bmeInventory.db'
EDITOR_PASSWORD = "BMETech"

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
    return render_template('db.html', entries=entries)

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
