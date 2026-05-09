"""
fred_loader.py

Production-grade ingestion class for the Federal Reserve Economic Data (FRED) API.
Handles rate limiting, retries, schema validation, and revision tracking.
"""

import logging
import time
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional

import requests
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class SeriesConfig:
    """Configuration for a single FRED series to ingest."""
    series_id: str
    description: str
    frequency: str
    category: str
    units: str = "lin"


@dataclass
class FetchResult:
    """Result container for a single series fetch."""
    series_id: str
    observations: pd.DataFrame
    metadata: dict
    fetched_at: datetime
    revision_detected: bool = False
    prior_hash: Optional[str] = None
    current_hash: Optional[str] = None


SERIES_REGISTRY: list[SeriesConfig] = [
    SeriesConfig("UNRATE",   "Unemployment Rate",             "m", "labor"),
    SeriesConfig("PAYEMS",   "Nonfarm Payrolls",              "m", "labor"),
    SeriesConfig("JTSJOL",   "Job Openings",                  "m", "labor"),
    SeriesConfig("CPIAUCSL", "CPI All Urban Consumers",       "m", "inflation"),
    SeriesConfig("PCEPI",    "PCE Price Index",               "m", "inflation"),
    SeriesConfig("CPILFESL", "Core CPI ex Food & Energy",     "m", "inflation"),
    SeriesConfig("GDP",      "Gross Domestic Product",        "q", "gdp"),
    SeriesConfig("GDPC1",    "Real GDP",                      "q", "gdp"),
    SeriesConfig("FEDFUNDS", "Federal Funds Effective Rate",  "m", "monetary"),
    SeriesConfig("M2SL",     "M2 Money Supply",               "m", "monetary"),
    SeriesConfig("DGS10",    "10-Year Treasury",              "d", "yield"),
    SeriesConfig("DGS2",     "2-Year Treasury",               "d", "yield"),
    SeriesConfig("T10Y2Y",   "10Y-2Y Treasury Spread",        "d", "yield"),
    # Eighth Federal Reserve District — St. Louis Fed specific
    SeriesConfig("MOURN",    "Missouri Unemployment Rate",    "m", "regional"),
    SeriesConfig("MOSTHPI",  "Missouri House Price Index",    "q", "regional"),
]


