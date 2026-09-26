"""Смоук установленной команды bslfmt: python tests/cli_smoke.py."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

UNFORMATTED = "\ufeffПроцедура П()\r\nА=1;\r\nКонецПроцедуры\r\n"
FORMATTED = "\ufeffПроцедура П()\r\n\tА = 1;\r\nКонецПроцедуры\r\n"


def run(*arguments: str) -> subprocess.CompletedProcess:
    command = shutil.which("bslfmt")
    assert command, "команда bslfmt не найдена в PATH"
    return subprocess.run([command, *arguments], capture_output=True)


def main() -> int:
    version = run("--version")
    assert version.returncode == 0 and version.stdout.startswith(b"bslfmt 0."), version
    assert "Использование".encode("utf-8") in run("--help").stdout
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "Модуль объекта.bsl"
        path.write_bytes(UNFORMATTED.encode("utf-8"))
        check = run("--check", str(path))
        assert check.returncode == 1, check
        assert "нужно отформатировать".encode("utf-8") in check.stdout, check
        fixed = run("-i", str(path))
        assert fixed.returncode == 0, fixed
        assert "изменён".encode("utf-8") in fixed.stdout, fixed
        assert path.read_bytes() == FORMATTED.encode("utf-8"), path.read_bytes()
        assert run("--check", str(path)).returncode == 0
    print("cli smoke: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
