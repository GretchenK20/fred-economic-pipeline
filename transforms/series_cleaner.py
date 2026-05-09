"""
series_cleaner.py

Transform layer for FRED economic time series.
Handles frequency alignment, anomaly detection, gap flagging,
and revision summary reporting.
"""

import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class SeriesCleaner:
    """
    Cleans and prepares raw FRED observations for dbt ingestion.

    Responsibilities:
    - Deduplication of observations
    - Anomaly detection (z-score based outlier flagging)
    - Gap detection (missing expected release dates)
    - Derived columns (MoM/YoY change, rolling averages)
    - Revision summary reporting
    """

    ANOMALY_ZSCORE_THRESHOLD = 4.0

    def __init__(self, raw_dir: Path, output_dir: Path):
        self.raw_dir = Path(raw_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def clean_all(self) -> pd.DataFrame:
        """Load all raw Parquet files, clean each, return combined DataFrame."""
        parquet_files = list(self.raw_dir.glob("*.parquet"))
        if not parquet_files:
            logger.warning(f"No Parquet files found in {self.raw_dir}")
            return pd.DataFrame()

        cleaned_frames = []
        for path in parquet_files:
            if path.name.startswith("_"):
                continue
            series_id = path.stem
            try:
                df = pd.read_parquet(path)
                cleaned = self.clean_series(df, series_id)
                cleaned_frames.append(cleaned)
                logger.info(
                    f"{series_id}: {len(cleaned)} clean rows, "
                    f"{cleaned['is_anomaly'].sum()} anomalies, "
                    f"{cleaned['is_gap'].sum()} gaps"
                )
            except Exception as e:
                logger.error(f"Failed to clean {series_id}: {e}")

        if not cleaned_frames:
            return pd.DataFrame()

        combined = pd.concat(cleaned_frames, ignore_index=True)
        out_path = self.output_dir / "cleaned_observations.parquet"
        combined.to_parquet(out_path, index=False)
        logger.info(f"Wrote {len(combined)} total rows to {out_path}")
        return combined

    def clean_series(self, df: pd.DataFrame, series_id: str) -> pd.DataFrame:
        """Clean a single series DataFrame."""
        df = df.copy()
        df = self._deduplicate(df, series_id)
        df = self._standardize_types(df)
        df = self._flag_anomalies(df, series_id)
        df = self._flag_gaps(df, series_id)
        df = self._add_derived_columns(df)
        return df

    def _deduplicate(self, df: pd.DataFrame, series_id: str) -> pd.DataFrame:
        """Remove duplicate dates, keeping most recent realtime_start."""
        before = len(df)
        df = (
            df.sort_values("realtime_start", ascending=False)
            .drop_duplicates(subset=["series_id", "date"], keep="first")
            .sort_values("date")
            .reset_index(drop=True)
        )
        dropped = before - len(df)
        if dropped > 0:
            logger.debug(f"{series_id}: Dropped {dropped} duplicate observations")
        return df

    def _standardize_types(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ensure consistent types across all series."""
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df["realtime_start"] = pd.to_datetime(df["realtime_start"])
        df["realtime_end"] = pd.to_datetime(df["realtime_end"])
        return df

    def _flag_anomalies(self, df: pd.DataFrame, series_id: str) -> pd.DataFrame:
        """Flag statistical outliers using z-score."""
        df["is_anomaly"] = False
        df["anomaly_zscore"] = np.nan

        non_null = df["value"].dropna()
        if len(non_null) < 10:
            return df

        mean = non_null.mean()
        std = non_null.std()

        if std == 0:
            return df

        df["anomaly_zscore"] = ((df["value"] - mean) / std).abs()
        df["is_anomaly"] = df["anomaly_zscore"] > self.ANOMALY_ZSCORE_THRESHOLD

        n_anomalies = df["is_anomaly"].sum()
        if n_anomalies > 0:
            logger.warning(f"{series_id}: {n_anomalies} anomalous values flagged")
        return df

    def _flag_gaps(self, df: pd.DataFrame, series_id: str) -> pd.DataFrame:
        """Detect gaps in time series based on expected frequency."""
        df["is_gap"] = False

        if df.empty or "frequency" not in df.columns:
            return df

        frequency = df["frequency"].iloc[0]
        freq_map = {"d": "B", "m": "MS", "q": "QS", "a": "AS"}
        pd_freq = freq_map.get(frequency)

        if not pd_freq:
            return df

        expected_dates = pd.date_range(
            start=df["date"].min(),
            end=df["date"].max(),
            freq=pd_freq,
        )

        actual_dates = set(df["date"].dt.normalize())
        missing_dates = [d for d in expected_dates if d not in actual_dates]

        if missing_dates:
            logger.warning(f"{series_id}: {len(missing_dates)} gap(s) detected")
            gap_rows = pd.DataFrame({
                "series_id": series_id,
                "date": pd.to_datetime(missing_dates),
                "value": np.nan,
                "is_gap": True,
                "category": df["category"].iloc[0] if "category" in df.columns else None,
                "frequency": frequency,
                "fetched_at": df["fetched_at"].iloc[0] if "fetched_at" in df.columns else None,
                "realtime_start": pd.array([pd.NaT] * len(missing_dates), dtype="datetime64[ns]"),
                "realtime_end": pd.array([pd.NaT] * len(missing_dates), dtype="datetime64[ns]"),
            }).reindex(columns=df.columns)
            df = pd.concat([df, gap_rows], ignore_index=True).sort_values("date")

        return df

    def _add_derived_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add period-over-period change and rolling average columns."""
        df = df.sort_values("date").reset_index(drop=True)
        frequency = df["frequency"].iloc[0] if "frequency" in df.columns and not df.empty else "m"
        # Periods per year by frequency code
        periods_per_year = {"d": 252, "m": 12, "q": 4, "a": 1}
        yoy_periods = periods_per_year.get(frequency, 12)
        df["value_mom_change"] = df["value"].pct_change(1, fill_method=None)
        df["value_yoy_change"] = df["value"].pct_change(yoy_periods, fill_method=None)
        df["value_rolling_12m"] = df["value"].rolling(yoy_periods, min_periods=3).mean()
        return df

    def summarize_revisions(
        self,
        current: pd.DataFrame,
        prior: pd.DataFrame,
        series_id: str,
    ) -> dict:
        """Compare current and prior DataFrames to identify revised values."""
        if current.empty or prior.empty:
            return {"series_id": series_id, "revision_count": 0, "details": []}

        merged = current[["date", "value"]].merge(
            prior[["date", "value"]],
            on="date",
            suffixes=("_current", "_prior"),
        )

        revised = merged[
            merged["value_current"].notna()
            & merged["value_prior"].notna()
            & (merged["value_current"] != merged["value_prior"])
        ].copy()

        revised["absolute_change"] = (
            revised["value_current"] - revised["value_prior"]
        ).abs()

        return {
            "series_id": series_id,
            "revision_count": len(revised),
            "max_absolute_change": float(revised["absolute_change"].max()) if not revised.empty else 0,
            "details": revised[["date", "value_prior", "value_current", "absolute_change"]].to_dict(orient="records")[:10],
        }