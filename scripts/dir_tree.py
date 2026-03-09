"""Visualise a directory tree for auditing folder transfers.

Recursively walks a directory and prints a visual tree using box-drawing
characters. File listings are truncated to 5 per folder (with total count)
to keep output manageable on large directories.

Usage:
    # Print tree to default output file (dir_tree_output.md beside target)
    python scripts/dir_tree.py path/to/directory

    # Specify output file
    python scripts/dir_tree.py path/to/directory -o audit_report.md

    # Change max files shown per folder (default: 5)
    python scripts/dir_tree.py path/to/directory --max-files 10
"""

import argparse
import sys
from pathlib import Path

MAX_FILES_DEFAULT = 5


def build_tree(
    directory: Path, prefix: str, max_files: int, progress: bool = False,
) -> tuple[list[str], int, int]:
    """Recursively build tree lines for *directory*.

    Returns (lines, total_folders, total_files).
    """
    if progress:
        print(f"Scanning: {directory}", file=sys.stderr, flush=True)

    lines: list[str] = []
    total_folders = 0
    total_files = 0

    try:
        entries = sorted(directory.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    except PermissionError:
        lines.append(f"{prefix}[Permission Denied]")
        return lines, 0, 0

    dirs = [e for e in entries if e.is_dir()]
    files = [e for e in entries if e.is_file()]

    total_folders += len(dirs)
    total_files += len(files)

    # Combine items for connector logic: dirs first, then (possibly truncated) files
    items: list[str] = []
    subtrees: dict[int, tuple[list[str], int, int]] = {}

    for i, d in enumerate(dirs):
        items.append(d.name + "/")
        subtrees[len(items) - 1] = build_tree(d, "", max_files, progress=progress)

    if len(files) <= max_files:
        for f in files:
            items.append(f.name)
    else:
        for f in files[:max_files]:
            items.append(f.name)
        remaining = len(files) - max_files
        items.append(f"... and {remaining} more file{'s' if remaining != 1 else ''}")

    for idx, item in enumerate(items):
        is_last = idx == len(items) - 1
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{item}")

        if idx in subtrees:
            sub_lines, sub_folders, sub_files = subtrees[idx]
            extension = "    " if is_last else "│   "
            for sl in sub_lines:
                lines.append(f"{prefix}{extension}{sl}")
            total_folders += sub_folders
            total_files += sub_files

    return lines, total_folders, total_files


def generate_tree(
    directory: Path, max_files: int = MAX_FILES_DEFAULT, progress: bool = False,
) -> str:
    """Return the full tree string for *directory*."""
    root = directory.resolve()
    if not root.is_dir():
        return f"Error: '{root}' is not a directory."

    tree_lines, total_folders, total_files = build_tree(root, "", max_files, progress=progress)

    output_lines = [
        f"# Directory Tree: {root.name}",
        "",
        "```",
        f"{root.name}/",
    ]
    output_lines.extend(tree_lines)
    output_lines.append("```")
    output_lines.append("")
    output_lines.append(f"**Summary:** {total_folders} folders, {total_files} files")
    output_lines.append("")

    return "\n".join(output_lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate a directory tree audit report (.md)."
    )
    parser.add_argument("directory", type=Path, help="Root directory to scan.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .md file (default: dir_tree_output.md beside target directory).",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=MAX_FILES_DEFAULT,
        help=f"Max files shown per folder before truncation (default: {MAX_FILES_DEFAULT}).",
    )

    args = parser.parse_args(argv)

    if not args.directory.is_dir():
        print(f"Error: '{args.directory}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or (args.directory.resolve().parent / "dir_tree_output.md")

    result = generate_tree(args.directory, max_files=args.max_files, progress=True)

    output_path.write_text(result, encoding="utf-8")
    print(f"Tree written to {output_path}")


if __name__ == "__main__":
    main()
