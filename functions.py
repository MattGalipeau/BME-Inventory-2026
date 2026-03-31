import subprocess
#import sys
import sqlite3
#import os
import datetime

DATABASE = 'bmeInventory.db'

cnt = sqlite3.connect(DATABASE, check_same_thread=False) 
cursor = cnt.cursor()

# This function will print out the barcode. Pass in szItemCode, which is a string of 8 numbers.
# Can also be used for reprint. Will have to grab "UPC" from the specified row in the database, and pass as szItemCode to this function.
def Print(Code_8, VbsFile, binType, binID):
    # Hey Ved, szVbsFile is calling the visual basic file I have inserted into this repo.
    # That file will have the "szItemCode" inserted into it. When you look in the vbs file, you can see that I change barcode text to this string.
    if binType == None:
        szBuf = "wscript \"{}\" \"{}\"".format(VbsFile, Code_8) # passes in an argument to the vbs, we are only passing in one arg, szItemCode.
    else:
        binType = f'{binType} #{binID}'
        szBuf = "wscript \"{}\" \"{}\" \"{}\"".format(VbsFile, Code_8, binType) # passes in an argument to the vbs, we are only passing in one arg, szItemCode.
    subprocess.run(szBuf, shell=True) # runs the file. I know you didn't like my past history with using subprocess lol. Or maybe that was Raph.
    return 0

def createItemLocator(itemName, binNumber, Qty, binType, Wall, Room):
    # check db for upc in item table. if not exist, create item
    WallID = wallDecider(Wall, Room)
    print(itemName, binNumber, Qty, binType, Wall, Room)
    clearing = cursor.fetchall()
    
    cursor.execute("SELECT * FROM item_bin WHERE Name LIKE ?", (itemName,))
    result = cursor.fetchone()
    if result:
        pass
    else: # else if there is no returned result from searching item_bin table for UPC, let us create it
        createItem(itemName)    

    UPC = cursor.execute("SELECT UPC FROM items WHERE Name LIKE ?", (itemName,))
    UPC = cursor.fetchone()
    UPC = UPC[0]

    clearing = cursor.fetchall()

    now = datetime.datetime.now()
    dateTime = now.strftime("%Y-%m-%d %H:%M:%S")
    date, time = dateTime.split(" ")

    binUPC = binUPCFinder(binType, binNumber)
    print(binUPC)
    cursor.execute("SELECT * FROM item_bin WHERE BinUPC LIKE ?", (binUPC,))
    resultBin = cursor.fetchone()
    print(resultBin)
    if resultBin:
        clearing = cursor.fetchall()
        cursor.execute("SELECT * FROM bins WHERE BinUPC LIKE ? AND WallID LIKE ?", (binUPC,WallID))
        resultBin = cursor.fetchone()
        if resultBin:
            executive = [(UPC, itemName, binUPC, Qty, date, time)]
            cnt.executemany("INSERT INTO item_bin(UPC, Name, BinUPC, Qty, Date, Time) VALUES(?,?,?,?,?,?)", executive)
            TotalQtyChange(UPC, Qty)
            cnt.commit()
        else: 
            print("Woah there pal, this would be an error. Your bin and location do not align.")
    else:
        binID, binUPC = createBin(binType, Wall, Room)
        executive = [(UPC, itemName, binUPC, Qty, date, time)]
        cnt.executemany("INSERT INTO item_bin(UPC, Name, BinUPC, Qty, Date, Time) VALUES(?,?,?,?,?,?)", executive)
        TotalQtyChange(UPC, Qty)
        cnt.commit()

def createBin(binType, Wall, Room):
    #cursor.execute("SELECT MAX(BinD) FROM bins")
    #try:
    #    binID = (cursor.fetchone())[0]+1
    #except:
    #    binID = 1
    WallID = wallDecider(Wall, Room)
    binUPC, binID = binUPCDecider(binType)
    executive = [(binID, binUPC, binType, WallID)]
    try:
        cnt.executemany("INSERT INTO bins (BinID, BinUPC, BinType, WallID) VALUES(?,?,?,?)", executive)
        VbsFile = "BcdBinLabel.vbs" 
        cursor.execute("SELECT * FROM bins WHERE BinType = ?", (binType,))
        results = cursor.fetchall()
        number = len(results)
        Print(binUPC, VbsFile, binType, number)
    except:
        print("This bin already exists, unforeseen issue as this will only get called if bin does not exist")
    cnt.commit()
    return binID, binUPC

def createItem(itemName):
    cursor.execute("SELECT MAX(UPC) FROM items")
    maxUPC = cursor.fetchone()

    try:
        UPC = maxUPC[0]+1
    except:
        UPC = 10000001

    VbsFile = "BcdLabel.vbs" 
    Print(UPC, VbsFile, None, None)
    executive = [(UPC, 0, itemName)]

    try:
        cursor.execute("SELECT Name FROM items WHERE Name LIKE ?", (itemName,))
        checkingName = cursor.fetchone()
        if checkingName == None:
            cnt.executemany("INSERT INTO items (UPC, TotalQty, Name) VALUES(?,?,?)", executive)
    except:
        print("Item already exists: this should not have happened, as function only called when it does not exist. Maybe another error idk")
    cnt.commit()

