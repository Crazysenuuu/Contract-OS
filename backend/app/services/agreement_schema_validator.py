"""Authoring validation — spec 2.03 sections 5-9.

The authoring studio is schema-driven: the agreement type's schema defines
the intake form, and the backend rebuilds the same rules as a JSON Schema
(JSON-Schema draft 2020-12) — the frontend never validates alone.

Field types (spec 2.03.6): text, textarea, number, money, date, datetime,
duration, percentage, boolean, select, multi_select, country, currency,
legal_entity, user, agreement_party, document, address, email, phone,
clause_selection.

Conditional fields (spec 2.03.7) are declarative and re-encoded as
JSON-Schema `if/then` so the backend enforces exactly the same rules the
form shows.
"""

from jsonschema import Draft202012Validator

_SUPPORTED_TYPES = {
    "text", "textarea", "number", "money", "date", "datetime", "duration",
    "percentage", "boolean", "select", "multi_select", "country", "currency",
    "legal_entity", "user", "agreement_party", "document", "address",
    "email", "phone", "clause_selection",
}


def build_json_schema(agreement_schema: dict) -> dict:
    """Translate a form schema into a JSON-Schema draft 2020-12 document."""
    fields = _collect_fields(agreement_schema)
    properties: dict = {}
    required: list[str] = []
    if_clauses: list[dict] = []

    for field in fields:
        key = field.get("key")
        field_type = str(field.get("type", "text")).lower()
        if not key:
            continue
        if field_type not in _SUPPORTED_TYPES:
            raise ValueError(f"Unsupported field type '{field_type}' for '{key}'")

        rules = _type_rules(field_type, field)
        properties[key] = {"title": field.get("label") or key, **rules}

        condition = field.get("condition")
        if condition:
            when_field = condition.get("field") or condition.get("withField")
            when_value = condition.get("value")
            op = condition.get("operator", "equals")
            if when_field and op == "equals":
                if_clauses.append(
                    {
                        "if": {"properties": {when_field: {"const": when_value}}},
                        "then": {"required": [key]},
                    }
                )
            continue  # conditional fields are never statically required

        if field.get("required"):
            required.append(key)

    schema: dict = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    if if_clauses:
        schema["allOf"] = if_clauses
    return schema


def validate(schema: dict, data: dict) -> None:
    """Validate `data` against `schema`, raising ValueError on failures.

    The error list matches the Pydantic-style shape consumed by the API:
    ``[{"path": [...], "message": "..."}]``.
    """
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        raise ValueError(
            [
                {"path": list(error.path), "message": error.message}
                for error in errors
            ]
        )


def _collect_fields(agreement_schema: dict) -> list[dict]:
    fields: list[dict] = []
    sections = agreement_schema.get("sections")
    if isinstance(sections, list):
        for section in sections:
            section_fields = section.get("fields")
            if isinstance(section_fields, list):
                fields.extend(section_fields)
    questions = agreement_schema.get("questions")
    if isinstance(questions, list):
        fields.extend(questions)
    return fields


def _options(field: dict) -> list:
    raw = field.get("options") or field.get("choices") or []
    out = []
    for opt in raw:
        if isinstance(opt, dict):
            out.append(opt.get("value") or opt.get("label"))
        else:
            out.append(opt)
    return [o for o in out if o is not None]


def _type_rules(field_type: str, field: dict) -> dict:
    if field_type in ("text", "textarea", "country", "currency"):  # noqa: SIM114
        return {"type": "string"}
    if field_type in ("date", "datetime"):
        fmt = "date" if field_type == "date" else "date-time"
        return {"type": "string", "format": fmt}
    if field_type in ("number", "money", "duration", "percentage"):
        return {"type": "number"}
    if field_type == "boolean":
        return {"type": "boolean"}
    if field_type == "email":
        return {"type": "string", "format": "email"}
    if field_type == "phone":
        return {"type": "string", "pattern": r"^[+0-9()\-\s]{6,20}$"}
    if field_type == "select":
        if field.get("multiple"):
            opts = _options(field)
            rule: dict = {"type": "array", "items": {"type": "string"}}
            if opts:
                rule["items"] = {"enum": opts}
            return rule
        opts = _options(field)
        if opts:
            return {"type": "string", "enum": opts}
        return {"type": "string"}
    if field_type == "multi_select":
        opts = _options(field)
        rule = {"type": "array", "items": {"type": "string"}}
        if opts:
            rule["items"] = {"enum": opts}
        return rule
    if field_type == "clause_selection":
        return {"type": "array", "items": {"type": "string"}}
    if field_type == "address":
        return {"type": "object"}
    # legal_entity / user / agreement_party / document — a UUID reference.
    return {"type": "string"}


def extract_entity_references(schema: dict) -> list[str]:
    """Keys of fields whose values reference a legal entity UUID."""
    refs: list[str] = []
    for field in _collect_fields(schema):
        if str(field.get("type", "")).lower() in ("legal_entity", "agreement_party"):
            key = field.get("key")
            if key:
                refs.append(key)
    return refs