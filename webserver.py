import sqlite3
from flask import Flask, render_template, request, jsonify
import functions

app = Flask(__name__)

# Database setup: Create an SQLite database and a table if it doesn't exist
DATABASE = 'bmeInventory.db'

def get_db():
    """Open a new database connection."""
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row  # This make the DB return rows as dictionaries
    return db

@app.route('/', methods=['GET', 'POST'])
def home():

    return render_template('search.html')

@app.route('/edit', methods=['GET', 'POST'])
def edit():

    return render_template('edit.html')

@app.route('/db', methods=['GET', 'POST'])
def db():
    db = get_db()
    cursor = db.cursor()
    items = []
    try:
        cursor.execute("""
            SELECT
                i.UPC,
                i.Name,
                i.TotalQty,
                COUNT(DISTINCT ib.BinUPC) AS LocationCount,
                MAX(datetime(ib.Date || ' ' || ib.Time)) AS LastAdded
            FROM items i
            LEFT JOIN item_bin ib ON ib.UPC = i.UPC
            GROUP BY i.UPC, i.Name, i.TotalQty
            ORDER BY
                LastAdded IS NULL,
                LastAdded DESC,
                i.UPC DESC
        """)
        rows = cursor.fetchall()
        items = [dict(row) for row in rows]
    except sqlite3.Error as e:
        print("An error occurred while loading database page:", e.args[0])
    finally:
        db.close()

    return render_template('db.html', items=items)

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
