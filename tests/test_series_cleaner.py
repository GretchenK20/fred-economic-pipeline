"""
test_series_cleaner.py

Unit tests for the SeriesCleaner transform class.
All tests use in-memory DataFrames — no file I/O.
"""

import pytest
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

from transforms.series_cleaner import SeriesCleaner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cleaner(tmp_path):
    return SeriesCleaner(
        raw_dir=tmp_path / "raw",
        output_dir=tmp_path / "cleaned",
    )


def make_series(values, dates=None, frequency="m", series_id="UNRATE"):
    """Helper to build a test DataFrame."""
    if dates is None:
        dates = pd.date_range("2020-01-01", periods=len(values), freq="MS")
    return pd.DataFrame({
        "series_id": series_id,
        "date": dates,
        "value": values,
        "realtime_start": dates,
        "realtime_end": pd.Timestamp("9999-12-31"),
        "category": "labor",
        "frequency": frequency,
        "fetched_at": datetime.now(timezone.utc),
    })


# ---------------------------------------------------------------------------
# Deduplication tests
# ---------------------------------------------------------------------------

class TestDeduplication:

    def test_removes_duplicate_dates(self, cleaner):
        df = make_series([3.7, 3.7])
        df["date"] = pd.to_datetime("2024-01-01")
        df["realtime_start"] = [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01")]
        cleaned = cleaner._deduplicate(df, "UNRATE")
        assert len(cleaned) == 1

    def test_keeps_most_recent_realtime_start(self, cleaner):
        df = make_series([3.7, 3.9])
        df["date"] = pd.to_datetime("2024-01-01")
        df["realtime_start"] = [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-06-01")]
        df["value"] = [3.7, 3.9]
        cleaned = cleaner._deduplicate(df, "UNRATE")
        assert cleaned["value"].iloc[0] == 3.9

    def test_no_duplicates_unchanged(self, cleaner):
        df = make_series([3.7, 3.9, 4.1])
        assert len(cleaner._deduplicate(df, "UNRATE")) == len(df)


# ---------------------------------------------------------------------------
# Anomaly detection tests
# ---------------------------------------------------------------------------

class TestAnomalyDetection:

    def test_normal_values_not_flagged(self, cleaner):
        values = [3.5, 3.6, 3.7, 3.8, 3.9, 4.0, 3.8, 3.7] * 5
        df = make_series(values)
        result = cleaner._flag_anomalies(df, "UNRATE")
        assert result["is_anomaly"].sum() == 0

    def test_extreme_outlier_flagged(self, cleaner):
        values = [3.5] * 20 + [99.0]
        df = make_series(values)
        result = cleaner._flag_anomalies(df, "UNRATE")
        assert result["is_anomaly"].iloc[-1] == True

    def test_constant_series_no_anomalies(self, cleaner):
        df = make_series([5.0] * 20)
        result = cleaner._flag_anomalies(df, "UNRATE")
        assert result["is_anomaly"].sum() == 0

    def test_short_series_skipped(self, cleaner):
        df = make_series([3.5, 3.6, 3.7])
        result = cleaner._flag_anomalies(df, "UNRATE")
        assert result["is_anomaly"].sum() == 0


# ---------------------------------------------------------------------------
# Gap detection tests
# ---------------------------------------------------------------------------

class TestGapDetection:

    def test_no_gaps_in_complete_series(self, cleaner):
        dates = pd.date_range("2020-01-01", "2023-12-01", freq="MS")
        df = make_series([3.5] * len(dates), dates=dates)
        result = cleaner._flag_gaps(df, "UNRATE")
        assert result["is_gap"].sum() == 0

    def test_detects_missing_month(self, cleaner):
        dates = pd.date_range("2020-01-01", "2020-12-01", freq="MS")
        dates = dates[dates != pd.Timestamp("2020-03-01")]
        df = make_series([3.5] * len(dates), dates=dates)
        result = cleaner._flag_gaps(df, "UNRATE")
        assert result["is_gap"].sum() == 1

    def test_gap_row_has_null_value(self, cleaner):
        dates = pd.date_range("2020-01-01", "2020-06-01", freq="MS")
        dates = dates[dates != pd.Timestamp("2020-03-01")]
        df = make_series([3.5] * len(dates), dates=dates)
        result = cleaner._flag_gaps(df, "UNRATE")
        gap_row = result[result["is_gap"] == True]
        assert gap_row["value"].isna().all()


# ---------------------------------------------------------------------------
# Derived columns tests
# ---------------------------------------------------------------------------

class TestDerivedColumns:

    def test_mom_change_calculated(self, cleaner):
        df = make_series([100.0, 102.0, 104.0, 103.0])
        result = cleaner._add_derived_columns(df)
        assert "value_mom_change" in result.columns
        assert abs(result["value_mom_change"].iloc[1] - 0.02) < 0.0001

    def test_rolling_average_calculated(self, cleaner):
        values = [float(i) for i in range(1, 25)]
        df = make_series(values)
        result = cleaner._add_derived_columns(df)
        assert "value_rolling_12m" in result.columns
        assert len(result["value_rolling_12m"].dropna()) > 0


# ---------------------------------------------------------------------------
# Revision summary tests
# ---------------------------------------------------------------------------

class TestRevisionSummary:

    def test_no_revisions_when_identical(self, cleaner):
        df = make_series([3.7, 3.9, 4.1])
        summary = cleaner.summarize_revisions(df, df.copy(), "UNRATE")
        assert summary["revision_count"] == 0

    def test_detects_single_revision(self, cleaner):
        current = make_series([3.7, 3.9, 4.1])
        prior = make_series([3.7, 3.8, 4.1])
        summary = cleaner.summarize_revisions(current, prior, "UNRATE")
        assert summary["revision_count"] == 1

    def test_empty_frames_return_zero(self, cleaner):
        summary = cleaner.summarize_revisions(
            pd.DataFrame(), pd.DataFrame(), "UNRATE"
        )
        assert summary["revision_count"] == 0