"""
Configuration manager — loads/saves button mappings to a JSON file.
Supports per-application profiles (for future use).
"""

import json
import os
import sys
from pathlib import Path

from core.app_catalog import APP_HINTS, get_legacy_icon, resolve_app_spec

CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Mouser")
if sys.platform == "darwin":
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Mouser")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# Which mouse events map to which friendly button names
# Order matches the Logi Options+ diagram (top view then side view)
BUTTON_NAMES = {
    "middle":        "Middle button",
    "gesture":       "Gesture button",
    "xbutton1":      "Back button",
    "xbutton2":      "Forward button",
    "hscroll_left":  "Horizontal scroll left",
    "hscroll_right": "Horizontal scroll right",
}

GESTURE_DIRECTION_BUTTONS = (
    "gesture_left",
    "gesture_right",
    "gesture_up",
    "gesture_down",
)

PROFILE_BUTTON_NAMES = {
    **BUTTON_NAMES,
    "gesture_left":  "Gesture swipe left",
    "gesture_right": "Gesture swipe right",
    "gesture_up":    "Gesture swipe up",
    "gesture_down":  "Gesture swipe down",
}

# Maps config button keys to the MouseEvent types they correspond to
BUTTON_TO_EVENTS = {
    "middle":        ("middle_down", "middle_up"),
    "gesture":       ("gesture_click",),
    "gesture_left":  ("gesture_swipe_left",),
    "gesture_right": ("gesture_swipe_right",),
    "gesture_up":    ("gesture_swipe_up",),
    "gesture_down":  ("gesture_swipe_down",),
    "xbutton1":      ("xbutton1_down", "xbutton1_up"),
    "xbutton2":      ("xbutton2_down", "xbutton2_up"),
    "hscroll_left":  ("hscroll_left",),
    "hscroll_right": ("hscroll_right",),
}

DEFAULT_CONFIG = {
    "version": 3,
    "active_profile": "default",
    "app_overrides": {},
    "profiles": {
        "default": {
            "label": "Default (All Apps)",
            "apps": [],          # empty = all apps (fallback profile)
            "mappings": {
                "middle": "none",
                "gesture": "none",
                "gesture_left": "none",
                "gesture_right": "none",
                "gesture_up": "none",
                "gesture_down": "none",
                "xbutton1": "alt_tab",
                "xbutton2": "alt_tab",
                "hscroll_left": "browser_back",
                "hscroll_right": "browser_forward",
            },
        }
    },
    "settings": {
        "start_minimized": True,
        "start_with_windows": False,
        "hscroll_threshold": 1,
        "invert_hscroll": False,  # swap horizontal scroll directions
        "invert_vscroll": False,  # swap vertical scroll directions
        "dpi": 1000,              # pointer speed / DPI setting
        "gesture_threshold": 50,
        "gesture_deadzone": 40,
        "gesture_timeout_ms": 3000,
        "gesture_cooldown_ms": 500,
        "appearance_mode": "system",
        "debug_mode": False,
    },
}

# Compatibility alias used by older UI/backend code. The richer app catalog now
# lives in core.app_catalog, but keeping this export avoids a wider refactor.
KNOWN_APPS = APP_HINTS


def get_icon_for_exe(exe_name: str) -> str:
    """Return the icon image filename (relative to images/) for an exe, or ''."""
    return get_legacy_icon(exe_name)


def ensure_config_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load_config():
    """Load config from disk, or return defaults if none exists."""
    ensure_config_dir()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # Merge any missing keys from default
            cfg = _migrate(cfg)
            cfg = _merge_defaults(cfg, DEFAULT_CONFIG)
            return cfg
        except Exception as e:
            print(f"[Config] Error loading config: {e}")
    return json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy


def save_config(cfg):
    """Persist config to disk."""
    ensure_config_dir()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def get_active_mappings(cfg):
    """Return the mappings dict for the currently active profile."""
    profile_name = cfg.get("active_profile", "default")
    profiles = cfg.get("profiles", {})
    profile = profiles.get(profile_name, profiles.get("default", {}))
    return profile.get("mappings", DEFAULT_CONFIG["profiles"]["default"]["mappings"])


def set_mapping(cfg, button, action_id, profile=None):
    """Set a mapping for a button in the given profile (or active profile)."""
    if profile is None:
        profile = cfg.get("active_profile", "default")
    cfg["profiles"].setdefault(profile, {
        "label": profile,
        "mappings": dict(DEFAULT_CONFIG["profiles"]["default"]["mappings"]),
    })
    cfg["profiles"][profile]["mappings"][button] = action_id
    save_config(cfg)
    return cfg


