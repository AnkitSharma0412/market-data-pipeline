with returns as (
    select ticker, trade_date, daily_return
    from {{ ref('fact_stock_metrics') }}
    where ticker != 'SPY'
),

weighted as (
    select
        r.trade_date,
        r.ticker,
        r.daily_return,
        p.weight,
        r.daily_return * p.weight as weighted_return
    from returns r
    inner join {{ ref('dim_portfolio_position') }} p
        on r.ticker = p.ticker
)

select
    trade_date,
    sum(weighted_return) as portfolio_daily_return
from weighted
group by trade_date