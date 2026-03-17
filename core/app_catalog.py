"""
Application catalog helpers.

Provides:
- installed-app discovery for the current platform
- stable app identifiers for profile matching
- friendly labels for UI display
- alias resolution so old config values keep matching
"""

from __future__ import annotations

import os
import plistlib
import sys
import threading
from pathlib import Path


WINDOWS_APP_HINTS = {
    "msedge.exe": {"label": "Microsoft Edge", "legacy_icon": ""},
    "chrome.exe": {"label": "Google Chrome", "legacy_icon": "chrom.png"},
    "Microsoft.Media.Player.exe": {
        "label": "Windows Media Player",
        "legacy_icon": "media.webp",
    },
    "wmplayer.exe": {
        "label": "Windows Media Player (Classic)",
        "legacy_icon": "media.webp",
    },
    "vlc.exe": {"label": "VLC Media Player", "legacy_icon": "VLC.png"},
    "Code.exe": {"label": "Visual Studio Code", "legacy_icon": "VSCODE.png"},
}

MAC_KNOWN_APP_HINTS = {
    "com.apple.Safari": {"label": "Safari", "legacy_icon": ""},
    "com.google.Chrome": {"label": "Google Chrome", "legacy_icon": "chrom.png"},
    "org.videolan.vlc": {"label": "VLC Media Player", "legacy_icon": "VLC.png"},
    "com.microsoft.VSCode": {"label": "Visual Studio Code", "legacy_icon": "VSCODE.png"},
    "com.apple.finder": {"label": "Finder", "legacy_icon": ""},
}

MAC_LEGACY_ALIASES = {
    # Legacy executable-name aliases from the previous macOS implementation.
    "Safari": {"label": "Safari", "legacy_icon": ""},
    "Google Chrome": {"label": "Google Chrome", "legacy_icon": "chrom.png"},
    "VLC": {"label": "VLC Media Player", "legacy_icon": "VLC.png"},
    "Code": {"label": "Visual Studio Code", "legacy_icon": "VSCODE.png"},
    "Finder": {"label": "Finder", "legacy_icon": ""},
}

APP_HINTS = {
    **WINDOWS_APP_HINTS,
    **MAC_KNOWN_APP_HINTS,
    **MAC_LEGACY_ALIASES,
}

_CATALOG_LOCK = threading.Lock()
_CATALOG_CACHE: list[dict] | None = None


def _dedupe_keep_order(values):
    result = []
    seen = set()
    for value in values:
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _entry_sort_key(entry: dict):
    return (entry.get("label", "").casefold(), entry.get("id", "").casefold())


def _make_entry(app_id: str, label: str, *, path: str = "", aliases=None, legacy_icon: str = ""):
    normalized_path = os.path.abspath(path) if path else ""
    alias_values = list(aliases or [])
    alias_values.extend([app_id, label])
    if normalized_path:
        alias_values.extend(
            [
                normalized_path,
                os.path.basename(normalized_path),
                Path(normalized_path).stem,
            ]
        )
    return {
        "id": app_id,
        "label": label,
        "path": normalized_path,
        "aliases": _dedupe_keep_order(alias_values),
        "legacy_icon": legacy_icon,
    }


def _merge_entry(entry: dict, existing: dict | None):
    if existing is None:
        return entry

    merged = dict(existing)
    merged["label"] = existing.get("label") or entry.get("label") or entry["id"]
    merged["path"] = existing.get("path") or entry.get("path") or ""
    merged["legacy_icon"] = (
        existing.get("legacy_icon") or entry.get("legacy_icon") or ""
    )
    merged["aliases"] = _dedupe_keep_order(
        list(existing.get("aliases", [])) + list(entry.get("aliases", []))
    )
    return merged


def _mac_app_dirs():
    return [
        "/Applications",
        "/System/Applications",
        "/System/Applications/Utilities",
        "/System/Library/CoreServices",
        "/Applications/Utilities",
        os.path.expanduser("~/Applications"),
    ]


def _iter_mac_app_bundles():
    seen = set()
    for root in _mac_app_dirs():
        if not os.path.isdir(root):
            continue

        for current_root, dirnames, _filenames in os.walk(root):
            dirnames.sort(key=str.casefold)
            app_dirs = [name for name in dirnames if name.endswith(".app")]
            for app_name in app_dirs:
                app_path = os.path.join(current_root, app_name)
                normalized = os.path.abspath(app_path)
                if normalized in seen:
                    continue
                seen.add(normalized)
                yield normalized

            # Do not walk into app bundles.
            dirnames[:] = [name for name in dirnames if not name.endswith(".app")]


def _read_mac_bundle_info(app_path: str) -> dict:
    info_path = os.path.join(app_path, "Contents", "Info.plist")
    if not os.path.exists(info_path):
        return {}
    try:
        with open(info_path, "rb") as handle:
            return plistlib.load(handle)
    except Exception:
        return {}


def _discover_macos_apps():
    entries = {}

    for app_path in _iter_mac_app_bundles():
        info = _read_mac_bundle_info(app_path)
        bundle_id = info.get("CFBundleIdentifier")
        executable = info.get("CFBundleExecutable")
        label = (
            info.get("CFBundleDisplayName")
            or info.get("CFBundleName")
            or Path(app_path).stem
        )
        app_id = bundle_id or executable or Path(app_path).stem
        if not app_id:
            continue

        aliases = [Path(app_path).stem, f"{Path(app_path).stem}.app"]
        if executable:
            aliases.append(executable)

        hint = APP_HINTS.get(app_id) or APP_HINTS.get(executable or "") or {}
        entry = _make_entry(
            app_id,
            hint.get("label") or label,
            path=app_path,
            aliases=aliases,
            legacy_icon=hint.get("legacy_icon", ""),
        )
        key = app_id.casefold()
        entries[key] = _merge_entry(entry, entries.get(key))

    for app_id, hint in MAC_KNOWN_APP_HINTS.items():
        key = app_id.casefold()
        entries[key] = _merge_entry(
            _make_entry(
                app_id,
                hint["label"],
                aliases=[],
                legacy_icon=hint.get("legacy_icon", ""),
            ),
            entries.get(key),
        )

    return sorted(entries.values(), key=_entry_sort_key)


