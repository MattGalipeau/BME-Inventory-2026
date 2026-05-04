# How To Run

GitHub repo: add your repo link here

## Quick Start

### 1. Open the project folder
Run all commands from the repo root:

```powershell
cd path\to\BME-Inventory-2026
```

### 2. Make sure Python is installed
This project expects **Python 3.12+**.

Check with:

```powershell
python --version
```

## Recommended: Run with `uv`

### 3. Install dependencies

```powershell
uv sync
```

### 4. Add your `.env`
Create a `.env` file in the project root.

Minimum needed to start the site:

```env
OPENAI_API_KEY=your_key_here
```

If you want checkout emails too, also add your SMTP settings.

### 5. Start the app

```powershell
uv run python webserver.py
```

### 6. Open it
In your browser, go to:

```text
http://127.0.0.1:5000
```

## Simple Alternative: Run with `pip`

Install dependencies:

```powershell
pip install -r requirements.txt
```

Start the app:

```powershell
python webserver.py
```

Then open:

```text
http://127.0.0.1:5000
```

## Notes

- The app runs in debug mode by default.
- Inventory data is stored in `bmeInventory.db`.
- Brother label printing features require the local Brother/b-PAC setup on Windows.
