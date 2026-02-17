"""Tests for image cache utilities."""

from pathlib import Path

from home_finder.utils.image_cache import (
    clear_image_cache,
    copy_cached_images,
    find_cached_file,
    get_cache_dir,
    get_cached_image_path,
    is_property_cached,
    is_valid_image_url,
    read_image_bytes,
    safe_dir_name,
    save_image_bytes,
    url_to_filename,
)


class TestSafeDirName:
    def test_colon_replaced(self) -> None:
        assert safe_dir_name("openrent:12345") == "openrent_12345"

    def test_no_special_chars(self) -> None:
        assert safe_dir_name("simple_name") == "simple_name"

    def test_multiple_special_chars(self) -> None:
        assert safe_dir_name("a:b/c\\d") == "a_b_c_d"


class TestGetCacheDir:
    def test_returns_expected_path(self) -> None:
        result = get_cache_dir("/data", "openrent:12345")
        assert result == Path("/data/image_cache/openrent_12345")


class TestUrlToFilename:
    def test_gallery_with_jpg(self) -> None:
        name = url_to_filename("https://example.com/img.jpg", "gallery", 3)
        assert name.startswith("gallery_003_")
        assert name.endswith(".jpg")

    def test_floorplan_with_png(self) -> None:
        name = url_to_filename("https://example.com/floor.png", "floorplan", 0)
        assert name.startswith("floorplan_000_")
        assert name.endswith(".png")

    def test_webp_extension(self) -> None:
        name = url_to_filename("https://example.com/photo.webp", "gallery", 1)
        assert name.endswith(".webp")

    def test_no_extension_defaults_to_jpg(self) -> None:
        name = url_to_filename("https://example.com/image", "gallery", 0)
        assert name.endswith(".jpg")

    def test_deterministic(self) -> None:
        url = "https://example.com/img.jpg"
        assert url_to_filename(url, "gallery", 0) == url_to_filename(url, "gallery", 0)

    def test_different_urls_different_names(self) -> None:
        name1 = url_to_filename("https://example.com/a.jpg", "gallery", 0)
        name2 = url_to_filename("https://example.com/b.jpg", "gallery", 0)
        assert name1 != name2

    def test_query_params_ignored_for_extension(self) -> None:
        name = url_to_filename("https://example.com/img.png?w=100", "gallery", 0)
        assert name.endswith(".png")


class TestIsPropertyCached:
    def test_false_when_no_dir(self, tmp_path: Path) -> None:
        assert not is_property_cached(str(tmp_path), "openrent:999")

    def test_false_when_dir_empty(self, tmp_path: Path) -> None:
        cache_dir = get_cache_dir(str(tmp_path), "openrent:999")
        cache_dir.mkdir(parents=True)
        assert not is_property_cached(str(tmp_path), "openrent:999")

    def test_true_when_files_present(self, tmp_path: Path) -> None:
        cache_dir = get_cache_dir(str(tmp_path), "openrent:999")
        cache_dir.mkdir(parents=True)
        (cache_dir / "gallery_000_abc12345.jpg").write_bytes(b"fake image")
        assert is_property_cached(str(tmp_path), "openrent:999")


class TestSaveAndReadImageBytes:
    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "image.jpg"
        data = b"\xff\xd8\xff\xe0fake jpeg data"
        save_image_bytes(path, data)
        assert read_image_bytes(path) == data

    def test_read_nonexistent_returns_none(self, tmp_path: Path) -> None:
        assert read_image_bytes(tmp_path / "missing.jpg") is None


class TestClearImageCache:
    def test_removes_cache_directory(self, tmp_path: Path) -> None:
        cache_dir = get_cache_dir(str(tmp_path), "zoopla:123")
        cache_dir.mkdir(parents=True)
        (cache_dir / "gallery_000_abc12345.jpg").write_bytes(b"fake")
        assert is_property_cached(str(tmp_path), "zoopla:123")

        clear_image_cache(str(tmp_path), "zoopla:123")
        assert not is_property_cached(str(tmp_path), "zoopla:123")
        assert not cache_dir.exists()

    def test_noop_when_no_cache(self, tmp_path: Path) -> None:
        """Should not raise when cache directory doesn't exist."""
        clear_image_cache(str(tmp_path), "nonexistent:999")


class TestGetCachedImagePath:
    def test_returns_expected_path(self) -> None:
        path = get_cached_image_path("/data", "zoopla:xyz", "https://cdn.com/img.jpg", "gallery", 2)
        assert path.parent == Path("/data/image_cache/zoopla_xyz")
        assert path.name.startswith("gallery_002_")
        assert path.name.endswith(".jpg")


