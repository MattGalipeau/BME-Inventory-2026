import sqlite3
import os

# Deleting and recreating db... only for this first script obviously. probably better way to deal with original inputs rewriting (current primaries are auto increment)
#-------------------------------------------------------------------------------#
db_file = "bmeInventory.db"
try:
    os.remove(db_file)
    print(f"Database '{db_file}' deleted successfully.")
except FileNotFoundError:
    print(f"Database '{db_file}' not found.")
except Exception as e:
    print(f"An error occurred: {e}")

#-------------------------------------------------------------------------------#
cnt = sqlite3.connect('bmeInventory.db') 
cursor = cnt.cursor()

# Creating rooms table for the 4 unique rooms
cnt.execute('''CREATE TABLE IF NOT EXISTS rooms( 
RoomID INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, 
RoomName TEXT NOT NULL
);''') 

# Creating the four rooms and having them be data within table "rooms"
genRooms = [("110",), ("110A",), ("110B",), ("110C",)]
cnt.executemany("INSERT INTO rooms(RoomName) VALUES(?)", genRooms)

# Creating walls table, which contains all 16 unique walls and their forign keys (room id)
cnt.execute('''CREATE TABLE IF NOT EXISTS walls( 
WallID INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, 
WallName TEXT NOT NULL,
RoomID INTEGER ,
FOREIGN KEY (RoomID) REFERENCES "rooms" (RoomID)
);''') 

# Inserting with respect to rooms, so RoomIDs range from 1-4 (110, 110A, 110B, and 110C)
genWalls = [("North",), ("East",), ("South",), ("West",)]
cnt.executemany("INSERT INTO walls(WallName, RoomID) VALUES(?, 1)", genWalls)
cnt.executemany("INSERT INTO walls(WallName, RoomID) VALUES(?, 2)", genWalls)
cnt.executemany("INSERT INTO walls(WallName, RoomID) VALUES(?, 3)", genWalls)
cnt.executemany("INSERT INTO walls(WallName, RoomID) VALUES(?, 4)", genWalls)

# creating the bins table, to store bins / containers and related data
cnt.execute('''CREATE TABLE IF NOT EXISTS bins( 
BinUPC INTEGER PRIMARY KEY NOT NULL,
BinID INTEGER NOT NULL, 
BinType TEXT NOT NULL, 
WallID INTEGER NOT NULL,
FOREIGN KEY (WallID) REFERENCES "walls" (WallID)
);''') 

# filling the bins table with random fake data
#genBin = [(1, "Container", 2), (2, "Drawer", 4), (3, "Container", 2), (4, "Tabletop", 15)]
#cnt.executemany("INSERT INTO bins(BinID, BinType, WallID) VALUES(?, ?, ?)", genBin)

# item table, to show total qty and upc of a unique item
cnt.execute('''CREATE TABLE IF NOT EXISTS items( 
UPC INTEGER PRIMARY KEY, 
TotalQty INTEGER NOT NULL,
Name TEXT NOT NULL
);''') 

# Item_bin table, to show amount of item in a bin
cnt.execute('''CREATE TABLE IF NOT EXISTS item_bin( 
EntryID INTEGER PRIMARY KEY AUTOINCREMENT,
UPC INTEGER NOT NULL, 
Name TEXT NOT NULL,
BinUPC INTEGER NOT NULL, 
Qty INTEGER NOT NULL,
Date TEXT NOT NULL,
Time TEXT NOT NULL,
FOREIGN KEY (UPC) REFERENCES "items" (UPC),
FOREIGN KEY (BinUPC) REFERENCES "bins" (BinID)
);''')

# I have created an index on both name of item, and upc. This can easily be changed for table location for our final result. Current on item_bin table.
cnt.execute('''CREATE INDEX nameIndex
            ON item_bin (Name)''')
cnt.execute('''CREATE INDEX UPCIndex
            ON item_bin (UPC)''')

# Commit changes then close the database
cnt.commit()
cnt.close()
