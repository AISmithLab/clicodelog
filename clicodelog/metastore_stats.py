"""Token-usage analytics over the metadata store, aggregated in SQL."""

import json

from .metastore import connect


def usage_totals(source_id: str, project_id: str | None = None) -> dict:
    """Aggregate token usage in SQL rather than in Python."""
    sql = ("SELECT count(*) sessions, "
           "COALESCE(sum(u_input),0) i, COALESCE(sum(u_output),0) o, "
           "COALESCE(sum(u_cache_read),0) cr, COALESCE(sum(u_cache_creation),0) cc "
           "FROM sessions WHERE source=?")
    params: list = [source_id]
    if project_id:
        sql += " AND project_id=?"
        params.append(project_id)
    r = connect().execute(sql, params).fetchone()
    return {"sessions": r["sessions"], "input": r["i"], "output": r["o"],
            "cache_read": r["cr"], "cache_creation": r["cc"]}


def usage_by(source_id: str, group: str, project_id: str | None = None,
             limit: int = 400) -> list:
    """GROUP BY day or project, entirely in SQL."""
    if group == "day":
        key = "substr(COALESCE(last_ts, first_ts), 1, 10)"
    elif group == "project":
        key = "project_name"
    else:
        return []
    sql = (f"SELECT {key} AS k, count(*) sessions, "
           f"COALESCE(sum(u_input),0) i, COALESCE(sum(u_output),0) o, "
           f"COALESCE(sum(u_cache_read),0) cr, COALESCE(sum(u_cache_creation),0) cc "
           f"FROM sessions WHERE source=?")
    params: list = [source_id]
    if project_id:
        sql += " AND project_id=?"
        params.append(project_id)
    sql += f" GROUP BY k ORDER BY {'k DESC' if group == 'day' else '(i+o+cr+cc) DESC'} LIMIT ?"
    params.append(limit)

    return [{
        "key": r["k"] or "unknown",
        "sessions": r["sessions"],
        "usage": {"input": r["i"], "output": r["o"],
                  "cache_read": r["cr"], "cache_creation": r["cc"]},
        "total_tokens": r["i"] + r["o"] + r["cr"] + r["cc"],
    } for r in connect().execute(sql, params)]


def json_counter_totals(source_id: str, column: str, limit: int = 25) -> dict:
    """Sum the small JSON counter columns (models, tools) by streaming."""
    if column not in ("models", "tools"):
        return {}
    totals: dict = {}
    cur = connect().execute(
        f"SELECT {column} c FROM sessions WHERE source=? AND {column} IS NOT NULL",
        (source_id,))
    while True:
        rows = cur.fetchmany(500)
        if not rows:
            break
        for r in rows:
            try:
                for name, n in json.loads(r["c"]).items():
                    totals[name] = totals.get(name, 0) + n
            except (json.JSONDecodeError, AttributeError):
                continue
    return dict(sorted(totals.items(), key=lambda kv: -kv[1])[:limit])
