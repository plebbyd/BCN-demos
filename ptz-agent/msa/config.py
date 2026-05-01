"""
msa/config.py — Configuration loader with deep-merge defaults.
"""

import yaml
from pathlib import Path


DEFAULT_CONFIG = {
    "model": {
        "backend": "ollama",
        "model": "gemma4:e2b",
        "max_tokens": 1024,
        "base_url": "http://127.0.0.1:11434",
        "timeout": 600,
    },
    "max_iterations": 12,
    "tools": {},
    "sensors": {},
    "supervisor": {
        "concurrency": 2,
        "poll_seconds": 1.5,
    },
    "webui": {
        "host": "127.0.0.1",
        "port": 8765,
    },
    "sim_ptz": {
        "watch_along": False,
        "move_delay_seconds": 0.45,
        "inference_delay_seconds": 0.35,
    },
}


def load_config(path: str = "config/config.yaml") -> dict:
    config = dict(DEFAULT_CONFIG)
    config_path = Path(path)
    if config_path.exists():
        with open(config_path) as f:
            user_config = yaml.safe_load(f) or {}
        _deep_merge(config, user_config)
    return config


def _deep_merge(base: dict, override: dict):
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
