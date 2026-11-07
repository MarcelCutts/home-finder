"""Construction evidence reaches the actual evaluation request as acoustic context."""

import json

import httpx2
import pytest

from home_finder.analysis.analyzer import PropertyQualityAnalyzer
from tests.support.analysis_responses import PHASE2_EVALUATION_RESPONSE

pytestmark = pytest.mark.usefixtures("native_analysis_spending")


@pytest.mark.parametrize(
    "flooring",
    [
        None,
        "historical unstructured evidence",
    ],
    ids=["missing", "unstructured"],
)
async def test_missing_or_unstructured_flooring_omits_acoustic_context(flooring):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=PHASE2_EVALUATION_RESPONSE)

    native = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    async with PropertyQualityAnalyzer("fixture", http_client=native) as analyzer:
        await analyzer._run_evaluation(
            {} if flooring is None else {"flooring_noise": flooring},
            "fixture:acoustics",
            description=None,
            price_pcm=1800,
            bedrooms=1,
            effective_average=1900,
            area_context=None,
            outcode="E8",
            council_tax_band_c=None,
            crime_summary=None,
            rent_trend=None,
            energy_estimate=None,
            hosting_tolerance=None,
        )

    assert len(requests) == 1
    prompt = requests[0]["messages"][0]["content"]
    assert "<acoustic_context>" not in prompt
    assert native.is_closed
