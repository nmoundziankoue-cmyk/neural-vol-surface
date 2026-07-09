"""Feedforward baseline: flattened IV grid (day J) -> flattened IV grid
(day J+1). No LSTM/Transformer until this baseline actually works."""

from __future__ import annotations

import torch
import torch.nn as nn


class VolSurfaceForecaster(nn.Module):
    def __init__(self, grid_size: int = 1600, hidden_sizes: tuple[int, ...] = (512, 256, 512)):
        super().__init__()
        dims = [grid_size, *hidden_sizes, grid_size]
        layers: list[nn.Module] = []
        for i in range(len(dims) - 1):
            layers.append(nn.Linear(dims[i], dims[i + 1]))
            if i < len(dims) - 2:
                layers.append(nn.ReLU())
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)
