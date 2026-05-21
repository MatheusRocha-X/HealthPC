from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import ctypes
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore[assignment]

import webview

from cleaner_core import CleanupTarget, ScanResult, delete_items, discover_targets, human_size, scan_targets


APP_TITLE = "HealthPC"
MAX_PREVIEW_ITEMS = 500
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
UI_DIR = BASE_DIR / "ui"
INDEX_FILE = UI_DIR / "index.html"
ICON_FILE = UI_DIR / "icon.png"
SCHEDULE_TASK_NAME = "HealthPC Auto Cleanup"
DEFAULT_SCHEDULE = {
    "enabled": False,
    "frequency": "weekly",
    "time": "09:00",
    "weekday": "MON",
    "monthday": 1,
}


def get_app_storage_dir() -> Path:
    if os.name == "nt":
        base_dir = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base_dir:
            storage_dir = Path(base_dir) / APP_TITLE
        else:
            storage_dir = Path.home() / "AppData" / "Local" / APP_TITLE
    else:
        storage_dir = Path.home() / f".{APP_TITLE.lower()}"

    storage_dir.mkdir(parents=True, exist_ok=True)
    mark_hidden_on_windows(storage_dir)
    return storage_dir


def mark_hidden_on_windows(path: Path) -> None:
    if os.name != "nt":
        return
    try:
        hidden_flag = 0x2
        current = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if current == -1:
            return
        ctypes.windll.kernel32.SetFileAttributesW(str(path), current | hidden_flag)
    except Exception:
        pass


APP_STORAGE_DIR = get_app_storage_dir()
HISTORY_FILE = APP_STORAGE_DIR / "cleanup_history.json"
SETTINGS_FILE = APP_STORAGE_DIR / "healthpc_settings.json"