class FREDLoader:
    """
    Fetches economic time series from the FRED public API.

    Design principles:
    - Retry logic with exponential backoff for transient failures
    - Schema validation on every response before writing
    - Revision detection by hashing observation values
    - Structured logging throughout for observability
    """

    BASE_URL = "https://api.stlouisfed.org/fred"
    MAX_RETRIES = 3
    RETRY_BACKOFF = 2.0
    RATE_LIMIT_DELAY = 0.5
    REQUIRED_OBS_COLUMNS = {"date", "value", "realtime_start", "realtime_end"}

    def __init__(
        self,
        api_key: str,
        output_dir: Path,
        series: Optional[list[SeriesConfig]] = None,
        observation_start: str = "2000-01-01",
    ):
        self.api_key = api_key
        self.output_dir = Path(output_dir)
        self.series = series or SERIES_REGISTRY
        self.observation_start = observation_start
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._hash_store: dict[str, str] = self._load_hash_store()

    def fetch_all(self) -> list[FetchResult]:
        """Fetch all series in the registry."""
        results = []
        for config in self.series:
            logger.info(f"Fetching {config.series_id} — {config.description}")
            try:
                result = self._fetch_series(config)
                self._write_parquet(result)
                self._update_hash_store(result)
                results.append(result)
                if result.revision_detected:
                    logger.warning(
                        f"REVISION DETECTED for {config.series_id}"
                    )
            except Exception as e:
                logger.error(f"Failed to fetch {config.series_id}: {e}")
            time.sleep(self.RATE_LIMIT_DELAY)
        self._save_hash_store()
        logger.info(f"Fetch complete. {len(results)}/{len(self.series)} series successful.")
        return results

    def _fetch_series(self, config: SeriesConfig) -> FetchResult:
        params = {
            "series_id": config.series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "observation_start": self.observation_start,
            "units": config.units,
            "frequency": config.frequency,
        }
        response_data = self._get_with_retry(
            f"{self.BASE_URL}/series/observations",
            params=params,
        )
        observations = self._parse_observations(response_data, config)
        self._validate_schema(observations, config.series_id)
        current_hash = self._hash_observations(observations)
        prior_hash = self._hash_store.get(config.series_id)
        revision_detected = prior_hash is not None and prior_hash != current_hash
        metadata = self._fetch_series_metadata(config.series_id)
        return FetchResult(
            series_id=config.series_id,
            observations=observations,
            metadata=metadata,
            fetched_at=datetime.now(timezone.utc),
            revision_detected=revision_detected,
            prior_hash=prior_hash,
            current_hash=current_hash,
        )

    def _get_with_retry(self, url: str, params: dict) -> dict:
        delay = self.RETRY_BACKOFF
        last_error = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                response = requests.get(url, params=params, timeout=30)
                response.raise_for_status()
                return response.json()
            except requests.HTTPError as e:
                if response.status_code == 429:
                    logger.warning(f"Rate limited. Waiting {delay}s (attempt {attempt})")
                    time.sleep(delay)
                    delay *= 2
                    last_error = e
                else:
                    raise
            except requests.RequestException as e:
                logger.warning(f"Request failed (attempt {attempt}/{self.MAX_RETRIES}): {e}")
                time.sleep(delay)
                delay *= 2
                last_error = e
        raise RuntimeError(f"All {self.MAX_RETRIES} retries failed. Last error: {last_error}")

    def _fetch_series_metadata(self, series_id: str) -> dict:
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
        }
        try:
            data = self._get_with_retry(f"{self.BASE_URL}/series", params=params)
            return data.get("seriess", [{}])[0]
        except Exception as e:
            logger.warning(f"Could not fetch metadata for {series_id}: {e}")
            return {}

    def _parse_observations(self, response_data: dict, config: SeriesConfig) -> pd.DataFrame:
        obs = response_data.get("observations", [])
        if not obs:
            return pd.DataFrame()
        df = pd.DataFrame(obs)
        df["value"] = df["value"].replace(".", None)
        df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df["realtime_start"] = pd.to_datetime(df["realtime_start"], format="%Y-%m-%d")
        # 9999-12-31 is FRED's sentinel for "currently active" — replace before parsing
        df["realtime_end"] = pd.to_datetime(
            df["realtime_end"].replace("9999-12-31", None), format="%Y-%m-%d"
        )
        df["series_id"] = config.series_id
        df["category"] = config.category
        df["frequency"] = config.frequency
        df["fetched_at"] = datetime.now(timezone.utc)
        return df[["series_id", "date", "value", "realtime_start",
                   "realtime_end", "category", "frequency", "fetched_at"]]

    def _validate_schema(self, df: pd.DataFrame, series_id: str) -> None:
        if df.empty:
            return
        missing = self.REQUIRED_OBS_COLUMNS - set(df.columns)
        if missing:
            raise ValueError(f"Schema validation failed for {series_id}. Missing: {missing}")
        null_dates = df["date"].isna().sum()
        if null_dates > 0:
            raise ValueError(f"Schema validation failed for {series_id}: {null_dates} null dates")

    def _hash_observations(self, df: pd.DataFrame) -> str:
        if df.empty:
            return ""
        canonical = df[["date", "value"]].sort_values("date").to_csv(index=False)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _write_parquet(self, result: FetchResult) -> None:
        if result.observations.empty:
            return
        out_path = self.output_dir / f"{result.series_id}.parquet"
        result.observations.to_parquet(out_path, index=False)

    def _load_hash_store(self) -> dict:
        hash_path = self.output_dir / "_revision_hashes.json"
        if hash_path.exists():
            with open(hash_path) as f:
                return json.load(f)
        return {}

    def _update_hash_store(self, result: FetchResult) -> None:
        if result.current_hash:
            self._hash_store[result.series_id] = result.current_hash

    def _save_hash_store(self) -> None:
        hash_path = self.output_dir / "_revision_hashes.json"
        with open(hash_path, "w") as f:
            json.dump(self._hash_store, f, indent=2)