from __future__ import annotations

from src import config, db
from src.models import Cost, GENERATION_STEPS


def log_cost(
    content_id: str,
    step: str,
    api_provider: str,
    tokens_or_units: int,
    cost_usd: float,
) -> None:
    if step not in GENERATION_STEPS:
        raise ValueError(f"Invalid step {step!r}. Must be one of {GENERATION_STEPS}")
    db.insert_cost(Cost(
        content_id=content_id,
        step=step,
        api_provider=api_provider,
        tokens_or_units=tokens_or_units,
        cost_usd=cost_usd,
    ))


def check_budget() -> tuple[float, float, bool]:
    daily_budget = float(config.get("daily_budget_usd", 50.0))
    spent_today = db.total_cost_today()
    return spent_today, daily_budget, spent_today <= daily_budget


def content_cost_summary(content_id: str) -> dict:
    costs = db.costs_for_content(content_id)
    steps: dict[str, float] = {}
    total = 0.0
    for c in costs:
        amount = c.cost_usd or 0.0
        steps[c.step] = steps.get(c.step, 0.0) + amount
        total += amount
    return {"total": total, "steps": steps, "content_id": content_id}
