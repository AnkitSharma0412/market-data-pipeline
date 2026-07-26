with metrics as (
    select * from {{ ref('fact_stock_metrics') }}
),

benchmark as (
    select
        trade_date,
        daily_return as benchmark_return
    from metrics
    where ticker = 'SPY'
)

select
    m.ticker,
    m.trade_date,
    m.close_price,
    m.daily_return,
    b.benchmark_return,
    m.daily_return - b.benchmark_return as excess_return_vs_benchmark
from metrics m
left join benchmark b
    on m.trade_date = b.trade_date
where m.ticker != 'SPY'