"""Basic training loop: train/val split, MSE loss, Adam, checkpoint saved to disk.

Reports masked RMSE (excluding clamp-extrapolated cells, see
surface_builder.py) for both the trained model and a naive persistence
baseline (predict day J+1 = day J), so a training run is never reported
in isolation - only relative to "doing nothing" beats it.

Run once enough daily snapshots have accumulated (see dataset.py for the
minimum-2-snapshots requirement; realistically want 5+ for a val split
that means anything):
    PYTHONPATH=. python -m app.ml.train
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import torch
from torch.utils.data import DataLoader, TensorDataset, random_split

from app.ml.dataset import InsufficientSnapshotsError, load_training_pairs
from app.ml.metrics import masked_rmse
from app.ml.model import VolSurfaceForecaster

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models"

logger = logging.getLogger("train")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_handler)


@dataclass
class TrainResult:
    checkpoint_path: Path
    n_pairs: int
    n_train: int
    n_val: int
    persistence_rmse_train: float
    model_rmse_train: float
    persistence_rmse_val: float | None
    model_rmse_val: float | None


def train(
    ticker: str = "SPY",
    n_moneyness: int = 40,
    n_tte: int = 40,
    epochs: int = 200,
    lr: float = 1e-3,
    val_ratio: float = 0.2,
    batch_size: int = 8,
    seed: int = 0,
) -> TrainResult:
    dataset = load_training_pairs(ticker=ticker, n_moneyness=n_moneyness, n_tte=n_tte)
    n_pairs = dataset.X.shape[0]
    logger.info("loaded %d training pair(s) on a %dx%d grid", n_pairs, n_moneyness, n_tte)

    n_val = max(1, round(n_pairs * val_ratio)) if n_pairs >= 2 else 0
    n_train = n_pairs - n_val
    if n_train < 1:
        raise InsufficientSnapshotsError(
            f"Only {n_pairs} training pair(s) available — not enough to reserve a "
            f"validation split too. Wait for more daily snapshots before training."
        )
    if n_val == 0:
        logger.warning(
            "only %d pair(s): training with no validation split — RMSE below is "
            "in-sample (the model has seen this exact pair), not a generalization estimate",
            n_pairs,
        )

    full_ds = TensorDataset(dataset.X, dataset.y, dataset.extrapolated_mask)
    generator = torch.Generator().manual_seed(seed)
    if n_val > 0:
        train_ds, val_ds = random_split(full_ds, [n_train, n_val], generator=generator)
    else:
        train_ds, val_ds = full_ds, None

    train_loader = DataLoader(train_ds, batch_size=min(batch_size, n_train), shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=min(batch_size, n_val)) if val_ds is not None else None

    grid_size = n_moneyness * n_tte
    model = VolSurfaceForecaster(grid_size=grid_size)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = torch.nn.MSELoss()

    log_every = max(1, epochs // 20)
    for epoch in range(1, epochs + 1):
        model.train()
        train_loss = 0.0
        for xb, yb, _mb in train_loader:
            optimizer.zero_grad()
            pred = model(xb)
            loss = loss_fn(pred, yb)
            loss.backward()
            optimizer.step()
            train_loss += loss.item() * xb.size(0)
        train_loss /= n_train

        val_loss = float("nan")
        if val_loader is not None:
            model.eval()
            val_loss = 0.0
            with torch.no_grad():
                for xb, yb, _mb in val_loader:
                    val_loss += loss_fn(model(xb), yb).item() * xb.size(0)
            val_loss /= n_val

        if epoch == 1 or epoch % log_every == 0 or epoch == epochs:
            logger.info("epoch %4d/%d  train_loss=%.6f  val_loss=%.6f", epoch, epochs, train_loss, val_loss)

    # Masked RMSE: model vs naive persistence (predict tomorrow = today),
    # both scored only on non-extrapolated cells of the target day.
    def _split_tensors(ds):
        idx = ds.indices if hasattr(ds, "indices") else range(len(ds))
        X = dataset.X[list(idx)]
        y = dataset.y[list(idx)]
        mask = dataset.extrapolated_mask[list(idx)]
        return X, y, mask

    model.eval()
    with torch.no_grad():
        X_tr, y_tr, mask_tr = _split_tensors(train_ds)
        persistence_rmse_train = masked_rmse(X_tr, y_tr, mask_tr)
        model_rmse_train = masked_rmse(model(X_tr), y_tr, mask_tr)

        persistence_rmse_val = model_rmse_val = None
        if val_ds is not None:
            X_val, y_val, mask_val = _split_tensors(val_ds)
            persistence_rmse_val = masked_rmse(X_val, y_val, mask_val)
            model_rmse_val = masked_rmse(model(X_val), y_val, mask_val)

    logger.info(
        "RMSE (train, non-extrapolated cells): persistence=%.6f  model=%.6f  (%s)",
        persistence_rmse_train, model_rmse_train,
        "model better" if model_rmse_train < persistence_rmse_train else "persistence better",
    )
    if val_ds is not None:
        logger.info(
            "RMSE (val, non-extrapolated cells): persistence=%.6f  model=%.6f  (%s)",
            persistence_rmse_val, model_rmse_val,
            "model better" if model_rmse_val < persistence_rmse_val else "persistence better",
        )

    MODEL_DIR.mkdir(exist_ok=True)
    out_path = MODEL_DIR / f"vol_surface_forecaster_{ticker}.pt"
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "grid_size": grid_size,
            "n_moneyness": n_moneyness,
            "n_tte": n_tte,
            "log_moneyness_grid": dataset.log_moneyness_grid,
            "tte_grid": dataset.tte_grid,
        },
        out_path,
    )
    logger.info("model saved to %s", out_path)

    return TrainResult(
        checkpoint_path=out_path,
        n_pairs=n_pairs,
        n_train=n_train,
        n_val=n_val,
        persistence_rmse_train=persistence_rmse_train,
        model_rmse_train=model_rmse_train,
        persistence_rmse_val=persistence_rmse_val,
        model_rmse_val=model_rmse_val,
    )


if __name__ == "__main__":
    train()
