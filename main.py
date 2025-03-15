import pandas as pd
import io
import os
import logging
import asyncio
import requests
import uuid
import shutil
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)

# Initialize FastAPI app
app = FastAPI()

# ✅ CORS Middleware (Allow frontend access)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all domains (update for production)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Environment Variables
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")

# ✅ Storage Paths
TEMP_DIR = Path("temp_files")
TEMP_DIR.mkdir(exist_ok=True)  # Ensure directory exists

# ✅ Store transactions & cleaned files (mock database)
verified_payments = {}  # Stores verified payments
cleaned_files = {}  # Stores cleaned file paths

# ✅ Auto-delete old files (cleanup function)
def cleanup_old_files():
    """Delete files older than 24 hours to free up space."""
    for file in TEMP_DIR.iterdir():
        if file.is_file() and datetime.fromtimestamp(file.stat().st_mtime) < datetime.now() - timedelta(hours=24):
            file.unlink()

# ✅ API Endpoints
@app.get("/")
def read_root():
    """Root route to check if API is running."""
    return {"message": "API is running successfully!"}

@app.get("/health")
def health_check():
    """Health check route for uptime monitoring."""
    return {"status": "healthy", "message": "API is running fine"}

@app.post("/clean-data/")
async def clean_data(
    file: UploadFile = File(...),
    columns_to_clean: str = Form(""),  # Comma-separated column names
    missing_value_strategy: str = Form("fill"),  # fill, drop
    remove_duplicates: bool = Form(True),
    text_format: str = Form("lowercase"),  # lowercase, uppercase, capitalize
):
    """Cleans user-uploaded data based on selected preferences."""
    try:
        # Read file contents
        contents = await file.read()

        # Validate file type
        if file.filename.endswith(".csv"):
            df = pd.read_csv(io.StringIO(contents.decode("utf-8")))
        elif file.filename.endswith(".json"):
            df = pd.read_json(io.StringIO(contents.decode("utf-8")))
        elif file.filename.endswith(".txt"):
            df = pd.read_csv(io.StringIO(contents.decode("utf-8")), delimiter="\t")
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type. Upload CSV, JSON, or TXT.")

        # ✅ Log file details
        logging.info(f"Processing file: {file.filename} with {df.shape[0]} rows and {df.shape[1]} columns")

        # ✅ Standardize column names
        df.columns = df.columns.str.strip().str.lower().str.replace(r'\.\d+', '', regex=True)

        # ✅ Select specific columns to clean (if provided)
        if columns_to_clean:
            selected_columns = [col.strip().lower() for col in columns_to_clean.split(",")]
            df = df[selected_columns] if all(col in df.columns for col in selected_columns) else df

        # ✅ Handle missing values
        if missing_value_strategy == "drop":
            df.dropna(inplace=True)
        elif missing_value_strategy == "fill":
            df.fillna("Unknown", inplace=True)

        # ✅ Remove duplicate rows (if enabled)
        if remove_duplicates:
            df.drop_duplicates(inplace=True)

        # ✅ Apply text formatting
        if text_format == "lowercase":
            df = df.applymap(lambda x: x.lower() if isinstance(x, str) else x)
        elif text_format == "uppercase":
            df = df.applymap(lambda x: x.upper() if isinstance(x, str) else x)
        elif text_format == "capitalize":
            df = df.applymap(lambda x: x.capitalize() if isinstance(x, str) else x)

        # ✅ Validate cleaned data
        if df.empty:
            raise HTTPException(status_code=400, detail="No data left after cleaning. Check your input file.")

        # ✅ Save cleaned CSV file
        cleaned_filename = f"{uuid.uuid4().hex}.csv"
        cleaned_path = TEMP_DIR / cleaned_filename
        df.to_csv(cleaned_path, index=False)
        cleaned_files[cleaned_filename] = str(cleaned_path)

        return {
            "status": "success",
            "original_rows": len(contents.splitlines()) - 1,  # Exclude header row
            "cleaned_rows": len(df),
            "download_token": cleaned_filename  # Use filename as token
        }

    except Exception as e:
        logging.error(f"Error processing file: {str(e)}")
        return {"status": "error", "message": str(e)}

@app.post("/paystack/webhook/")
async def verify_payment(request: Request):
    """Handles Paystack webhook for payment verification."""
    try:
        payload = await request.json()
        event = payload.get("event", "")

        if event != "charge.success":
            return {"status": "error", "message": "Invalid event"}

        payment_reference = payload["data"]["reference"]

        # ✅ Verify payment with Paystack
        headers = {
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json"
        }
        response = requests.get(f"https://api.paystack.co/transaction/verify/{payment_reference}", headers=headers)
        payment_data = response.json()

        if payment_data.get("status") and payment_data["data"].get("status") == "success":
            # ✅ Payment Successful — Generate a unique download token
            download_token = str(uuid.uuid4())
            verified_payments[download_token] = payment_reference

            return {
                "status": "success",
                "message": "Payment verified. Use the token to download your CSV.",
                "download_token": download_token
            }

        return {"status": "error", "message": "Payment verification failed"}

    except Exception as e:
        logging.error(f"Webhook error: {str(e)}")
        return {"status": "error", "message": str(e)}

@app.get("/download/{token}")
async def download_csv(token: str):
    """Allows CSV download only if payment is verified."""
    if token not in verified_payments:
        raise HTTPException(status_code=403, detail="Invalid or expired token. Payment required.")

    filename = verified_payments[token]
    file_path = cleaned_files.get(filename)

    if not file_path or not Path(file_path).exists():
        raise HTTPException(status_code=404, detail="File not found.")

    # ✅ Read and return file contents
    with open(file_path, "r") as f:
        csv_content = f.read()

    # ✅ Remove token and file after download (one-time use)
    del verified_payments[token]
    del cleaned_files[filename]
    Path(file_path).unlink(missing_ok=True)

    return {
        "status": "success",
        "message": "Download link is ready.",
        "csv_content": csv_content  # Replace with actual file serving method
    }

# ✅ Uvicorn Configuration
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))  # Render assigns a dynamic port
    cleanup_old_files()  # Run cleanup on startup
    config = uvicorn.Config(app, host="0.0.0.0", port=port)
    server = uvicorn.Server(config)
    server.run()