class TestFindCachedFile:
    def test_finds_file_by_url_hash(self, tmp_path: Path) -> None:
        """Should find a cached file regardless of the index in the filename."""
        url = "https://example.com/photo.jpg"
        uid = "openrent:100"
        # Save with index 3
        fname = url_to_filename(url, "gallery", 3)
        cache_dir = get_cache_dir(str(tmp_path), uid)
        cache_dir.mkdir(parents=True)
        (cache_dir / fname).write_bytes(b"image data")

        # find_cached_file should locate it without knowing the index
        result = find_cached_file(str(tmp_path), uid, url, "gallery")
        assert result is not None
        assert result.name == fname

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        result = find_cached_file(
            str(tmp_path), "openrent:999", "https://example.com/x.jpg", "gallery"
        )
        assert result is None

    def test_returns_none_when_dir_exists_but_no_match(self, tmp_path: Path) -> None:
        uid = "openrent:100"
        cache_dir = get_cache_dir(str(tmp_path), uid)
        cache_dir.mkdir(parents=True)
        (cache_dir / "gallery_000_ffffffff.jpg").write_bytes(b"other")

        result = find_cached_file(
            str(tmp_path), uid, "https://example.com/different.jpg", "gallery"
        )
        assert result is None

    def test_matches_correct_image_type(self, tmp_path: Path) -> None:
        """Should not match a floorplan file when looking for gallery."""
        url = "https://example.com/photo.jpg"
        uid = "openrent:100"
        # Save as floorplan
        fname = url_to_filename(url, "floorplan", 0)
        cache_dir = get_cache_dir(str(tmp_path), uid)
        cache_dir.mkdir(parents=True)
        (cache_dir / fname).write_bytes(b"data")

        # Looking for gallery should not find it
        result = find_cached_file(str(tmp_path), uid, url, "gallery")
        assert result is None

        # Looking for floorplan should find it
        result = find_cached_file(str(tmp_path), uid, url, "floorplan")
        assert result is not None


class TestCopyCachedImages:
    def test_copies_files(self, tmp_path: Path) -> None:
        src_id = "openrent:100"
        dst_id = "rightmove:200"
        src_dir = get_cache_dir(str(tmp_path), src_id)
        src_dir.mkdir(parents=True)
        (src_dir / "gallery_000_aaa.jpg").write_bytes(b"img1")
        (src_dir / "gallery_001_bbb.jpg").write_bytes(b"img2")

        copied = copy_cached_images(str(tmp_path), src_id, dst_id)
        assert copied == 2

        dst_dir = get_cache_dir(str(tmp_path), dst_id)
        assert (dst_dir / "gallery_000_aaa.jpg").read_bytes() == b"img1"
        assert (dst_dir / "gallery_001_bbb.jpg").read_bytes() == b"img2"

    def test_skips_existing_files(self, tmp_path: Path) -> None:
        src_id = "openrent:100"
        dst_id = "rightmove:200"
        src_dir = get_cache_dir(str(tmp_path), src_id)
        dst_dir = get_cache_dir(str(tmp_path), dst_id)
        src_dir.mkdir(parents=True)
        dst_dir.mkdir(parents=True)

        (src_dir / "gallery_000_aaa.jpg").write_bytes(b"new data")
        (dst_dir / "gallery_000_aaa.jpg").write_bytes(b"existing data")

        copied = copy_cached_images(str(tmp_path), src_id, dst_id)
        assert copied == 0
        # Existing file should not be overwritten
        assert (dst_dir / "gallery_000_aaa.jpg").read_bytes() == b"existing data"

    def test_empty_source_dir(self, tmp_path: Path) -> None:
        copied = copy_cached_images(str(tmp_path), "nonexistent:999", "rightmove:200")
        assert copied == 0


class TestIsValidImageUrl:
    def test_extensionless_url_allowed(self) -> None:
        """CDN URL without extension -> True."""
        assert is_valid_image_url("https://lc.zoocdn.com/u/floor/abc123") is True

    def test_pdf_rejected(self) -> None:
        """.pdf -> False."""
        assert is_valid_image_url("https://example.com/floorplan.pdf") is False

    def test_svg_rejected(self) -> None:
        """.svg -> False."""
        assert is_valid_image_url("https://example.com/icon.svg") is False

    def test_jpg_allowed(self) -> None:
        """.jpg -> True (regression)."""
        assert is_valid_image_url("https://example.com/photo.jpg") is True

    def test_query_params_ignored(self) -> None:
        """Extension check strips query params."""
        assert is_valid_image_url("https://example.com/img.pdf?v=1") is False
        assert is_valid_image_url("https://example.com/img.jpg?v=1") is True

    def test_non_image_extensions_rejected(self) -> None:
        """Non-image web extensions -> False."""
        assert is_valid_image_url("https://example.com/page.html") is False
        assert is_valid_image_url("https://example.com/script.js") is False
        assert is_valid_image_url("https://example.com/style.css") is False
        assert is_valid_image_url("https://example.com/data.json") is False
        assert is_valid_image_url("https://example.com/feed.xml") is False
