import pandas as pd
import io
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Form
import logging
import os
import uvicorn
import asyncio
import requests
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import uuid  # For generating unique download tokens

load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)

# Initialize FastAPI app
app = FastAPI()

# ✅ Allow frontend to connect (CORS Middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all domains (change in production)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Store transactions (mock database for simplicity)
verified_payments = {}
cleaned_files = {}  # Store cleaned CSVs temporarily

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")

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

        # Determine file type and read accordingly
        if file.filename.endswith(".csv"):
            df = pd.read_csv(io.StringIO(contents.decode("utf-8")))
        elif file.filename.endswith(".json"):
            df = pd.read_json(io.StringIO(contents.decode("utf-8")))
        elif file.filename.endswith(".txt"):
            df = pd.read_csv(io.StringIO(contents.decode("utf-8")), delimiter="\t")
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type. Please upload CSV, JSON, or TXT.")

        # Log file processing
        logging.info(f"Processing file: {file.filename} with {df.shape[0]} rows and {df.shape[1]} columns")

        # Step 1: Standardize column names
        df.columns = df.columns.str.strip().str.lower().str.replace(r'\.\d+', '', regex=True)

        # Step 2: Select columns to clean (if specified)
        if columns_to_clean:
            selected_columns = [col.strip().lower() for col in columns_to_clean.split(",")]
            df = df[selected_columns] if all(col in df.columns for col in selected_columns) else df

        # Step 3: Handle missing values
        if missing_value_strategy == "drop":
            df.dropna(inplace=True)
        elif missing_value_strategy == "fill":
            df.fillna("Unknown", inplace=True)

        # Step 4: Remove duplicate rows
        if remove_duplicates:
            df.drop_duplicates(inplace=True)

        # Step 5: Apply text formatting
        if text_format == "lowercase":
            df = df.applymap(lambda x: x.lower() if isinstance(x, str) else x)
        elif text_format == "uppercase":
            df = df.applymap(lambda x: x.upper() if isinstance(x, str) else x)
        elif text_format == "capitalize":
            df = df.applymap(lambda x: x.capitalize() if isinstance(x, str) else x)

        # Check if data is still present
        if df.empty:
            raise HTTPException(status_code=400, detail="No data left after cleaning. Please check your input file.")

        # Save cleaned CSV file
        cleaned_filename = f"cleaned_{uuid.uuid4().hex}.csv"
        df.to_csv(cleaned_filename, index=False)
        cleaned_files[cleaned_filename] = cleaned_filename

        return {
            "status": "success",
            "original_rows": len(contents.splitlines()) - 1,  # Exclude header row
            "cleaned_rows": len(df),
            "download_token": cleaned_filename  # Use filename as token for now
        }

    except Exception as e:
        logging.error(f"Error processing file: {str(e)}")
        return {"status": "error", "message": str(e)}

@app.post("/paystack/webhook/")
async def verify_payment(request: Request):
    """Handles Paystack webhook for payment verification"""
    payload = await request.json()
    event = payload.get("event")

    if event == "charge.success":
        payment_reference = payload["data"]["reference"]
        
        # Verify payment status
        headers = {
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json"
        }
        response = requests.get(f"https://api.paystack.co/transaction/verify/{payment_reference}", headers=headers)
        payment_data = response.json()

        if payment_data["status"] and payment_data["data"]["status"] == "success":
            # ✅ Payment Successful — Generate a unique download token
            download_token = str(uuid.uuid4())
            verified_payments[download_token] = payment_reference

            return {
                "status": "success",
                "message": "Payment verified. Use the token to download your CSV.",
                "download_token": download_token
            }
        else:
            raise HTTPException(status_code=400, detail="Payment verification failed.")

    return {"status": "error", "message": "Invalid event"}

@app.get("/download/{token}")
async def download_csv(token: str):
    """Allow CSV download only if payment is verified."""
    if token not in verified_payments:
        raise HTTPException(status_code=403, detail="Invalid or expired token. Payment required.")

    # Retrieve stored cleaned file
    filename = verified_payments[token]
    if filename not in cleaned_files:
        raise HTTPException(status_code=404, detail="File not found.")

    # Read and return file contents
    with open(cleaned_files[filename], "r") as f:
        csv_content = f.read()

    # Remove token after download (one-time use)
    del verified_payments[token]
    del cleaned_files[filename]

    return {
        "status": "success",
        "message": "Download link is ready.",
        "csv_content": csv_content  # Replace with actual file serving method
    }

# ✅ Uvicorn Configuration
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))  # Render assigns a dynamic port
    config = uvicorn.Config(app, host="0.0.0.0", port=port)
    server = uvicorn.Server(config)
    server.run()
