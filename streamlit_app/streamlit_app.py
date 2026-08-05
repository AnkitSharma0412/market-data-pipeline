import os
import json
import yaml
import streamlit as st
from groq import Groq
import snowflake.connector
from duckduckgo_search import DDGS

st.set_page_config(page_title="Portfolio Terminal", page_icon="📈", layout="centered")

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
TICKERS = ["AAPL", "MSFT", "GOOGL", "JPM", "GS", "SPY"]

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


@st.cache_data(ttl=300, show_spinner=False)
def fetch_ticker_tape():
    """Latest close + prior-day return per ticker, for the header strip."""
    try:
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
        cur.execute("""
            SELECT ticker, close_price, daily_return
            FROM FACT_STOCK_METRICS
            QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY trade_date DESC) = 1
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return {r[0]: (r[1], r[2]) for r in rows}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Question routing (unchanged logic from before)
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
- Generate EXACTLY ONE SELECT statement. Never return multiple statements.
- Never generate INSERT, UPDATE, DELETE, DROP, ALTER, or any other statement.
- Use fully qualified table names (MARKET_DB.MARTS.<table>).
- Never use SELECT * — always select specific, relevant columns.
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
        return ("I'm afraid I couldn't find an answer to that in the data — "
                "could you try asking it a different way?", sql)

    try:
        answer = summarize_answer(question, columns, rows)
    except Exception:
        return ("I found the data but had trouble summarizing it — please try again.", sql)

    return (answer, sql)


# ---------------------------------------------------------------------------
# STYLE — trading-terminal aesthetic
# ---------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=Inter:wght@400;500;600&display=swap');

:root {
    --bg: #F5F6F8;
    --panel: #EEF2F7;
    --panel-border: #D9DFE8;
    --navy: #16223F;
    --navy-light: #2C3E63;
    --green: #16A34A;
    --red: #DC2626;
    --text: #14181F;
    --text-muted: #6B7280;
}

.stApp { background-color: var(--bg); }
* { font-family: 'Inter', sans-serif; }

.terminal-header {
    font-family: 'IBM Plex Mono', monospace;
    font-weight: 700;
    font-size: 1.6rem;
    letter-spacing: 0.04em;
    color: var(--navy);
    text-transform: uppercase;
    border-bottom: 2px solid var(--navy);
    padding-bottom: 10px;
    margin-bottom: 4px;
}
.terminal-header span { color: var(--green); }
.terminal-sub {
    font-family: 'IBM Plex Mono', monospace;
    color: var(--text-muted);
    font-size: 0.8rem;
    letter-spacing: 0.03em;
    margin-bottom: 18px;
}

.ticker-tape-wrap {
    overflow: hidden;
    white-space: nowrap;
    background: var(--navy);
    border: 1px solid var(--panel-border);
    border-radius: 6px;
    padding: 10px 0;
    margin-bottom: 22px;
}
.ticker-tape {
    display: inline-block;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.85rem;
    animation: scroll-left 28s linear infinite;
    padding-left: 100%;
}
.ticker-tape span.sym { color: #FFFFFF; font-weight: 600; margin-left: 28px; }
.ticker-tape span.up { color: #4ADE80; }
.ticker-tape span.down { color: #F87171; }
@keyframes scroll-left {
    0% { transform: translateX(0); }
    100% { transform: translateX(-100%); }
}

[data-testid="stSidebar"] {
    background-color: var(--panel);
    border-right: 1px solid var(--panel-border);
}
[data-testid="stSidebar"] h2 {
    font-family: 'IBM Plex Mono', monospace;
    color: var(--navy);
    font-size: 0.95rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}

[data-testid="stChatMessage"] {
    background-color: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 8px;
    box-shadow: 0 1px 2px rgba(20, 24, 31, 0.04);
}

div[data-testid="stChatInput"] {
    background-color: var(--panel) !important;
    border: 1px solid var(--panel-border) !important;
    border-radius: 10px !important;
}
div[data-testid="stChatInput"] textarea {
    font-family: 'IBM Plex Mono', monospace;
    background-color: transparent !important;
    border: none !important;
    box-shadow: none !important;
    color: var(--text) !important;
}

code, pre { font-family: 'IBM Plex Mono', monospace !important; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Ticker tape (signature element)
# ---------------------------------------------------------------------------
with st.spinner("Loading market data..."):
    prices = fetch_ticker_tape()
tape_html = ""
for t in TICKERS:
    if t in prices:
        close, ret = prices[t]
        direction = "up" if (ret or 0) >= 0 else "down"
        arrow = "▲" if direction == "up" else "▼"
        pct = f"{ret * 100:+.2f}%" if ret is not None else "—"
        tape_html += f'<span class="sym">{t}</span> <span class="{direction}">${close:.2f} {arrow} {pct}</span>'
    else:
        tape_html += f'<span class="sym">{t}</span> <span style="color:var(--text-muted)">—</span>'

st.markdown(
    f'<div class="ticker-tape-wrap"><div class="ticker-tape">{tape_html}{tape_html}</div></div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.markdown('<div class="terminal-header">PORTFOLIO <span>//</span> TERMINAL</div>', unsafe_allow_html=True)
st.markdown('<div class="terminal-sub">NATURAL LANGUAGE QUERY ENGINE · AAPL · MSFT · GOOGL · JPM · GS · SPY</div>', unsafe_allow_html=True)

with st.sidebar:
    st.header("Watchlist Queries")
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

question = st.chat_input("Ask about your portfolio...")

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Querying..."):
            answer, sql = route_question(question)
        st.markdown(answer)
        if sql:
            with st.expander("Generated SQL"):
                st.code(sql, language="sql")

    st.session_state.messages.append({"role": "assistant", "content": answer, "sql": sql})