"""Comparison tool sidecar.

Given two metric maps (pre + post) and per-metric improvement thresholds,
returns deltas + a per-metric pass/fail + an overall verdict. The
Verification Agent uses this to decide whether the LLM evaluator should
even be given the benefit of the doubt.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="comparison-tool", version="0.1.0")


class CompareRequest(BaseModel):
    pre: dict[str, float] = Field(..., description="Baseline metrics keyed by name")
    post: dict[str, float] = Field(..., description="Post-fix metrics keyed by name")
    improvement_required_pct: dict[str, float] = Field(
        default_factory=dict,
        description="Per-metric: minimum % improvement (relative to pre) for that metric to count as 'improved'",
    )


class MetricDelta(BaseModel):
    metric: str
    pre: float
    post: float
    delta: float
    delta_pct: float | None
    required_pct: float | None
    improved: bool


class CompareResponse(BaseModel):
    metrics: list[MetricDelta]
    improved_count: int
    regressed_count: int
    overall_improved: bool


def _pct(pre: float, post: float) -> float | None:
    if pre == 0:
        return None
    return round((post - pre) / pre * 100.0, 2)


@app.post("/compare", response_model=CompareResponse)
async def compare(req: CompareRequest) -> CompareResponse:
    out: list[MetricDelta] = []
    improved = 0
    regressed = 0
    for metric in sorted(set(req.pre) | set(req.post)):
        pre = float(req.pre.get(metric, 0.0))
        post = float(req.post.get(metric, 0.0))
        delta = post - pre
        dp = _pct(pre, post)
        required = req.improvement_required_pct.get(metric)
        # Improvement is metric-direction-aware: lower is better for *_rate /
        # *_failures / *_latency / drops*; higher is better for *_success*.
        lower_is_better = any(token in metric for token in ("rate", "failures", "latency", "drops"))
        if lower_is_better:
            improved_flag = (dp is not None and dp <= -1.0 * (required if required is not None else 0.0))
        else:
            improved_flag = (delta >= (required if required is not None else 0.0))
        if improved_flag:
            improved += 1
        elif delta * (1 if not lower_is_better else -1) < 0:
            regressed += 1
        out.append(MetricDelta(
            metric=metric, pre=pre, post=post, delta=round(delta, 4),
            delta_pct=dp, required_pct=required, improved=improved_flag,
        ))
    return CompareResponse(metrics=out, improved_count=improved, regressed_count=regressed,
                           overall_improved=improved > 0 and regressed == 0)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "comparison-tool", "version": "0.1.0"}
