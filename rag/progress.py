"""Progress reporting for RAG indexing (tqdm when available, else stderr %)."""

from __future__ import annotations

import sys


class Reporter:
    def __init__(self, *, quiet: bool) -> None:
        self.quiet = quiet
        self._use_tqdm = False
        if not quiet and sys.stderr.isatty():
            try:
                import tqdm  # noqa: F401

                self._use_tqdm = True
            except ImportError:
                pass

    def line(self, msg: str) -> None:
        if not self.quiet:
            print(msg, file=sys.stderr, flush=True)

    def phase(self, n: int, total: int, label: str) -> None:
        self.line(f"[{n}/{total}] {label}")

    def bar(self, total: int, desc: str):
        if self.quiet or total <= 0:
            return NullBar()
        if self._use_tqdm:
            from tqdm import tqdm

            return tqdm(total=total, desc=desc, unit="file", file=sys.stderr, leave=False)
        return SimpleBar(total, desc, self.line)


class NullBar:
    def update(self, n: int = 1) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        self.close()


class SimpleBar:
    def __init__(self, total: int, desc: str, emit) -> None:
        self.total = max(total, 1)
        self.desc = desc
        self.emit = emit
        self.done = 0
        self._last_pct = -1

    def update(self, n: int = 1) -> None:
        self.done += n
        pct = int(100 * self.done / self.total)
        if pct != self._last_pct and (pct % 5 == 0 or self.done >= self.total):
            self._last_pct = pct
            self.emit(f"  {self.desc}: {self.done}/{self.total} ({pct}%)")

    def close(self) -> None:
        if self.done < self.total:
            self.emit(f"  {self.desc}: {self.total}/{self.total} (100%)")

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        self.close()
