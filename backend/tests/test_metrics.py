import pytest
import torch

from app.ml.metrics import masked_rmse


def test_masked_rmse_excludes_extrapolated_cells():
    pred = torch.tensor([[1.0, 100.0, 3.0]])
    target = torch.tensor([[1.0, 0.0, 5.0]])
    mask = torch.tensor([[False, True, False]])  # middle cell is extrapolated -> excluded

    # If included, the (100 - 0) error would dominate the RMSE.
    rmse = masked_rmse(pred, target, mask)
    expected = ((1.0 - 1.0) ** 2 + (3.0 - 5.0) ** 2) ** 0.5 / (2 ** 0.5)
    assert rmse == pytest.approx(expected, abs=1e-5)


def test_masked_rmse_zero_for_perfect_prediction():
    pred = torch.tensor([[0.2, 0.3]])
    target = torch.tensor([[0.2, 0.3]])
    mask = torch.tensor([[False, False]])
    assert masked_rmse(pred, target, mask) == pytest.approx(0.0, abs=1e-7)


def test_masked_rmse_raises_when_all_cells_extrapolated():
    pred = torch.tensor([[1.0, 2.0]])
    target = torch.tensor([[1.0, 2.0]])
    mask = torch.tensor([[True, True]])
    with pytest.raises(ValueError):
        masked_rmse(pred, target, mask)
