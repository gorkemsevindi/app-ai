"""Storage adapter. All buckets are private; clients only ever see short-lived signed URLs.
Swap providers (S3, R2, GCS-interop, MinIO) by configuration, not code."""

from dataclasses import dataclass
from typing import Protocol

from ..config import Settings, get_settings

PHOTO_MIMES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
VIDEO_MIMES = {"video/mp4", "video/quicktime"}

# Magic-byte signatures used to verify what was actually uploaded (never trust Content-Type).
_SIGNATURES: list[tuple[str, int, bytes]] = [
    ("image/jpeg", 0, b"\xff\xd8\xff"),
    ("image/png", 0, b"\x89PNG\r\n\x1a\n"),
    ("image/webp", 8, b"WEBP"),
]
_FTYP_BRANDS = {
    b"heic": "image/heic", b"heix": "image/heic", b"mif1": "image/heif", b"msf1": "image/heif",
    b"qt  ": "video/quicktime", b"isom": "video/mp4", b"mp41": "video/mp4", b"mp42": "video/mp4",
    b"avc1": "video/mp4", b"iso5": "video/mp4", b"iso6": "video/mp4", b"M4V ": "video/mp4",
}


def sniff_mime(head: bytes) -> str | None:
    for mime, off, sig in _SIGNATURES:
        if head[off:off + len(sig)] == sig:
            if mime == "image/webp" and head[:4] != b"RIFF":
                continue
            return mime
    if head[4:8] == b"ftyp":
        return _FTYP_BRANDS.get(head[8:12])
    return None


def mime_compatible(declared: str, sniffed: str | None) -> bool:
    if sniffed is None:
        return False
    if declared == sniffed:
        return True
    groups = [{"image/heic", "image/heif"}, {"video/mp4", "video/quicktime"}]
    return any(declared in g and sniffed in g for g in groups)


@dataclass
class PresignedPost:
    url: str
    fields: dict[str, str]
    expires_in: int


class Storage(Protocol):
    def presign_upload(self, key: str, mime: str, max_bytes: int) -> PresignedPost: ...
    def presign_get(self, key: str, ttl_s: int | None = None) -> str: ...
    def presign_put(self, key: str, mime: str, ttl_s: int | None = None) -> str: ...
    def head(self, key: str) -> dict | None: ...
    def read_head_bytes(self, key: str, n: int = 32) -> bytes: ...
    def delete_prefix(self, prefix: str) -> int: ...
    def delete(self, key: str) -> None: ...


class S3Storage:
    def __init__(self, settings: Settings):
        import boto3
        from botocore.config import Config

        self.s = settings
        kw = dict(region_name=settings.s3_region, aws_access_key_id=settings.s3_access_key_id,
                  aws_secret_access_key=settings.s3_secret_access_key,
                  config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
        self.client = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, **kw)
        # Separate client for URLs handed to devices (public hostname differs from in-cluster one).
        self.signer = boto3.client("s3", endpoint_url=settings.s3_public_endpoint_url or settings.s3_endpoint_url,
                                   **kw)
        self.bucket = settings.s3_bucket_private

    def presign_upload(self, key: str, mime: str, max_bytes: int) -> PresignedPost:
        ttl = self.s.upload_url_ttl_s
        post = self.signer.generate_presigned_post(
            self.bucket, key,
            Fields={"Content-Type": mime},
            Conditions=[{"Content-Type": mime}, ["content-length-range", 1, max_bytes]],
            ExpiresIn=ttl,
        )
        return PresignedPost(post["url"], post["fields"], ttl)

    def presign_get(self, key: str, ttl_s: int | None = None) -> str:
        return self.signer.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key},
                                                  ExpiresIn=ttl_s or self.s.download_url_ttl_s)

    def presign_put(self, key: str, mime: str, ttl_s: int | None = None) -> str:
        # Used by GPU workers (they may run outside our VPC), hence the internal endpoint is not assumed.
        return self.signer.generate_presigned_url(
            "put_object", Params={"Bucket": self.bucket, "Key": key, "ContentType": mime},
            ExpiresIn=ttl_s or 3600)

    def head(self, key: str) -> dict | None:
        from botocore.exceptions import ClientError

        try:
            r = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError:
            return None
        return {"size": r["ContentLength"], "content_type": r.get("ContentType")}

    def read_head_bytes(self, key: str, n: int = 32) -> bytes:
        r = self.client.get_object(Bucket=self.bucket, Key=key, Range=f"bytes=0-{n - 1}")
        return r["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> int:
        n = 0
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objs, "Quiet": True})
                n += len(objs)
        return n


class MemoryStorage:
    """In-process fake for tests only."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def presign_upload(self, key: str, mime: str, max_bytes: int) -> PresignedPost:
        return PresignedPost(f"memory://upload/{key}", {"key": key, "Content-Type": mime}, 600)

    def presign_get(self, key: str, ttl_s: int | None = None) -> str:
        return f"memory://get/{key}?ttl={ttl_s or 900}"

    def presign_put(self, key: str, mime: str, ttl_s: int | None = None) -> str:
        return f"memory://put/{key}"

    def put(self, key: str, data: bytes, mime: str) -> None:
        self.objects[key] = (data, mime)

    def head(self, key: str) -> dict | None:
        o = self.objects.get(key)
        return None if o is None else {"size": len(o[0]), "content_type": o[1]}

    def read_head_bytes(self, key: str, n: int = 32) -> bytes:
        return self.objects[key][0][:n]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def delete_prefix(self, prefix: str) -> int:
        keys = [k for k in self.objects if k.startswith(prefix)]
        for k in keys:
            del self.objects[k]
        return len(keys)


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = S3Storage(get_settings())
    return _storage


def set_storage(storage: Storage) -> None:
    global _storage
    _storage = storage


def user_prefix(user_id) -> str:
    return f"users/{user_id}/"
