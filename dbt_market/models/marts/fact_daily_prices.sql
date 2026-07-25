select
    ticker,
    trade_date,
    open_price,
    high_price,
    low_price,
    close_price,
    volume
from {{ ref('stg_daily_prices') }}