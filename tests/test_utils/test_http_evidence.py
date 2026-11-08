"""Recognising a Cloudflare challenge page instead of the requested listing."""

import pytest

from home_finder.utils.http_evidence import is_challenge


class TestChallengeDetection:
    @pytest.mark.parametrize("value", ["challenge", "Challenge"])
    def test_cf_mitigated_challenge_header_marks_a_clean_page(self, value):
        assert is_challenge("<html>Normal page</html>", headers={"cf-mitigated": value})

    @pytest.mark.parametrize("value", ["captcha", "skipped"])
    def test_other_cf_mitigated_values_are_not_challenges(self, value):
        """Only 'challenge' is a documented cf-mitigated value; other values are ignored."""
        assert not is_challenge("<html>Normal page</html>", headers={"cf-mitigated": value})

    def test_challenge_body_marker_is_detected_without_a_header(self):
        assert is_challenge("Just a moment... Cloudflare Ray ID: abc")
