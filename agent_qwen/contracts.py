"""JSON contracts shared by the visual planner and reviewer."""

import jsonschema


def record(**fields):
    return {"type": "object", "properties": fields, "required": list(fields),
            "additionalProperties": False}


def items(schema):
    return {"type": "array", "items": schema}


def choice(*values):
    return {"type": "string", "enum": list(values)}


TEXT = {"type": "string"}


class InvalidResponse(ValueError):
    """A structured answer cannot be used as a decision."""


def checked(schema, value):
    try:
        jsonschema.validate(value, schema)
    except jsonschema.ValidationError as exc:
        raise InvalidResponse(f"{exc.json_path}: {exc.message[:350]}") from exc
    return value
