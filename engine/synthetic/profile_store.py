"""Profile persistence: save/load calibrated AssetProfiles.

Pickle (not JSON) because the profile embeds the fitted hmmlearn model; scalars,
numpy arrays and dataclasses pickle cleanly. A versioned header guards against
format drift. Phase 3 serves these artifacts; Phase 5's reproducibility controls
reference them.
"""

from __future__ import annotations

import pickle
from pathlib import Path

from synthetic.calibration import AssetProfile
from synthetic.data import REPO_ROOT

FORMAT_VERSION = 1
DEFAULT_PROFILE_DIR = REPO_ROOT / "data" / "profiles"


def save_profile(profile: AssetProfile, path: str | Path | None = None) -> Path:
    """Serialize a profile. Default location: data/profiles/<asset>.pkl."""
    path = Path(path) if path else DEFAULT_PROFILE_DIR / f"{profile.asset}.pkl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump({"format_version": FORMAT_VERSION, "profile": profile}, f)
    return path


def load_profile(path: str | Path) -> AssetProfile:
    with open(path, "rb") as f:
        blob = pickle.load(f)
    if blob.get("format_version") != FORMAT_VERSION:
        raise ValueError(f"unsupported profile format version: {blob.get('format_version')}")
    return blob["profile"]
