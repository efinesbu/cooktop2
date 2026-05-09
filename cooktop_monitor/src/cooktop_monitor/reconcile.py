"""Combine bar graph, labels, masks → ElementReading + confidence + warnings."""

from __future__ import annotations

from typing import Any

from cooktop_monitor.detect_element import (
    CLUSTER_LIT_FRACTION_ACTIVE,
    ON_INDICATOR_LIT_FRACTION,
)
from cooktop_monitor.schema import ElementLabel, ElementReading


_LABEL_PRIORITY: tuple[ElementLabel, ...] = (
    "BOIL",
    "SIMMER",
    "K. WARM",
    "MELT",
)
_LABEL_TO_LEVEL: dict[ElementLabel, int] = {
    "MELT": 1,
    "K. WARM": 2,
    "SIMMER": 3,
    "BOIL": 10,
}


def reconcile_element(
    signals: dict[str, Any],
    *,
    has_zone_size: bool,
) -> tuple[ElementReading, float, list[str]]:
    warnings: list[str] = []

    cf = float(signals["cluster_lit_fraction"])
    onf = float(signals["on_indicator_lit_fraction"])
    h_detected = bool(signals["h_detected"])
    bar_count = int(signals["bar_count"])
    label_lit_raw = signals["label_lit"]
    label_lit: dict[str, bool] = {str(k): bool(v) for k, v in label_lit_raw.items()}

    active = cf >= CLUSTER_LIT_FRACTION_ACTIVE or onf >= ON_INDICATOR_LIT_FRACTION

    def zone_reading(for_active_or_hot: bool) -> int | None:
        if not has_zone_size:
            return None
        if not for_active_or_hot:
            return None
        zdc = signals.get("zone_dot_count")
        if zdc is None:
            return None
        zi = int(zdc)
        if 1 <= zi <= 3:
            return zi
        warnings.append("zone size dots out of range")
        return None

    if active:
        hot = bool(h_detected)
        lit_labels = [lbl for lbl in _LABEL_PRIORITY if label_lit.get(lbl, False)]
        chosen_label: ElementLabel | None = None

        if len(lit_labels) > 1:
            for cand in _LABEL_PRIORITY:
                if cand in lit_labels:
                    chosen_label = cand
                    break
            warnings.append("multiple labels lit; precedence applied BOIL>SIMMER>K. WARM>MELT")

        elif len(lit_labels) == 1:
            chosen_label = lit_labels[0]

        level_from_label: int | None = (
            _LABEL_TO_LEVEL[chosen_label] if chosen_label is not None else None
        )

        confidence: float
        label_out: ElementLabel | None = chosen_label
        level: int | None

        if chosen_label is not None and level_from_label is not None:
            lbl_lv = level_from_label
            diff = abs(bar_count - lbl_lv)
            if diff == 0:
                level = lbl_lv
                confidence = 0.95
            elif diff == 1:
                level = bar_count
                confidence = 0.70
                warnings.append(
                    "bar graph differs from lit label level by one; using bar count"
                )
            else:
                level = lbl_lv
                confidence = 0.55
                warnings.append("bar graph strongly disagrees with lit label level; using label level")
        elif 1 <= bar_count <= 10:
            level = bar_count
            label_out = None
            confidence = 0.70
        elif bar_count == 0:
            level = None
            label_out = None
            confidence = 0.40
            warnings.append("active element has zero bar segments lit")
        else:
            # Bar count unusable (>10 shouldn't happen); keep active without level confidence.
            level = None
            label_out = None
            confidence = 0.40
            warnings.append("bar graph count unusable")

        zs = zone_reading(True)
        return (
            ElementReading(
                state="active",
                level=level,
                label=label_out,
                hot=hot,
                zone_size=zs,
            ),
            confidence,
            warnings,
        )

    hot_off = bool(h_detected)

    warnings_off: list[str] = []

    zs_off = zone_reading(hot_off)

    if hot_off:
        conf_off = 0.85
        reading = ElementReading(
            state="off",
            level=None,
            label=None,
            hot=True,
            zone_size=zs_off,
        )
        return reading, conf_off, warnings_off

    if (
        cf < CLUSTER_LIT_FRACTION_ACTIVE
        and onf < ON_INDICATOR_LIT_FRACTION
    ):
        conf_off = 0.90
    else:
        conf_off = 0.60
        warnings_off.append("ambiguous low control signal vs off thresholds")

    return (
        ElementReading(
            state="off",
            level=None,
            label=None,
            hot=False,
            zone_size=zs_off,
        ),
        conf_off,
        warnings_off,
    )
