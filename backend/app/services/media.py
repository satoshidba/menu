"""Image/file storage. Untrusted images are decoded and re-encoded (strips metadata, rejects non-images)."""
from __future__ import annotations

import hashlib
import io
import shutil
import uuid
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import get_settings

Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "image/gif"}
RECEIPT_MIMES = IMAGE_MIMES | {"application/pdf", "text/plain", "text/html", "message/rfc822"}


def safe_rel(path: str) -> Path:
    p = Path(path)
    if p.is_absolute() or ".." in p.parts:
        raise HTTPException(400, "Bad path")
    return p


def abs_media(rel: str) -> Path:
    return get_settings().media_dir / safe_rel(rel)


def sniff_mime(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc", b"ftypmsf1"):
        return "image/heic"
    if data[:5] == b"%PDF-":
        return "application/pdf"
    return None


def reencode_image(data: bytes, max_side: int = 2000, quality: int = 84) -> tuple[bytes, int, int]:
    """Decode an untrusted image and write a clean JPEG. Raises 422 for anything that isn't a decodable image."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((max_side, max_side))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
            return out.getvalue(), im.width, im.height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise HTTPException(422, "That file isn't an image we can read") from e


def store_image(data: bytes, folder: str, max_side: int = 2000) -> tuple[str, int, int]:
    clean, w, h = reencode_image(data, max_side)
    rel = f"{folder}/{uuid.uuid4().hex}.jpg"
    dest = abs_media(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(clean)
    return rel, w, h


def move_media(rel_src: str, folder: str) -> str:
    src = abs_media(rel_src)
    rel = f"{folder}/{src.name}"
    dest = abs_media(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), dest)
    return rel


def delete_media(rel: str | None) -> None:
    if not rel:
        return
    try:
        abs_media(rel).unlink(missing_ok=True)
    except HTTPException:
        pass


def store_receipt_file(data: bytes, mime: str, household_id: uuid.UUID) -> tuple[str, str]:
    """Store an original receipt (outside the media tree). Returns (relative path, sha256)."""
    digest = hashlib.sha256(data).hexdigest()
    ext = {"application/pdf": "pdf", "text/plain": "txt", "text/html": "html", "message/rfc822": "eml"}.get(mime, "jpg")
    rel = f"{household_id}/{digest[:2]}/{digest}.{ext}"
    dest = get_settings().receipts_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(data)
    return rel, digest


def receipt_abs(rel: str) -> Path:
    return get_settings().receipts_dir / safe_rel(rel)
