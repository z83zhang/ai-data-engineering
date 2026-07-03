from agent import (setup_database, get_date_range, load_context, generate_sql,
                   run_sql, validate_result, explain_result,
                   reflect_sql, MAX_ATTEMPTS)


def print_failure(error, sql):
    print("❌ Could not answer this question.")
    print("Reason:", error)
    print("SQL attempted:", sql)


def run_pipeline(question, conn, context):
    sql = generate_sql(context, question)
    if sql.startswith("OUT_OF_RANGE:"):
        print("⚠️", sql.replace("OUT_OF_RANGE:", "").strip())
        return

    attempt = 1
    result = run_sql(conn, sql)
    success = result["success"]
    error = result.get("error")

    while not success and attempt < MAX_ATTEMPTS:
        result = reflect_sql(conn, context, question, sql, error, attempt)
        sql = result["sql"]
        success = result["success"]
        error = result.get("error")
        attempt = result.get("attempts", attempt + 1)

    if not success:
        print_failure(error, sql)
        return

    validation = validate_result(question, sql, result["data"], context)
    if not validation["valid"]:
        result = reflect_sql(conn, context, question, sql, validation["reason"], attempt)
        sql = result["sql"]
        success = result["success"]
        error = result.get("error")
        attempt = result.get("attempts", attempt + 1)

        if not success:
            print_failure(error, sql)
            return

        validation = validate_result(question, sql, result["data"], context)
        if not validation["valid"]:
            print_failure(validation["reason"], sql)
            return

    explanation = explain_result(question, sql, result["data"], context)
    print(f"✅ Answer verified — {attempt} attempt(s)")
    print("\nResult:")
    print(result["data"])
    print("\nExplanation:")
    print(explanation)


conn = setup_database()
min_date, max_date = get_date_range(conn)
context = load_context(min_date, max_date)

run_pipeline("What is the total revenue by customer region?",
             conn, context)
run_pipeline("What is the average order value by month in 1995?",
             conn, context)
# Demonstrates intentional out-of-range handling
run_pipeline("What was revenue last year?",
             conn, context)
