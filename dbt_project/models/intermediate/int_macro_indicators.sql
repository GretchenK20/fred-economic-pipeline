-- int_macro_indicators.sql
-- Intermediate layer: joins related series, aligns frequencies,
-- calculates composite economic indicators

with base as (
    select * from {{ ref('stg_fred_series') }}
    where not is_gap
      and not is_anomaly
),

labor as (
    select
        observation_date,
        max(case when series_id = 'UNRATE'  then value end) as unemployment_rate,
        max(case when series_id = 'PAYEMS'  then value end) as nonfarm_payrolls,
        max(case when series_id = 'JTSJOL'  then value end) as job_openings,
        max(case when series_id = 'UNRATE'  then value_yoy_pct_change end) as unemployment_yoy
    from base
    where category = 'labor'
    group by 1
),

inflation as (
    select
        observation_date,
        max(case when series_id = 'CPIAUCSL'  then value end) as cpi,
        max(case when series_id = 'PCEPI'     then value end) as pce,
        max(case when series_id = 'CPILFESL'  then value end) as core_cpi,
        max(case when series_id = 'CPIAUCSL'  then value_yoy_pct_change end) as cpi_yoy,
        max(case when series_id = 'PCEPI'     then value_yoy_pct_change end) as pce_yoy
    from base
    where category = 'inflation'
    group by 1
),

monetary as (
    select
        observation_date,
        max(case when series_id = 'FEDFUNDS' then value end) as fed_funds_rate,
        max(case when series_id = 'M2SL'     then value end) as m2_money_supply,
        max(case when series_id = 'M2SL'     then value_yoy_pct_change end) as m2_yoy_growth
    from base
    where category = 'monetary'
    group by 1
),

yield_monthly as (
    select
        date_trunc('month', observation_date)::date  as observation_date,
        avg(case when series_id = 'DGS10'   then value end) as treasury_10y_avg,
        avg(case when series_id = 'DGS2'    then value end) as treasury_2y_avg,
        avg(case when series_id = 'T10Y2Y'  then value end) as yield_spread_10y2y_avg,
        avg(case when series_id = 'T10Y2Y'  then value end) < 0 as yield_curve_inverted
    from base
    where category = 'yield'
    group by 1
),

joined as (
    select
        coalesce(
            lab.observation_date,
            inf.observation_date,
            mon.observation_date,
            yld.observation_date
        )                                               as observation_date,
        lab.unemployment_rate,
        lab.nonfarm_payrolls,
        lab.job_openings,
        lab.unemployment_yoy,
        inf.cpi,
        inf.pce,
        inf.core_cpi,
        inf.cpi_yoy,
        inf.pce_yoy,
        mon.fed_funds_rate,
        mon.m2_money_supply,
        mon.m2_yoy_growth,
        yld.treasury_10y_avg,
        yld.treasury_2y_avg,
        yld.yield_spread_10y2y_avg,
        coalesce(yld.yield_curve_inverted, false)       as yield_curve_inverted,
        case
            when mon.fed_funds_rate is not null and inf.cpi_yoy is not null
            then mon.fed_funds_rate - (inf.cpi_yoy * 100)
        end                                             as real_fed_funds_rate,
        current_timestamp                               as dbt_loaded_at
    from labor        lab
    full outer join inflation    inf on lab.observation_date = inf.observation_date
    full outer join monetary     mon on coalesce(lab.observation_date, inf.observation_date) = mon.observation_date
    full outer join yield_monthly yld on coalesce(lab.observation_date, inf.observation_date, mon.observation_date) = yld.observation_date
)

select * from joined
where observation_date is not null
order by observation_date