from __future__ import annotations

import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class CleanupTarget:
    name: str
    path: Path
    description: str
    enabled: bool = True
    group: str = "Geral"


@dataclass(frozen=True)
class CleanupItem:
    path: Path
    root_path: Path
    source: str
    size: int
    modified_at: datetime


@dataclass(frozen=True)
class ScanResult:
    items: list[CleanupItem]
    errors: list[str]

    @property
    def total_size(self) -> int:
        return sum(item.size for item in self.items)


@dataclass(frozen=True)
class DeleteResult:
    deleted_files: int
    deleted_bytes: int
    removed_dirs: int
    errors: list[str]


def human_size(size: int) -> str:
    amount = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"


def discover_targets() -> list[CleanupTarget]:
    targets: list[CleanupTarget] = []
    seen: set[str] = set()

    def add(
        name: str,
        value: str | os.PathLike[str] | None,
        description: str,
        enabled: bool = True,
        group: str = "Geral",
    ) -> None:
        if not value:
            return

        path = Path(value).expanduser()
        try:
            if not path.exists() or not path.is_dir():
                return
            key = _path_key(path)
        except OSError:
            return

        if key in seen:
            return

        seen.add(key)
        targets.append(CleanupTarget(name=name, path=path, description=description, enabled=enabled, group=group))

    _add_user_temp_targets(add)
    _add_windows_temp_targets(add)
    _add_browser_cache_targets(add)
    _add_gpu_cache_targets(add)
    _add_prefetch_targets(add)
    _add_old_log_targets(add)
    _add_recycle_bin_targets(add)
    _add_windows_update_targets(add)

    if not targets:
        add(
            "Temporários do sistema",
            tempfile.gettempdir(),
            "Pasta temporária padrão desta plataforma.",
            True,
            "Temp do Usuário",
        )

    return targets


def _add_user_temp_targets(add) -> None:
    add(
        "Temp do Usuário",
        tempfile.gettempdir(),
        "Arquivos temporários da conta atual.",
        True,
        "Temp do Usuário",
    )
    add(
        "TEMP do ambiente",
        os.environ.get("TEMP"),
        "Pasta apontada pela variável TEMP.",
        True,
        "Temp do Usuário",
    )
    add(
        "TMP do ambiente",
        os.environ.get("TMP"),
        "Pasta apontada pela variável TMP.",
        True,
        "Temp do Usuário",
    )

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        add(
            "Temp do AppData",
            Path(local_app_data) / "Temp",
            "Temporários de aplicativos do usuário.",
            True,
            "Temp do Usuário",
        )


def _add_windows_temp_targets(add) -> None:
    windir = os.environ.get("WINDIR")
    if windir:
        add(
            "Arquivos Temporários do Windows",
            Path(windir) / "Temp",
            "Temporários do sistema. Pode exigir permissão de administrador.",
            False,
            "Arquivos Temporários do Windows",
        )


def _add_browser_cache_targets(add) -> None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    app_data = os.environ.get("APPDATA")

    if local_app_data:
        chromium_browsers = (
            ("Chrome", Path(local_app_data) / "Google" / "Chrome" / "User Data"),
            ("Edge", Path(local_app_data) / "Microsoft" / "Edge" / "User Data"),
            ("Brave", Path(local_app_data) / "BraveSoftware" / "Brave-Browser" / "User Data"),
            ("Vivaldi", Path(local_app_data) / "Vivaldi" / "User Data"),
        )
        for browser_name, base_path in chromium_browsers:
            _add_chromium_browser_cache(add, browser_name, base_path)

        opera_roots = (
            Path(local_app_data) / "Opera Software" / "Opera Stable",
            Path(local_app_data) / "Opera Software" / "Opera GX Stable",
        )
        for root in opera_roots:
            _add_browser_folder(add, "Opera - Cache", root / "Cache", "Cache HTTP do Opera.")
            _add_browser_folder(add, "Opera - Code Cache", root / "Code Cache", "Cache de código do Opera.")
            _add_browser_folder(
                add,
                "Opera - Service Worker",
                root / "Service Worker" / "CacheStorage",
                "Cache offline do Opera.",
            )

        firefox_local_profiles = Path(local_app_data) / "Mozilla" / "Firefox" / "Profiles"
        _add_firefox_cache_targets(add, firefox_local_profiles)

    if app_data:
        firefox_roaming_profiles = Path(app_data) / "Mozilla" / "Firefox" / "Profiles"
        _add_firefox_cache_targets(add, firefox_roaming_profiles)


