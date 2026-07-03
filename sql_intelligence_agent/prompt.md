# SQL Intelligence Agent Project Prompt

## Step 1
Create a Python project folder called `sql_intelligence_agent` with the following structure:

```text
sql_intelligence_agent/
|-- context/
|   |-- table_catalog.md
|   |-- metric_definitions.md
|   `-- schema.sql
|-- agent.py
|-- main.py
`-- requirements.txt
```

## Project Rules

- Leave `agent.py` and `main.py` empty for now.
- `requirements.txt` should include:
  - `openai`
  - `duckdb`
  - `pandas`
- Populate the context files with TPC-H data described as a realistic layered warehouse.

## Warehouse Layers

1. Aggregated layer: pre-computed daily/monthly metrics. Use this first.
2. Fact layer: `orders` and `lineitem`. Use when the aggregated layer lacks granularity.
3. Dimension layer: `customer`, `supplier`, `nation`, and `region`.

## Context Files

### `table_catalog.md`

Describe each table, including:

- Which warehouse layer it belongs to
- When to use it
- Caveats
- Columns for each table

### `metric_definitions.md`

Each metric must include:

- Canonical definition
- Canonical SQL
- Which table(s) to use
- Caveats

#### Revenue

- Definition: net merchandise revenue after line-level discount, before tax
- Formula: `SUM(l_extendedprice * (1 - l_discount))`
- Source table: `lineitem`
- Caveats:
  - Do not use `orders.o_totalprice` as canonical revenue.
  - Do not include tax unless explicitly asked.
  - For revenue by order date, join `lineitem` to `orders`, group by `o_orderdate`.
  - For revenue by customer region: `lineitem` -> `orders` -> `customer` -> `nation` -> `region`.
  - For revenue by supplier region: `lineitem` -> `supplier` -> `nation` -> `region`.
  - Never mix customer and supplier geography paths in one query without aliasing `nation` and `region` tables explicitly.

#### Order Volume

- Definition: count of distinct customer orders
- Formula: `COUNT(DISTINCT o_orderkey)`
- Source table: `orders`
- Caveats:
  - Never count `lineitem` rows as order volume; that counts order lines.
  - If joining `orders` to `lineitem`, still use `COUNT(DISTINCT orders.o_orderkey)`.
  - Use `o_orderdate` as the canonical date for trends.

#### Average Order Value

- Definition: revenue divided by order volume; measures spend per order
- Formula: `SUM(l_extendedprice * (1 - l_discount)) / COUNT(DISTINCT o_orderkey)`
- Source tables: `lineitem` joined to `orders` on `l_orderkey = o_orderkey`
- Caveats:
  - Always compute from the same grain in a single query; never divide two separately computed numbers from different queries.
  - Exclude cancelled orders (`o_orderstatus <> 'C'`) for consistency.

#### Revenue by Customer Region

- Definition: revenue attributed to the customer's geographic region
- Formula: `SUM(l_extendedprice * (1 - l_discount))` grouped by `r_name`
- Join path: `lineitem` -> `orders` -> `customer` -> `nation` -> `region`
- Caveats:
  - This is customer geography; do not use the supplier join path.
  - Alias `nation` as `customer_nation` and `region` as `customer_region` if supplier geography also appears in the same query.
  - Use `o_orderdate` for time filtering, not `l_shipdate`.

#### Revenue by Supplier Region

- Definition: revenue attributed to the supplier's geographic region
- Formula: `SUM(l_extendedprice * (1 - l_discount))` grouped by `r_name`
- Join path: `lineitem` -> `supplier` -> `nation` -> `region`
- Caveats:
  - This is supplier geography; do not use the customer join path.
  - Alias `nation` as `supplier_nation` and `region` as `supplier_region` if customer geography also appears in the same query.
  - Revenue by supplier region and revenue by customer region will return different numbers for the same time period; both are correct because they measure different things.

### `schema.sql`

Include TPC-H `CREATE TABLE` statements for:

- `orders`
- `lineitem`
- `customer`
- `supplier`
- `nation`
- `region`

