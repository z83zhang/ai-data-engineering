"""
Evaluation cases for Metric Intelligence Agent.

The suite covers four common failure modes:
- wrong_layer: the agent uses fact tables when an aggregate table can answer.
- wrong_join_path: the agent chooses customer geography vs supplier geography incorrectly.
- wrong_metric_formula: the agent uses a non-canonical metric formula or forgets exclusions.
- out_of_range: the agent generates SQL for dates outside the demo data range.

run_eval.py should iterate through TEST_CASES, invoke the graph for each question,
compare final_state against the expected row count, columns, layer, and range
handling, then write results through eval.logger.
"""

TEST_CASES = [
    {
        "question": "What was daily revenue in January 1995?",
        "failure_mode": "wrong_layer",
        "expected_row_count": 31,
        "expected_columns": ["order_date"],
        "expected_layer": "aggregated",
        "notes": "Checks that daily revenue groups by date only and uses agg_daily_sales. January 1995 has 31 calendar days all with orders at sf=0.1.",
    },
    {
        "question": "What was revenue by month in 1995?",
        "failure_mode": "wrong_layer",
        "expected_row_count": 12,
        "expected_columns": ["order_year", "order_month"],
        "expected_layer": "aggregated",
        "notes": "Checks that one-year monthly revenue uses agg_monthly_sales and returns all 12 months.",
    },
    {
        "question": "What was total revenue by year?",
        "failure_mode": "wrong_layer",
        "expected_row_count": 7,
        "expected_columns": ["order_year"],
        "expected_layer": "aggregated",
        "notes": "Checks that annual rollups can be answered from monthly aggregates across 1992-1998; 1998 is partial (Jan-Aug only) so this year still returns a row.",
    },
    {
        "question": "What is total revenue by customer region?",
        "failure_mode": "wrong_join_path",
        "expected_row_count": 5,
        "expected_columns": ["customer_region"],
        "expected_layer": "aggregated",
        "notes": "Checks that customer region can be answered from the aggregate layer.",
    },
    {
        "question": "What is total revenue by customer nation?",
        "failure_mode": "wrong_join_path",
        "expected_row_count": 25,
        "expected_columns": ["customer_nation"],
        "expected_layer": "aggregated",
        "notes": "Checks that customer nation can be answered from the aggregate layer.",
    },
    {
        "question": "What is total revenue by supplier region?",
        "failure_mode": "wrong_join_path",
        "expected_row_count": 5,
        "expected_columns": ["supplier_region"],
        "expected_layer": "fact",
        "notes": "Checks that supplier geography joins lineitem to supplier, nation, and region.",
    },
    {
        "question": "What is total revenue by region?",
        "failure_mode": "wrong_join_path",
        "expected_row_count": 5,
        "expected_columns": ["customer_region"],
        "expected_layer": "aggregated",
        "notes": "Checks that ambiguous geography defaults to customer region and can use the aggregate layer.",
    },
    {
        "question": "What is total revenue?",
        "failure_mode": "wrong_metric_formula",
        "expected_row_count": 1,
        "expected_columns": [],
        "expected_layer": "aggregated",
        "expected_value": 20535072231.415,
        "expected_value_tolerance": 0.01,
        "notes": "Checks that total revenue prefers the aggregated layer and uses canonical revenue formula excluding cancelled orders.",
    },
    {
        "question": "What is order volume by month in 1995?",
        "failure_mode": "wrong_metric_formula",
        "expected_row_count": 12,
        "expected_columns": ["order_year", "order_month"],
        "expected_layer": "aggregated",
        "expected_value": 22909,
        "expected_value_tolerance": 0.001,
        "notes": "Checks order volume uses canonical COUNT(DISTINCT o_orderkey) excluding cancelled orders. Verified 22909 distinct orders in 1995 at TPC-H sf=0.1.",
    },
    {
        "question": "What is average order value by month in 1995?",
        "failure_mode": "wrong_metric_formula",
        "expected_row_count": 12,
        "expected_columns": ["order_year", "order_month"],
        "expected_layer": "aggregated",
        "expected_value": 135372.004536,
        "expected_value_tolerance": 0.01,
        "expected_value_aggregation": "first",
        "notes": "AOV can use agg_monthly_sales by summing revenue and order_volume separately before dividing -- correct as long as numerator and denominator are summed at the same grain before division.",
    },
    {
        "question": "What was revenue last year?",
        "failure_mode": "out_of_range",
        "expected_row_count": None,
        "expected_columns": None,
        "expected_layer": None,
        "notes": "Checks that relative time outside 1992-1998 exits early without SQL.",
    },
    {
        "question": "What is revenue this month?",
        "failure_mode": "out_of_range",
        "expected_row_count": None,
        "expected_columns": None,
        "expected_layer": None,
        "notes": "Checks that current-period phrasing exits early because the demo data ends in 1998.",
    },
    {
        "question": "What was revenue in 2024?",
        "failure_mode": "out_of_range",
        "expected_row_count": None,
        "expected_columns": None,
        "expected_layer": None,
        "notes": "Checks that explicit future-year requests outside the data range return OUT_OF_RANGE.",
    },
]