def _add_chromium_browser_cache(add, browser_name: str, base_path: Path) -> None:
    for profile_path in _iter_chromium_profiles(base_path):
        profile_name = profile_path.name
        suffix = f"{browser_name} - {profile_name}"

        _add_browser_folder(add, f"{suffix} - Cache", profile_path / "Cache", f"Cache HTTP do {suffix}.")
        _add_browser_folder(add, f"{suffix} - Code Cache", profile_path / "Code Cache", f"Cache de código do {suffix}.")
        _add_browser_folder(
            add,
            f"{suffix} - Service Worker",
            profile_path / "Service Worker" / "CacheStorage",
            f"Cache offline de service workers do {suffix}.",
        )


def _add_browser_folder(add, name: str, path: Path, description: str) -> None:
    add(
        f"Cache de Navegador - {name}",
        path,
        description,
        True,
        "Cache de Navegador",
    )


def _add_firefox_cache_targets(add, profiles_path: Path) -> None:
    if not profiles_path.exists() or not profiles_path.is_dir():
        return

    for profile_path in _safe_iter_dirs(profiles_path):
        profile_name = profile_path.name
        add(
            f"Cache de Navegador - Firefox ({profile_name})",
            profile_path / "cache2",
            f"Cache HTTP do perfil Firefox {profile_name}.",
            True,
            "Cache de Navegador",
        )
        add(
            f"Cache de Navegador - Firefox startup ({profile_name})",
            profile_path / "startupCache",
            f"Cache de inicialização do perfil Firefox {profile_name}.",
            True,
            "Cache de Navegador",
        )


def _add_gpu_cache_targets(add) -> None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return

    local_root = Path(local_app_data)
    directx_candidates = (
        ("D3DSCache", local_root / "D3DSCache", "Cache de shaders DirectX do usuário."),
        (
            "DirectX Shader Cache",
            local_root / "Microsoft" / "DirectX Shader Cache",
            "Cache de shaders DirectX do Windows.",
        ),
        ("NVIDIA DXCache", local_root / "NVIDIA" / "DXCache", "Cache DirectX da NVIDIA."),
        ("NVIDIA GLCache", local_root / "NVIDIA" / "GLCache", "Cache OpenGL da NVIDIA."),
        ("NVIDIA NV_Cache", local_root / "NVIDIA Corporation" / "NV_Cache", "Cache legado da NVIDIA."),
        ("AMD DxCache", local_root / "AMD" / "DxCache", "Cache DirectX da AMD."),
        ("AMD GLCache", local_root / "AMD" / "GLCache", "Cache OpenGL da AMD."),
        ("AMD VkCache", local_root / "AMD" / "VkCache", "Cache Vulkan da AMD."),
        ("Intel ShaderCache", local_root / "Intel" / "ShaderCache", "Cache de shaders da Intel."),
    )

    for name, path, description in directx_candidates:
        add(
            f"Cache da GPU/DirectX - {name}",
            path,
            description,
            True,
            "Cache da GPU/DirectX",
        )

    chromium_roots = (
        ("Chrome", local_root / "Google" / "Chrome" / "User Data"),
        ("Edge", local_root / "Microsoft" / "Edge" / "User Data"),
        ("Brave", local_root / "BraveSoftware" / "Brave-Browser" / "User Data"),
        ("Vivaldi", local_root / "Vivaldi" / "User Data"),
    )
    for browser_name, base_path in chromium_roots:
        for profile_path in _iter_chromium_profiles(base_path):
            profile_name = profile_path.name
            add(
                f"Cache da GPU - {browser_name} ({profile_name})",
                profile_path / "GPUCache",
                f"Cache gráfico do perfil {profile_name}.",
                True,
                "Cache da GPU/DirectX",
            )

        for folder_name in ("ShaderCache", "GrShaderCache", "GraphiteDawnCache"):
            add(
                f"Cache da GPU - {browser_name} - {folder_name}",
                base_path / folder_name,
                f"Cache gráfico compartilhado do {browser_name}.",
                True,
                "Cache da GPU/DirectX",
            )


