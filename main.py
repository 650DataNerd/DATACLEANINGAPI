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

# ✅ Store transactions & cleaned files
verified_payments = {}  # Stores verified payments
cleaned_files = {}  # Stores cleaned file paths

async def cleanup_old_files():
    """Deletes files older than 24 hours periodically to free up space."""
    while True:
        now = datetime.now()
        for file in TEMP_DIR.iterdir():
            if file.is_file() and datetime.fromtimestamp(file.stat().st_mtime) < now - timedelta(hours=24):
                file.unlink()
                logging.info(f"Deleted old file: {file.name}")
        await asyncio.sleep(3600)  # Run cleanup every hour

@app.get("/")
def read_root():
    return {"message": "API is running successfully!"}

@app.get("/health")
def health_check():
    return {"status": "healthy", "message": "API is running fine"}

@app.post("/clean-data/")
async def clean_data(
    file: UploadFile = File(...),
    columns_to_clean: str = Form(""),  
    missing_value_strategy: str = Form("fill"),  
    remove_duplicates: bool = Form(True),
    text_format: str = Form("lowercase"),  
):
    """Cleans user-uploaded data based on selected preferences."""
    try:
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
            for col in df.columns:
                if df[col].dtype == "float64":
                    df[col].fillna(0, inplace=True)  # Fill numeric columns with 0
                else:
                    df[col].fillna("Unknown", inplace=True)  # Fill text columns with "Unknown"

        # ✅ Remove duplicate rows (if enabled)
        if remove_duplicates:
            df.drop_duplicates(inplace=True)

        # ✅ Apply text formatting
        if text_format == "lowercase":
            df = df.map(lambda x: x.lower() if isinstance(x, str) else x)
        elif text_format == "uppercase":
            df = df.map(lambda x: x.upper() if isinstance(x, str) else x)
        elif text_format == "capitalize":
            df = df.map(lambda x: x.capitalize() if isinstance(x, str) else x)

        if df.empty:
            raise HTTPException(status_code=400, detail="No data left after cleaning. Check your input file.")

        cleaned_filename = f"{uuid.uuid4().hex}.csv"
        cleaned_path = TEMP_DIR / cleaned_filename
        df.to_csv(cleaned_path, index=False)
        cleaned_files[cleaned_filename] = str(cleaned_path)

        return {
            "status": "success",
            "original_rows": len(contents.splitlines()) - 1,  
            "cleaned_rows": len(df),
            "download_token": cleaned_filename  
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

        headers = {
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json"
        }
        response = requests.get(f"https://api.paystack.co/transaction/verify/{payment_reference}", headers=headers)
        payment_data = response.json()

        if payment_data.get("status") and payment_data["data"].get("status") == "success":
            download_token = str(uuid.uuid4())

            # ✅ Assign token to the correct cleaned file
            for filename, path in cleaned_files.items():
                if Path(path).exists():
                    verified_payments[download_token] = filename
                    return {
                        "status": "success",
                        "message": "Payment verified. Use the token to download your CSV.",
                        "download_token": download_token
                    }

            return {"status": "error", "message": "No cleaned files found to assign a token."}

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

    with open(file_path, "r") as f:
        csv_content = f.read()

    # ✅ Delete only the specific file instead of the entire directory
    Path(file_path).unlink(missing_ok=True)
    del verified_payments[token]
    del cleaned_files[filename]

    return {
        "status": "success",
        "message": "Download link is ready.",
        "csv_content": csv_content  
    }

# ✅ Uvicorn Configuration
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))  
    loop = asyncio.get_event_loop()
    loop.create_task(cleanup_old_files())  # Run cleanup in background
    uvicorn.run(app, host="0.0.0.0", port=port)
