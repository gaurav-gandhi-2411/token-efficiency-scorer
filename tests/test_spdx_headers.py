# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("spdx_headers", ROOT / "scripts" / "spdx_headers.py")
assert _SPEC is not None and _SPEC.loader is not None
spdx = importlib.util.module_from_spec(_SPEC)
sys.modules["spdx_headers"] = spdx
_SPEC.loader.exec_module(spdx)

HEADER = "# Copyright 2026 Gaurav Gandhi\n# SPDX-License-Identifier: Apache-2.0\n"


def test_header_goes_first_and_future_import_stays_first_code_line() -> None:
    out = spdx.add_header("from __future__ import annotations\n\nx = 1\n")
    assert out == HEADER + "\nfrom __future__ import annotations\n\nx = 1\n"


def test_header_after_shebang_and_coding_line() -> None:
    out = spdx.add_header('#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n"""Doc."""\n')
    assert out.startswith("#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n" + HEADER)
    assert out.endswith('"""Doc."""\n')


def test_add_header_is_idempotent() -> None:
    once = spdx.add_header("x = 1\n")
    assert spdx.add_header(once) == once


def test_empty_file_gets_just_the_header() -> None:
    assert spdx.add_header("") == HEADER


def test_dual_licence_block_is_replaced() -> None:
    dual = (
        "# Copyright 2026 Gaurav Gandhi\n#\n# Dual-licensed. You may use this file under the terms of"
        " either:\n#   - the GNU Affero General Public License v3.0 only (AGPL-3.0-only), or\n"
        "#   - the Apache License, Version 2.0 (Apache-2.0),\n# at your option.\n#\n"
        "# SPDX-License-Identifier: AGPL-3.0-only OR Apache-2.0\n#\n"
        "# AGPL-3.0-only text: see LICENSE in the repository root.\n"
        "# Apache-2.0 text: see LICENSE-APACHE in the repository root.\n\nx = 1\n"
    )
    out = spdx.add_header(dual)
    assert out == HEADER + "\nx = 1\n"
    assert "AGPL" not in out


def test_process_preserves_crlf_and_check_mode_does_not_write(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_bytes(b"x = 1\r\ny = 2\r\n")
    assert spdx.process(f, write=False) is True
    assert f.read_bytes() == b"x = 1\r\ny = 2\r\n"
    assert spdx.process(f, write=True) is True
    data = f.read_bytes()
    assert data.startswith(b"# Copyright 2026 Gaurav Gandhi\r\n# SPDX-License-Identifier")
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert spdx.process(f, write=True) is False


def test_every_first_party_python_file_carries_the_header() -> None:
    missing = [
        p.relative_to(ROOT).as_posix()
        for p in spdx.python_files(ROOT)
        if not spdx.has_header(p.read_text(encoding="utf-8-sig"))
    ]
    assert not missing, f"run `python scripts/spdx_headers.py`: {missing}"


def test_frozen_detector_module_is_excluded() -> None:
    names = {p.relative_to(ROOT).as_posix() for p in spdx.python_files(ROOT)}
    assert "tes/_waste_detectors.py" not in names
    assert "tes/cost.py" in names
