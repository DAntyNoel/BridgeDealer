"""Named, seat-independent hand conditions persisted on the local machine."""

import fcntl
import json
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from .conditions import Conditions

DEFAULT_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "user_data/hand-templates.json"


class TemplateConflict(ValueError):
    pass


def template_name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80:
        raise ValueError("模板名称须为 1–80 个字符。")
    value = value.strip()
    if not value.isprintable():
        raise ValueError("模板名称不能包含换行或控制字符。")
    return value


def hand_condition(value):
    return Conditions.from_dict({"N": value}).to_dict().get("N", {})


class HandTemplates:
    def __init__(self, path=DEFAULT_TEMPLATE_PATH):
        self.path = Path(path)
        self.lock = threading.Lock()

    @contextmanager
    def _file_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_name(self.path.name + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (ValueError, UnicodeError) as error:
            raise OSError("本地模板文件无法解析，请先备份并检查文件。") from error
        try:
            if not isinstance(raw, dict) or raw.get("version") != 1:
                raise ValueError("Unsupported template file")
            entries = raw["templates"]
            if not isinstance(entries, list):
                raise ValueError("Invalid template list")
            ids, names = set(), set()
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {"id", "name", "condition"}:
                    raise ValueError("Invalid template")
                identity = entry["id"]
                if not isinstance(identity, str) or uuid.UUID(hex=identity).hex != identity:
                    raise ValueError("Invalid template identity")
                name = template_name(entry["name"])
                hand_condition(entry["condition"])
                if identity in ids or name in names:
                    raise ValueError("Repeated template")
                ids.add(identity)
                names.add(name)
            return entries
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise OSError("本地模板文件格式不正确，原文件已保留。") from error

    def _write(self, entries):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as output:
                temporary = Path(output.name)
                json.dump(
                    {"version": 1, "templates": entries}, output, ensure_ascii=False, indent=2
                )
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def list(self):
        with self.lock:
            return self._read()

    def save(self, raw, identity=None):
        if not isinstance(raw, dict) or set(raw) != {"name", "condition"}:
            raise ValueError("请提供模板名称 name 和单家条件 condition。")
        name, condition = template_name(raw["name"]), hand_condition(raw["condition"])
        with self.lock, self._file_lock():
            entries = self._read()
            if identity is not None and not any(e["id"] == identity for e in entries):
                raise KeyError(identity)
            if any(e["name"] == name and e["id"] != identity for e in entries):
                raise TemplateConflict("已有同名模板，请使用其他名称，或选择原模板后更新。")
            entry = {"id": identity or uuid.uuid4().hex, "name": name, "condition": condition}
            if identity is None:
                entries.append(entry)
            else:
                entries = [entry if e["id"] == identity else e for e in entries]
            self._write(entries)
            return entry

    def delete(self, identity):
        with self.lock, self._file_lock():
            entries = self._read()
            remaining = [e for e in entries if e["id"] != identity]
            if len(remaining) == len(entries):
                raise KeyError(identity)
            self._write(remaining)
