select *
from {{ ref('fact_daily_prices') }}
where open_price <= 0 or close_price <= 0