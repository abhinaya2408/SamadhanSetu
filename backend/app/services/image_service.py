"""Citizen evidence uploads: validation, metadata stripping, private storage.

Design rules (brief section 3E):

* **Validate on the backend, not just in JavaScript.** A file is accepted only if
  its *content* says it is a real JPEG / PNG / WebP — the client-declared MIME
  type and filename are treated as untrusted labels.
* **Restrict size and content type, generate safe names, never execute.** Stored
  filenames are generated from a content hash; the original name is kept only as
  a display string and is escaped by the frontend.
* **Prevent path traversal.** No part of the client filename is ever used to build
  a path. Files live under a fixed directory derived from the challenge id.
* **Keep images private by default.** Nothing is served from a public static
  directory; files are streamed through an authorised endpoint.
* **Strip sensitive metadata where practical** (GPS/EXIF typically lives here).

When Pillow is installed the image is fully decoded and re-encoded, which both
proves the bytes are a decodable image and removes all metadata. When Pillow is
absent a dependency-free fallback removes EXIF/comment segments from JPEG, text
chunks from PNG and EXIF/XMP chunks from WebP, and says so in the record's
``validation_note`` so the UI never overstates what was checked.
"""

from __future__ import annotations

import hashlib
import io
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import UPLOAD_DIR, settings

CHALLENGE_UPLOAD_DIR = UPLOAD_DIR / "challenges"

# Content types we accept, and the canonical extension we store them with.
ALLOWED_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

MAX_ATTACHMENTS_PER_CHALLENGE = 6
MAX_ATTACHMENTS_PER_SESSION = 6
MIN_BYTES = 64  # anything smaller cannot be a real photo


class UploadRejected(Exception):
    """Raised with a message that is safe to show to the citizen."""


@dataclass
class ProcessedImage:
    data: bytes
    content_type: str
    extension: str
    extension_type: str
    metadata_stripped: bool
    note: str
    sha256: str
    size_bytes: int


# ---------------------------------------------------------------------------
# Content sniffing (never trust the client)
# ---------------------------------------------------------------------------

def sniff_type(data: bytes) -> Optional[str]:
    """Return the MIME type implied by the file's magic bytes, or None."""
    if len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


# ---------------------------------------------------------------------------
# Metadata stripping — dependency-free fallback
# ---------------------------------------------------------------------------

_JPEG_STRIP_MARKERS = {0xE1, 0xED, 0xFE}  # APP1 (EXIF/XMP), APP13 (IPTC), COM


def _strip_jpeg(data: bytes) -> bytes:
    """Drop EXIF/XMP/IPTC/comment segments from a JPEG, keeping image data."""
    if data[:2] != b"\xff\xd8":
        return data
    out = bytearray(data[:2])
    i = 2
    size = len(data)
    while i < size - 1:
        if data[i] != 0xFF:
            # Not a marker (should not happen before SOS) — keep the rest as-is.
            out.extend(data[i:])
            return bytes(out)
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            out.extend(data[i:i + 2])
            i += 2
            continue
        if marker == 0xDA:  # start of scan: the rest is entropy-coded data
            out.extend(data[i:])
            return bytes(out)
        if i + 4 > size:
            break
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        end = i + 2 + seg_len
        if end > size:
            break
        if marker not in _JPEG_STRIP_MARKERS:
            out.extend(data[i:end])
        i = end
    return bytes(out)


_PNG_STRIP_CHUNKS = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}


def _strip_png(data: bytes) -> bytes:
    """Drop text/EXIF/time chunks from a PNG, preserving critical chunks."""
    out = bytearray(data[:8])
    i = 8
    size = len(data)
    while i + 8 <= size:
        chunk_len = struct.unpack(">I", data[i:i + 4])[0]
        chunk_type = data[i + 4:i + 8]
        end = i + 12 + chunk_len
        if end > size:
            break
        if chunk_type not in _PNG_STRIP_CHUNKS:
            out.extend(data[i:end])
        if chunk_type == b"IEND":
            break
        i = end
    return bytes(out)


_WEBP_STRIP_CHUNKS = {b"EXIF", b"XMP "}


def _strip_webp(data: bytes) -> bytes:
    """Drop EXIF/XMP RIFF chunks from a WebP container."""
    out = bytearray(data[:12])
    i = 12
    size = len(data)
    while i + 8 <= size:
        fourcc = data[i:i + 4]
        chunk_len = struct.unpack("<I", data[i + 4:i + 8])[0]
        padded = chunk_len + (chunk_len % 2)
        end = i + 8 + padded
        if end > size:
            break
        if fourcc not in _WEBP_STRIP_CHUNKS:
            out.extend(data[i:end])
        i = end
    # Rewrite the RIFF size field to match the rebuilt container.
    if len(out) >= 8:
        out[4:8] = struct.pack("<I", len(out) - 8)
    return bytes(out)


def strip_metadata_fallback(data: bytes, content_type: str) -> bytes:
    if content_type == "image/jpeg":
        return _strip_jpeg(data)
    if content_type == "image/png":
        return _strip_png(data)
    if content_type == "image/webp":
        return _strip_webp(data)
    return data


# ---------------------------------------------------------------------------
# Optional strict path (Pillow): decode + re-encode
# ---------------------------------------------------------------------------

