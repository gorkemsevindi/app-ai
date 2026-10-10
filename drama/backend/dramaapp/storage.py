"""Object storage. Local filesystem in dev; S3/R2 adapter has the same interface (put/path/url).

Media is never publicly addressable: playback URLs are HMAC-signed and short-lived, and they are only
issued after a server-side entitlement check (see modules/catalog.py playback endpoint)."""

import hashlib
import mimetypes
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from .config import get_settings
from .models import Asset
from .security import sign_media


def root() -> Path:
    return get_settings().storage_dir


def local_path(key: str) -> Path:
    p = (root() / key).resolve()
    if not str(p).startswith(str(root().resolve())):
        raise ValueError("path traversal")
    return p


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def put_file(db: Session, src: Path, key: str, kind: str, owner_id: str | None, meta: dict | None = None,
             character_id: str | None = None, move: bool = False) -> Asset:
    dst = local_path(key)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        size = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())
        digest = None
        mime = "application/x-directory"
    else:
        (shutil.move if move else shutil.copyfile)(src, dst)
        size = dst.stat().st_size
        digest = sha256_file(dst)
        mime = mimetypes.guess_type(dst.name)[0] or "application/octet-stream"
    a = Asset(owner_id=owner_id, kind=kind, storage_key=key, mime=mime, bytes=size, content_hash=digest,
              meta=meta or {}, character_id=character_id)
    db.add(a)
    db.flush()
    return a


def signed_url(key: str, ttl_s: int = 3600) -> str:
    return f"{get_settings().public_base_url}/media/{key}?{sign_media(key, ttl_s)}"
