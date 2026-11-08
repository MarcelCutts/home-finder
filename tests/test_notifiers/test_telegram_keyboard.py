"""The inline keyboard sent with each discovery alert: status buttons, links and map."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import HttpUrl

from home_finder.models import MergedProperty, Property, PropertySource
from home_finder.notifiers.telegram import TelegramNotifier, _build_inline_keyboard


def _buttons(keyboard):
    return [button for row in keyboard.inline_keyboard for button in row]


class TestStatusButtons:
    def test_first_row_marks_interested_or_skips(
        self, notification_merged_property: MergedProperty
    ) -> None:
        uid = notification_merged_property.unique_id
        first_row = _build_inline_keyboard(notification_merged_property).inline_keyboard[0]
        assert [button.text for button in first_row] == ["\U0001f44d Interested", "⏭ Skip"]
        # "Skip" sets the archived status, which the dashboard calls "Archive".
        assert [button.callback_data for button in first_row] == [
            f"st:{uid}:interested",
            f"st:{uid}:archived",
        ]

    @pytest.mark.parametrize(
        ("id_length", "has_status_row"),
        # "st:openrent:" + id + ":interested" is 64 bytes for a 41-character id.
        [(41, True), (42, False)],
    )
    def test_status_row_needs_callbacks_within_telegrams_64_bytes(
        self, notification_merged_property: MergedProperty, id_length: int, has_status_row: bool
    ) -> None:
        canonical = notification_merged_property.canonical.model_copy(
            update={"source_id": "1" * id_length}
        )
        merged = notification_merged_property.model_copy(update={"canonical": canonical})
        callbacks = [b.callback_data for b in _buttons(_build_inline_keyboard(merged))]
        status_callbacks = [data for data in callbacks if data is not None]
        assert bool(status_callbacks) is has_status_row
        assert all(len(data.encode("utf-8")) <= 64 for data in status_callbacks)

    @pytest.mark.parametrize("source_id", ["1" * 50, "é" * 25])
    def test_long_ids_keep_links_but_never_send_truncated_callbacks(
        self,
        notification_merged_property: MergedProperty,
        source_id: str,
    ) -> None:
        prop = notification_merged_property.canonical.model_copy(update={"source_id": source_id})
        merged = notification_merged_property.model_copy(update={"canonical": prop})
        kb = _build_inline_keyboard(merged, web_base_url="https://owner.example")
        buttons = _buttons(kb)
        assert buttons
        assert all(button.callback_data is None for button in buttons)
        assert any(button.text == "Details" for button in buttons)

    def test_links_follow_the_status_row(
        self, notification_merged_property: MergedProperty
    ) -> None:
        rows = _build_inline_keyboard(notification_merged_property).inline_keyboard
        assert [button.text for button in rows[1]] == ["OpenRent", "Zoopla"]
        assert all(button.url and not button.callback_data for row in rows[1:] for button in row)


class TestLinkButtons:
    @pytest.mark.parametrize(
        ("web_base_url", "origin"),
        [
            ("https://home-finder.fly.dev", "https://home-finder.fly.dev"),
            ("http://localhost:8000", "http://localhost:8000"),
            ("https://home-finder.fly.dev/", "https://home-finder.fly.dev"),
        ],
    )
    def test_details_is_a_plain_link_to_the_detail_page(
        self, notification_merged_property: MergedProperty, web_base_url: str, origin: str
    ) -> None:
        rows = _build_inline_keyboard(
            notification_merged_property, web_base_url=web_base_url
        ).inline_keyboard
        details = next(button for row in rows[1:] for button in row if button.text == "Details")
        assert details.web_app is None
        assert details.url == f"{origin}/property/{notification_merged_property.unique_id}"

    def test_no_details_button_without_web_base_url(
        self, notification_merged_property: MergedProperty
    ) -> None:
        keyboard = _build_inline_keyboard(notification_merged_property, web_base_url="")
        assert "Details" not in [button.text for button in _buttons(keyboard)]

    def test_every_source_keeps_its_link_beside_details(
        self, notification_merged_property: MergedProperty
    ) -> None:
        keyboard = _build_inline_keyboard(
            notification_merged_property, web_base_url="https://home-finder.fly.dev"
        )
        links = {button.text: button.url for button in _buttons(keyboard)}
        assert links["OpenRent"] == "https://openrent.com/property/12345"
        assert links["Zoopla"] == "https://www.zoopla.co.uk/to-rent/details/99999"

    def test_map_button_uses_coordinates(
        self, notification_merged_property: MergedProperty
    ) -> None:
        keyboard = _build_inline_keyboard(notification_merged_property)
        map_buttons = [button for button in _buttons(keyboard) if "Map" in button.text]
        assert [button.url for button in map_buttons] == [
            "https://www.google.com/maps?q=51.5465,-0.0553"
        ]

    def test_map_button_searches_the_address_without_coordinates(self) -> None:
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="999",
            url=HttpUrl("https://openrent.com/999"),
            title="Test",
            price_pcm=1500,
            bedrooms=1,
            address="No coords place",
        )
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop.url},
            min_price=1500,
            max_price=1500,
        )
        keyboard = _build_inline_keyboard(merged)
        map_buttons = [button for button in _buttons(keyboard) if "Map" in button.text]
        assert [button.url for button in map_buttons] == [
            "https://www.google.com/maps/search/?api=1&query=No%20coords%20place"
        ]


async def test_sent_photo_carries_the_details_link(
    notification_merged_property: MergedProperty,
) -> None:
    """The notifier passes its dashboard URL to the keyboard attached to the photo."""
    notifier = TelegramNotifier(
        bot_token="123:ABC",
        chat_id=12345,
        web_base_url="https://home-finder.fly.dev",
    )
    mock_bot = AsyncMock()
    mock_bot.send_photo = AsyncMock(return_value=MagicMock(message_id=1))
    mock_bot.send_venue = AsyncMock(return_value=MagicMock(message_id=2))

    with patch.object(notifier, "_get_bot", return_value=mock_bot):
        assert await notifier.send_merged_property_notification(notification_merged_property)

    mock_bot.send_photo.assert_called_once()
    keyboard = mock_bot.send_photo.call_args.kwargs["reply_markup"]
    details = next(button for button in _buttons(keyboard) if button.text == "Details")
    assert details.url == (
        f"https://home-finder.fly.dev/property/{notification_merged_property.unique_id}"
    )