_PIL_FORMATS = {
    "image/jpeg": ("JPEG", "RGB"),
    "image/png": ("PNG", None),
    "image/webp": ("WEBP", None),
}


def _try_pillow_strict(data: bytes, content_type: str) -> Optional[tuple[bytes, str]]:
    """Decode and re-encode with Pillow. Returns (bytes, note) or None."""
    try:
        from PIL import Image  # type: ignore
    except Exception:  # pragma: no cover - Pillow is optional
        return None

    fmt, force_mode = _PIL_FORMATS.get(content_type, (None, None))
    if not fmt:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            if getattr(img, "n_frames", 1) > 1:
                return None  # animated: caller falls back to segment stripping
            img.load()
            if force_mode and img.mode not in ("RGB", "L"):
                img = img.convert(force_mode)
            buf = io.BytesIO()
            save_kwargs: dict = {}
            if fmt == "JPEG":
                save_kwargs = {"quality": 88, "optimize": True}
            elif fmt == "PNG":
                save_kwargs = {"optimize": True}
            elif fmt == "WEBP":
                save_kwargs = {"quality": 88}
            # No ``exif``/``icc_profile`` argument is passed, so no metadata is written.
            img.save(buf, format=fmt, **save_kwargs)
        return buf.getvalue(), "Fully decoded and re-encoded; all metadata removed."
    except Exception:
        return None


def safe_display_name(filename: str) -> str:
    """Reduce a client filename to a short, control-character-free display label."""
    name = (filename or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    name = name.lstrip(".")
    if not name:
        return "photo"
    return name[:180]


# ---------------------------------------------------------------------------
# Public processing entry point
# ---------------------------------------------------------------------------

def process_upload(filename: str, data: bytes, declared_type: str = "") -> ProcessedImage:
    """Validate and sanitise an uploaded image. Raises :class:`UploadRejected`."""
    if not data:
        raise UploadRejected("The file is empty.")
    if len(data) < MIN_BYTES:
        raise UploadRejected("That file is too small to be a photograph.")
    if len(data) > settings.max_upload_bytes:
        mb = settings.max_upload_bytes / (1024 * 1024)
        raise UploadRejected(f"Images must be smaller than {mb:.0f} MB.")

    content_type = sniff_type(data)
    if content_type is None:
        raise UploadRejected(
            "That does not look like a JPEG, PNG or WebP image. "
            "Please upload a photograph of the problem."
        )
    if content_type not in ALLOWED_TYPES:
        raise UploadRejected("Only JPEG, PNG and WebP images are accepted.")

    strict = _try_pillow_strict(data, content_type)
    if strict is not None:
        cleaned, note = strict
        stripped = True
    else:
        cleaned = strip_metadata_fallback(data, content_type)
        stripped = cleaned != data
        if declared_type and declared_type != content_type:
            note = (
                "Content check: the file was recognised as "
                f"{content_type} (it claimed to be {declared_type}). "
                + ("EXIF/comment segments removed." if stripped else "No metadata segments found.")
            )
        else:
            note = (
                "Signature checked; EXIF/comment segments removed."
                if stripped
                else "Signature checked; no metadata segments were present."
            )

    if not cleaned:
        raise UploadRejected("The image could not be processed. Please try another photo.")

    digest = hashlib.sha256(cleaned).hexdigest()
    extension = ALLOWED_TYPES[content_type]
    return ProcessedImage(
        data=cleaned,
        content_type=content_type,
        extension=extension,
        extension_type=content_type,
        metadata_stripped=stripped,
        note=note[:250],
        sha256=digest,
        size_bytes=len(cleaned),
    )


def storage_dir(challenge_id: Optional[int], staging_key: Optional[str] = None) -> Path:
    """Directory for an attachment. Never derived from client input."""
    if challenge_id is not None:
        target = CHALLENGE_UPLOAD_DIR / f"challenge-{int(challenge_id)}"
    else:
        key = re.sub(r"[^A-Za-z0-9_.-]", "", staging_key or "staged")[:64] or "staged"
        target = CHALLENGE_UPLOAD_DIR / f"staged-{key}"
    target.mkdir(parents=True, exist_ok=True)
    return target


def generated_name(processed: ProcessedImage) -> str:
    """A collision-resistant name built only from the content hash."""
    return f"{processed.sha256[:40]}{processed.extension}"


def write_file(directory: Path, name: str, data: bytes) -> Path:
    path = directory / name
    path.write_bytes(data)
    return path


def path_for(attachment) -> Path:
    challenge_id = attachment.challenge_id
    directory = storage_dir(challenge_id, attachment.staging_key)
    return directory / attachment.stored_name


def delete_file(attachment) -> bool:
    try:
        path = path_for(attachment)
    except Exception:
        return False
    try:
        if path.exists():
            path.unlink()
        return True
    except OSError:
        return False


def describe_limits() -> dict:
    return {
        "allowed_types": sorted(ALLOWED_TYPES),
        "max_bytes": settings.max_upload_bytes,
        "max_per_challenge": MAX_ATTACHMENTS_PER_CHALLENGE,
        "metadata_stripping": "strict (Pillow decode + re-encode)" if _pillow_available()
        else "segment-level (EXIF/comment chunks removed, no decoder bundled)",
        "private_by_default": True,
    }


def _pillow_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("PIL") is not None
