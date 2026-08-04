import os
import json
import yaml
import streamlit as st
from groq import Groq
import snowflake.connector
from duckduckgo_search import DDGS

st.set_page_config(page_title="Portfolio Q&A Assistant", page_icon="📈", layout="centered")

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------
def get_secret(key):
    if key in st.secrets:
        return st.secrets[key]
    return os.environ.get(key)


# ---------------------------------------------------------------------------
# Password gate
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
    return True


if not check_password():
    st.stop()

client = Groq(api_key=get_secret("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"

ABOUT_ME_ANSWER = """I'm a portfolio analytics assistant. Here's what I can help with:

**Stock prices** — open, high, low, close, and trading volume for AAPL, MSFT, GOOGL, JPM, GS, and SPY.

**Performance** — daily returns, rolling 20-day volatility, and 50/200-day moving averages per stock.

**Portfolio-level metrics** — your weighted portfolio's daily return, and Value at Risk (VaR) at 95%/99% confidence.

**Benchmark comparison** — how each stock performed against the SPY index (alpha).

Ask me things like *"What was AAPL's closing price yesterday?"* or *"What's my portfolio's VaR?"* — for anything outside this scope, I'll do a quick web search and give you my best general answer instead."""


# ---------------------------------------------------------------------------
# Semantic model
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Question routing
# ---------------------------------------------------------------------------
GREETINGS = {"hi", "hello", "hey", "hi there", "hello there", "good morning",
             "good afternoon", "good evening", "howdy", "yo", "sup", "hiya"}

ABOUT_ME_TRIGGERS = [
    "what can you do", "what do you know", "who are you", "what are you",
    "what is this", "help", "what can i ask", "what questions can i ask",
    "how do you work", "what data do you have",
]


def is_greeting(question):
    return question.strip().lower().rstrip("!.?") in GREETINGS


def is_about_me(question):
    normalized = question.strip().lower().rstrip("!.?")
    return any(trigger in normalized for trigger in ABOUT_ME_TRIGGERS)


def generate_sql(question, schema_context):
    system_prompt = f"""You are a SQL generation assistant for a stock market analytics database in Snowflake.

Available tables and columns:
{schema_context}

Rules:
- Generate EXACTLY ONE SELECT statement. Never return multiple statements, and never separate statements with semicolons or newlines containing another SELECT.
- Never generate INSERT, UPDATE, DELETE, DROP, ALTER, or any other statement.
- Use fully qualified table names (MARKET_DB.MARTS.<table>).
- Never use SELECT * — always select specific, relevant columns for the question.
- daily_return, portfolio_daily_return, excess_return_vs_benchmark, var_95_1day, and var_99_1day are stored as decimal fractions (0.01 = 1%), not percentages.
- Return ONLY the SQL query, no explanation, no markdown formatting, no backticks.
- If the question cannot be answered with the available tables, return exactly: NO_QUERY_POSSIBLE
"""
    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=300,
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
    )
    sql = response.choices[0].message.content.strip()
    sql = sql.replace("```sql", "").replace("```", "").strip()

    # Hard guard: even with the prompt rule above, models sometimes still
    # return multiple statements. Only ever keep the first one.
    first_statement = sql.split(";")[0].strip()
    lines = first_statement.splitlines()
    clean_lines = []
    seen_select = False
    for line in lines:
        if line.strip().upper().startswith("SELECT"):
            if seen_select:
                break
            seen_select = True
        clean_lines.append(line)
    return "\n".join(clean_lines).strip()


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
        return "I didn't find any data for that."
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


def web_search(query, max_results=4):
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception:
        return []


def answer_general_question(question):
    results = web_search(question)
    if results:
        context = "\n\n".join(f"{r['title']}: {r['body']} (Source: {r['href']})" for r in results)
        user_content = f"Question: {question}\n\nWeb search results:\n{context}"
        system_prompt = (
            "You are a friendly, helpful general-purpose assistant. Use the "
            "provided web search results to answer accurately and concisely. "
            "Cite sources briefly. If results don't fully answer it, say so."
        )
    else:
        user_content = question
        system_prompt = (
            "You are a friendly, helpful general-purpose assistant. Web search "
            "wasn't available, so answer using your own knowledge and mention "
            "that this wasn't verified with a live search."
        )
    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=400,
        temperature=0.4,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    return response.choices[0].message.content


def route_question(question):
    """Returns (answer_text, debug_sql_or_none)."""
    if is_greeting(question):
        return ("Hi! Ask me about stock prices, returns, volatility, or "
                "portfolio performance — or type 'what can you do' to see examples.", None)

    if is_about_me(question):
        return (ABOUT_ME_ANSWER, None)

    try:
        semantic_model = load_semantic_model()
        schema_context = build_schema_context(semantic_model)
        sql = generate_sql(question, schema_context)
    except Exception:
        return ("I'm having trouble understanding that right now — could you try rephrasing?", None)

    if sql == "NO_QUERY_POSSIBLE" or not sql:
        answer = answer_general_question(question)
        return (answer, None)

    if not is_safe_select(sql):
        return ("I'm afraid I can't run that request.", None)

    try:
        columns, rows = run_query(sql)
    except Exception:
        # Never surface raw SQL/database errors to the user.
        return ("I'm afraid I couldn't find an answer to that in the data — "
                "could you try asking it a different way?", sql)

    try:
        answer = summarize_answer(question, columns, rows)
    except Exception:
        return ("I found the data but had trouble summarizing it — please try again.", sql)

    return (answer, sql)


# ---------------------------------------------------------------------------
# UI — chat style
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .stChatMessage { border-radius: 12px; }
    div[data-testid="stChatInput"] { border-radius: 12px; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📈 Portfolio Q&A Assistant")
st.caption("Ask about stock prices, returns, volatility, or portfolio performance — plain English, no SQL needed.")

with st.sidebar:
    st.header("What can I ask?")
    st.markdown("""
- What was AAPL's closing price recently?
- What was the high/low for MSFT last week?
- What's my total portfolio gain this month?
- Which stock has been most volatile?
- Did AAPL outperform the market yesterday?
- What's my portfolio's Value at Risk?

*Anything else gets a general web-search-backed answer.*
    """)
    if st.button("Clear conversation"):
        st.session_state.messages = []
        st.rerun()

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sql"):
            with st.expander("Generated SQL"):
                st.code(msg["sql"], language="sql")

question = st.chat_input("Ask a question...")

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            answer, sql = route_question(question)
        st.markdown(answer)
        if sql:
            with st.expander("Generated SQL"):
                st.code(sql, language="sql")

    st.session_state.messages.append({"role": "assistant", "content": answer, "sql": sql})