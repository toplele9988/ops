"""Fictional NAS directories live under pytest tmp paths, which are not real mount points.

The attachment module now requires the archive root to be a real mount point (review R7C-02). These older
fixtures simulate a mounted NAS with plain directories, so treat any pytest tmp path as mounted. Tests that
exercise a missing or replaced mount do it by detaching/renaming the directory, which the identity checks catch.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def fictional_nas_is_a_mount(monkeypatch, tmp_path_factory):
    real = os.path.ismount
    base = str(tmp_path_factory.getbasetemp())

    def ismount(path):
        text = os.fspath(path)
        return os.path.isdir(text) and text.startswith(base) or real(path)

    monkeypatch.setattr(os.path, 'ismount', ismount)
