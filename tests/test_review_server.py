import pytest
import os
from fastapi import FastAPI
from scripts.review_server import create_app

def test_create_app(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(os, "environ", {"ZURVAN_CONFIG_DIR": str(config_dir)})
    
    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.title == "Zurvan Local Review Workbench"
