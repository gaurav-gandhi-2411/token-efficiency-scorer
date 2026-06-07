from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tes.web.server import ServerConfig, create_app, start_server


def test_server_binds_127_0_0_1(tmp_path: Path) -> None:
    """start_server must pass host='127.0.0.1' to Flask.run — never 0.0.0.0."""
    config = ServerConfig(host="127.0.0.1", port=9001, db_path=tmp_path / "tes.db")
    with patch("tes.web.server.Flask.run") as mock_run:
        start_server(config)
    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args
    host = call_kwargs.kwargs.get("host") if call_kwargs.kwargs else call_kwargs.args[0]
    assert host == "127.0.0.1", f"Expected 127.0.0.1, got {host!r}"
    assert host != "0.0.0.0"
