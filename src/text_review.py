from __future__ import annotations

import importlib
import logging
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from src import config, db
from src.models import TextInsight

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You analyze short-form social content performance and extract one reusable text-only insight.

Write a single concise paragraph for future prompt injection.
- Focus on hook wording, narrative framing, proof language, CTA phrasing, and other text-level patterns.
- Ground every claim in the supplied sample.
- Do not give image, video, editing, rendering, posting cadence, or generic platform advice.
- If the evidence is mixed, state what seems to correlate rather than overclaiming.
- Keep the insight practical and reusable.
- Use plain text only. No bullets, no markdown, no JSON.
"""


def run_text_review(
    min_posts: int,
    product_sku: str | None = None,
    platform: str | None = None,
    creative_format: str | None = None,
    lookback_days: int = 30,
) -> TextInsight | None:
    if min_posts <= 0:
        raise ValueError("min_posts must be greater than 0")

    rows = db.list_recent_text_review_rows(
        product_sku=product_sku,
        platform=platform,
        creative_format=creative_format,
        lookback_days=lookback_days,
    )
    if len(rows) < min_posts:
        return None

    api_key = config.get("openai.api_key")
    if not api_key:
        raise ValueError(
            "Missing `openai.api_key` in config.yaml. "
            "Copy config.example.yaml to config.yaml and add your OpenAI credentials."
        )

    model = config.get("openai.model", "gpt-5.4")
    openai_module = _load_openai_module()
    client = openai_module.OpenAI(api_key=api_key)

    prompt = _build_analysis_prompt(
        rows,
        product_sku=product_sku,
        platform=platform,
        creative_format=creative_format,
        lookback_days=lookback_days,
    )
    response = _call_with_retries(
        client,
        openai_module,
        model,
        prompt,
        max_attempts=3,
        max_output_tokens=220,
    )
    insight_text = _normalize_insight_text(_response_text(response))
    if not insight_text:
        raise ValueError("Text review returned an empty insight.")

    insight = TextInsight(
        id=uuid.uuid4().hex[:16],
        product_sku=product_sku,
        platform=platform,
        creative_format=creative_format,
        insight_text=insight_text,
        source_post_count=len(rows),
        created_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    )
    db.insert_text_insight(insight)
    return insight


def _build_analysis_prompt(
    rows: list[dict[str, object]],
    *,
    product_sku: str | None,
    platform: str | None,
    creative_format: str | None,
    lookback_days: int,
) -> str:
    overall_avg = _average_rate(rows)
    theme_summary = _summarize_by_key(rows, "theme")
    hook_type_summary = _summarize_by_key(rows, "hook_type")

    top_rows = sorted(rows, key=_performance_sort_key, reverse=True)[:10]
    recent_rows = rows[:10]

    lines = [
        "Scope:",
        f"- product_sku: {product_sku or 'all'}",
        f"- platform: {platform or 'all'}",
        f"- creative_format: {creative_format or 'all'}",
        f"- lookback_days: {lookback_days}",
        f"- analyzed_content_rows: {len(rows)}",
        f"- overall_avg_engagement_rate: {_format_rate(overall_avg)}",
        "",
        "Theme pattern summary:",
        *theme_summary,
        "",
        "Hook type pattern summary:",
        *hook_type_summary,
        "",
        "Highest-performing content samples:",
        *_format_row_lines(top_rows),
        "",
        "Most recent content samples:",
        *_format_row_lines(recent_rows),
        "",
        "Task:",
        "Write one concise reusable text insight for future prompt injection.",
        "Focus on hook wording, narrative framing, proof language, CTA phrasing, and which text patterns seem to correlate with stronger engagement.",
        "Avoid image advice, production advice, and generic platform playbooks.",
    ]
    return "\n".join(lines)


def _summarize_by_key(rows: list[dict[str, object]], key: str) -> list[str]:
    buckets: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        label = _clean_text_fragment(str(row.get(key) or "unknown"), limit=80) or "unknown"
        buckets[label].append(row)

    summary_lines: list[str] = []
    ranked = sorted(
        buckets.items(),
        key=lambda item: (
            _average_rate(item[1]),
            sum(int(sample.get("total_views") or 0) for sample in item[1]),
            item[0],
        ),
        reverse=True,
    )
    for label, bucket in ranked[:6]:
        total_views = sum(int(sample.get("total_views") or 0) for sample in bucket)
        summary_lines.append(
            f"- {label}: {len(bucket)} rows, avg engagement_rate {_format_rate(_average_rate(bucket))}, total_views {total_views}"
        )
    return summary_lines or ["- none"]


def _format_row_lines(rows: list[dict[str, object]]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        lines.append(
            "- "
            f"rate {_format_rate(float(row.get('engagement_rate') or 0.0))} | "
            f"views {int(row.get('total_views') or 0)} | "
            f"engagements {int(row.get('total_engagements') or 0)} | "
            f"theme {row.get('theme') or 'unknown'} | "
            f"hook_type {row.get('hook_type') or 'unknown'} | "
            f"hook_text \"{_clean_text_fragment(row.get('hook_text'), limit=160)}\""
        )
    return lines or ["- none"]


def _average_rate(rows: list[dict[str, object]]) -> float:
    if not rows:
        return 0.0
    return sum(float(row.get("engagement_rate") or 0.0) for row in rows) / len(rows)


def _performance_sort_key(row: dict[str, object]) -> tuple[float, int, int, str]:
    return (
        float(row.get("engagement_rate") or 0.0),
        int(row.get("total_engagements") or 0),
        int(row.get("total_views") or 0),
        str(row.get("content_id") or ""),
    )


def _format_rate(value: float) -> str:
    return f"{value * 100:.2f}%"


def _clean_text_fragment(value: object, limit: int = 160) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    if not text:
        return "(none)"
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _normalize_insight_text(value: str) -> str:
    text = value.strip()
    text = re.sub(r"\s+", " ", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return text.strip()


def _load_openai_module() -> Any:
    try:
        return importlib.import_module("openai")
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI SDK is not installed. Run `pip install -r requirements.txt`."
        ) from exc


def _uses_responses_api(model: str, client: Any) -> bool:
    normalized_model = (model or "").strip().lower()
    return normalized_model.startswith("gpt-5") and hasattr(client, "responses")


def _create_openai_response(
    client: Any,
    model: str,
    user_msg: str,
    max_output_tokens: int,
) -> Any:
    if _uses_responses_api(model, client):
        return client.responses.create(
            model=model,
            instructions=_SYSTEM_PROMPT,
            input=user_msg,
            max_output_tokens=max_output_tokens,
        )
    return client.chat.completions.create(
        model=model,
        max_completion_tokens=max_output_tokens,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )


def _call_with_retries(
    client: Any,
    openai_module: Any,
    model: str,
    user_msg: str,
    *,
    max_attempts: int,
    max_output_tokens: int,
) -> Any:
    delay = 2.0
    for attempt in range(1, max_attempts + 1):
        try:
            response = _create_openai_response(
                client,
                model,
                user_msg,
                max_output_tokens,
            )
            _response_text(response)
            return response
        except (
            openai_module.APIConnectionError,
            openai_module.RateLimitError,
            openai_module.APIStatusError,
        ) as exc:
            if attempt == max_attempts:
                raise
            logger.warning(
                "OpenAI text review attempt %d/%d failed: %s",
                attempt,
                max_attempts,
                exc,
            )
            time.sleep(delay)
            delay *= 2
        except ValueError:
            if attempt == max_attempts:
                raise ValueError(
                    "OpenAI returned an empty response after multiple retry attempts."
                )
            time.sleep(delay)
            delay *= 2


def _response_text(response: Any) -> str:
    raw = getattr(response, "output_text", None)
    if raw is None and hasattr(response, "choices"):
        choice = response.choices[0]
        raw = choice.message.content or ""
    if raw is None:
        raw = ""
    if not raw.strip():
        raise ValueError("OpenAI returned an empty response.")
    return raw.strip()
