"""Evaluation metrics for surface forecasts."""

from __future__ import annotations

import torch


def masked_rmse(pred: torch.Tensor, target: torch.Tensor, extrapolated_mask: torch.Tensor) -> float:
    """RMSE over cells where extrapolated_mask is False (real/interpolated
    data). Excluding extrapolated (clamp-filled) cells avoids inflating the
    score on trivially-constant regions that both a naive and a trained
    model can match for free."""
    valid = ~extrapolated_mask.bool()
    if valid.sum() == 0:
        raise ValueError("No non-extrapolated cells available to compute RMSE")
    diff2 = (pred - target) ** 2
    return torch.sqrt(diff2[valid].mean()).item()
