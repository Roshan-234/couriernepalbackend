"""Storing an uploaded advert image safely.

A promotion for Dashain cargo is a picture of something. Until now
``AdCreative.image_path`` was a string with nothing able to fill it, so every
advert was text-only and the festival campaigns this system exists to run
could not actually look like campaigns.

An upload endpoint is the most dangerous thing in an admin console, so the
rules here are deliberately blunt:

**The file is never trusted and never stored as sent.** Every upload is
decoded by Pillow and written out again from the decoded pixels. That single
decision kills most of the attack surface at once: a polyglot file that is
both a valid GIF and a valid HTML page does not survive a re-encode, and
neither does a payload hidden in a comment block or a trailing appendix.

**The uploaded name is thrown away.** The stored name is generated here. A
caller cannot choose where its bytes land, so there is no path traversal to
reason about and no way to overwrite an existing creative's image.

**Camera metadata is dropped.** Re-encoding strips EXIF, which on a phone
photo routinely carries GPS coordinates and a device serial. An advert image
is often whatever someone had on their phone, and publishing the office's
coordinates with a banner is not a trade anyone chose to make.

**Bounded before it is decoded.** ``MAX_CONTENT_LENGTH`` (2 MB) rejects a
large body at the server, and the dimension cap below rejects a small file
that decodes to an enormous bitmap — the decompression bomb a byte-size limit
alone does not catch.
"""

import io
import os
import secrets

from flask import current_app
from PIL import Image, UnidentifiedImageError
from werkzeug.exceptions import BadRequest

#: What the site will serve. Narrow on purpose: these three cover every real
#: banner, and each is a format Pillow can fully decode and re-encode. SVG is
#: deliberately absent — it is a document that can carry script, not a bitmap,
#: and nothing here could make it safe to serve from our own origin.
ALLOWED_FORMATS = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
}

#: Refuses a decompression bomb: a few kilobytes of PNG can describe a bitmap
#: of hundreds of megabytes, and the decode happens before any size check we
#: could make on the result.
MAX_PIXELS = 30_000_000
MAX_EDGE = 6000

#: Smaller than this is a mistake rather than a banner; saying so at upload
#: time is kinder than a stretched image on the live site.
MIN_EDGE = 80

SUBDIR = "ad_images"


def images_dir():
    configured = current_app.config.get("AD_IMAGE_DIR")
    directory = configured or os.path.join(
        current_app.root_path, "..", "storage", SUBDIR)
    os.makedirs(directory, exist_ok=True)
    return os.path.abspath(directory)


def public_url(stored_name):
    """The path an advert's ``image_path`` holds.

    Site-relative, so it passes the same validation as every other link
    field on a creative and cannot become an off-site reference.
    """
    return f"/api/ads/images/{stored_name}"


def is_managed_url(value):
    """True when a creative's image_path points at a file we stored."""
    return bool(value) and value.startswith("/api/ads/images/")


def stored_name_from_url(value):
    if not is_managed_url(value):
        return None
    name = value.rsplit("/", 1)[-1]
    return name if safe_stored_name(name) else None


def safe_stored_name(name):
    """Names this module generates, and nothing else.

    The serving route matches against this rather than sanitising whatever
    arrives: an allowlist of a shape we created is easier to be sure about
    than a blocklist of everything a caller might try.
    """
    if not name or len(name) > 80 or name.count(".") != 1:
        return False
    stem, _, extension = name.partition(".")
    if extension not in {ext for ext, _ in ALLOWED_FORMATS.values()}:
        return False
    return len(stem) == 32 and all(c in "0123456789abcdef" for c in stem)


def store_image(file_storage):
    """Validate, re-encode and write one upload.

    Returns ``(stored_name, content_type, width, height)``. Raises
    ``BadRequest`` with a reason an administrator can act on — "that file is
    not an image we can use" is not a useful thing to read when a banner is
    due to go live.
    """
    if file_storage is None or not file_storage.filename:
        raise BadRequest("Attach an image file under the field name 'file'")

    raw = file_storage.read()
    if not raw:
        raise BadRequest("The uploaded file is empty")

    try:
        probe = Image.open(io.BytesIO(raw))
        image_format = probe.format
        width, height = probe.size
    except UnidentifiedImageError:
        raise BadRequest(
            "That file is not a PNG, JPEG or WebP image. Note that renaming a "
            "file does not change what it is; the contents are what is checked.")
    except Exception:
        raise BadRequest("That image could not be read; it may be truncated")

    if image_format not in ALLOWED_FORMATS:
        raise BadRequest(
            f"{image_format or 'That format'} is not accepted. Use PNG, JPEG or WebP.")

    if width * height > MAX_PIXELS or width > MAX_EDGE or height > MAX_EDGE:
        raise BadRequest(
            f"That image is {width}x{height}, which is larger than this site "
            f"will serve. Resize it to at most {MAX_EDGE}px on its longest edge.")
    if width < MIN_EDGE or height < MIN_EDGE:
        raise BadRequest(
            f"That image is {width}x{height}, too small for a banner. "
            f"Each edge needs to be at least {MIN_EDGE}px.")

    extension, content_type = ALLOWED_FORMATS[image_format]

    # Re-open rather than reuse the probe: `verify()`-style reads leave the
    # file object unusable, and we want the decode that produces the pixels
    # to be the one whose output we store.
    try:
        source = Image.open(io.BytesIO(raw))
        source.load()
    except Exception:
        raise BadRequest("That image could not be decoded; it may be corrupt")

    buffer = io.BytesIO()
    if image_format == "JPEG":
        # A JPEG with transparency information is not a thing; flatten any
        # alpha onto white rather than letting Pillow refuse the save.
        if source.mode not in ("RGB", "L"):
            source = source.convert("RGB")
        source.save(buffer, format="JPEG", quality=85, optimize=True)
    elif image_format == "PNG":
        source.save(buffer, format="PNG", optimize=True)
    else:
        source.save(buffer, format="WEBP", quality=85, method=4)

    stored_name = f"{secrets.token_hex(16)}.{extension}"
    path = os.path.join(images_dir(), stored_name)
    # 0644: the web server reads these, and they are public by nature.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(buffer.getvalue())

    return stored_name, content_type, width, height


def delete_image(stored_name):
    """Remove a stored file. Missing is success — the goal is that it is gone."""
    if not safe_stored_name(stored_name):
        return False
    try:
        os.remove(os.path.join(images_dir(), stored_name))
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False
