with prices as (
    select * from {{ ref('fact_daily_prices') }}
),

with_returns as (
    select
        *,
        (close_price / lag(close_price) over (
            partition by ticker order by trade_date
        ) - 1) as daily_return
    from prices
)

select
    ticker,
    trade_date,
    close_price,
    daily_return,
    stddev(daily_return) over (
        partition by ticker order by trade_date
        rows between 19 preceding and current row
    ) as rolling_20d_volatility,
    avg(close_price) over (
        partition by ticker order by trade_date
        rows between 49 preceding and current row
    ) as moving_avg_50d,
    avg(close_price) over (
        partition by ticker order by trade_date
        rows between 199 preceding and current row
    ) as moving_avg_200d
from with_returns