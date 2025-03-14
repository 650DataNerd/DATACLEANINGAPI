import pandas as pd
import io
from fastapi import FastAPI, UploadFile, File, HTTPException
import logging
import os
import uvicorn
import asyncio
from fastapi.middleware.cors import CORSMiddleware

# Set up logging
logging.basicConfig(level=logging.INFO)

# Initialize FastAPI app
app = FastAPI()

# ✅ Fix: Allow frontend to connect (CORS Middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all domains (change this in production)
    allow_credentials=True,
    allow_methods=["*"],  
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "API is running successfully!"}

@app.post("/clean-data/")
async def clean_data(file: UploadFile = File(...)):
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

        # Step 2: Remove duplicate rows
        df = df.drop_duplicates()

        # Step 3: Handle missing values
        df.dropna(how='all', inplace=True)  # Drop fully empty rows

        def safe_fill(value, column):
            """Fill missing values safely with better defaults."""
            if pd.isna(value) or (isinstance(value, str) and value.strip() == ""):
                return "Untitled Business" if column == "title" else "Unknown"
            return value

        df = df.apply(lambda col: col.map(lambda x: safe_fill(x, col.name)))

        # Check if data is still present
        if df.empty:
            raise HTTPException(status_code=400, detail="No data left after cleaning. Please check your input file.")

        # Convert cleaned data to JSON format
        cleaned_data = df.to_dict(orient="records")

        return {
            "status": "success",
            "original_rows": len(contents.splitlines()) - 1,  # Exclude header row
            "cleaned_rows": len(cleaned_data),
            "cleaned_data_sample": cleaned_data[:10]  # Show only first 10 rows for preview
        }

    except Exception as e:
        logging.error(f"Error processing file: {str(e)}")
        return {"status": "error", "message": str(e)}

# ✅ Fix: Corrected Uvicorn Configuration (Fixes asyncio.run() issue)
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))  # Render assigns a dynamic port
    loop = asyncio.get_event_loop()
    loop.run_until_complete(uvicorn.run(app, host="0.0.0.0", port=port))
