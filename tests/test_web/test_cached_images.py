"""Cached listing images: the /images route and the local URLs pages use instead of CDN ones."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import HttpUrl

from home_finder.config import Settings
from home_finder.db.storage import PropertyStorage
from home_finder.models import MergedProperty, PropertyImage, PropertySource
from home_finder.utils.image_cache import (
    get_cache_dir,
    safe_dir_name,
    save_image_bytes,
    url_to_filename,
)
from home_finder.web.routes import _resolve_cached_thumbnail


class TestImageRoute:
    """GET /images/{unique_id}/{filename}."""

    def test_serves_cached_image(self, client: TestClient, settings: Settings) -> None:
        unique_id = "openrent:100"
        filename = "gallery_000_abc12345.jpg"
        cache_dir = get_cache_dir(settings.data_dir, unique_id)
        save_image_bytes(cache_dir / filename, b"fake jpeg")

        safe_id = safe_dir_name(unique_id)
        resp = client.get(f"/images/{safe_id}/{filename}")
        assert resp.status_code == 200
        assert resp.content == b"fake jpeg"
        assert "max-age=31536000" in resp.headers["cache-control"]

    def test_returns_404_for_missing_image(self, client: TestClient) -> None:
        resp = client.get("/images/openrent_100/nonexistent.jpg")
        assert resp.status_code == 404

    @pytest.mark.parametrize(
        ("path", "error"),
        [
            ("/images/openrent_100/..%5C..%5Csecret.jpg", "invalid filename"),
            ("/images/openrent_100/..secret.jpg", "invalid filename"),
            ("/images/..%5Cother/secret.jpg", "invalid unique_id"),
        ],
    )
    def test_rejects_path_segments_in_either_name(
        self, client: TestClient, path: str, error: str
    ) -> None:
        """The route refuses '..' and separators before it touches the disk.

        httpx removes '/../' segments from a URL before sending it, so a traversal
        written with forward slashes never reaches this route; '..' and an encoded
        backslash inside one segment do.
        """
        resp = client.get(path)
        assert resp.status_code == 400
        assert resp.json() == {"error": error}


class TestCardThumbnail:
    """_resolve_cached_thumbnail(): the local image a dashboard card or map marker shows."""

    def test_thumbnail_skips_epc_files(self, tmp_path: Path) -> None:
        """Fallback should skip epc_* files and pick the first gallery_* file."""
        unique_id = "zoopla:55555"
        data_dir = str(tmp_path)

        cache_dir = get_cache_dir(data_dir, unique_id)
        # Create an epc file (alphabetically first) and a gallery file
        save_image_bytes(cache_dir / "epc_000_aaa11111.jpg", b"fake")
        save_image_bytes(cache_dir / "gallery_001_bbb22222.jpg", b"fake")

        result = _resolve_cached_thumbnail(unique_id, None, data_dir)
        assert result == "/images/zoopla_55555/gallery_001_bbb22222.jpg"

    def test_thumbnail_fast_path_finds_gallery_by_hash(self, tmp_path: Path) -> None:
        """Hash-based lookup should find a gallery file regardless of index."""
        unique_id = "rightmove:66666"
        data_dir = str(tmp_path)
        img_url = "https://cdn.example.com/photo.jpg"

        # Save with index 7 (not 0)
        fname = url_to_filename(img_url, "gallery", 7)
        cache_dir = get_cache_dir(data_dir, unique_id)
        save_image_bytes(cache_dir / fname, b"fake")

        result = _resolve_cached_thumbnail(unique_id, img_url, data_dir)
        assert result == f"/images/rightmove_66666/{fname}"

    def test_prefers_thumb_file_fast_path(self, tmp_path: Path) -> None:
        """When a thumb_ file exists, the hash-based path should prefer it."""
        unique_id = "rightmove:77777"
        data_dir = str(tmp_path)
        img_url = "https://cdn.example.com/photo.jpg"

        fname = url_to_filename(img_url, "gallery", 0)
        thumb_name = f"thumb_{Path(fname).stem}.jpg"
        cache_dir = get_cache_dir(data_dir, unique_id)
        save_image_bytes(cache_dir / fname, b"fake")
        save_image_bytes(cache_dir / thumb_name, b"fake")

        result = _resolve_cached_thumbnail(unique_id, img_url, data_dir)
        assert result == f"/images/rightmove_77777/{thumb_name}"

    def test_prefers_thumb_file_fallback_path(self, tmp_path: Path) -> None:
        """When a thumb_ file exists, the fallback path should prefer it."""
        unique_id = "openrent:88888"
        data_dir = str(tmp_path)

        cache_dir = get_cache_dir(data_dir, unique_id)
        save_image_bytes(cache_dir / "gallery_000_aaa11111.jpg", b"fake")
        save_image_bytes(cache_dir / "thumb_gallery_000_aaa11111.jpg", b"fake")

        result = _resolve_cached_thumbnail(unique_id, None, data_dir)
        assert result == "/images/openrent_88888/thumb_gallery_000_aaa11111.jpg"

    def test_falls_back_to_original_without_thumb(self, tmp_path: Path) -> None:
        """When no thumb_ file exists, should serve the original."""
        unique_id = "openrent:99999"
        data_dir = str(tmp_path)

        cache_dir = get_cache_dir(data_dir, unique_id)
        save_image_bytes(cache_dir / "gallery_000_aaa11111.jpg", b"fake")

        result = _resolve_cached_thumbnail(unique_id, None, data_dir)
        assert result == "/images/openrent_99999/gallery_000_aaa11111.jpg"


async def test_detail_page_maps_cdn_urls_to_cached_files_whatever_their_index(
    client: TestClient, storage: PropertyStorage, settings: Settings, merged_a: MergedProperty
) -> None:
    """The detail page's image_url_map finds cached files by URL hash, not position.

    After merging, inherited images may have different indices than the
    enumerate() position. The URL-hash lookup must still find them.
    """
    img_url = "https://cdn.example.com/gallery/photo1.jpg"
    await storage.save_merged_property(merged_a)
    await storage.save_property_images(
        merged_a.unique_id,
        [PropertyImage(source=PropertySource.OPENRENT, url=HttpUrl(img_url), image_type="gallery")],
    )

    # Save cached file with index 7 (not 0 as enumerate would produce)
    fname = url_to_filename(img_url, "gallery", 7)
    save_image_bytes(get_cache_dir(settings.data_dir, merged_a.unique_id) / fname, b"fake")

    resp = client.get(f"/property/{merged_a.unique_id}")
    assert resp.status_code == 200
    assert f"/images/{safe_dir_name(merged_a.unique_id)}/{fname}" in resp.text
