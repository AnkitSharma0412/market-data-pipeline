# Market Data ELT Pipeline + Natural Language Analytics

Extracts daily OHLCV price data for a small equity/ETF basket (AAPL, MSFT,
GOOGL, JPM, GS, SPY), orchestrates ingestion with Airflow running in Docker,
lands raw data in Snowflake, transforms it into an analytics-ready star
schema with dbt, and exposes it through a natural-language chat interface
so non-technical users can ask questions without writing SQL.

## Architecture
Alpha Vantage API → Python extraction script → Airflow (Docker)
    → Snowflake (raw)
    → dbt (staging → marts)
        - dim_ticker, dim_date, dim_portfolio_position (seed)
        - fact_daily_prices (incremental)
        - fact_stock_metrics (returns, rolling volatility, moving averages)
        - fact_benchmark_comparison (alpha vs. SPY)
        - fact_portfolio_performance (weighted portfolio returns)
        - fact_portfolio_risk (parametric VaR, 95%/99%)
    → dbt tests (schema + custom + CI via GitHub Actions)
    → dbt docs (lineage)
    → semantic model (YAML) → Streamlit chat app (NL-to-SQL via Groq API)

## Stack

- **Extraction**: Python (`requests`), rate-limit aware (Alpha Vantage free tier)
- **Orchestration**: Apache Airflow, running in Docker, scheduled after
  US market close on weekdays; chains extraction → dbt seed → dbt run → dbt test
- **Warehouse**: Snowflake
- **Transformation**: dbt — staging models, a star schema, and derived
  analytics marts covering stock-level and portfolio-level metrics
- **Testing**: dbt tests — not_null, unique, relationships, plus custom
  domain checks (high >= low, no negative prices, extreme single-day
  return flagging); automated on every PR via GitHub Actions
- **Natural language interface**: a semantic model (YAML) describing the
  marts in business terms, powering a Streamlit chat app that translates
  plain-English questions into SQL using the Groq API (Llama 3.3), with a
  free-web-search fallback for questions outside the dataset

## What this demonstrates

- ELT pattern: raw data lands untransformed, all transformation logic
  lives in version-controlled dbt SQL
- Idempotent, retry-aware orchestration (Airflow retries, rate-limit handling,
  incremental models)
- Dimensional modeling (fact/dimension tables) plus portfolio-level derived
  analytics (weighted returns, alpha vs. benchmark, parametric VaR)
- Data quality testing as a first-class part of the pipeline, enforced in CI
- Documented lineage via dbt docs
- A semantic layer that makes the data usable by non-technical stakeholders,
  not just engineers — including deliberate safety guardrails (read-only
  Snowflake role, SELECT-only query validation) around LLM-generated SQL

## How to run it

**Pipeline:**
1. Set up a `.env` file with Alpha Vantage, Snowflake, and Groq credentials
2. `python scripts/extract_load.py` — test extraction manually
3. `docker compose up -d` (from the Airflow project folder) — start orchestration
4. Trigger `market_data_pipeline` DAG from the Airflow UI at localhost:8080
   (runs extraction, dbt seed, dbt run, and dbt test in sequence)
5. `dbt docs generate && dbt docs serve` — view the lineage graph

**Natural language app:**
6. `cd streamlit_app`
7. Add Snowflake, Groq, and an `APP_PASSWORD` to `.streamlit/secrets.toml`
   (see `streamlit_app.py` for the exact keys expected)
8. `streamlit run streamlit_app.py` — chat with the data locally
9. Deployed version: hosted on Streamlit Community Cloud, password-protected
   (link in repo description)

## CI/CD

Every pull request against `main` triggers `.github/workflows/dbt_ci.yml`,
which runs `dbt run` and `dbt test` against a dedicated `CI` schema using
GitHub Secrets for credentials — broken transformation logic is caught
before merge, not after.

## Notes on design decisions

- **Snowflake Cortex Analyst/Agents** were the original plan for the NL
  interface, but Cortex's LLM-backed features are gated on trial accounts
  without a payment method. Rather than upgrade, the semantic model YAML
  was repurposed into a custom NL-to-SQL layer using the free-tier Groq
  API — same schema definition, different execution engine.
- **VaR** is computed using the parametric (variance-covariance) method
  rather than historical simulation — a deliberate simplification, reusing
  the rolling-volatility calculation already built for stock-level metrics.
- **LLM-generated SQL is never trusted blindly**: every query is checked
  against a SELECT-only keyword filter and run under a dedicated read-only
  Snowflake role, as defense in depth.

[Add your dbt docs lineage graph screenshot here]
<img width="1302" height="539" alt="image" src="https://github.com/user-attachments/assets/1ba44f73-7dad-4882-b489-2a09241b7071" />

[Add a screenshot of a query against fact_stock_metrics here]
<img width="1213" height="433" alt="image" src="https://github.com/user-attachments/assets/fcf9909e-128a-41fc-ba89-9d66d0129607" />


