-- high price should never be below low price
select *
from {{ ref('fact_daily_prices') }}
where high_price < low_price