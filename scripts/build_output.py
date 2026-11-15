"""Replace one fixed, generated preview directory after a successful build."""

from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile
import uuid


@contextmanager
def fresh_output(root: Path, name: str):
    if name not in {"site", "demo"}:
        raise ValueError("Only the fixed site and demo preview outputs are supported")
    parent = root / "build"
    output = parent / name
    if parent.is_symlink() or output.is_symlink():
        raise ValueError("Generated output directories must not be symlinks")
    parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not output.is_dir():
        raise ValueError("Generated output target must be a directory")
    with tempfile.TemporaryDirectory(prefix="." + name + "-staging-", dir=parent) as temporary:
        staging = Path(temporary)
        yield staging
        if parent.is_symlink() or output.is_symlink():
            raise ValueError("Generated output directory changed to a symlink during the build")
        previous = parent / ("." + name + "-previous-" + uuid.uuid4().hex)
        if output.exists():
            output.rename(previous)
        try:
            staging.rename(output)
        except BaseException:
            if previous.exists():
                previous.rename(output)
            raise
        if previous.exists():
            shutil.rmtree(previous)
