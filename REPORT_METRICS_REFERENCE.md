# Report Metrics Reference

This file documents the percent-based metrics shown in the `report` command's morning briefing output, what each metric means, how it is calculated, and why it is useful.

## Core Idea

The report uses percentages for four different purposes:

- Direct performance ratios
- Week-over-week change metrics
- Ranking buckets
- Decision thresholds

They all look similar in the output, but they do not mean the same thing.

## 1. Post Engagement Rate

Label in report:

- `X% engagement`

Formula:

```text
(likes + comments + shares + saves) / views
```

Where it is used:

- `Top Performers`
- `Worst Performers`
- Per-post comparisons across the report

Why it exists:

- This normalizes interaction by audience size.
- A post with fewer views but stronger response should rank above a post with the same raw likes on much larger reach.
- It is the report's main "quality per view" metric.

Caveat:

- Platform adapters do not all provide the same interaction fields, so this is directionally useful but not perfectly apples-to-apples across every platform.

## 2. Aggregate Engagement Rate

Label in report:

- `Engagement: X%`
- `Best platform: ... X% engagement`
- Combo and cohort engagement percentages

Formula:

```text
total_engagements / total_views
```

Where `total_engagements` is:

```text
likes + comments + shares + saves
```

Why it exists:

- This is the rolled-up version of post engagement.
- It is more stable than averaging each post's engagement rate equally because larger posts naturally contribute more weight.
- It helps compare platforms, themes, hooks, and cohort groupings on a common normalized basis.

## 3. Average Watch-Through Rate

Label in report:

- `Avg watch-through rate: X%`
- `WTR X%`

Formula:

```text
average of all non-null watch_through_rate values in the selected window
```

Why it exists:

- Watch-through rate measures retention, not interaction.
- A creative can hold attention even if it does not yet generate many likes or shares.
- This is useful for short-form video because strong hooks often show up in retention before they show up in social engagement.

Caveat:

- This is a simple average of available watch-through values, not a view-weighted average.
- It only appears when a platform integration actually stores `watch_through_rate`.

## 4. Week-Over-Week Views Change

Label in report:

- `views up/down X%`
- `7-day views down X% vs prior week`

Formula:

```text
(current_7d_views - prior_7d_views) / prior_7d_views
```

Why it exists:

- This tracks reach trend over time.
- It answers: "Are we getting more or less exposure than the prior week?"
- It is a volume trend metric, not a quality metric.

Caveat:

- If the prior period has zero or near-zero views, the comparison becomes unstable or unavailable.

## 5. Week-Over-Week Engagement Change

Label in report:

- `engagement up/down X%`
- `7-day engagement down X% vs prior week`

Formula:

```text
(current_7d_engagement_rate - prior_7d_engagement_rate) / prior_7d_engagement_rate
```

Why it exists:

- This separates quality shift from reach shift.
- If views are flat but engagement is down, the issue is probably creative quality rather than distribution alone.
- It is one of the main trend signals used in action items.

## 6. Top 25 Percent / Bottom 25 Percent Cohort Labels

Label in report:

- `Top 25% = winners, bottom 25% = losers`

What it means:

- This is not a direct measured metric.
- It is a percentile-based ranking cutoff used in organic evaluation.

Formula:

```text
winners = top 25% of ranked cohorts
losers = bottom 25% of ranked cohorts
middle = everything in between
```

Why it exists:

- The report needs a practical review workflow, not just raw scores.
- Ranking peers within the same evaluation batch is more operationally useful than trying to define one universal "good" score.
- It helps identify which cohorts to repeat, remix, or retire.

Caveat:

- The label depends on the configured ranking objective.
- By default, cohorts are ranked by engagement rate, but they can also be ranked by views, revenue, sessions, or purchases.

## 7. Organic Evaluation Winner Engagement

Label in report:

- `Winners (repeat or promote): ... X%`
- `Middle (consider remixing): ... X%`
- `Losers (retire or refresh): ... X%`

