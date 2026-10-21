from __future__ import annotations

from app.schema_ctx.manifest import Manifest

_SYSTEM_RULES = (
    "You generate a single PostgreSQL SELECT query that answers the user's question. "
    "Only use the tables and columns listed below. "
    "Rules: one statement, SELECT only, no writes, no locking, explicit column names, "
    "and a LIMIT unless the question demands every row."
)


def build_schema_slice(manifest: Manifest) -> str:
    lines: list[str] = []
    for schema_name, schema in manifest.schemas.items():
        for table_name, table in schema.tables.items():
            lines.append(f"{schema_name}.{table_name} ({table.description})")
            for column_name, column in table.columns.items():
                lines.append(f"  {column_name} {column.type} - {column.description}")
    lines.append("joins:")
    lines.extend(f"  {relation}" for relation in manifest.relations)
    return "\n".join(lines)


def build_messages(question: str, sport: str, manifest: Manifest) -> list[dict[str, str]]:
    system = f"{_SYSTEM_RULES}\n\n{build_schema_slice(manifest)}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"sport: {sport}\nquestion: {question}"},
    ]
