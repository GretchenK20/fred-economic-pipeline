"""
test_fred_loader.py

Unit tests for the FREDLoader class.
Uses mocking to isolate API behavior — no real HTTP calls.
"""

import pytest
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd

from ingestion.fred_loader import FREDLoader, SeriesConfig, SERIES_REGISTRY


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_output(tmp_path):
    return tmp_path / "raw"


@pytest.fixture
def loader(tmp_output):
    return FREDLoader(
        api_key="test_api_key",
        output_dir=tmp_output,
        observation_start="2020-01-01",
    )


@pytest.fixture
def sample_api_response():
    return {
        "observations": [
            {
                "realtime_start": "2024-01-01",
                "realtime_end": "9999-12-31",
                "date": "2024-01-01",
                "value": "3.7",
            },
            {
                "realtime_start": "2024-02-01",
                "realtime_end": "9999-12-31",
                "date": "2024-02-01",
                "value": "3.9",
            },
            {
                "realtime_start": "2024-03-01",
                "realtime_end": "9999-12-31",
                "date": "2024-03-01",
                "value": ".",
            },
        ]
    }


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------

class TestSchemaValidation:

    def test_valid_schema_passes(self, loader):
        df = pd.DataFrame({
            "series_id": ["UNRATE"],
            "date": [pd.Timestamp("2024-01-01")],
            "value": [3.7],
            "realtime_start": [pd.Timestamp("2024-01-01")],
            "realtime_end": [pd.Timestamp("9999-12-31")],
            "category": ["labor"],
            "frequency": ["m"],
            "fetched_at": [datetime.now(timezone.utc)],
        })
        loader._validate_schema(df, "UNRATE")

    def test_missing_column_raises(self, loader):
        df = pd.DataFrame({
            "series_id": ["UNRATE"],
            "value": [3.7],
        })
        with pytest.raises(ValueError, match="Missing"):
            loader._validate_schema(df, "UNRATE")

    def test_null_dates_raise(self, loader):
        df = pd.DataFrame({
            "series_id": ["UNRATE"],
            "date": [None],
            "value": [3.7],
            "realtime_start": [pd.Timestamp("2024-01-01")],
            "realtime_end": [pd.Timestamp("9999-12-31")],
        })
        with pytest.raises(ValueError, match="null dates"):
            loader._validate_schema(df, "UNRATE")

    def test_empty_dataframe_passes(self, loader):
        loader._validate_schema(pd.DataFrame(), "UNRATE")


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------

class TestParsing:

    def test_missing_value_sentinel_becomes_null(self, loader, sample_api_response):
        config = SeriesConfig("UNRATE", "Unemployment Rate", "m", "labor")
        df = loader._parse_observations(sample_api_response, config)
        assert df["value"].isna().sum() == 1

    def test_dates_are_datetime(self, loader, sample_api_response):
        config = SeriesConfig("UNRATE", "Unemployment Rate", "m", "labor")
        df = loader._parse_observations(sample_api_response, config)
        assert pd.api.types.is_datetime64_any_dtype(df["date"])

    def test_values_are_numeric(self, loader, sample_api_response):
        config = SeriesConfig("UNRATE", "Unemployment Rate", "m", "labor")
        df = loader._parse_observations(sample_api_response, config)
        assert pd.api.types.is_float_dtype(df["value"])

    def test_metadata_columns_added(self, loader, sample_api_response):
        config = SeriesConfig("UNRATE", "Unemployment Rate", "m", "labor")
        df = loader._parse_observations(sample_api_response, config)
        assert "series_id" in df.columns
        assert "category" in df.columns
        assert df["series_id"].iloc[0] == "UNRATE"

    def test_empty_observations_returns_empty_df(self, loader):
        config = SeriesConfig("UNRATE", "Unemployment Rate", "m", "labor")
        df = loader._parse_observations({"observations": []}, config)
        assert df.empty


# ---------------------------------------------------------------------------
# Revision detection tests
# ---------------------------------------------------------------------------

class TestRevisionDetection:

    def test_identical_data_same_hash(self, loader):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-01", "2024-02-01"]),
            "value": [3.7, 3.9],
        })
        assert loader._hash_observations(df) == loader._hash_observations(df)

    def test_changed_value_different_hash(self, loader):
        df1 = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-01"]),
            "value": [3.7],
        })
        df2 = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-01"]),
            "value": [3.8],
        })
        assert loader._hash_observations(df1) != loader._hash_observations(df2)

    def test_empty_df_returns_empty_hash(self, loader):
        assert loader._hash_observations(pd.DataFrame()) == ""


# ---------------------------------------------------------------------------
# Series registry tests
# ---------------------------------------------------------------------------

class TestSeriesRegistry:

    def test_all_series_have_required_fields(self):
        for config in SERIES_REGISTRY:
            assert config.series_id
            assert config.description
            assert config.frequency in ("d", "m", "q", "a")
            assert config.category

    def test_no_duplicate_series_ids(self):
        ids = [s.series_id for s in SERIES_REGISTRY]
        assert len(ids) == len(set(ids))

    def test_yield_curve_series_present(self):
        ids = {s.series_id for s in SERIES_REGISTRY}
        assert "DGS10" in ids
        assert "DGS2" in ids
        assert "T10Y2Y" in ids

    def test_eighth_district_series_present(self):
        ids = {s.series_id for s in SERIES_REGISTRY}
        assert "MOURN" in ids