def create_profile(cfg, name, label=None, copy_from="default", apps=None):
    """Create a new profile, optionally copying from an existing one."""
    if label is None:
        label = name
    source = cfg["profiles"].get(copy_from, cfg["profiles"].get("default", {}))
    cfg["profiles"][name] = {
        "label": label,
        "apps": apps if apps is not None else [],
        "mappings": dict(source.get("mappings", {})),
    }
    save_config(cfg)
    return cfg


def set_app_override(cfg, app_id, label=None, path=None):
    """Persist custom metadata for an app identifier."""
    if not app_id:
        return cfg
    overrides = cfg.setdefault("app_overrides", {})
    overrides[app_id] = {
        "label": label or app_id,
        "path": os.path.abspath(path) if path else "",
    }
    save_config(cfg)
    return cfg


def delete_profile(cfg, name):
    """Delete a profile (cannot delete 'default')."""
    if name == "default":
        return cfg
    cfg["profiles"].pop(name, None)
    if cfg["active_profile"] == name:
        cfg["active_profile"] = "default"
    save_config(cfg)
    return cfg


def resolve_app_for_config(cfg, spec):
    """Resolve app metadata, preferring custom overrides stored in config."""
    if not spec:
        return None

    key = spec.casefold()
    for app_id, override in cfg.get("app_overrides", {}).items():
        path = os.path.abspath(override.get("path", "")) if override.get("path") else ""
        aliases = [app_id, override.get("label", app_id)]
        if path:
            aliases.extend([path, os.path.basename(path), Path(path).stem])
        if key in {value.casefold() for value in aliases if value}:
            return {
                "id": app_id,
                "label": override.get("label", app_id),
                "path": path,
                "aliases": aliases,
                "legacy_icon": "",
            }

    return resolve_app_spec(spec)


def get_profile_for_app(cfg, exe_name):
    """Return the profile name that matches the given app identifier, or 'default'."""
    if not exe_name:
        return "default"

    resolved = resolve_app_for_config(cfg, exe_name)
    candidate_ids = {alias.casefold() for alias in resolved.get("aliases", [])}
    candidate_ids.add(resolved.get("id", exe_name).casefold())
    for pname, pdata in cfg.get("profiles", {}).items():
        for app_id in pdata.get("apps", []):
            if app_id and app_id.casefold() in candidate_ids:
                return pname
    return "default"


def _migrate(cfg):
    """Migrate config from older versions to current."""
    version = cfg.get("version", 1)
    if version < 2:
        # v1 → v2:  add 'apps' list to each profile, new settings keys
        for pdata in cfg.get("profiles", {}).values():
            pdata.setdefault("apps", [])
        cfg.setdefault("settings", {})
        cfg["settings"].setdefault("invert_hscroll", False)
        cfg["settings"].setdefault("invert_vscroll", False)
        cfg["settings"].setdefault("dpi", 1000)
        cfg["version"] = 2

    if version < 3:
        settings = cfg.setdefault("settings", {})
        settings.setdefault("gesture_threshold", 50)
        settings.setdefault("gesture_deadzone", 40)
        settings.setdefault("gesture_timeout_ms", 3000)
        settings.setdefault("gesture_cooldown_ms", 500)
        for pdata in cfg.get("profiles", {}).values():
            mappings = pdata.setdefault("mappings", {})
            mappings.setdefault("gesture", "none")
            for key in GESTURE_DIRECTION_BUTTONS:
                mappings.setdefault(key, "none")
        cfg["version"] = 3

    cfg.setdefault("settings", {})
    cfg["settings"].setdefault("appearance_mode", "system")
    cfg["settings"].setdefault("debug_mode", False)
    cfg.setdefault("app_overrides", {})

    # Always migrate old wmplayer.exe → Microsoft.Media.Player.exe in profile apps
    for pdata in cfg.get("profiles", {}).values():
        apps = pdata.get("apps", [])
        for i, a in enumerate(apps):
            if a.lower() == "wmplayer.exe":
                apps[i] = "Microsoft.Media.Player.exe"

    return cfg


def _merge_defaults(cfg, defaults):
    """Recursively merge missing keys from defaults into cfg."""
    for key, val in defaults.items():
        if key not in cfg:
            cfg[key] = val
        elif isinstance(val, dict) and isinstance(cfg.get(key), dict):
            _merge_defaults(cfg[key], val)
    return cfg
