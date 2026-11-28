"""Where a command's time goes: one JSON line per timed span in ~/.beacon/trace.jsonl. `python timing.py` prints the
median of each span over the last commands."""
import json
import statistics
import sys
import time
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

TRACE = Path("~/.beacon/trace.jsonl").expanduser()
RECENT_COMMANDS = 50
command: ContextVar[str] = ContextVar("command", default="")  # tasks started inside a command inherit it


@contextmanager
def span(name: str, **fields):
    """Time the block and record it under the running command; the block may add fields to the dict it gets."""
    start, at = time.perf_counter(), time.time()
    try:
        yield fields
    finally:
        record = {"command": command.get(), "span": name, "at": round(at, 3),
                  "seconds": round(time.perf_counter() - start, 3), **fields}
        TRACE.parent.mkdir(parents=True, exist_ok=True)
        with TRACE.open("a") as log:
            log.write(json.dumps(record, ensure_ascii=False) + "\n")


def summary(path: Path = TRACE, recent: int = RECENT_COMMANDS) -> str:
    records = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    commands = list(dict.fromkeys(record["command"] for record in records if record["command"]))[-recent:]
    by_span: dict[str, list[float]] = defaultdict(list)
    for record in records:
        if record["command"] in commands:
            by_span[record["span"]].append(record["seconds"])
    rows = [f"{name:<16} {statistics.median(times):6.2f} s median  {max(times):6.2f} s max  ×{len(times)}"
            for name, times in sorted(by_span.items())]
    return "\n".join([f"last {len(commands)} commands", *rows])


if __name__ == "__main__":
    print(summary(Path(sys.argv[1]) if len(sys.argv) > 1 else TRACE))
