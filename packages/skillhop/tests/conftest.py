import io
import shlex
import sys
from pathlib import Path

import pytest

from skillhop.cli import main

FAKE = Path(__file__).parent / "servers" / "fakeskills.py"
sys.path.insert(0, str(FAKE.parent))

PDF = """---
name: pdf-processing
description: Extract, fill, and assemble PDF documents
allowed-tools: Bash(python:*)
license: Apache-2.0
metadata:
  version: "1.2"
---

# PDF processing

Choose the matching template from `templates/`, then run `scripts/extract.py`.
"""

REFUNDS = """---
name: refunds
description: Process customer refund requests per company policy
---

Follow `examples/email.md`.
"""


@pytest.fixture
def skills_root(tmp_path):
    root = tmp_path / "served"
    files = {
        "pdf-processing/SKILL.md": PDF,
        "pdf-processing/templates/invoice.md": "# Invoice\n\nCustomer:\nAmount:\n",
        "pdf-processing/scripts/extract.py": "print('extract')\n",
        "acme/billing/refunds/SKILL.md": REFUNDS,
        "acme/billing/refunds/examples/email.md": "Dear customer,\n",
        "acme/support/refunds/SKILL.md": REFUNDS.replace("company policy", "support policy"),
        "git-workflow/SKILL.md": "---\nname: git-workflow\ndescription: Follow this team's Git conventions\n---\n\nUse rebase.\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (root / "pdf-processing" / "assets").mkdir()
    (root / "pdf-processing" / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\xfe")
    return root


@pytest.fixture
def cmd(skills_root):
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE))} {shlex.quote(str(skills_root))}"


@pytest.fixture
def run():
    def _run(*argv):
        out, err = io.StringIO(), io.StringIO()
        code = main([str(a) for a in argv], out, err)
        return code, out.getvalue(), err.getvalue()
    return _run
