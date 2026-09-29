"""`main.py`: identidad del worker y chequeo de ffmpeg al arrancar.

El `worker_id` en EC2 no lleva el pid: si systemd reinicia el proceso, el
heartbeat pisa el mismo ítem de `Workers` en vez de dejar un worker "caído"
para siempre en el panel.
"""

from __future__ import annotations

import pytest

from shared.routing import Pool
from workers import main


def test_worker_id_on_ec2_is_stable_across_restarts() -> None:
    assert main.worker_identity(Pool.VIDEO, ("i-0abc", "c7i-flex.large")) == (
        "video-i-0abc",
        "c7i-flex.large",
    )


def test_worker_id_on_a_laptop_includes_host_and_pid(monkeypatch) -> None:
    monkeypatch.setattr(main.socket, "gethostname", lambda: "laptop")
    monkeypatch.setattr(main.os, "getpid", lambda: 4711)

    assert main.worker_identity(Pool.METADATA, None) == (
        "metadatos-laptop-4711",
        "local",
    )


def test_ec2_identity_is_none_off_ec2(monkeypatch) -> None:
    def unreachable(*args, **kwargs):
        raise OSError("sin IMDS")

    monkeypatch.setattr(main, "urlopen", unreachable)

    assert main.ec2_identity() is None


def test_refuses_to_start_without_ffmpeg(monkeypatch) -> None:
    monkeypatch.setattr(main.shutil, "which", lambda name: None)

    with pytest.raises(SystemExit, match="ffmpeg"):
        main.check_ffmpeg()