Use standard TPC-H column names and types.


## Step 2
In agent.py, write a single function called load_context() that reads 
the three context files and combines them into one structured string.

Requirements:
- Read these three files:
    context/table_catalog.md
    context/metric_definitions.md
    context/schema.sql

- Combine them into a single
structured string with labeled sections.

Returns a string in this format:
    === TABLE CATALOG ===
    ...contents...

    === METRIC DEFINITIONS ===
    ...contents...

    === SCHEMA ===
    ...contents...

- If any file is missing, raise a clear error message that tells 
  the user which file is missing, for example:
  "Context file not found: context/table_catalog.md"

- Add a short docstring to the function explaining what it does 
  and what it returns

Rules:
- No hardcoded file contents — always read from disk
- No LLM calls in this function — pure file reading only
- Keep it under 30 lines


## Step 3
In agent.py, write a function called generate_sql() below the 
existing load_context() function.

This function takes two arguments:
- context: the string returned by load_context()
- question: a plain English question from the user (string)

It calls the OpenAI API and returns the SQL query as a plain string 
with no markdown formatting, no backticks, no explanation — just the 
raw SQL.

Requirements:

- Use the openai Python library with this model: gpt-4o
- Make a chat completion call with two messages:
    1. A system message that tells the model:
       - It is a data engineering assistant
       - It writes SQL for DuckDB
       - It must prefer the aggregated layer first, then fall back 
         to fact tables only when needed
       - It must follow the metric definitions and caveats exactly
       - It must return only raw SQL with no markdown, no backticks, 
         no explanation
       - The full context block is provided below the instruction
    2. A user message that contains the question

- The system message should include the full context string

- Strip any accidental markdown from the response before returning:
  remove ```sql and ``` if they appear

- Add a clear docstring explaining arguments and return value

- Do not hardcode the API key — use os.environ.get("OPENAI_API_KEY")
  and raise a clear error if it is not set

Rules:
- No retry logic yet — that comes in reflect_sql()
- No printing inside the function — just return the SQL string
- Keep it under 40 lines


Make three improvements to generate_sql():

1. Replace the system message with this more detailed version:

   "You are a data engineering assistant that writes SQL for DuckDB.
   
   Rules you must follow exactly:
   - Prefer the aggregated layer (agg_daily_sales, agg_monthly_sales) 
     first. Only fall back to fact tables (orders, lineitem) when the 
     aggregated layer cannot answer the question.
   - Treat every metric definition and caveat in the context as law. 
     Do not deviate from canonical formulas, join paths, or exclusion 
     rules.
   - Always exclude cancelled orders (o_orderstatus <> 'C') unless 
     the user explicitly asks for all orders.
   - If the question is ambiguous about customer vs supplier geography, 
     default to customer region and note the assumption in a SQL comment 
     at the top of the query.
   - Return only raw SQL. No markdown, no backticks, no explanation, 
     no preamble.
   
   Full context:
   {context}"

2. Add temperature=0 to the API call

3. Replace the markdown stripping logic with this:
   import re
   sql = re.sub(r'```[a-zA-Z]*', '', sql).replace('```', '').strip()

4. In agent.py, move the OpenAI client instantiation out of 
generate_sql() and make it a module-level variable. 

Place it after the imports, before any function definitions:

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set.")
    client = OpenAI(api_key=api_key)

Then remove the api_key check and client instantiation from 
inside generate_sql().

## Step 4
In agent.py, do two things:

1. Write a function called setup_database() that:
   - Creates an in-memory DuckDB connection
   - Loads the TPC-H dataset at scale factor 0.1 using:
       conn.execute("CALL dbgen(sf=0.1)")
   - Creates agg_daily_sales as:
       CREATE TABLE agg_daily_sales AS
       SELECT
         o_orderdate AS order_date,
         SUM(l_extendedprice * (1 - l_discount)) AS revenue,
         COUNT(DISTINCT o_orderkey) AS order_volume,
         AVG(l_discount) AS discount_rate
       FROM orders
       JOIN lineitem ON o_orderkey = l_orderkey
       WHERE o_orderstatus <> 'C'
       GROUP BY o_orderdate
   - Creates agg_monthly_sales as:
       CREATE TABLE agg_monthly_sales AS
       SELECT
         YEAR(o_orderdate) AS order_year,
         MONTH(o_orderdate) AS order_month,
         SUM(l_extendedprice * (1 - l_discount)) AS revenue,
         COUNT(DISTINCT o_orderkey) AS order_volume,
         AVG(l_discount) AS discount_rate
       FROM orders
       JOIN lineitem ON o_orderkey = l_orderkey
       WHERE o_orderstatus <> 'C'
       GROUP BY YEAR(o_orderdate), MONTH(o_orderdate)
   - Returns the connection
   - Add a clear docstring

2. Write a function called run_sql() that:
   - Takes two arguments: conn (a DuckDB connection) and sql (a string)
   - Attempts to run the SQL using conn.execute(sql).df()
   - Returns a dictionary:
       If success: {"success": True, "data": DataFrame}
       If failure: {"success": False, "error": error message as string}
   - Catches any exception and returns the error dict
   - Add a clear docstring
   - No LLM calls, no printing
   - Keep it under 20 lines

Also add import duckdb at the top of agent.py


## Step 5
In agent.py, do two things:

1. Add this line near the top of the file, just below the 
   client instantiation:

   MAX_ATTEMPTS = int(os.environ.get("MAX_REFLECTION_ATTEMPTS", 3))

2. Write a function called reflect_sql() below run_sql().

This function takes six arguments:
- conn: an active DuckDB connection
- context: the structured context string from load_context()
- question: the original plain English question from the user
- sql: the SQL from the previous attempt (failed or semantically wrong)
- error: a string describing what went wrong. This could be:
    - A DuckDB error message (technical failure)
    - A semantic failure description from validate_result()
      e.g. "Result returned 0 rows for a metric that should
      have data" or "Revenue value is negative which is invalid"
- attempt: the current attempt number as an integer (starts at 1
  because at least one attempt already failed before this is called)

It attempts to rewrite the SQL using the LLM, then runs it again.
Maximum total attempts is controlled by the module-level 
MAX_ATTEMPTS variable.

On each attempt it should:
1. If attempt >= MAX_ATTEMPTS, immediately return the failure dict 
   without calling the LLM again
2. Otherwise call the LLM with a system message that includes
   the full context and these instructions:
   - You are a data engineering assistant fixing a SQL query for DuckDB
   - Follow all metric definitions and caveats in the context exactly
   - Return only raw SQL, no markdown, no backticks, no explanation
   And a user message structured like this:

   "The following SQL failed to return a correct result.

   Original question: {question}

   SQL attempted:
   {sql}

   Problem:
   {error}

   Rewrite the SQL to fix the problem. Return only raw SQL."

3. Strip markdown from the response the same way generate_sql() does
4. Run the new SQL using run_sql()
5. Return the run_sql() result dict with one added key:
   "attempts": the current attempt number

If max attempts reached return:
{
    "success": False,
    "error": the last error string passed in,
    "sql": the last sql string passed in,
    "attempts": attempt,
    "message": "Maximum reflection attempts reached. Could not
                generate valid SQL for this question."
}

Requirements:
- Use the module-level client and temperature=0
- Use MAX_ATTEMPTS for the attempt limit, not a hardcoded number
- Use the same markdown stripping regex as generate_sql()
- Add a clear docstring explaining all arguments and return value
- No printing inside the function
- Keep it under 50 lines

6. In reflect_sql(), change:
    result["attempts"] = attempt
to:
    result["attempts"] = attempt + 1
7. In reflect_sql(), replace the system message with:

    "You are a data engineering assistant fixing a SQL query for DuckDB.\n\n"
    "Rules you must follow exactly:\n"
    "- Prefer the aggregated layer (agg_daily_sales, agg_monthly_sales) "
    "first. Only fall back to fact tables when needed.\n"
    "- Always exclude cancelled orders (o_orderstatus <> 'C') unless "
    "the user explicitly asks for all orders.\n"
    "- Follow all metric definitions and caveats in the context exactly.\n"
    "- Return only raw SQL, no markdown, no backticks, no explanation.\n\n"
    "Full context:\n"
    f"{context}"

