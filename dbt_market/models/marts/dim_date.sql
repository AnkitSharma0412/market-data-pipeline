select distinct
    trade_date,
    extract(dow from trade_date) as day_of_week,
    extract(month from trade_date) as month,
    extract(year from trade_date) as year
from {{ ref('stg_daily_prices') }}