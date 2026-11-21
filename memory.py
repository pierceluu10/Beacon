"""What the user asked Beacon to remember: notes they typed (memory.md) and files they added (the memory folder). The
menu bar's Memory window writes both; every new conversation reads them afresh."""
import base64
import mimetypes
from pathlib import Path

MAX_FILE_BYTES = 10_000_000  # larger files are named but not read; the API refuses documents much past this
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
PDF = "application/pdf"
HEADER = "What the user asked you to remember (their own notes and files; follow the notes like instructions):"
NOTES = "Notes:\n{notes}"
FILE = "File {name}:"
TEXT_FILE = "File {name}:\n{text}"
UNREADABLE = "File {name} (not readable here, but it is in {folder} if a task needs it)"


class Memory:
    def __init__(self, notes: Path, folder: Path) -> None:
        self.notes_path, self.folder = notes, folder

    def notes(self) -> str:
        return self.notes_path.read_text() if self.notes_path.exists() else ""

    def files(self) -> list[Path]:
        return sorted(path for path in self.folder.iterdir() if path.is_file() and not path.name.startswith(".")) \
            if self.folder.exists() else []

    def stamp(self) -> tuple:
        """Changes whenever the notes or the files do."""
        return tuple((path.name, path.stat().st_mtime_ns) for path in [self.notes_path, *self.files()] if path.exists())

    def blocks(self) -> list[dict]:
        """Content blocks for the start of a conversation: the notes, then each file the API can read. [] when empty."""
        notes, files = self.notes().strip(), self.files()
        if not notes and not files:
            return []
        blocks = [{"type": "text", "text": HEADER}]
        if notes:
            blocks.append({"type": "text", "text": NOTES.format(notes=notes)})
        for path in files:
            blocks += _file_blocks(path, self.folder)
        return blocks


def _file_blocks(path: Path, folder: Path) -> list[dict]:
    kind = mimetypes.guess_type(path.name)[0] or ""
    if path.stat().st_size <= MAX_FILE_BYTES:
        data = path.read_bytes()
        if kind == PDF or kind in IMAGE_TYPES:
            source = {"type": "base64", "media_type": kind, "data": base64.standard_b64encode(data).decode()}
            return [{"type": "text", "text": FILE.format(name=path.name)},
                    {"type": "document" if kind == PDF else "image", "source": source}]
        try:
            return [{"type": "text", "text": TEXT_FILE.format(name=path.name, text=data.decode())}]
        except UnicodeDecodeError:
            pass
    return [{"type": "text", "text": UNREADABLE.format(name=path.name, folder=folder)}]