def wallDecider(Wall, Room):
    # Room 110 is 1-4, 110A is 5-8, etc.
    wallID = 0
    if Room == "110":
        wallID = 0
    elif Room == "110A":
        wallID = 4
    elif Room == "110B":
        wallID = 8
    elif Room == "110C":
        wallID = 12
    # ex: if 110 North, 0 + 1 = 1. 110 North has WallID of 1.
    if Wall == "North":
        wallID = wallID + 1
    elif Wall == "East":
        wallID = wallID + 2
    elif Wall == "South":
        wallID = wallID + 3
    elif Wall == "West":
        wallID = wallID + 4
    ## Bringin it back baby (this will be wallid from)
    return wallID

def deleteItemEntry(EntryID): #entry ID is the primary key for item_bin. Would it be easy to grab this when you select an item on the user interface?
    cnt.execute("DELETE FROM item_bin WHERE EntryID = ?", (EntryID,))
    cnt.commit()

def binUPCFinder(binType, binID):
    cursor.execute("SELECT * FROM bins WHERE BinType = ? AND BinID = ?", (binType, binID))
    results = cursor.fetchall()
    binID = len(results)
    if binID:
        binUPC = int(binID) + 50000000
        if binType == "Bin":
            binUPC = binUPC + 1000000
        if binType == "Shelf":
            binUPC = binUPC + 2000000
        if binType == "Drawer":
            binUPC = binUPC + 3000000
        if binType == "Cabinet":
            binUPC = binUPC + 4000000
        if binType == "Tabletop":
            binUPC = binUPC + 5000000
        if binType == "Overhead":
            binUPC = binUPC + 6000000
        if binType == "Other":
            binUPC = binUPC + 7000000
    else:
        binUPC = None
        
    clearing = cursor.fetchall()
    return binUPC
    

def binUPCDecider(binType):
    cursor.execute("SELECT * FROM bins WHERE BinType = ?", (binType,))
    results = cursor.fetchall()
    binID = len(results) + 1

    binUPC = int(binID) + 50000000
    if binType == "Bin":
        binUPC = binUPC + 1000000
    if binType == "Shelf":
        binUPC = binUPC + 2000000
    if binType == "Drawer":
        binUPC = binUPC + 3000000
    if binType == "Cabinet":
        binUPC = binUPC + 4000000
    if binType == "Tabletop":
        binUPC = binUPC + 5000000
    if binType == "Overhead":
        binUPC = binUPC + 6000000
    if binType == "Other":
        binUPC = binUPC + 7000000
    return binUPC, binID

def editQtyEntry(qtyUpdate, EntryID):
    cursor.execute("SELECT Qty FROM item_bin WHERE EntryID = ?", (EntryID,))
    OldQty = (cursor.fetchone())[0]
    cursor.execute("SELECT UPC FROM item_bin WHERE EntryID = ?", (EntryID,))
    UPC = (cursor.fetchone())[0]
    cnt.execute("UPDATE item_bin set Qty = ? WHERE EntryID = ?", (qtyUpdate, EntryID))
    QtyDif = OldQty-qtyUpdate
    TotalQtyChange(UPC, QtyDif)
    cnt.commit()

def TotalQtyChange(UPC, qtyChange):
    cursor.execute("SELECT TotalQty FROM items WHERE UPC = ?", (UPC,))
    oldTotal = (cursor.fetchone())[0]
    newTotal = int(oldTotal)+int(qtyChange)
    cursor.execute("UPDATE items set TotalQty = ? WHERE UPC = ?", (newTotal, UPC))

def editItemLocation(newBinLocation, EntryID, binType, Wall, Room):
    cursor.execute("SELECT * FROM bins WHERE BinID = ?", (newBinLocation,))
    if cursor.fetchone() == None:
        print("new bin does not exist. creating the bin first..")
        createBin(binType, Wall, Room)
        cnt.execute("UPDATE item_bin set BinID = ? WHERE EntryID = ?", (newBinLocation, EntryID))
    else:
        cnt.execute("UPDATE item_bin set BinID = ? WHERE EntryID = ?", (newBinLocation, EntryID))
    cnt.commit()

def returnBinList(WallID, binType):
    cursor.execute("SELECT BinID FROM bins WHERE WallID LIKE ? AND BinType LIKE ?", ((WallID, binType)))
    rows = cursor.fetchall()
    theList =[{"id": row[0]} for row in rows]
    return theList
    
#editItemLocation(10, 5, "Drawer", "East", "110A")
#editQtyEntry(999,4)
#deleteItemEntry(2)
#createItem("MyName")
######## ENTRY LIKE BELOW FOR THE INFORMATION FROM POST WHEN CREATING ########
#for i in range(5):
#createItemLocator('baba', 5, 1, "Drawer", "West", "110")
                  # (itemName, binNumber, Qty, binType, "Wall", Room) #
#Print("11001100")

#returnBinList(4, "Drawer")
if __name__ == "__main__":
    for i in range(18):
        createBin("Drawer", "South", "110A")
#createBin(binNumber, binType, Wall, Room)
