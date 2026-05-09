import logging
from pathlib import Path
from transforms.series_cleaner import SeriesCleaner

logging.basicConfig(level=logging.INFO)

cleaner = SeriesCleaner(
    raw_dir=Path("data/raw"),
    output_dir=Path("data/cleaned"),
)
df = cleaner.clean_all()
print(f"Cleaned {len(df)} total rows.")
