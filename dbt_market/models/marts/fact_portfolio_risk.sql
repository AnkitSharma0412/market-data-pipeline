with portfolio as (
    select * from {{ ref('fact_portfolio_performance') }}
),

with_stats as (
    select
        trade_date,
        portfolio_daily_return,
        avg(portfolio_daily_return) over (
            order by trade_date rows between 19 preceding and current row
        ) as rolling_20d_avg_return,
        stddev(portfolio_daily_return) over (
            order by trade_date rows between 19 preceding and current row
        ) as rolling_20d_volatility
    from portfolio
)

select
    trade_date,
    portfolio_daily_return,
    rolling_20d_avg_return,
    rolling_20d_volatility,
    rolling_20d_avg_return - 1.645 * rolling_20d_volatility as var_95_1day,
    rolling_20d_avg_return - 2.326 * rolling_20d_volatility as var_99_1day
from with_stats