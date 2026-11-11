"""Dashboard cards show a floor-area badge only when the area is known."""

from pydantic import HttpUrl

from tests.support.web import web_client


def _card_home(make_merged_property, **area):
    # The dashboard lists only homes with an image.
    return make_merged_property(image_url=HttpUrl("https://example.com/img.jpg"), **area)


async def test_card_shows_listed_floor_area(storage, make_merged_property, tmp_path):
    home = _card_home(make_merged_property, floor_area_sqm=60.0, floor_area_source="zoopla")
    await storage.save_merged_property(home)

    page = web_client(storage, tmp_path).get("/")

    assert page.status_code == 200
    assert '<span class="card-area">' in page.text
    assert "60 m²" in page.text


async def test_card_without_floor_area_has_no_badge(storage, make_merged_property, tmp_path):
    home = _card_home(make_merged_property)
    await storage.save_merged_property(home)

    page = web_client(storage, tmp_path).get("/")

    assert page.status_code == 200
    assert home.canonical.title in page.text
    assert "card-area" not in page.text
