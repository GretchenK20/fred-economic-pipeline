import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from ingestion.fred_loader import FREDLoader

logging.basicConfig(level=logging.INFO)
load_dotenv()

loader = FREDLoader(
    api_key=os.environ["FRED_API_KEY"],
    output_dir=Path("data/raw"),
)
results = loader.fetch_all()
print(f"Fetched {len(results)} series.")
