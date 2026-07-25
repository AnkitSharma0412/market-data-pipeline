-- flags a >50% single-day move as worth investigating (data error vs. real event)
select *
from {{ ref('fact_stock_metrics') }}
where abs(daily_return) > 0.5