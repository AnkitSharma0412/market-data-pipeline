import os
import json
import yaml
from groq import Groq
import snowflake.connector
from dotenv import load_dotenv


# Explicit path to the project root .env, since this script lives in a
# subfolder (semantic_models/), not the project root itself.
_script_dir = os.path.dirname(os.path.abspath(__file__))
_env_path = os.path.join(_script_dir, "..", ".env")  # one level up = project root
load_dotenv(_env_path)

client = Groq(api_key=os.environ["GROQ_API_KEY"])

MODEL = "llama-3.3-70b-versatile"


def load_semantic_model(path=None):
    if path is None:
        # Resolve relative to this script's own folder, so it works
        # regardless of which directory you run `python` from.
        script_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(script_dir, "market_semantic_model.yaml")
    with open(path, "r") as f:
        return yaml.safe_load(f)


def build_schema_context(semantic_model):
    """Turns the semantic model into a compact text description for the prompt."""
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
    # Strip accidental markdown code fences, in case the model adds them anyway
    sql = sql.replace("```sql", "").replace("```", "").strip()
    return sql


def is_safe_select(sql):
    """Basic guard: only allow SELECT statements, block anything destructive."""
    normalized = sql.strip().upper()
    if not normalized.startswith("SELECT"):
        return False
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "MERGE", "CREATE", "GRANT", "REVOKE"]
    return not any(word in normalized for word in forbidden)


def run_query(sql):
    conn = snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database="MARKET_DB",
        schema="MARTS",
        role="READONLY_ANALYST",   # see setup note below if this role doesn't exist yet
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


def ask(question):
    semantic_model = load_semantic_model()
    schema_context = build_schema_context(semantic_model)

    sql = generate_sql(question, schema_context)

    if sql == "NO_QUERY_POSSIBLE":
        return "I can't answer that with the available data."

    if not is_safe_select(sql):
        return f"Blocked a potentially unsafe query: {sql}"

    print(f"\nGenerated SQL:\n{sql}\n")

    columns, rows = run_query(sql)
    answer = summarize_answer(question, columns, rows)
    return answer


if __name__ == "__main__":
    while True:
        q = input("\nAsk a question (or 'quit'): ")
        if q.lower() == "quit":
            break
        print("\n" + ask(q))