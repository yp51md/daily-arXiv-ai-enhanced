"""Retry incomplete generations while retaining usable fields and explicit nulls."""

import json
import sys
import time

from structure import Structure


AI_FIELDS = tuple(Structure.model_fields)


def empty_fields():
    return dict.fromkeys(AI_FIELDS)


def generation_status(fields, attempts):
    missing = [name for name in AI_FIELDS if fields[name] is None]
    return {
        "status": "complete" if not missing else "failed" if len(missing) == len(AI_FIELDS) else "partial",
        "attempts": attempts,
        "missing_fields": missing,
    }


def extract_fields(response):
    """Recover valid strings from include_raw output even if schema parsing failed."""
    parsed = response.get("parsed")
    if isinstance(parsed, Structure):
        candidate = parsed.model_dump()
    else:
        candidate = {}
        raw = response.get("raw")
        calls = list(getattr(raw, "tool_calls", []) or [])
        calls += list(getattr(raw, "invalid_tool_calls", []) or [])
        for call in calls:
            if call.get("name") != Structure.__name__:
                continue
            args = call.get("args")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (ValueError, TypeError):
                    continue
            if isinstance(args, dict):
                candidate = args
                break
    return {
        name: value for name, value in candidate.items()
        if name in AI_FIELDS and isinstance(value, str) and value.strip()
    }


def generate_fields(chain, summary, language, paper_id, max_attempts=3):
    fields = empty_fields()
    for attempt in range(1, max_attempts + 1):
        missing = [name for name in AI_FIELDS if fields[name] is None]
        content = summary
        if attempt > 1:
            content += (
                "\n\nPlease return all five structured summary fields. "
                f"The previous response did not provide valid values for: {', '.join(missing)}. "
                "Use null only when a field cannot be determined from the abstract."
            )
        try:
            response = chain.invoke({"language": language, "content": content})
            for name, value in extract_fields(response).items():
                if fields[name] is None:
                    fields[name] = value
            if response.get("parsing_error"):
                print(f"AI schema parsing failed for {paper_id} on attempt {attempt}; retaining valid fields", file=sys.stderr)
        except Exception as error:
            print(f"AI generation attempt {attempt}/{max_attempts} failed for {paper_id}: {error}", file=sys.stderr)
        if all(value is not None for value in fields.values()):
            break
        if attempt < max_attempts:
            time.sleep(attempt)
    status = generation_status(fields, attempt)
    if status["missing_fields"]:
        print(f"AI generation {status['status']} for {paper_id}; using null for {', '.join(status['missing_fields'])}", file=sys.stderr)
    return fields, status