def _add_prefetch_targets(add) -> None:
    windir = os.environ.get("WINDIR")
    if not windir:
        return

    add(
        "Prefetch",
        Path(windir) / "Prefetch",
        "Dados de pré-carregamento de aplicativos. O Windows recria quando necessário.",
        False,
        "Prefetch",
    )


def _add_old_log_targets(add) -> None:
    windir = os.environ.get("WINDIR")
    local_app_data = os.environ.get("LOCALAPPDATA")
    program_data = os.environ.get("PROGRAMDATA")

    if local_app_data:
        add(
            "Logs Antigos - CrashDumps",
            Path(local_app_data) / "CrashDumps",
            "Relatórios de falha antigos do usuário.",
            True,
            "Logs Antigos",
        )
        add(
            "Logs Antigos - WER do usuário",
            Path(local_app_data) / "Microsoft" / "Windows" / "WER",
            "Relatórios de erro do Windows para o usuário atual.",
            True,
            "Logs Antigos",
        )

    if program_data:
        add(
            "Logs Antigos - WER do sistema",
            Path(program_data) / "Microsoft" / "Windows" / "WER",
            "Relatórios de erro do Windows compartilhados pelo sistema.",
            False,
            "Logs Antigos",
        )

    if windir:
        add(
            "Logs Antigos - Windows Logs",
            Path(windir) / "Logs",
            "Logs textuais e relatórios do Windows. Pode exigir permissão de administrador.",
            False,
            "Logs Antigos",
        )
        add(
            "Logs Antigos - LogFiles",
            Path(windir) / "System32" / "LogFiles",
            "Logs de componentes e serviços do Windows.",
            False,
            "Logs Antigos",
        )


def _add_recycle_bin_targets(add) -> None:
    if os.name != "nt":
        return

    sid = _current_windows_sid()
    if not sid:
        return

    for drive_root in _iter_windows_drive_roots():
        add(
            "Lixeira",
            drive_root / "$Recycle.Bin" / sid,
            "Conteúdo da Lixeira do usuário neste disco. A exclusão é permanente.",
            False,
            "Lixeira",
        )


def _add_windows_update_targets(add) -> None:
    windir = os.environ.get("WINDIR")
    if not windir:
        return

    add(
        "Cache do Windows Update",
        Path(windir) / "SoftwareDistribution" / "Download",
        "Arquivos baixados pelo Windows Update. Pode exigir permissão de administrador.",
        False,
        "Cache do Windows Update",
    )


def _iter_chromium_profiles(base_path: Path) -> list[Path]:
    if not base_path.exists() or not base_path.is_dir():
        return []

    profiles: list[Path] = []
    profile_names = {"Default", "Guest Profile", "System Profile"}

    for path in _safe_iter_dirs(base_path):
        if path.name in profile_names or path.name.startswith("Profile "):
            profiles.append(path)

    return profiles


def _safe_iter_dirs(path: Path) -> list[Path]:
    try:
        return [child for child in path.iterdir() if child.is_dir() and not _is_link_or_junction(child)]
    except OSError:
        return []


