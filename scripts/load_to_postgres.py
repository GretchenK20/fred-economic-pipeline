import os
import logging
from pathlib import Path
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

host = os.environ["POSTGRES_HOST"]
port = os.environ["POSTGRES_PORT"]
db = os.environ["POSTGRES_DB"]
user = os.environ["POSTGRES_USER"]
password = os.environ["POSTGRES_PASSWORD"]

engine = create_engine(f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}")

cleaned_path = Path("data/cleaned/cleaned_observations.parquet")
if not cleaned_path.exists():
    raise FileNotFoundError(f"Run 'make clean-data' first — {cleaned_path} not found")

df = pd.read_parquet(cleaned_path)
logger.info(f"Loaded {len(df)} rows from {cleaned_path}")

with engine.begin() as conn:
    conn.execute(text("CREATE SCHEMA IF NOT EXISTS raw"))

df.to_sql(
    name="cleaned_observations",
    con=engine,
    schema="raw",
    if_exists="replace",
    index=False,
    method="multi",
    chunksize=1000,
)
logger.info(f"Wrote {len(df)} rows to raw.cleaned_observations")
