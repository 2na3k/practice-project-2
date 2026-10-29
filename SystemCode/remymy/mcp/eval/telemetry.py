"""Langfuse score publishing for offline evaluation metrics."""

from __future__ import annotations

import math
from numbers import Real
from uuid import NAMESPACE_URL, uuid5
import logging

from langfuse import get_client

logger = logging.getLogger(__name__)

def push_scores(trace_id, metrics: dict, source: str, comment: str, *, no_push: bool = False) -> int:
    if no_push or not trace_id:
        return 0

    client = get_client()
    pushed = 0

    for name, value in metrics.items():
        if value is None or isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
            continue

        try:
            client.create_score(
                trace_id=trace_id,
                name=name,
                value=float(value),
                data_type="NUMERIC",
                score_id=str(uuid5(NAMESPACE_URL, f"remymy/{trace_id}/{source}/{name}")),
                comment=comment,
                metadata={"source": source},
            )
            pushed += 1
        except Exception as e:
            logger.error(f"Failed to push score '{name}': {e}")

    # Force the SDK to send the payload before the script exits
    client.flush()
    return pushed
