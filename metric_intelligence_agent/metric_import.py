import re

from sqlglot import exp, parse_one


def render_metric_markdown(metrics):
    sections = ["# Metric Definitions"]
    for metric in metrics:
        columns = ", ".join(
            f"{item['table_name']}.{item['column_name']}"
            for item in metric["column_references"]
        )
        fields = [
            ("Definition", metric.get("description", "")),
            ("Formula", metric.get("formula", "")),
            ("Source tables", ", ".join(metric["source_tables"])),
            ("Column references", columns),
            ("Join conditions", "; ".join(metric["join_conditions"])),
            ("Filters", "; ".join(metric["filters"])),
            ("Grain", metric.get("grain", "")),
            ("Business rules", metric.get("business_rules", "")),
            ("Trust level", metric.get("trust_level", "")),
        ]
        lines = [f"## {metric.get('name') or 'Unnamed metric'}"]
        lines.extend(f"- {label}: {value}" for label, value in fields)
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def merge_metric_definitions(existing, incoming):
    """Replace all case-insensitive heading matches and retain one new section."""
    existing_sections = _metric_markdown_sections(existing)
    incoming_sections = _metric_markdown_sections(incoming)
    if not incoming_sections:
        raise ValueError("Metric definitions must contain at least one level-two heading.")

    replacements = {}
    for name, section in incoming_sections:
        replacements[name.casefold()] = (name, section)
    kept = [
        section
        for name, section in existing_sections
        if name.casefold() not in replacements
    ]
    merged_sections = kept + [section for _, section in replacements.values()]
    return "# Metric Definitions\n\n" + "\n\n".join(merged_sections) + "\n"


def _metric_markdown_sections(content):
    headings = list(re.finditer(r"(?m)^##[ \t]+(.+?)[ \t]*$", content))
    sections = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
        sections.append(
            (heading.group(1).strip(), content[heading.start() : end].strip())
        )
    return sections


def parse_sql_facts(sql):
    """Extract metric facts that are explicit in a SQL statement."""
    tree = parse_one(sql)
    tables, aliases = _sql_tables_and_aliases(tree)
    columns = _column_references([tree], tables, aliases)

    formulas = []
    select = tree.find(exp.Select)
    if select:
        for expression in select.expressions:
            value = expression.this if isinstance(expression, exp.Alias) else expression
            if value.find(exp.AggFunc):
                formulas.append(value.sql())

    filters = []
    for clause_type in (exp.Where, exp.Having):
        clause = tree.find(clause_type)
        if clause is not None:
            filters.append(clause.this.sql())

    joins = [join.args["on"].sql() for join in tree.find_all(exp.Join) if join.args.get("on")]
    group = tree.find(exp.Group)
    group_by = [item.sql() for item in group.expressions] if group else []
    return {
        "source_tables": tables,
        "column_references": columns,
        "formulas": formulas,
        "join_conditions": joins,
        "filters": filters,
        "group_by": group_by,
    }


def parse_sql_metric_facts(sql):
    """Return one fact set per aggregate expression in the top-level SELECT."""
    tree = parse_one(sql)
    shared = parse_sql_facts(sql)
    tables, aliases = _sql_tables_and_aliases(tree)
    shared_expressions = []
    for clause_type in (exp.Where, exp.Having):
        clause = tree.find(clause_type)
        if clause is not None:
            shared_expressions.append(clause.this)
    shared_expressions.extend(
        join.args["on"] for join in tree.find_all(exp.Join) if join.args.get("on")
    )
    group = tree.find(exp.Group)
    if group:
        shared_expressions.extend(group.expressions)

    metrics = []
    select = tree.find(exp.Select)
    for expression in select.expressions if select else []:
        value = expression.this if isinstance(expression, exp.Alias) else expression
        if not value.find(exp.AggFunc):
            continue
        metrics.append(
            {
                "name": expression.alias if isinstance(expression, exp.Alias) else "",
                "formula": value.sql(),
                "source_tables": list(shared["source_tables"]),
                "column_references": _column_references(
                    [value, *shared_expressions], tables, aliases
                ),
                "join_conditions": list(shared["join_conditions"]),
                "filters": list(shared["filters"]),
                "group_by": list(shared["group_by"]),
            }
        )
    return metrics


def _sql_tables_and_aliases(tree):
    cte_names = {cte.alias_or_name.casefold() for cte in tree.find_all(exp.CTE)}
    tables = []
    aliases = {}
    for table in tree.find_all(exp.Table):
        name = table.name
        if name.casefold() in cte_names:
            continue
        qualified = ".".join(part for part in (table.catalog, table.db, name) if part)
        if qualified not in tables:
            tables.append(qualified)
        aliases[table.alias_or_name.casefold()] = qualified
        aliases[name.casefold()] = qualified
    return tables, aliases


def _column_references(expressions, tables, aliases):
    columns = []
    seen_columns = set()
    for expression in expressions:
        for column in expression.find_all(exp.Column):
            qualifier = column.table
            if qualifier:
                table_name = aliases.get(qualifier.casefold(), qualifier)
            elif len(tables) == 1:
                table_name = tables[0]
            else:
                table_name = "<unqualified>"
            key = (table_name.casefold(), column.name.casefold())
            if key not in seen_columns:
                seen_columns.add(key)
                columns.append({"table_name": table_name, "column_name": column.name})
    return columns


