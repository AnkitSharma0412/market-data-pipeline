{{ config(
    materialized='incremental',
    unique_key=['ticker', 'trade_date']
) }}

select
    ticker,
    trade_date,
    open_price,
    high_price,
    low_price,
    close_price,
    volume
from {{ ref('stg_daily_prices') }}

{% if is_incremental() %}
where trade_date > (select max(trade_date) from {{ this }})
{% endif %}