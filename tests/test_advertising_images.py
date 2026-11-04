"""Advert image uploads: the dangerous part of an admin console.

An upload endpoint takes a file from a person and puts it on a public URL
served from our own origin. Three things have to hold, and each has a test
here that fails loudly rather than subtly:

* **what comes back out is not what went in** — every upload is decoded and
  re-encoded, so appended payloads and polyglots do not survive;
* **the caller never chooses a filename or a path**; and
* **the serving route reaches nothing outside the image directory**, whatever
  it is asked for.

The fourth property is a privacy one: a phone photo carries GPS in its EXIF,
and an advert image is often whatever someone had on their phone.
"""

import io
import os

import pytest
from PIL import Image

from app.advertising import uploads
from app.extensions import db
from app.models.advertising import AdCampaign, AdCreative, AdPlacement


@pytest.fixture
def image_dir(app, tmp_path):
    previous = app.config.get("AD_IMAGE_DIR")
    app.config["AD_IMAGE_DIR"] = str(tmp_path / "ad_images")
    yield str(tmp_path / "ad_images")
    if previous is None:
        app.config.pop("AD_IMAGE_DIR", None)
    else:
        app.config["AD_IMAGE_DIR"] = previous


@pytest.fixture
def creative(app):
    with app.app_context():
        placement = AdPlacement.query.filter_by(code="image_slot").first()
        if not placement:
            placement = AdPlacement(code="image_slot", name="Image slot")
            db.session.add(placement)
            db.session.flush()
        campaign = AdCampaign.query.filter_by(code="IMAGE_TESTS").first()
        if not campaign:
            campaign = AdCampaign(code="IMAGE_TESTS", name="Image tests")
            db.session.add(campaign)
            db.session.flush()
        item = AdCreative(campaign_id=campaign.id, placement_id=placement.id,
                          headline="Image test creative")
        db.session.add(item)
        db.session.commit()
        return item.id


def _stored(directory):
    """What is on disk, tolerating a directory that was never created.

    A rejected upload never reaches the point of making the folder, which is
    the behaviour we want — so "nothing stored" and "no folder" are the same
    answer here.
    """
    return sorted(os.listdir(directory)) if os.path.isdir(directory) else []


def _png(width=400, height=200, colour=(20, 80, 140)):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(client, token, creative_id, data, filename="banner.png",
            content_type="image/png", field="file"):
    return client.post(
        f"/api/ads/creatives/{creative_id}/image",
        data={field: (io.BytesIO(data), filename, content_type)},
        content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {token}"},
    )


# ── The happy path ───────────────────────────────────────────────────────────

def test_an_image_is_stored_and_linked(client, admin_token, creative, image_dir, app):
    r = _upload(client, admin_token, creative, _png())
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["image"]["width"] == 400 and body["image"]["height"] == 200
    url = body["creative"]["image_path"]
    assert url.startswith("/api/ads/images/")
    # Site-relative, so it satisfies the same rule as every other link field.
    assert not url.startswith("//") and ":" not in url
    assert len(_stored(image_dir)) == 1


