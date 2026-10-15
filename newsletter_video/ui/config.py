"""Where the control panel reads and writes things. Everything is derived
from one `UIPaths`, so tests can point the whole app at a temp directory."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LAUNCHD_LABEL = "com.tldrbrainrot.daily"


@dataclass(frozen=True)
class UIPaths:
    root: Path
    env_path: Path
    output_dir: Path
    state_dir: Path  # jobs, voice samples, uploads in flight
    characters_dir: Path
    legacy_character_dir: Path
    background_dir: Path
    secrets_dir: Path
    launchd_plist: Path | None

    @classmethod
    def default(cls, root: Path = PROJECT_ROOT, env_path: Path | None = None) -> "UIPaths":
        return cls.under(root, env_path=env_path,
                         launchd_plist=Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist")

    @classmethod
    def under(cls, root: Path, env_path: Path | None = None, launchd_plist: Path | None = None) -> "UIPaths":
        output = root / "output"
        return cls(
            root=root,
            env_path=env_path or root / ".env",
            output_dir=output,
            state_dir=output / "_ui",
            characters_dir=root / "assets" / "characters",
            legacy_character_dir=root / "assets" / "character",
            background_dir=root / "assets" / "background",
            secrets_dir=root / "secrets",
            launchd_plist=launchd_plist,
        )