def parse_dbt_manifest_metrics(manifest):
    """Read metrics from the supported dbt manifest v10-v11 semantic shape."""
    schema_url = str(manifest.get("metadata", {}).get("dbt_schema_version", ""))
    match = re.search(r"/manifest/v(\d+)\.json", schema_url)
    if not match or int(match.group(1)) not in (10, 11):
        return []
    metrics = manifest.get("metrics") or {}
    semantic_models = manifest.get("semantic_models") or {}
    if not metrics or not semantic_models:
        return []

    nodes = manifest.get("nodes") or {}
    measures = {}
    for semantic in semantic_models.values():
        model_nodes = semantic.get("depends_on", {}).get("nodes", [])
        node = next((nodes.get(node_id) for node_id in model_nodes if nodes.get(node_id)), {})
        sql = node.get("raw_code") or node.get("compiled_code") or ""
        parse_failed = not bool(sql)
        try:
            sql_facts = parse_sql_facts(sql) if sql else _empty_sql_facts()
        except Exception:
            compiled = node.get("compiled_code") or ""
            try:
                if not compiled or compiled == sql:
                    raise ValueError("No distinct compiled SQL is available")
                sql_facts = parse_sql_facts(compiled)
                parse_failed = False
            except Exception:
                sql_facts = _empty_sql_facts()
                parse_failed = True
        relation = node.get("relation_name") or node.get("alias") or node.get("name")
        if relation and not sql_facts["source_tables"]:
            sql_facts["source_tables"] = [relation]
        for measure in semantic.get("measures") or []:
            measures[measure.get("name")] = (measure, sql_facts, parse_failed)

    metric_by_name = {metric.get("name"): metric for metric in metrics.values()}
    result = []
    for metric in metrics.values():
        type_params = metric.get("type_params") or {}
        measure_refs = _measure_references(type_params)
        referenced_measures = [measures[name] for name in measure_refs if name in measures]
        fact_sets = [measure[1] for measure in referenced_measures]
        facts = _merge_sql_facts(fact_sets)
        sql_parse_failed = any(measure[2] for measure in referenced_measures)
        formula = "" if sql_parse_failed else _metric_formula(metric, metric_by_name, measures)
        result.append(
            {
                "name": metric.get("name", ""),
                "description": metric.get("description", ""),
                "formula": formula,
                "source_tables": list(facts["source_tables"]),
                "column_references": list(facts["column_references"]),
                "join_conditions": list(facts["join_conditions"]),
                "filters": list(facts["filters"]),
                "grain": ", ".join(facts["group_by"]),
                "business_rules": "",
                "trust_level": "dbt manifest v10-v11",
                "sql_parse_failed": sql_parse_failed,
            }
        )
    return result


def _metric_formula(metric, metric_by_name, measures, seen=None):
    seen = set(seen or ())
    name = metric.get("name", "")
    if name in seen:
        return ""
    seen.add(name)
    params = metric.get("type_params") or {}
    metric_type = metric.get("type", "")
    if metric_type == "derived" and params.get("expr"):
        return str(params["expr"])
    if metric_type == "ratio":
        numerator = _reference_name(params.get("numerator"))
        denominator = _reference_name(params.get("denominator"))
        return f"{numerator} / {denominator}" if numerator and denominator else ""
    measure_name = _reference_name(params.get("measure"))
    if measure_name in measures:
        measure = measures[measure_name][0]
        expression = measure.get("expr") or measure.get("name") or ""
        return _aggregate_formula(measure.get("agg"), expression)
    referenced = params.get("metrics") or []
    if referenced:
        return str(params.get("expr") or "")
    return ""


def _measure_references(params):
    references = []
    for key in ("measure", "numerator", "denominator", "base_measure", "conversion_measure"):
        name = _reference_name(params.get(key))
        if name:
            references.append(name)
    conversion = params.get("conversion_type_params") or {}
    for key in ("base_measure", "conversion_measure"):
        name = _reference_name(conversion.get(key))
        if name:
            references.append(name)
    return references


def _reference_name(reference):
    if isinstance(reference, str):
        return reference
    if isinstance(reference, dict):
        return reference.get("name") or reference.get("measure") or ""
    return ""


def _merge_sql_facts(fact_sets):
    merged = _empty_sql_facts()
    for facts in fact_sets:
        for key in merged:
            for value in facts[key]:
                if value not in merged[key]:
                    merged[key].append(value)
    return merged


def _aggregate_formula(aggregate, expression):
    if not aggregate or not expression:
        return expression
    aggregate = str(aggregate).casefold()
    if aggregate == "count_distinct":
        return f"COUNT(DISTINCT {expression})"
    return f"{aggregate.upper()}({expression})"


def _empty_sql_facts():
    return {
        "source_tables": [],
        "column_references": [],
        "formulas": [],
        "join_conditions": [],
        "filters": [],
        "group_by": [],
    }
