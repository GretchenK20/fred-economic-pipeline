-- stg_fred_series.sql
-- Staging layer: type casting, column standardization, null handling
-- One row per (series_id, date) observation

with source as (
    select * from {{ source('fred_raw', 'cleaned_observations') }}
),

staged as (
    select
        -- Keys
        series_id                                       as series_id,
        cast(date as date)                             as observation_date,

        -- Core value
        cast(value as double precision)                as value,

        -- Quality flags
        coalesce(is_anomaly, false)                    as is_anomaly,
        coalesce(is_gap, false)                        as is_gap,
        cast(anomaly_zscore as double precision)       as anomaly_zscore,

        -- Derived metrics
        cast(value_mom_change as double precision)     as value_mom_pct_change,
        cast(value_yoy_change as double precision)     as value_yoy_pct_change,
        cast(value_rolling_12m as double precision)    as value_rolling_12m_avg,

        -- Metadata
        category,
        frequency,
        cast(realtime_start as date)                   as realtime_start,
        cast(realtime_end as date)                     as realtime_end,
        cast(fetched_at as timestamp)                  as fetched_at,

        -- Audit
        current_timestamp                              as dbt_loaded_at

    from source
    where series_id is not null
      and date is not null
)

select * from staged