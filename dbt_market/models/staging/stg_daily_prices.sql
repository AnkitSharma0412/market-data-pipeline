with source as (
    select * from {{ source('raw', 'daily_prices') }}
),

deduped as (
    select
        ticker,
        trade_date,
        open_price,
        high_price,
        low_price,
        close_price,
        volume,
        row_number() over (
            partition by ticker, trade_date
            order by ingested_at desc
        ) as rn
    from source
)

select
    ticker,
    trade_date,
    open_price,
    high_price,
    low_price,
    close_price,
    volume
from deduped
where rn = 1