def _resolve_windows_known_path(exe_name: str) -> str:
    candidates = []
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", "")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", "")

    if exe_name.lower() == "msedge.exe":
        candidates.append(
            os.path.join(program_files_x86, "Microsoft", "Edge", "Application", exe_name)
        )
    elif exe_name.lower() == "chrome.exe":
        candidates.extend(
            [
                os.path.join(program_files, "Google", "Chrome", "Application", exe_name),
                os.path.join(program_files_x86, "Google", "Chrome", "Application", exe_name),
                os.path.join(local_appdata, "Google", "Chrome", "Application", exe_name),
            ]
        )
    elif exe_name.lower() == "vlc.exe":
        candidates.extend(
            [
                os.path.join(program_files, "VideoLAN", "VLC", exe_name),
                os.path.join(program_files_x86, "VideoLAN", "VLC", exe_name),
            ]
        )
    elif exe_name.lower() == "code.exe":
        candidates.extend(
            [
                os.path.join(local_appdata, "Programs", "Microsoft VS Code", exe_name),
                os.path.join(program_files, "Microsoft VS Code", exe_name),
                os.path.join(program_files_x86, "Microsoft VS Code", exe_name),
            ]
        )
    elif exe_name.lower() == "wmplayer.exe":
        candidates.extend(
            [
                os.path.join(program_files, "Windows Media Player", exe_name),
                os.path.join(program_files_x86, "Windows Media Player", exe_name),
            ]
        )

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return os.path.abspath(candidate)
    return ""


def _discover_windows_apps():
    entries = []
    for app_id, hint in WINDOWS_APP_HINTS.items():
        path = _resolve_windows_known_path(app_id)
        entries.append(
            _make_entry(
                app_id,
                hint["label"],
                path=path,
                aliases=[Path(path).stem] if path else [],
                legacy_icon=hint.get("legacy_icon", ""),
            )
        )
    return sorted(entries, key=_entry_sort_key)


def _build_catalog():
    if sys.platform == "darwin":
        return _discover_macos_apps()
    if sys.platform == "win32":
        return _discover_windows_apps()
    return []


def get_app_catalog(refresh: bool = False):
    global _CATALOG_CACHE
    with _CATALOG_LOCK:
        if refresh or _CATALOG_CACHE is None:
            _CATALOG_CACHE = _build_catalog()
        return [dict(entry) for entry in _CATALOG_CACHE]


def _find_catalog_entry(spec: str):
    if not spec:
        return None

    key = spec.casefold()
    for entry in get_app_catalog():
        if entry["id"].casefold() == key:
            return entry
        for alias in entry.get("aliases", []):
            if alias.casefold() == key:
                return entry
    return None


def _resolve_path_entry(path: str):
    if not path:
        return None
    normalized = os.path.abspath(path)
    if not os.path.exists(normalized):
        return None

    if sys.platform == "darwin" and normalized.endswith(".app"):
        info = _read_mac_bundle_info(normalized)
        executable = info.get("CFBundleExecutable")
        app_id = info.get("CFBundleIdentifier") or executable or Path(normalized).stem
        label = (
            info.get("CFBundleDisplayName")
            or info.get("CFBundleName")
            or Path(normalized).stem
        )
        hint = APP_HINTS.get(app_id) or APP_HINTS.get(executable or "") or {}
        aliases = [Path(normalized).stem, f"{Path(normalized).stem}.app"]
        if executable:
            aliases.append(executable)
        return _make_entry(
            app_id,
            hint.get("label") or label,
            path=normalized,
            aliases=aliases,
            legacy_icon=hint.get("legacy_icon", ""),
        )

    if normalized.lower().endswith(".exe"):
        exe_name = os.path.basename(normalized)
        hint = APP_HINTS.get(exe_name, {})
        return _make_entry(
            exe_name,
            hint.get("label") or Path(normalized).stem,
            path=normalized,
            aliases=[Path(normalized).stem],
            legacy_icon=hint.get("legacy_icon", ""),
        )

    return None


def resolve_app_spec(spec: str):
    """
    Resolve an app identifier, alias, or path into a catalog entry.

    Returns a dict with keys: id, label, path, aliases, legacy_icon.
    """
    if not spec:
        return None

    if os.path.isabs(spec) or os.path.exists(spec):
        entry = _resolve_path_entry(spec)
        if entry:
            return entry

    entry = _find_catalog_entry(spec)
    if entry:
        return entry

    hint = APP_HINTS.get(spec)
    if hint:
        return _make_entry(
            spec,
            hint["label"],
            aliases=[],
            legacy_icon=hint.get("legacy_icon", ""),
        )

    return _make_entry(spec, spec, aliases=[])


def get_app_aliases(spec: str):
    entry = resolve_app_spec(spec)
    if not entry:
        return []
    return _dedupe_keep_order([entry["id"], *entry.get("aliases", [])])


def get_app_label(spec: str):
    entry = resolve_app_spec(spec)
    return entry.get("label", spec) if entry else spec


def get_legacy_icon(spec: str):
    entry = resolve_app_spec(spec)
    return entry.get("legacy_icon", "") if entry else ""