## Step 6
In agent.py, write a function called validate_result() below reflect_sql().

This function takes four arguments:
- question: the original plain English question from the user
- sql: the SQL that produced the result
- df: a pandas DataFrame containing the query result
- context: the structured context string from load_context()

It runs two layers of checks in sequence:

LAYER 1 — Python checks (run first, no LLM):

Check 1: Zero rows
  - If len(df) == 0, fail immediately with reason:
    "Query returned 0 rows. The SQL may have an overly restrictive
     filter or incorrect join condition."

Check 2: All-null numeric columns
  - Find all numeric columns using df.select_dtypes(include='number')
  - If any numeric column is entirely null, fail with reason:
    "Numeric column '{column_name}' contains only null values."

Check 3: Negative values in any numeric column
  - For every numeric column, check if any value is negative
  - If so, fail with reason:
    "Column '{column_name}' contains negative values. Verify whether
     this is valid for the metric being computed."

If all Layer 1 checks pass, proceed to Layer 2.

LAYER 2 — LLM semantic check (one call):

System message:
  "You are a data quality validator for a SQL intelligence agent.
   Use the metric definitions and table catalog in the context to
   judge whether the result is correct.

   Full context:
   {context}"

User message containing:
  - The original question
  - The SQL that was run
  - The first 10 rows of the result as a string
    (use df.head(10).to_string())
  - The total number of rows returned
  - Instructions to check:
      1. Does the result actually answer the question asked?
      2. Do the categorical values look valid and meaningful?
      3. Do the numeric values look plausible in magnitude given
         the metric definitions in the context?
      4. Does the row count make sense for this type of question?
      5. Was the correct data layer used based on the SQL?

  Tell the LLM to respond in this exact format and nothing else:
      VALID: yes
  or:
      VALID: no
      REASON: explanation of what looks wrong

Parse the response:
  - If "VALID: yes" appears → return {"valid": True, "reason": None}
  - If "VALID: no" appears → extract everything after "REASON:" 
    to the end of the response as the reason string, return:
    {"valid": False, "reason": reason}
  - If response cannot be parsed → return {"valid": True, "reason": None}
    (default to valid if parsing fails — never block a correct answer
     due to LLM formatting ambiguity)

Requirements:
- Use the module-level client and temperature=0
- Layer 1 must run before any LLM call
- The context string must be included in the Layer 2 system message
- Add a clear docstring explaining all arguments and return value
- No printing inside the function
- Keep it under 65 lines

## Step 7
In agent.py, write a function called explain_result() as the last 
function in the file.

This function takes four arguments:
- question: the original plain English question from the user
- sql: the SQL that produced the verified result
- df: the pandas DataFrame containing the verified result
- context: the structured context string from load_context()

It makes one LLM call and returns a plain English explanation 
string suitable for a non-technical user.

System message:
  "You are a data analytics assistant explaining query results
   to a non-technical audience such as product managers and
   data scientists.

   Use the metric definitions and table catalog in the context
   to give accurate, precise explanations.

   Full context:
   {context}"

User message containing:
  - The original question
  - The SQL that was run
  - The first 20 rows of the result (use df.head(20).to_string())
  - The total number of rows returned
  - Instructions to write a response that:
      1. Directly answers the question in plain English
      2. States the key finding or number upfront
      3. References the metric definition used
         (e.g. which formula, which table layer)
      4. Surfaces any assumptions made
         (e.g. defaulted to customer region, excluded cancelled orders)
      5. Keeps the tone conversational — like a senior analytics
         engineer explaining to a PM in Slack
      6. Stays under 150 words
      7. Does not mention SQL, DuckDB, DataFrames, or any
         technical implementation details

Returns the explanation as a plain string.

Requirements:
- Use the module-level client
- Use temperature=0 for consistency with all other functions
- Add a clear docstring explaining all arguments and return value
- No printing inside the function
- Keep it under 30 lines

