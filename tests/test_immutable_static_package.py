"""Static UI assets must be immutable, packaged, and not created on import.

The installed Router image must work with a read-only root filesystem.
This source-only check is paired with a real built-wheel/image CI smoke.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACKAGE = ROOT / "src" / "auto_router"
ASSETS = ("vendor/htmx.min.js", "vendor/tailwind.min.js")


def test_static_assets_are_locally_vendored_and_not_empty():
    for relative in ASSETS:
        asset = SOURCE_PACKAGE / "static" / relative
        assert asset.is_file(), relative
        assert asset.stat().st_size > 1024, relative


def test_router_import_never_creates_installed_static_directories():
    tree = ast.parse((SOURCE_PACKAGE / "main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        assert not (
            isinstance(func, ast.Attribute)
            and func.attr == "mkdir"
            and isinstance(func.value, (ast.Name, ast.BinOp))
            and (
                isinstance(func.value, ast.Name)
                and func.value.id == "static_dir"
                or isinstance(func.value, ast.BinOp)
                and isinstance(func.value.left, ast.Name)
                and func.value.left.id == "static_dir"
            )
        ), "Router must not create installed static folders at runtime"


def test_wheel_explicitly_packages_both_vendor_assets():
    config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '[tool.setuptools.package-data]' in config
    assert 'auto_router = ["static/vendor/*.js"]' in config