class HealthPCApi:
    def __init__(self) -> None:
        self._window: webview.Window | None = None
        self._targets = discover_targets()
        self._target_by_key = {self._target_key(target): target for target in self._targets}
        self._target_group_by_name = {target.name: target.group for target in self._targets}
        self._settings = load_json_file(SETTINGS_FILE, {})
        default_selected = {self._target_key(target) for target in self._targets if target.enabled}
        saved_selected = self._settings.get("selectedKeys")
        if isinstance(saved_selected, list):
            valid_keys = set(self._target_by_key)
            restored = {str(key) for key in saved_selected if str(key) in valid_keys}
            self._selected_keys = restored or default_selected
        else:
            self._selected_keys = default_selected
        self._scan_result = ScanResult(items=[], errors=[])
        self._worker_thread: threading.Thread | None = None
        self._progress_started_at: float | None = None
        self._progress = 0
        self._progress_text = "Aguardando"
        self._mode = "Pronto"
        self._status = "Seu PC está protegido"
        self._history = self._load_history()
        self._last_cleanup = self._history[0]["when"] if self._history else "Ainda não realizada"
        self._cleanup_summary = self._history[0] if self._history else None
        self._protect_recent = bool(self._settings.get("protectRecent", True))
        self._age_hours = self._coerce_age_hours(self._settings.get("ageHours", 24.0))
        self._is_maximized = False
        self._lock = threading.Lock()

    def _bind_window(self, window: webview.Window) -> None:
        self._window = window

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            selected_targets = self._selected_targets_unlocked()
            total_size = self._scan_result.total_size
            return {
                "mode": self._mode,
                "status": self._status,
                "busy": self._worker_thread is not None,
                "canClean": bool(self._scan_result.items) and self._worker_thread is None,
                "selectedCount": len(selected_targets),
                "targetCount": len(self._targets),
                "files": len(self._scan_result.items),
                "size": human_size(total_size),
                "sizeBytes": total_size,
                "warnings": len(self._scan_result.errors),
                "progress": self._progress,
                "progressText": self._progress_text,
                "lastCleanup": self._last_cleanup,
                "protectRecent": self._protect_recent,
                "ageHours": self._age_hours,
                "categories": self._categories_unlocked(),
                "monitor": self._monitor_unlocked(),
                "cleanupSummary": self._cleanup_summary_unlocked(),
            }

    def get_targets(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "key": self._target_key(target),
                    "name": target.name,
                    "group": target.group,
                    "path": str(target.path),
                    "description": target.description,
                    "enabled": self._target_key(target) in self._selected_keys,
                    "sensitive": not target.enabled,
                }
                for target in self._targets
            ]

    def set_group(self, group: str, enabled: bool) -> dict[str, Any]:
        with self._lock:
            for target in self._targets:
                if target.group == group:
                    key = self._target_key(target)
                    if enabled:
                        self._selected_keys.add(key)
                    else:
                        self._selected_keys.discard(key)
            self._save_settings_unlocked()
        return {"ok": True}

    def set_target(self, key: str, enabled: bool) -> dict[str, Any]:
        with self._lock:
            if key not in self._target_by_key:
                return {"ok": False, "error": "Local não encontrado."}
            if enabled:
                self._selected_keys.add(key)
            else:
                self._selected_keys.discard(key)
            self._save_settings_unlocked()
        return {"ok": True}

    def set_all(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            if enabled:
                self._selected_keys = {self._target_key(target) for target in self._targets}
            else:
                self._selected_keys.clear()
            self._save_settings_unlocked()
        return {"ok": True}

    def set_selected_targets(self, keys: list[str]) -> dict[str, Any]:
        with self._lock:
            valid_keys = set(self._target_by_key)
            self._selected_keys = {key for key in keys if key in valid_keys}
            self._save_settings_unlocked()
        return {"ok": True}

    def set_options(self, protect_recent: bool, age_hours: float) -> dict[str, Any]:
        try:
            age = float(age_hours)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Informe uma idade mínima válida."}
        if age < 0:
            return {"ok": False, "error": "A idade mínima não pode ser negativa."}
        with self._lock:
            self._protect_recent = bool(protect_recent)
            self._age_hours = age
            self._save_settings_unlocked()
        return {"ok": True}

    def start_scan(self) -> dict[str, Any]:
        with self._lock:
            if self._worker_thread is not None:
                return {"ok": False, "error": "Uma operação já está em andamento."}
            targets = self._selected_targets_unlocked()
            if not targets:
                return {"ok": False, "error": "Selecione pelo menos um local de limpeza."}

            min_age_seconds = 0 if not self._protect_recent else int(self._age_hours * 60 * 60)
            self._scan_result = ScanResult(items=[], errors=[])
            self._mode = "Analisando"
            self._status = "Procurando arquivos temporários, caches e logs."
            self._progress = 0
            self._progress_text = "Preparando análise"
            self._progress_started_at = time.monotonic()

        def worker() -> None:
            try:
                result = scan_targets(targets, min_age_seconds, progress_callback=self._progress_callback)
                with self._lock:
                    self._scan_result = result
                    self._progress = 100
                    self._progress_text = "Análise concluída"
                    if result.items:
                        self._mode = "Pronto para limpar"
                        self._status = "Revise o resultado e limpe quando quiser."
                    else:
                        self._mode = "Tudo certo"
                        self._status = "Nenhum arquivo elegível encontrado."
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._mode = "Atenção"
                    self._status = str(exc)
                    self._progress_text = "Erro na análise"
            finally:
                with self._lock:
                    self._worker_thread = None
                    self._progress_started_at = None

        thread = threading.Thread(target=worker, daemon=True)
        with self._lock:
            self._worker_thread = thread
        thread.start()
        return {"ok": True}

    def start_cleanup(self) -> dict[str, Any]:
        with self._lock:
            if self._worker_thread is not None:
                return {"ok": False, "error": "Uma operação já está em andamento."}
            if not self._scan_result.items:
                return {"ok": False, "error": "Execute uma análise antes de limpar."}

            items = list(self._scan_result.items)
            self._mode = "Limpando"
            self._status = "Removendo arquivos encontrados."
            self._progress = 0
            self._progress_text = "Preparando limpeza"
            self._progress_started_at = time.monotonic()

        def worker() -> None:
            try:
                result = delete_items(items, progress_callback=self._progress_callback)
                with self._lock:
                    now_label = datetime.now().strftime("%d/%m/%Y %H:%M")
                    entry = {
                        "when": now_label,
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "files": result.deleted_files,
                        "bytes": result.deleted_bytes,
                        "size": human_size(result.deleted_bytes),
                        "folders": result.removed_dirs,
                        "warnings": len(result.errors),
                    }
                    self._scan_result = ScanResult(items=[], errors=result.errors)
                    self._progress = 100
                    self._progress_text = "Limpeza concluída"
                    self._mode = "Limpeza feita"
                    self._status = f"{human_size(result.deleted_bytes)} liberados em {result.deleted_files} arquivos."
                    self._last_cleanup = now_label
                    self._cleanup_summary = entry
                    self._history.insert(0, entry)
                    self._history = self._history[:80]
                    self._save_history_unlocked()
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._mode = "Atenção"
                    self._status = str(exc)
                    self._progress_text = "Erro na limpeza"
            finally:
                with self._lock:
                    self._worker_thread = None
                    self._progress_started_at = None

        thread = threading.Thread(target=worker, daemon=True)
        with self._lock:
            self._worker_thread = thread
        thread.start()
        return {"ok": True}

    def get_history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)

    def get_schedule(self) -> dict[str, Any]:
        settings = load_json_file(SETTINGS_FILE, {})
        schedule = normalize_schedule(settings.get("schedule"))
        return {
            "ok": True,
            "schedule": {
                **schedule,
                "taskExists": schedule_task_exists(),
            },
        }

    def save_schedule(
        self,
        enabled: bool,
        frequency: str,
        time_of_day: str,
        weekday: str,
        monthday: int,
    ) -> dict[str, Any]:
        try:
            schedule = normalize_schedule(
                {
                    "enabled": enabled,
                    "frequency": frequency,
                    "time": time_of_day,
                    "weekday": weekday,
                    "monthday": monthday,
                }
            )
            apply_schedule_task(schedule)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

        with self._lock:
            self._settings["schedule"] = schedule
            self._save_settings_unlocked()
        return {"ok": True}

    def minimize_window(self) -> dict[str, Any]:
        if self._window is not None:
            self._window.minimize()
        return {"ok": True}

    def toggle_maximize_window(self) -> dict[str, Any]:
        if self._window is not None:
            if self._is_maximized:
                self._window.restore()
            else:
                self._window.maximize()
            self._is_maximized = not self._is_maximized
        return {"ok": True}

    def close_window(self) -> dict[str, Any]:
        if self._window is not None:
            self._window.destroy()
        return {"ok": True}

    def open_task_scheduler(self) -> dict[str, Any]:
        if os.name != "nt":
            return {"ok": False, "error": "Disponível apenas no Windows."}
        import ctypes
        ctypes.windll.shell32.ShellExecuteW(None, "runas", "mmc.exe", "taskschd.msc", None, 1)
        return {"ok": True}

    def _target_key(self, target: CleanupTarget) -> str:
        return f"{target.group}|{target.name}|{target.path}"

    def _selected_targets_unlocked(self) -> list[CleanupTarget]:
        return [target for target in self._targets if self._target_key(target) in self._selected_keys]

    def _categories_unlocked(self) -> list[dict[str, Any]]:
        groups: dict[str, list[CleanupTarget]] = {}
        for target in self._targets:
            groups.setdefault(target.group, []).append(target)

        size_by_group: Counter[str] = Counter()
        count_by_group: Counter[str] = Counter()
        for item in self._scan_result.items:
            group = self._target_group_by_name.get(item.source, "Geral")
            size_by_group[group] += item.size
            count_by_group[group] += 1

        categories = []
        for group, targets in groups.items():
            selected = sum(1 for target in targets if self._target_key(target) in self._selected_keys)
            categories.append(
                {
                    "group": group,
                    "selected": selected,
                    "total": len(targets),
                    "enabled": selected > 0,
                    "size": human_size(size_by_group[group]),
                    "bytes": size_by_group[group],
                    "files": count_by_group[group],
                    "icon": self._group_icon(group),
                }
            )
        return categories

    def _cleanup_summary_unlocked(self) -> dict[str, Any]:
        if self._cleanup_summary is None:
            return {
                "files": 0,
                "size": "0 B",
                "warnings": 0,
                "folders": 0,
                "lastCleanup": "Ainda não realizada",
            }
        return {
            "files": self._cleanup_summary["files"],
            "size": self._cleanup_summary["size"],
            "warnings": self._cleanup_summary["warnings"],
            "folders": self._cleanup_summary["folders"],
            "lastCleanup": self._cleanup_summary["when"],
        }

    def _monitor_unlocked(self) -> dict[str, Any]:
        if psutil is None:
            return {
                "memory": {"percent": 0, "text": "Instale psutil"},
                "disk": {"percent": 0, "text": "Instale psutil"},
            }
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(Path.home().anchor or Path.home()))
        return {
            "memory": {
                "percent": round(memory.percent),
                "text": f"{human_size(memory.used)} / {human_size(memory.total)}",
            },
            "disk": {
                "percent": round(disk.percent),
                "text": f"Livre: {human_size(disk.free)}",
            },
        }

    def _load_history(self) -> list[dict[str, Any]]:
        data = load_json_file(HISTORY_FILE, [])
        if not isinstance(data, list):
            return []
        return [entry for entry in data if isinstance(entry, dict)][:80]

    def _save_history_unlocked(self) -> None:
        save_json_file(HISTORY_FILE, self._history)

    def _save_settings_unlocked(self) -> None:
        self._settings["selectedKeys"] = sorted(self._selected_keys)
        self._settings["protectRecent"] = self._protect_recent
        self._settings["ageHours"] = self._age_hours
        if "schedule" not in self._settings:
            self._settings["schedule"] = normalize_schedule(None)
        save_json_file(SETTINGS_FILE, self._settings)

    def _group_icon(self, group: str) -> str:
        text = group.casefold()
        if "navegador" in text:
            return "O"
        if "gpu" in text or "directx" in text:
            return "D"
        if "lixeira" in text:
            return "R"
        if "log" in text:
            return "L"
        if "windows" in text or "prefetch" in text:
            return "W"
        return "C"

    def _progress_callback(self, done: int, total: int, detail: str) -> None:
        fraction = 0 if total <= 0 else max(0, min(done / total, 1))
        with self._lock:
            self._progress = round(fraction * 100)
            self._progress_text = f"{detail} | restante: {self._estimated_remaining_unlocked(fraction)}"

    def _estimated_remaining_unlocked(self, fraction: float) -> str:
        if self._progress_started_at is None or fraction <= 0:
            return "calculando"
        if fraction >= 1:
            return "0s"
        elapsed = time.monotonic() - self._progress_started_at
        seconds = int(max((elapsed / fraction) - elapsed, 0))
        if seconds < 60:
            return f"{seconds}s"
        minutes, seconds = divmod(seconds, 60)
        return f"{minutes}min {seconds:02d}s"

    def _coerce_age_hours(self, value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 24.0
        return parsed if parsed >= 0 else 24.0


def load_json_file(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_schedule(data: Any) -> dict[str, Any]:
    merged = dict(DEFAULT_SCHEDULE)
    if isinstance(data, dict):
        merged.update(data)

    enabled = bool(merged.get("enabled", False))
    frequency = str(merged.get("frequency", "weekly")).lower()
    if frequency not in {"daily", "weekly", "monthly"}:
        frequency = "weekly"

    time_of_day = str(merged.get("time", "09:00"))
    if not valid_time_string(time_of_day):
        time_of_day = "09:00"

    weekday = str(merged.get("weekday", "MON")).upper()
    if weekday not in {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}:
        weekday = "MON"

    try:
        monthday = int(merged.get("monthday", 1))
    except (TypeError, ValueError):
        monthday = 1
    monthday = min(max(monthday, 1), 31)

    return {
        "enabled": enabled,
        "frequency": frequency,
        "time": time_of_day,
        "weekday": weekday,
        "monthday": monthday,
    }


def valid_time_string(value: str) -> bool:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (ValueError, AttributeError):
        return False
    return 0 <= hour <= 23 and 0 <= minute <= 59


def schedule_task_exists() -> bool:
    if os.name != "nt":
        return False
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", SCHEDULE_TASK_NAME],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result.returncode == 0


def apply_schedule_task(schedule: dict[str, Any]) -> None:
    if os.name != "nt":
        raise RuntimeError("Agendamentos automáticos estão disponíveis apenas no Windows.")

    if not schedule["enabled"]:
        subprocess.run(
            ["schtasks", "/Delete", "/TN", SCHEDULE_TASK_NAME, "/F"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return

    if getattr(sys, "frozen", False):
        task_command = f'"{Path(sys.executable).resolve()}" --scheduled-clean'
    else:
        python_executable = Path(sys.executable)
        if python_executable.name.lower() == "python.exe":
            pythonw_candidate = python_executable.with_name("pythonw.exe")
            runner = pythonw_candidate if pythonw_candidate.exists() else python_executable
        else:
            runner = python_executable
        task_command = f'"{runner}" "{Path(__file__).resolve()}" --scheduled-clean'
    create_command = [
        "schtasks",
        "/Create",
        "/F",
        "/TN",
        SCHEDULE_TASK_NAME,
        "/TR",
        task_command,
        "/SC",
        schedule["frequency"].upper(),
        "/ST",
        schedule["time"],
    ]

    if schedule["frequency"] == "weekly":
        create_command += ["/D", schedule["weekday"]]
    elif schedule["frequency"] == "monthly":
        create_command += ["/D", str(schedule["monthday"])]

    result = subprocess.run(
        create_command,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "Não foi possível criar o agendamento.").strip()
        raise RuntimeError(message)


def run_scheduled_cleanup() -> int:
    targets = discover_targets()
    target_by_key = {f"{target.group}|{target.name}|{target.path}": target for target in targets}
    settings = load_json_file(SETTINGS_FILE, {})
    selected_keys = settings.get("selectedKeys")

    if isinstance(selected_keys, list):
        selected_targets = [target_by_key[key] for key in selected_keys if key in target_by_key]
    else:
        selected_targets = [target for target in targets if target.enabled]

    if not selected_targets:
        return 0

    protect_recent = bool(settings.get("protectRecent", True))
    try:
        age_hours = max(float(settings.get("ageHours", 24.0)), 0.0)
    except (TypeError, ValueError):
        age_hours = 24.0
    min_age_seconds = 0 if not protect_recent else int(age_hours * 60 * 60)

    scan_result = scan_targets(selected_targets, min_age_seconds)
    if not scan_result.items:
        return 0

    delete_result = delete_items(scan_result.items)
    history = load_json_file(HISTORY_FILE, [])
    if not isinstance(history, list):
        history = []
    history.insert(
        0,
        {
            "when": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "files": delete_result.deleted_files,
            "bytes": delete_result.deleted_bytes,
            "size": human_size(delete_result.deleted_bytes),
            "folders": delete_result.removed_dirs,
            "warnings": len(delete_result.errors),
        },
    )
    save_json_file(HISTORY_FILE, history[:80])
    return 0


def main() -> int:
    if "--scheduled-clean" in sys.argv:
        return run_scheduled_cleanup()

    api = HealthPCApi()
    window = webview.create_window(
        APP_TITLE,
        url=INDEX_FILE.as_uri(),
        js_api=api,
        width=1120,
        height=760,
        min_size=(980, 680),
        frameless=True,
        easy_drag=True,
        background_color="#07111d",
    )
    api._bind_window(window)
    webview.start(debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
