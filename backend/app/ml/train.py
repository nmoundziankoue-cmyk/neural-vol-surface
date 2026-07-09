"""Basic training loop: train/val split, MSE loss, Adam, checkpoint saved to disk.

Run once enough daily snapshots have accumulated (see dataset.py for the
minimum-2-snapshots requirement; realistically want 5+ for a val split
that means anything):
    PYTHONPATH=. python -m app.ml.train
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import torch
from torch.utils.data import DataLoader, TensorDataset, random_split

from app.ml.dataset import InsufficientSnapshotsError, load_training_pairs
from app.ml.model import VolSurfaceForecaster

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models"

logger = logging.getLogger("train")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_handler)


def train(
    ticker: str = "SPY",
    n_moneyness: int = 40,
    n_tte: int = 40,
    epochs: int = 200,
    lr: float = 1e-3,
    val_ratio: float = 0.2,
    batch_size: int = 8,
    seed: int = 0,
) -> Path:
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
        logger.warning("only %d pair(s): training with no validation split", n_pairs)

    full_ds = TensorDataset(dataset.X, dataset.y)
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
        for xb, yb in train_loader:
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
                for xb, yb in val_loader:
                    val_loss += loss_fn(model(xb), yb).item() * xb.size(0)
            val_loss /= n_val

        if epoch == 1 or epoch % log_every == 0 or epoch == epochs:
            logger.info("epoch %4d/%d  train_loss=%.6f  val_loss=%.6f", epoch, epochs, train_loss, val_loss)

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
    return out_path


if __name__ == "__main__":
    train()
