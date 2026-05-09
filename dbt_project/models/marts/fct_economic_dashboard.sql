-- fct_economic_dashboard.sql
-- Final mart: production-ready economic dashboard facts
-- One row per month, all key macro indicators in one place

with indicators as (
    select * from {{ ref('int_macro_indicators') }}
),

recession_periods as (
    select * from (values
        (date '2001-03-01', date '2001-11-01', 'Dot-com recession'),
        (date '2007-12-01', date '2009-06-01', 'Great Financial Crisis'),
        (date '2020-02-01', date '2020-04-01', 'COVID-19 recession')
    ) as t(recession_start, recession_end, recession_name)
),

with_recession as (
    select
        i.*,
        r.recession_name,
        case
            when r.recession_start is not null then true
            else false
        end                                         as in_recession,
        case
            when r.recession_start is not null then 'Recession'
            when i.unemployment_rate < 4.5
             and i.cpi_yoy > 0.015
             and i.yield_spread_10y2y_avg > 0 then 'Expansion'
            when i.unemployment_rate >= 4.5
             and i.cpi_yoy < 0.02 then 'Slowdown'
            else 'Uncertain'
        end                                         as business_cycle_phase
    from indicators i
    left join recession_periods r
           on i.observation_date between r.recession_start and r.recession_end
),

with_trends as (
    select
        *,
        avg(unemployment_rate) over (
            order by observation_date
            rows between 2 preceding and current row
        )                                           as unemployment_3m_avg,
        avg(cpi_yoy) over (
            order by observation_date
            rows between 2 preceding and current row
        )                                           as inflation_3m_avg,
        case
            when unemployment_rate < lag(unemployment_rate, 3) over (order by observation_date)
            then 'Improving'
            when unemployment_rate > lag(unemployment_rate, 3) over (order by observation_date)
            then 'Deteriorating'
            else 'Stable'
        end                                         as labor_market_trend,
        case
            when cpi_yoy < lag(cpi_yoy, 3) over (order by observation_date)
            then 'Decelerating'
            when cpi_yoy > lag(cpi_yoy, 3) over (order by observation_date)
            then 'Accelerating'
            else 'Stable'
        end                                         as inflation_trend,
        lag(unemployment_rate, 12) over (
            order by observation_date
        )                                           as unemployment_rate_1y_ago,
        lag(fed_funds_rate, 12) over (
            order by observation_date
        )                                           as fed_funds_rate_1y_ago
    from with_recession
)

select
    observation_date,
    extract(year from observation_date)             as observation_year,
    extract(month from observation_date)            as observation_month,
    to_char(observation_date, 'YYYY-MM')            as year_month,
    in_recession,
    recession_name,
    business_cycle_phase,
    unemployment_rate,
    unemployment_3m_avg,
    labor_market_trend,
    unemployment_rate_1y_ago,
    round(cast(unemployment_rate - unemployment_rate_1y_ago as numeric), 2) as unemployment_yoy_delta,
    nonfarm_payrolls,
    job_openings,
    fed_funds_rate,
    fed_funds_rate_1y_ago,
    round(cast(fed_funds_rate - fed_funds_rate_1y_ago as numeric), 2) as fed_funds_yoy_delta,
    real_fed_funds_rate,
    m2_money_supply,
    round(cast(m2_yoy_growth * 100 as numeric), 2)  as m2_yoy_pct,
    cpi,
    core_cpi,
    pce,
    round(cast(cpi_yoy * 100 as numeric), 2)        as cpi_yoy_pct,
    round(cast(pce_yoy * 100 as numeric), 2)        as pce_yoy_pct,
    inflation_3m_avg,
    inflation_trend,
    treasury_10y_avg,
    treasury_2y_avg,
    round(cast(yield_spread_10y2y_avg as numeric), 3) as yield_spread_10y2y,
    yield_curve_inverted,
    dbt_loaded_at
from with_trends
where observation_date is not null
order by observation_date