def _current_windows_sid() -> str | None:
    try:
        output = subprocess.check_output(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None

    parts = [part.strip().strip('"') for part in output.split(",")]
    if len(parts) < 2:
        return None

    sid = parts[1]
    if sid.startswith("S-1-"):
        return sid
    return None


def _iter_windows_drive_roots() -> list[Path]:
    roots: list[Path] = []
    system_drive = os.environ.get("SystemDrive")
    if system_drive:
        roots.append(Path(f"{system_drive.rstrip(':')}:\\"))

    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        roots.append(Path(Path(user_profile).anchor))

    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path(f"{letter}:\\")
        recycle_root = root / "$Recycle.Bin"
        try:
            if recycle_root.exists() and recycle_root.is_dir():
                roots.append(root)
        except OSError:
            continue

    unique_roots: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            key = _path_key(root)
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            unique_roots.append(root)

    return unique_roots


def scan_targets(
    targets: list[CleanupTarget],
    min_age_seconds: int = 24 * 60 * 60,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> ScanResult:
    items: list[CleanupItem] = []
    errors: list[str] = []
    cutoff = time.time() - max(min_age_seconds, 0)
    total_targets = len(targets)

    for index, target in enumerate(targets, start=1):
        if progress_callback:
            progress_callback(index - 1, total_targets, f"Analisando {target.name}")

        root = _safe_resolve(target.path)
        if not root.exists() or not root.is_dir():
            errors.append(f"{target.name}: pasta não encontrada ({target.path})")
            if progress_callback:
                progress_callback(index, total_targets, f"{target.name} indisponível")
            continue

        for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            current_dir = Path(dirpath)
            safe_dirnames: list[str] = []

            for dirname in dirnames:
                child_dir = current_dir / dirname
                if _is_link_or_junction(child_dir):
                    continue
                if _is_relative_to(_safe_resolve(child_dir), root):
                    safe_dirnames.append(dirname)
            dirnames[:] = safe_dirnames

            for filename in filenames:
                file_path = current_dir / filename
                try:
                    if _is_link_or_junction(file_path):
                        continue
                    resolved_file = _safe_resolve(file_path)
                    if not _is_relative_to(resolved_file, root):
                        continue

                    stat = file_path.stat()
                    if min_age_seconds > 0 and stat.st_mtime > cutoff:
                        continue

                    items.append(
                        CleanupItem(
                            path=file_path,
                            root_path=root,
                            source=target.name,
                            size=stat.st_size,
                            modified_at=datetime.fromtimestamp(stat.st_mtime),
                        )
                    )
                except (FileNotFoundError, PermissionError):
                    continue
                except OSError as exc:
                    errors.append(f"{file_path}: {exc}")

        if progress_callback:
            progress_callback(index, total_targets, f"{target.name} analisado")

    items.sort(key=lambda item: item.size, reverse=True)
    return ScanResult(items=items, errors=errors)


def delete_items(
    items: list[CleanupItem],
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> DeleteResult:
    deleted_files = 0
    deleted_bytes = 0
    removed_dirs = 0
    errors: list[str] = []
    roots = {_path_key(item.root_path): item.root_path for item in items}
    total_items = len(items)

    for index, item in enumerate(items, start=1):
        try:
            root = _safe_resolve(item.root_path)
            file_path = item.path
            resolved_file = _safe_resolve(file_path)

            if not _is_relative_to(resolved_file, root):
                errors.append(f"Ignorado fora da pasta permitida: {file_path}")
                continue
            if _is_link_or_junction(file_path):
                continue
            if not file_path.exists() or not file_path.is_file():
                continue

            size = file_path.stat().st_size
            file_path.unlink()
            deleted_files += 1
            deleted_bytes += size
        except PermissionError:
            errors.append(f"Sem permissão: {item.path}")
        except OSError as exc:
            errors.append(f"{item.path}: {exc}")

        if progress_callback:
            progress_callback(index, total_items, f"Limpando {item.source}")

    if progress_callback and roots:
        progress_callback(total_items, total_items, "Removendo pastas vazias")

    for root in roots.values():
        removed_dirs += remove_empty_dirs(root)

    return DeleteResult(
        deleted_files=deleted_files,
        deleted_bytes=deleted_bytes,
        removed_dirs=removed_dirs,
        errors=errors,
    )


def remove_empty_dirs(root: Path) -> int:
    removed = 0
    safe_root = _safe_resolve(root)

    if not safe_root.exists() or not safe_root.is_dir():
        return removed

    for dirpath, dirnames, _filenames in os.walk(safe_root, topdown=False, followlinks=False):
        current_dir = Path(dirpath)
        if _path_key(current_dir) == _path_key(safe_root):
            continue
        if _is_link_or_junction(current_dir):
            continue
        if not _is_relative_to(_safe_resolve(current_dir), safe_root):
            continue

        try:
            current_dir.rmdir()
            removed += 1
        except OSError:
            pass

    return removed


def _path_key(path: Path) -> str:
    return str(_safe_resolve(path)).casefold()


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True
