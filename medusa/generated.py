from pathlib import Path


def write_generated(files: dict[Path, str], generated_dir: Path) -> None:
    expected = set(files)

    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    for orphan in _orphan_files(generated_dir, expected):
        orphan.unlink()

    _prune_empty_dirs(generated_dir)


def stale_files(files: dict[Path, str], generated_dir: Path) -> list[Path]:
    expected = set(files)
    stale: list[Path] = []

    for path, content in files.items():
        if not path.exists():
            stale.append(path)
            continue

        if path.read_text(encoding="utf-8") != content:
            stale.append(path)

    stale.extend(sorted(_orphan_files(generated_dir, expected)))
    return stale


def _orphan_files(generated_dir: Path, expected: set[Path]) -> list[Path]:
    if not generated_dir.exists():
        return []

    return [
        path
        for path in generated_dir.rglob("*")
        if path.is_file() and path not in expected
    ]


def _prune_empty_dirs(generated_dir: Path) -> None:
    if not generated_dir.exists():
        return

    for path in sorted(
        (p for p in generated_dir.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        if not any(path.iterdir()):
            path.rmdir()