## Step 8
Replace main.py with a complete pipeline.

At the top, import:
from agent import (setup_database, load_context, generate_sql,
                   run_sql, validate_result, explain_result, 
                   reflect_sql, MAX_ATTEMPTS)

Write a function called run_pipeline(question, conn, context) 
that takes the question, an active DuckDB connection, and the 
context string as arguments.

The function should:

1. Call generate_sql(context, question) to get the first SQL attempt
   Set attempt = 1

2. Call run_sql(conn, sql) to execute it

3. If run_sql() fails (success is False):
   - Enter a while loop that continues while:
     success is False AND attempt < MAX_ATTEMPTS
   - Each iteration: call reflect_sql(conn, context, question, 
     sql, error, attempt)
   - Update sql, success, error, and attempt from the result
   - If loop exits with success still False:
     print failure output and return

4. If run_sql() succeeds:
   - Call validate_result(question, sql, result data, context)
   - If validation fails:
     - Call reflect_sql(conn, context, question, sql, 
       validation reason, attempt) once
     - Increment attempt
     - If reflect_sql() fails or validation fails again:
       print failure output and return
     - If reflect_sql() succeeds, run validate_result() once more
     - If still invalid: print failure output and return
   
5. If both checks pass:
   - Call explain_result(question, sql, result data, context)
   - Print success output:

     ✅ Answer verified — {attempt} attempt(s)
     
     Result:
     {result dataframe}
     
     Explanation:
     {explanation}

   Or if failed at any point:
     ❌ Could not answer this question.
     Reason: {last error or validation reason}
     SQL attempted: {last sql}

At the bottom of main.py, outside the function:

conn = setup_database()
context = load_context()

run_pipeline("What is the total revenue by customer region?", 
             conn, context)
run_pipeline("What is the average order value by month in 1995?", 
             conn, context)
## Step 9

In main.py, add a helper function before run_pipeline():

def print_failure(error, sql):
    print("❌ Could not answer this question.")
    print("Reason:", error)
    print("SQL attempted:", sql)

Then replace all three failure print blocks in run_pipeline() 
with: print_failure(error, sql)

## Step 10
### Instruction 10a — Dynamic date range: get_date_range()
Add a new function get_date_range(conn) in agent.py after 
setup_database():

def get_date_range(conn):
    result = conn.execute("""
        SELECT
          MIN(o_orderdate) AS min_date,
          MAX(o_orderdate) AS max_date
        FROM orders
    """).df()
    min_date = result["min_date"].iloc[0].strftime("%Y-%m-%d")
    max_date = result["max_date"].iloc[0].strftime("%Y-%m-%d")
    return min_date, max_date

Keep setup_database() returning just conn — do not change it.

### Instruction 10b — Dynamic date range: load_context()
Change load_context() signature to:
def load_context(min_date, max_date)

min_date and max_date are required string arguments in YYYY-MM-DD 
format. After the existing three sections, always append a fourth:

=== DATA RANGE ===
This database contains order data from {min_date} to {max_date} only.

If a question references a time period outside this range — including
relative references such as "last year", "recent", "current", 
"latest", or "this quarter" that would resolve to dates after 
{max_date} or before {min_date} — do NOT generate SQL. Instead 
return this exact message and nothing else:

OUT_OF_RANGE: This dataset only contains data from {min_date} to 
{max_date}. Your question refers to a time period outside this 
range. Please specify a year between {min_date[:4]} and 
{max_date[:4]}.

Only generate SQL if the question refers to a date range that 
falls within {min_date} to {max_date}.

Note: the OUT_OF_RANGE message must be on one line with no 
mid-sentence newlines.

### Instruction 10c — main.py updates
Add get_date_range to the import line.
Update the bottom of main.py:

    conn = setup_database()
    min_date, max_date = get_date_range(conn)
    context = load_context(min_date, max_date)

In run_pipeline(), after generate_sql() returns, add:

    if sql.startswith("OUT_OF_RANGE:"):
        print("⚠️", sql.replace("OUT_OF_RANGE:", "").strip())
        return