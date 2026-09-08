"""The persistence baseline (IV_hat(t+1) = IV(t)) is the yardstick the
whole project is measured against, so it gets its own explicit tests."""

import torch

from app.ml.metrics import masked_rmse


def test_persistence_has_zero_error_on_an_unchanged_surface():
    surface = torch.rand(1, 50)
    mask = torch.zeros(1, 50, dtype=torch.bool)
    # persistence prediction IS the previous surface
    assert masked_rmse(surface, surface, mask) == 0.0


def test_persistence_rmse_matches_hand_calc():
    x = torch.tensor([[0.20, 0.21, 0.19, 0.25]])   # day t  (== persistence prediction)
    y = torch.tensor([[0.22, 0.21, 0.17, 0.30]])   # day t+1 (realized)
    mask = torch.tensor([[False, False, False, True]])  # last cell extrapolated -> excluded

    got = masked_rmse(x, y, mask)
    expected = (((0.20 - 0.22) ** 2 + (0.21 - 0.21) ** 2 + (0.19 - 0.17) ** 2) / 3) ** 0.5
    assert abs(got - expected) < 1e-6


def test_masked_rmse_equals_plain_rmse_when_nothing_is_masked():
    x = torch.rand(3, 40)
    y = torch.rand(3, 40)
    mask = torch.zeros(3, 40, dtype=torch.bool)
    plain = torch.sqrt(((x - y) ** 2).mean()).item()
    assert abs(masked_rmse(x, y, mask) - plain) < 1e-6
