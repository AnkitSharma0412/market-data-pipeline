import os
import json
import yaml
import streamlit as st
from groq import Groq
import snowflake.connector

st.set_page_config(page_title="Portfolio Q&A Assistant", page_icon="📈")

# ---------------------------------------------------------------------------
# Secrets: works both locally (via .env, loaded by your terminal beforehand
# or python-dotenv) and on Streamlit Community Cloud (via st.secrets, set in
# the app's Settings -> Secrets, never committed to GitHub).
# ---------------------------------------------------------------------------
def get_secret(key):
    # Streamlit Cloud provides st.secrets; locally, fall back to env vars.
    if key in st.secrets:
        return st.secrets[key]
    return os.environ.get(key)


# ---------------------------------------------------------------------------
# Simple password gate. This app calls paid-adjacent resources (Snowflake
# trial credits, Groq's rate-limited free tier) -- without this, anyone with
# the URL could run up usage against your accounts. Set APP_PASSWORD in
# Streamlit Cloud's secrets (never hardcode it here).
# ---------------------------------------------------------------------------
def check_password():
    def password_entered():
        if st.session_state["password"] == get_secret("APP_PASSWORD"):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        st.error("Incorrect password")
        return False
    else:
        return True


if not check_password():
    st.stop()

# ---------------------------------------------------------------------------
# Core NL-to-SQL logic (same as nl_query.py, adapted for Streamlit's secrets)
# ---------------------------------------------------------------------------
client = Groq(api_key=get_secret("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"


@st.cache_data
def load_semantic_model():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(script_dir, "market_semantic_model.yaml")
    with open(path, "r") as f:
        return yaml.safe_load(f)


def build_schema_context(semantic_model):
    lines = []
    for table in semantic_model["tables"]:
        lines.append(f"\nTable: MARKET_DB.MARTS.{table['base_table']['table']}")
        lines.append(f"Description: {table['description']}")
        for dim in table.get("dimensions", []):
            syns = ", ".join(dim.get("synonyms", []))
            lines.append(f"  - {dim['name']} ({dim['data_type']}): {dim.get('description', '')} [also called: {syns}]")
        for fact in table.get("facts", []):
            syns = ", ".join(fact.get("synonyms", []))
            lines.append(f"  - {fact['name']} ({fact['data_type']}): {fact.get('description', '')} [also called: {syns}]")
    return "\n".join(lines)


def generate_sql(question, schema_context):
    system_prompt = f"""You are a SQL generation assistant for a stock market analytics database in Snowflake.

Available tables and columns:
{schema_context}

Rules:
- Only generate SELECT statements. Never generate INSERT, UPDATE, DELETE, DROP, ALTER, or any other statement.
- Use fully qualified table names (MARKET_DB.MARTS.<table>).
- daily_return, portfolio_daily_return, excess_return_vs_benchmark, var_95_1day, and var_99_1day are stored as decimal fractions (0.01 = 1%), not percentages.
- Return ONLY the SQL query, no explanation, no markdown formatting, no backticks.
- If the question cannot be answered with the available tables, return exactly: NO_QUERY_POSSIBLE
"""
    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=500,
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
    )
    sql = response.choices[0].message.content.strip()
    return sql.replace("```sql", "").replace("```", "").strip()


def is_safe_select(sql):
    normalized = sql.strip().upper()
    if not normalized.startswith("SELECT"):
        return False
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "MERGE", "CREATE", "GRANT", "REVOKE"]
    return not any(word in normalized for word in forbidden)


def run_query(sql):
    conn = snowflake.connector.connect(
        account=get_secret("SNOWFLAKE_ACCOUNT"),
        user=get_secret("SNOWFLAKE_USER"),
        password=get_secret("SNOWFLAKE_PASSWORD"),
        warehouse=get_secret("SNOWFLAKE_WAREHOUSE"),
        database="MARKET_DB",
        schema="MARTS",
        role="READONLY_ANALYST",
    )
    cur = conn.cursor()
    try:
        cur.execute(sql)
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        return columns, rows
    finally:
        cur.close()
        conn.close()


def summarize_answer(question, columns, rows):
    if not rows:
        return "No data found for that question."
    data_preview = json.dumps([dict(zip(columns, row)) for row in rows[:20]], default=str)
    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=300,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": "You answer questions about stock market data concisely, in plain English, "
                           "for a business user with no SQL knowledge. Format percentages clearly "
                           "(multiply decimal fractions by 100 and add a % sign). Be direct — lead with the answer.",
            },
            {
                "role": "user",
                "content": f"Question: {question}\n\nQuery result data: {data_preview}\n\nAnswer the question using this data.",
            },
        ],
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("📈 Portfolio Q&A Assistant")
st.caption("Ask about stock prices, returns, volatility, or portfolio performance — no SQL needed.")

with st.expander("Example questions"):
    st.markdown("""
    - What was AAPL's closing price on its most recent trading day?
    - What was the highest and lowest price for MSFT last week?
    - What's my total portfolio gain this month?
    - Which stock has been the most volatile recently?
    - Did AAPL outperform the market yesterday?
    """)

question = st.text_input("Ask a question:")

if question:
    with st.spinner("Thinking..."):
        try:
            semantic_model = load_semantic_model()
            schema_context = build_schema_context(semantic_model)
            sql = generate_sql(question, schema_context)

            if sql == "NO_QUERY_POSSIBLE":
                st.warning("I can't answer that with the available data.")
            elif not is_safe_select(sql):
                st.error(f"Blocked a potentially unsafe query: {sql}")
            else:
                with st.expander("Generated SQL (for transparency)"):
                    st.code(sql, language="sql")
                columns, rows = run_query(sql)
                answer = summarize_answer(question, columns, rows)
                st.success(answer)
        except Exception as e:
            st.error(f"Something went wrong: {e}")