Formula:

```text
cohort total engagements / cohort total views
```

Why it exists:

- Cohorts group creative performance at a more strategic level.
- This helps compare repeated patterns across product, platform, format, hook, and CTA.
- It is meant for decision support, not automatic promotion or retirement.

## 8. Bandit Mean Percent

Label in report:

- `mean X%`

Formula:

```text
alpha / (alpha + beta)
```

What it means:

- This is the posterior mean of a bandit arm.
- It is not the same thing as observed engagement rate.

Why it exists:

- The recommender needs a smoothed estimate of how likely an arm is to succeed in the future.
- Bayesian smoothing keeps early results from being too noisy.
- It also allows newer arms to remain competitive enough to be explored.

Important note:

- "Success" is defined relative to the configured ranking objective.
- By default, success means beating the median engagement rate.
- If the objective is `views`, `revenue`, `sessions`, or `purchases`, success instead means beating the median for that objective.

Practical interpretation:

- `mean 56%` means "this arm is estimated to beat the success threshold about 56% of the time."
- It does not mean "this arm gets 56% engagement."

## 9. Product Decline Percent

Label in report:

- `Declining engagement ... down X%`

Formula:

```text
(recent_avg_engagement_rate - prior_avg_engagement_rate) / prior_avg_engagement_rate
```

Why it exists:

- This is a product health signal.
- It helps flag sustained deterioration across a product's recent content rather than overreacting to one weak post.

## 10. Pause Threshold: Average Engagement Below 1 Percent

Label in report:

- `Consider pausing (avg engagement <1%)`

What it means:

- This is a fixed threshold, not a computed comparison output.

Rule:

```text
average engagement rate < 0.01
```

Why it exists:

- It acts as a simple operational guardrail.
- If a product is consistently below 1% engagement over enough history, it may need a major creative reset before more budget or generation effort is spent on it.

## 11. Creative Retest Threshold: 1.5 Percent

Label in report:

- `Retest ... creative (7-day engagement X%)`

Rule:

```text
weakest repeated combo engagement rate < 0.015
```

Why it exists:

- Repeated creative combinations that are persistently weak should be reviewed instead of reused blindly.
- This threshold turns low engagement into a specific action item.

## 12. Views Down 20 Percent Threshold

Label in report:

- `7-day views down X% vs prior week`

Rule:

```text
week-over-week views change < -20%
```

Why it exists:

- This is a prioritization threshold.
- Small movement is common; a drop larger than 20% is treated as large enough to deserve attention.

## 13. Engagement Down 15 Percent Threshold

Label in report:

- `7-day engagement down X% vs prior week`

Rule:

```text
week-over-week engagement change < -15%
```

Why it exists:

- This flags a meaningful drop in content quality or audience response.
- It is slightly more sensitive than the views threshold because engagement deterioration can show up before distribution problems become obvious.

## 14. Budget Usage Percent

Label in report:

- `Budget usage at X% of daily limit`

Formula:

```text
yesterday_cost / daily_budget
```

Why it exists:

- This shows how much of the daily budget was consumed.
- It provides a quick budget-pressure signal for action items.

Caveat:

- In the current implementation, this uses yesterday's spend while other budget logic also checks today's status, so the semantics are not perfectly aligned.

## Summary

The main percent metrics answer different questions:

- Engagement percent: how much interaction did we get per view?
- Watch-through percent: how much of the content did people actually consume?
- Change percent: are we improving or declining versus the previous week?
- Winner/loser percent bands: where does this cohort rank relative to peers?
- Bandit mean percent: how likely is this creative arm to beat the current success threshold in the future?

## Key Caveats

- Small sample sizes can create misleadingly high or low percentages.
- Cross-platform engagement is not perfectly comparable because different adapters expose different source fields.
- Some metrics are direct observations, while others are ranking or decision thresholds.
- Budget percent currently mixes yesterday-spend framing with today-status checks.
