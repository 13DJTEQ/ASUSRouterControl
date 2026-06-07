from __future__ import annotations

import pytest

from asusroutercontrol.cli import _scoped_launchd_label
from asusroutercontrol.config import Config, production_data_dir
from asusroutercontrol.scheduler import MonitorScheduler


def test_non_prod_launchd_labels_are_scoped() -> None:
    base_label = "com.asusroutermonitor"
    assert _scoped_launchd_label(base_label, "prod") == base_label
    assert _scoped_launchd_label(base_label, "dev") == f"{base_label}.dev"


def test_scheduler_rejects_non_prod_with_prod_data_dir() -> None:
    cfg = Config(
        runtime_env="dev",
        data_dir=production_data_dir(),
    )
    with pytest.raises(ValueError, match="cannot use production DATA_DIR"):
        MonitorScheduler(store=object(), cfg=cfg)  # type: ignore[arg-type]
