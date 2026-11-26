"""Record boundaries for UTF-8 JSON Lines, without normalizing string data."""

import json


def record_lines(content: str) -> list[str]:
    if not content:
        return []
    return content.removesuffix("\n").split("\n")


def read_records(payload: bytes) -> list:
    return [json.loads(line) for line in record_lines(payload.decode("utf-8"))]