def test_the_stored_image_is_served(client, admin_token, creative, image_dir):
    url = _upload(client, admin_token, creative, _png()).get_json()["creative"]["image_path"]
    r = client.get(url)
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("image/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert Image.open(io.BytesIO(r.data)).size == (400, 200)


def test_serving_is_public(app, admin_token, client, creative, image_dir):
    """Adverts appear on pages nobody has signed in to."""
    url = _upload(client, admin_token, creative, _png()).get_json()["creative"]["image_path"]
    assert app.test_client().get(url).status_code == 200


def test_replacing_an_image_removes_the_previous_file(client, admin_token, creative, image_dir):
    first = _upload(client, admin_token, creative, _png()).get_json()["creative"]["image_path"]
    second = _upload(client, admin_token, creative, _png(300, 300)).get_json()["creative"]["image_path"]
    assert first != second
    assert len(_stored(image_dir)) == 1, "the replaced file was orphaned"
    assert client.get(first).status_code == 404


def test_deleting_the_image_leaves_a_text_only_creative(client, admin_token, creative, image_dir):
    url = _upload(client, admin_token, creative, _png()).get_json()["creative"]["image_path"]
    r = client.delete(f"/api/ads/creatives/{creative}/image",
                      headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    assert r.get_json()["image_path"] is None
    assert _stored(image_dir) == []
    assert client.get(url).status_code == 404


# ── What comes out is not what went in ───────────────────────────────────────

def test_a_payload_appended_to_a_valid_image_does_not_survive(
        client, admin_token, creative, image_dir):
    """The classic polyglot: a real PNG with a script tag stapled on.

    Browsers can be talked into treating such a file as HTML. Re-encoding
    from decoded pixels is what removes it, and this test is the reason that
    design decision cannot be quietly reverted for performance.
    """
    poisoned = _png() + b"<script>alert('xss')</script>"
    r = _upload(client, admin_token, creative, poisoned)
    assert r.status_code == 201

    url = r.get_json()["creative"]["image_path"]
    served = client.get(url).data
    assert b"<script>" not in served
    assert b"alert" not in served


def test_exif_is_stripped(client, admin_token, creative, image_dir):
    """A phone photo carries GPS and a device serial. A banner should not."""
    buffer = io.BytesIO()
    image = Image.new("RGB", (400, 200), (10, 10, 10))
    exif = image.getexif()
    exif[271] = "SecretCameraMake"       # Make
    exif[305] = "PrivateSoftware 1.0"    # Software
    image.save(buffer, format="JPEG", exif=exif)

    url = _upload(client, admin_token, creative, buffer.getvalue(),
                  filename="photo.jpg", content_type="image/jpeg"
                  ).get_json()["creative"]["image_path"]

    served = client.get(url).data
    assert b"SecretCameraMake" not in served
    assert b"PrivateSoftware" not in served
    assert not dict(Image.open(io.BytesIO(served)).getexif())


def test_a_file_that_is_not_an_image_is_refused(client, admin_token, creative, image_dir):
    r = _upload(client, admin_token, creative, b"<html><body>not an image</body></html>")
    assert r.status_code == 400
    assert _stored(image_dir) == []


def test_renaming_a_file_does_not_make_it_an_image(client, admin_token, creative, image_dir):
    """Extension and declared content type are both caller-controlled."""
    r = _upload(client, admin_token, creative, b"GIF89a<script>alert(1)</script>",
                filename="innocent.png", content_type="image/png")
    assert r.status_code == 400
    assert _stored(image_dir) == []


def test_an_svg_is_refused(client, admin_token, creative, image_dir):
    """SVG is a document that can carry script, not a bitmap."""
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    r = _upload(client, admin_token, creative, svg,
                filename="logo.svg", content_type="image/svg+xml")
    assert r.status_code == 400


def test_an_empty_upload_is_refused(client, admin_token, creative, image_dir):
    assert _upload(client, admin_token, creative, b"").status_code == 400


def test_a_missing_field_is_refused(client, admin_token, creative, image_dir):
    r = client.post(f"/api/ads/creatives/{creative}/image",
                    data={}, content_type="multipart/form-data",
                    headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 400


# ── Bounds ───────────────────────────────────────────────────────────────────

def test_an_oversized_bitmap_is_refused(client, admin_token, creative, image_dir):
    """Small file, enormous decode — the bomb a byte limit does not catch."""
    r = _upload(client, admin_token, creative, _png(7000, 100))
    assert r.status_code == 400
    assert "longest edge" in r.get_json()["message"].lower()
    assert _stored(image_dir) == []


def test_a_tiny_image_is_refused_with_a_useful_reason(client, admin_token, creative, image_dir):
    r = _upload(client, admin_token, creative, _png(20, 20))
    assert r.status_code == 400
    assert "too small" in r.get_json()["message"].lower()


# ── The caller never chooses a path ──────────────────────────────────────────

def test_the_uploaded_filename_is_discarded(client, admin_token, creative, image_dir):
    url = _upload(client, admin_token, creative, _png(),
                  filename="../../../../etc/passwd.png"
                  ).get_json()["creative"]["image_path"]
    stored = _stored(image_dir)
    assert len(stored) == 1
    assert "passwd" not in stored[0]
    assert ".." not in url
    assert uploads.safe_stored_name(stored[0])


@pytest.mark.parametrize("name", [
    "../../../../etc/passwd", "..%2f..%2fsecret.png", "....//secret.png",
    "app.db", "x.png.exe", "x.svg", "x.php", "", "a" * 200 + ".png",
    "NOTHEX0000000000000000000000000.png",
])
def test_the_serving_route_refuses_anything_it_did_not_generate(client, name, image_dir):
    r = client.get(f"/api/ads/images/{name}")
    assert r.status_code in (301, 308, 404), f"{name!r} returned {r.status_code}"
    if r.status_code == 404:
        assert b"root:" not in r.data


def test_safe_stored_name_accepts_only_the_generated_shape():
    assert uploads.safe_stored_name("0123456789abcdef0123456789abcdef.png")
    assert uploads.safe_stored_name("0123456789abcdef0123456789abcdef.jpg")
    assert not uploads.safe_stored_name("0123456789abcdef0123456789abcdef.gif")
    assert not uploads.safe_stored_name("0123456789abcdef0123456789abcde.png")  # 31
    assert not uploads.safe_stored_name("0123456789abcdef0123456789abcdeZ.png")
    assert not uploads.safe_stored_name("../0123456789abcdef0123456789abcd.png")
    assert not uploads.safe_stored_name("0123456789abcdef0123456789abcdef.png.png")


# ── Authorisation ────────────────────────────────────────────────────────────

def test_upload_requires_an_admin(app, creative, image_dir):
    r = app.test_client().post(f"/api/ads/creatives/{creative}/image",
                               data={}, content_type="multipart/form-data")
    assert r.status_code == 401


def test_a_customer_cannot_upload(client, creative, image_dir):
    from tests.conftest import _login, _register
    email = "image_customer@test.com"
    _register(client, email)
    token = _login(client, email)
    assert _upload(client, token, creative, _png()).status_code == 403


# ── The alt-text rule still governs going live ───────────────────────────────

def test_uploading_does_not_bypass_the_alt_text_requirement(
        client, admin_token, creative, image_dir):
    """Artwork and copy are different sittings; activation needs both."""
    assert _upload(client, admin_token, creative, _png()).status_code == 201
    headers = {"Authorization": f"Bearer {admin_token}"}

    refused = client.post(f"/api/ads/creatives/{creative}/activate", headers=headers)
    assert refused.status_code == 400

    client.put(f"/api/ads/creatives/{creative}",
               json={"image_alt": "Dashain cargo offer banner"}, headers=headers)
    assert client.post(f"/api/ads/creatives/{creative}/activate",
                       headers=headers).status_code == 200
