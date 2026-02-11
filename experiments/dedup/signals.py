"""Signal computation functions for property pair comparison.

Each signal function takes two property dicts (from snapshot JSON) and returns
a float in [0, 1] representing match strength, or None if the signal can't fire.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

import numpy as np
from rapidfuzz import fuzz

from home_finder.filters.deduplication import (
    COORDINATE_DISTANCE_METERS,
    FULL_POSTCODE_PATTERN,
    PRICE_TOLERANCE,
    haversine_distance,
)
from home_finder.utils.address import extract_outcode, normalize_street_name

# --------------------------------------------------------------------------- #
# Types
# --------------------------------------------------------------------------- #

PropertyDict = dict  # A property record from a snapshot JSON


@dataclass
class SignalResult:
    """Result of computing one signal between two properties."""

    name: str
    fired: bool  # Whether the signal produced a meaningful comparison
    value: float  # 0.0 = no match, 1.0 = perfect match
    detail: str = ""  # Human-readable explanation


@dataclass
class SignalBundle:
    """All signal results for a property pair."""

    signals: list[SignalResult] = field(default_factory=list)

    def get(self, name: str) -> SignalResult | None:
        for s in self.signals:
            if s.name == name:
                return s
        return None

    def to_dict(self) -> dict[str, dict]:
        return {
            s.name: {"fired": s.fired, "value": s.value, "detail": s.detail} for s in self.signals
        }


# --------------------------------------------------------------------------- #
# Helper: get nested detail fields
# --------------------------------------------------------------------------- #


def _get_description(prop: PropertyDict) -> str | None:
    """Get description from either top-level or detail sub-dict."""
    if prop.get("description"):
        return prop["description"]
    detail = prop.get("detail", {})
    if detail and detail.get("description"):
        return detail["description"]
    return None


def _get_features(prop: PropertyDict) -> list[str]:
    """Get features list from detail sub-dict."""
    detail = prop.get("detail", {})
    if detail and detail.get("features"):
        return detail["features"]
    return []


def _get_gallery_urls(prop: PropertyDict) -> list[str]:
    """Get gallery URLs from detail sub-dict."""
    detail = prop.get("detail", {})
    if detail and detail.get("gallery_urls"):
        return detail["gallery_urls"]
    return []


# --------------------------------------------------------------------------- #
# Existing signals (mirrors production code)
# --------------------------------------------------------------------------- #


def signal_full_postcode(a: PropertyDict, b: PropertyDict) -> SignalResult:
    """Full postcode exact match (e.g. 'E8 3RH')."""
    pc_a = a.get("postcode")
    pc_b = b.get("postcode")

    if not pc_a or not pc_b:
        return SignalResult("full_postcode", fired=False, value=0.0, detail="missing postcode")

    norm_a = " ".join(pc_a.upper().split())
    norm_b = " ".join(pc_b.upper().split())

    is_full_a = bool(FULL_POSTCODE_PATTERN.match(norm_a))
    is_full_b = bool(FULL_POSTCODE_PATTERN.match(norm_b))

    if not is_full_a or not is_full_b:
        return SignalResult(
            "full_postcode", fired=False, value=0.0, detail=f"partial: {norm_a} / {norm_b}"
        )

    match = norm_a == norm_b
    return SignalResult(
        "full_postcode",
        fired=True,
        value=1.0 if match else 0.0,
        detail=f"{norm_a} vs {norm_b}",
    )


def signal_outcode(a: PropertyDict, b: PropertyDict) -> SignalResult:
    """Outcode match (e.g. 'E8')."""
    out_a = extract_outcode(a.get("postcode"))
    out_b = extract_outcode(b.get("postcode"))

    if not out_a or not out_b:
        return SignalResult("outcode", fired=False, value=0.0, detail="missing outcode")

    match = out_a == out_b
    return SignalResult(
        "outcode", fired=True, value=1.0 if match else 0.0, detail=f"{out_a} vs {out_b}"
    )


def signal_coordinates(
    a: PropertyDict, b: PropertyDict, max_meters: float = COORDINATE_DISTANCE_METERS
) -> SignalResult:
    """Coordinate proximity (haversine distance)."""
    lat_a, lon_a = a.get("latitude"), a.get("longitude")
    lat_b, lon_b = b.get("latitude"), b.get("longitude")

    if lat_a is None or lon_a is None or lat_b is None or lon_b is None:
        return SignalResult("coordinates", fired=False, value=0.0, detail="missing coordinates")

    distance = haversine_distance(lat_a, lon_a, lat_b, lon_b)
    match = distance <= max_meters

    # Graduated value: 1.0 at 0m, 0.5 at max_meters, 0.0 at 2*max_meters
    if distance <= max_meters:
        value = 1.0 - (distance / max_meters) * 0.5
    elif distance <= max_meters * 2:
        value = 0.5 - ((distance - max_meters) / max_meters) * 0.5
    else:
        value = 0.0

    return SignalResult(
        "coordinates",
        fired=True,
        value=value,
        detail=f"{distance:.0f}m (threshold {max_meters}m)",
    )


def signal_street_name(a: PropertyDict, b: PropertyDict) -> SignalResult:
    """Normalized street name exact match."""
    street_a = normalize_street_name(a.get("address", ""))
    street_b = normalize_street_name(b.get("address", ""))

    if not street_a or not street_b:
        return SignalResult("street_name", fired=False, value=0.0, detail="no street extracted")

    match = street_a == street_b
    return SignalResult(
        "street_name",
        fired=True,
        value=1.0 if match else 0.0,
        detail=f"'{street_a}' vs '{street_b}'",
    )


def signal_price(
    a: PropertyDict, b: PropertyDict, tolerance: float = PRICE_TOLERANCE
) -> SignalResult:
    """Price proximity (symmetric percentage difference)."""
    price_a = a.get("price_pcm", 0)
    price_b = b.get("price_pcm", 0)

    if price_a == 0 or price_b == 0:
        return SignalResult("price", fired=False, value=0.0, detail="zero price")

    if price_a == price_b:
        return SignalResult("price", fired=True, value=1.0, detail=f"exact: £{price_a}")

    diff = abs(price_a - price_b)
    avg = (price_a + price_b) / 2
    pct = diff / avg

    # Graduated: 1.0 at exact match, 0.5 at tolerance, 0.0 at 2*tolerance
    if pct <= tolerance:
        value = 1.0 - (pct / tolerance) * 0.5
    elif pct <= tolerance * 2:
        value = 0.5 - ((pct - tolerance) / tolerance) * 0.5
    else:
        value = 0.0

    return SignalResult(
        "price",
        fired=True,
        value=value,
        detail=f"£{price_a} vs £{price_b} ({pct:.1%} diff)",
    )


# --------------------------------------------------------------------------- #
# New signals
# --------------------------------------------------------------------------- #


def signal_fuzzy_address(a: PropertyDict, b: PropertyDict) -> SignalResult:
    """Fuzzy address match using rapidfuzz token_sort_ratio."""
    addr_a = a.get("address", "").strip()
    addr_b = b.get("address", "").strip()

    if not addr_a or not addr_b:
        return SignalResult("fuzzy_address", fired=False, value=0.0, detail="missing address")

    # Normalize: lowercase, remove postcodes
    def clean(addr: str) -> str:
        addr = addr.lower()
        addr = re.sub(r"[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d?[A-Z]{0,2}", "", addr, flags=re.IGNORECASE)
        addr = " ".join(addr.split())
        return addr

    clean_a = clean(addr_a)
    clean_b = clean(addr_b)

    ratio = fuzz.token_sort_ratio(clean_a, clean_b) / 100.0

    return SignalResult(
        "fuzzy_address",
        fired=True,
        value=ratio,
        detail=f"'{clean_a}' vs '{clean_b}' → {ratio:.2f}",
    )


def signal_address_number(a: PropertyDict, b: PropertyDict) -> SignalResult:
    """Extract house/flat numbers and compare.

    Same number = positive signal. Different number = anti-signal
    (different flats in same building).
    """

    def extract_numbers(address: str) -> set[str]:
        """Extract flat/house numbers from address."""
        addr = address.lower()
        numbers = set()
        # "Flat 2" / "Unit 3a" / "Apt 4"
        for m in re.finditer(r"(?:flat|unit|apt|apartment)\s*(\d+[a-z]?)", addr):
            numbers.add(m.group(1))
        # Leading house number "123 Mare Street"
        m = re.match(r"(\d+[a-z]?)\s+\w", addr)
        if m:
            numbers.add(m.group(1))
        return numbers

    nums_a = extract_numbers(a.get("address", ""))
    nums_b = extract_numbers(b.get("address", ""))

    if not nums_a or not nums_b:
        return SignalResult("address_number", fired=False, value=0.0, detail="no numbers found")

    overlap = nums_a & nums_b
    if overlap:
        return SignalResult(
            "address_number",
            fired=True,
            value=1.0,
            detail=f"shared: {overlap}",
        )

    # Different numbers → anti-signal
    return SignalResult(
        "address_number",
        fired=True,
        value=-1.0,
        detail=f"different: {nums_a} vs {nums_b}",
    )


def signal_title_similarity(a: PropertyDict, b: PropertyDict) -> SignalResult:
    """Title similarity after stripping boilerplate."""
    title_a = a.get("title", "")
    title_b = b.get("title", "")

    if not title_a or not title_b:
        return SignalResult("title_similarity", fired=False, value=0.0, detail="missing title")

    def clean_title(t: str) -> str:
        t = t.lower()
        # Remove common boilerplate
        t = re.sub(
            r"\b(to rent|to let|per month|pcm|bedroom|bed|flat|apartment|house|property)\b",
            "",
            t,
        )
        # Remove bedroom counts like "2 bed"
        t = re.sub(r"\d+\s*(bed|bedroom)s?", "", t)
        # Remove prices
        t = re.sub(r"£[\d,]+", "", t)
        t = " ".join(t.split())
        return t

    clean_a = clean_title(title_a)
    clean_b = clean_title(title_b)

    if not clean_a or not clean_b or len(clean_a) < 4 or len(clean_b) < 4:
        return SignalResult(
            "title_similarity",
            fired=False,
            value=0.0,
            detail=f"too short after cleanup: '{clean_a}' / '{clean_b}'",
        )

    ratio = fuzz.token_set_ratio(clean_a, clean_b) / 100.0

    return SignalResult(
        "title_similarity",
        fired=True,
        value=ratio,
        detail=f"'{clean_a}' vs '{clean_b}' → {ratio:.2f}",
    )


# --------------------------------------------------------------------------- #
# Description cleaning helpers
# --------------------------------------------------------------------------- #

# Regex patterns compiled once at module level
_RE_HTML_TAGS = re.compile(r"<[^>]+>")
_RE_PROPERTY_REF = re.compile(r"property\s*reference[:\s]*\S+\.?\s*", re.IGNORECASE)
_RE_OPENRENT_OPENER = re.compile(
    r"we\s+are\s+proud\s+to\s+offer\s+this\s+delightful\s+.*?(?:property|home|flat|apartment|house|room)[\.\s]*",
    re.IGNORECASE,
)
_RE_OPENRENT_VIEWING = re.compile(
    r"viewing\s+highly\s+recommended\.?\s*contact\s+openrent\s+today.*?(?:viewing!?|\.)\s*",
    re.IGNORECASE | re.DOTALL,
)
_RE_OPENRENT_SUMMARY = re.compile(
    r"summary\s+rent\s+£[\d,]+.*$",
    re.IGNORECASE | re.DOTALL,
)
_RE_PHOTOS_TO_FOLLOW = re.compile(r"photos?\s+to\s+follow\s+shortly\.?\s*", re.IGNORECASE)
_RE_AGENT_MARKETING = re.compile(
    r"(?:^|\n)\s*(?:ABOUT\s+THE\s+(?:BUILDING|AREA|DEVELOPMENT|COMMUNITY|NEIGHBOURHOOD)|"
    r"ABOUT\s+(?:COMMUNITY\s+LIFE|WAY\s+OF\s+LIFE)|"
    r"about\s+the\s+(?:building|area|development|community|neighbourhood)|"
    r"about\s+(?:community\s+life|way\s+of\s+life))"
    r".*$",
    re.IGNORECASE | re.DOTALL,
)
_RE_SOCIAL_MEDIA = re.compile(
    r"follow\s+us\s+on\s+(?:instagram|twitter|facebook)\S*\.?\s*", re.IGNORECASE
)
_RE_ILLUSTRATIVE = re.compile(
    r"all\s+images\s+are\s+for\s+illustrative\s+purposes\s+only\.?\s*", re.IGNORECASE
)
_RE_DEPOSIT_HOLDING = re.compile(
    r"(?:holding\s+)?deposit[:\s]*(?:equivalent\s+to\s+)?\d+\s*weeks?['\u2019]?\s*(?:rent)?[:\s]*£?[\d,]+\.?\d*\s*",
    re.IGNORECASE,
)
_RE_COUNCIL_TAX = re.compile(r"council\s*tax\s*(?:band)?[:\s]*[a-g]\b", re.IGNORECASE)
_RE_EPC = re.compile(r"epc\s*(?:rating)?[:\s]*[a-g]\b", re.IGNORECASE)
_RE_CMP = re.compile(
    r"client\s+money\s+protection\s*\(CMP\)\s*(?:provided\s+by)?[:\s]*\S*\.?\s*", re.IGNORECASE
)
_RE_OMBUDSMAN = re.compile(
    r"(?:the\s+)?property\s+ombudsman\s*(?:scheme)?[,:\s]*(?:membership\s*(?:no)?[:\s]*\S+)?\.?\s*",
    re.IGNORECASE,
)
_RE_AGENT_DISCLAIMER = re.compile(
    r"(?:whilst|while)\s+\S+\s+uses?\s+reasonable\s+endeavours.*?(?:\n\n|$)",
    re.IGNORECASE | re.DOTALL,
)
_RE_PARTICULARS = re.compile(
    r"these\s+particulars\s+are\s+intended\s+to\s+give\s+a\s+fair\s+description.*?(?:\n\n|$)",
    re.IGNORECASE | re.DOTALL,
)
_RE_CHINA_DESK = re.compile(r"our\s+china\s+desk\s+is\s+here\s+for\s+you[^.]*\.?\s*", re.IGNORECASE)
_RE_PRICE_PCM = re.compile(
    r"£[\d,]+(?:\.\d{1,2})?\s*(?:per\s+(?:month|week|annum)|p[/.]?[cwm]|pcm)\b", re.IGNORECASE
)
_RE_WEEKS_DEPOSIT = re.compile(r"\d+\s*weeks?\s*deposit[:\s]*£[\d,]+", re.IGNORECASE)
_RE_AVAILABLE_FROM = re.compile(
    r"available\s+(?:from|to\s+rent\s+from)\s+\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", re.IGNORECASE
)
_RE_EMOJI = re.compile(
    "[\U0001f300-\U0001f9ff\U00002702-\U000027b0\U0000fe00-\U0000fe0f"
    "\U0000200d\U00002600-\U000026ff\U00002700-\U000027bf]+",
)
_RE_HTML_ENTITIES = re.compile(r"&(?:amp|nbsp|lt|gt|quot|apos|#\d+);?")
_ENTITY_MAP = {"&amp;": "&", "&nbsp;": " ", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'"}


def clean_description(text: str) -> str:
    """Strip boilerplate from a property description.

    Removes HTML tags, platform-specific templates (OpenRent, agent marketing),
    financial/legal boilerplate, prices, dates, emoji, and normalizes whitespace.
    """
    if not text:
        return ""

    # HTML entities
    for entity, replacement in _ENTITY_MAP.items():
        text = text.replace(entity, replacement)
    text = _RE_HTML_ENTITIES.sub(" ", text)

    # HTML tags → space
    text = _RE_HTML_TAGS.sub(" ", text)

    # Property reference numbers
    text = _RE_PROPERTY_REF.sub("", text)

    # OpenRent template parts
    text = _RE_OPENRENT_OPENER.sub("", text)
    text = _RE_OPENRENT_VIEWING.sub("", text)
    text = _RE_OPENRENT_SUMMARY.sub("", text)
    text = _RE_PHOTOS_TO_FOLLOW.sub("", text)

    # Agent marketing / social / illustrative
    text = _RE_SOCIAL_MEDIA.sub("", text)
    text = _RE_ILLUSTRATIVE.sub("", text)

    # Financial / legal boilerplate
    text = _RE_DEPOSIT_HOLDING.sub("", text)
    text = _RE_COUNCIL_TAX.sub("", text)
    text = _RE_EPC.sub("", text)
    text = _RE_CMP.sub("", text)
    text = _RE_OMBUDSMAN.sub("", text)
    text = _RE_AGENT_DISCLAIMER.sub("", text)
    text = _RE_PARTICULARS.sub("", text)
    text = _RE_CHINA_DESK.sub("", text)

    # Prices and deposits
    text = _RE_PRICE_PCM.sub("", text)
    text = _RE_WEEKS_DEPOSIT.sub("", text)

    # Available dates
    text = _RE_AVAILABLE_FROM.sub("", text)

    # Emoji
    text = _RE_EMOJI.sub("", text)

    # Collapse whitespace
    return " ".join(text.split()).strip()


def extract_property_text(text: str) -> str:
    """Isolate property-specific text, truncating before building/area marketing.

    Many descriptions have a property-specific first section followed by lengthy
    building-level ("ABOUT THE BUILDING") or area-level ("ABOUT THE AREA")
    marketing sections that are identical across all units in a development.
    This function strips building/area marketing to reduce false positives.
    """
    cleaned = clean_description(text)
    if not cleaned:
        return ""

    # Truncate before agent marketing sections
    # Re-apply to cleaned text since clean_description already tries, but
    # some patterns survive partial cleaning. Use a simpler scan here.
    marketing_headers = re.compile(
        r"\b(?:ABOUT\s+THE\s+(?:BUILDING|AREA|DEVELOPMENT|COMMUNITY|NEIGHBOURHOOD)|"
        r"ABOUT\s+(?:COMMUNITY\s+LIFE|WAY\s+OF\s+LIFE)|"
        r"About\s+the\s+(?:building|area|development|community|neighbourhood)|"
        r"About\s+(?:community\s+life|way\s+of\s+life))\b",
        re.IGNORECASE,
    )
    m = marketing_headers.search(cleaned)
    if m:
        cleaned = cleaned[: m.start()].strip()

    return cleaned


# --------------------------------------------------------------------------- #
# Description signals
# --------------------------------------------------------------------------- #


def signal_description_tfidf(
    a: PropertyDict,
    b: PropertyDict,
    *,
    vectorizer=None,
) -> SignalResult:
    """Description similarity using TF-IDF cosine on cleaned, property-specific text.

    Handles the common case where one platform truncates the description:
    truncates both to the shorter length before comparing. Also detects
    substring containment (same agent copy-paste) as a high-confidence signal.

    When a corpus-level vectorizer is provided (fitted on the full snapshot),
    IDF weights are meaningful. Otherwise falls back to pair-level fit.
    """
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    desc_a = _get_description(a)
    desc_b = _get_description(b)

    if not desc_a or not desc_b:
        return SignalResult(
            "description_tfidf", fired=False, value=0.0, detail="missing description"
        )

    clean_a = extract_property_text(desc_a)
    clean_b = extract_property_text(desc_b)

    if len(clean_a) < 20 or len(clean_b) < 20:
        return SignalResult(
            "description_tfidf",
            fired=False,
            value=0.0,
            detail=f"too short: {len(clean_a)} / {len(clean_b)} chars",
        )

    # Check substring containment (one description starts with the other)
    shorter, longer = (clean_a, clean_b) if len(clean_a) <= len(clean_b) else (clean_b, clean_a)
    prefix_match = longer[: len(shorter)].lower() == shorter.lower()
    if prefix_match:
        return SignalResult(
            "description_tfidf",
            fired=True,
            value=1.0,
            detail=f"prefix containment (shorter={len(shorter)}, longer={len(longer)})",
        )

    # Truncate both to shorter length to handle partial copy-paste
    trunc_a = clean_a[: len(shorter)]
    trunc_b = clean_b[: len(shorter)]

    if vectorizer is None:
        vectorizer = TfidfVectorizer(
            stop_words="english",
            max_features=5000,
            ngram_range=(1, 2),
        )
        tfidf = vectorizer.fit_transform([trunc_a, trunc_b])
    else:
        tfidf = vectorizer.transform([trunc_a, trunc_b])

    sim = cosine_similarity(tfidf[0:1], tfidf[1:2])[0][0]

    return SignalResult(
        "description_tfidf",
        fired=True,
        value=float(sim),
        detail=f"cosine={sim:.3f} (len {len(clean_a)}/{len(clean_b)}, compared {len(shorter)})",
    )


def _prop_uid(prop: PropertyDict) -> str:
    """Build a unique key for a property (source:source_id)."""
    return f"{prop.get('source', '')}:{prop.get('source_id', '')}"


# Module-level singleton for sentence-transformers model (lazy-loaded)
_sentence_model = None


def _get_sentence_model():
    global _sentence_model
    if _sentence_model is None:
        from sentence_transformers import SentenceTransformer

        _sentence_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _sentence_model


def signal_description_semantic(
    a: PropertyDict,
    b: PropertyDict,
    *,
    embeddings: dict[str, np.ndarray] | None = None,
) -> SignalResult:
    """Description similarity using sentence embeddings (all-MiniLM-L6-v2).

    Complementary to TF-IDF: catches semantic equivalence when different agents
    describe the same property in different words. Uses pre-computed embeddings
    when available, otherwise lazy-loads the model and encodes on the fly.

    Args:
        embeddings: Pre-computed {source:source_id -> embedding} dict.
    """
    desc_a = _get_description(a)
    desc_b = _get_description(b)

    if not desc_a or not desc_b:
        return SignalResult(
            "description_semantic", fired=False, value=0.0, detail="missing description"
        )

    clean_a = extract_property_text(desc_a)
    clean_b = extract_property_text(desc_b)

    if len(clean_a) < 50 or len(clean_b) < 50:
        return SignalResult(
            "description_semantic",
            fired=False,
            value=0.0,
            detail=f"too short after cleaning: {len(clean_a)} / {len(clean_b)} chars",
        )

    uid_a = _prop_uid(a)
    uid_b = _prop_uid(b)

    # Try pre-computed embeddings first
    emb_a = embeddings.get(uid_a) if embeddings else None
    emb_b = embeddings.get(uid_b) if embeddings else None

    # Fall back to on-the-fly encoding
    if emb_a is None or emb_b is None:
        model = _get_sentence_model()
        texts_to_encode = []
        indices = {}
        if emb_a is None:
            indices["a"] = len(texts_to_encode)
            texts_to_encode.append(clean_a[:512])
        if emb_b is None:
            indices["b"] = len(texts_to_encode)
            texts_to_encode.append(clean_b[:512])

        if texts_to_encode:
            encoded = model.encode(texts_to_encode)
            if "a" in indices:
                emb_a = encoded[indices["a"]]
            if "b" in indices:
                emb_b = encoded[indices["b"]]

    # Cosine similarity
    dot = float(np.dot(emb_a, emb_b))
    norm_a = float(np.linalg.norm(emb_a))
    norm_b = float(np.linalg.norm(emb_b))
    if norm_a == 0 or norm_b == 0:
        sim = 0.0
    else:
        sim = dot / (norm_a * norm_b)

    # Clamp to [0, 1]
    sim = max(0.0, min(1.0, sim))

    return SignalResult(
        "description_semantic",
        fired=True,
        value=sim,
        detail=f"cosine={sim:.3f} (len {len(clean_a)}/{len(clean_b)})",
    )


def signal_feature_overlap(a: PropertyDict, b: PropertyDict) -> SignalResult:
    """Compare extracted amenity/feature keywords from descriptions.

    Extracts structured features (dishwasher, garden, parking, etc.)
    and computes Jaccard similarity.
    """
    FEATURE_PATTERNS = [
        r"\bdishwasher\b",
        r"\bwashing machine\b",
        r"\bgarden\b",
        r"\bbalcony\b",
        r"\bparking\b",
        r"\bgarage\b",
        r"\ben[- ]?suite\b",
        r"\bwood(?:en)?\s*floor",
        r"\bcarpet",
        r"\bgas\s*(?:central)?\s*heat",
        r"\belectric\s*heat",
        r"\bdouble\s*glaz",
        r"\bfurnished\b",
        r"\bunfurnished\b",
        r"\bpets?\s*(?:allowed|friendly|considered)\b",
        r"\bno\s*pets?\b",
        r"\bbike\s*stor",
        r"\bconcierge\b",
        r"\blift\b",
        r"\belevator\b",
        r"\bbath\b",
        r"\bshower\b",
        r"\bterrace\b",
        r"\broof\s*terrace\b",
        r"\bcommunal\s*garden\b",
        r"\bprivate\s*garden\b",
        r"\bepc\s*(?:rating)?\s*[a-g]\b",
    ]

    def extract_features(prop: PropertyDict) -> set[str]:
        features = set()
        # Check both description and features list
        texts = []
        desc = _get_description(prop)
        if desc:
            texts.append(desc.lower())
        for feat in _get_features(prop):
            texts.append(feat.lower())

        combined = " ".join(texts)
        for pattern in FEATURE_PATTERNS:
            if re.search(pattern, combined, re.IGNORECASE):
                features.add(pattern)
        return features

    feats_a = extract_features(a)
    feats_b = extract_features(b)

    if not feats_a and not feats_b:
        return SignalResult("feature_overlap", fired=False, value=0.0, detail="no features found")

    if not feats_a or not feats_b:
        return SignalResult(
            "feature_overlap",
            fired=False,
            value=0.0,
            detail=f"one-sided: {len(feats_a)} vs {len(feats_b)}",
        )

    intersection = feats_a & feats_b
    union = feats_a | feats_b
    jaccard = len(intersection) / len(union) if union else 0.0

    return SignalResult(
        "feature_overlap",
        fired=True,
        value=jaccard,
        detail=f"{len(intersection)}/{len(union)} features shared (J={jaccard:.2f})",
    )


# --------------------------------------------------------------------------- #
# Gallery image signals
# --------------------------------------------------------------------------- #


def signal_gallery_images(a: PropertyDict, b: PropertyDict) -> SignalResult:
    """Cross-compare gallery image hashes between two properties.

    Uses pre-computed dual hashes (pHash + wHash) stored in
    detail.gallery_hashes by hash_snapshot.py. Match if EITHER
    hash type is within threshold (complementary robustness).

    Graduated value based on match count:
      1 match  → 0.5  (could be coincidental building exterior)
      2 matches → 0.85 (strong evidence)
      3+ matches → 1.0  (very high confidence)
    """
    from image_hashing import ImageHashes, count_gallery_matches

    def _load_gallery_hashes(prop: PropertyDict) -> list[ImageHashes]:
        detail = prop.get("detail", {}) or {}
        raw = detail.get("gallery_hashes") or []
        hashes = []
        for h in raw:
            if h.get("phash") or h.get("whash") or h.get("crop_hash"):
                hashes.append(
                    ImageHashes(
                        url=h.get("url", ""),
                        phash=h.get("phash"),
                        whash=h.get("whash"),
                        crop_hash=h.get("crop_hash"),
                    )
                )
        # Fall back to hero image hash when gallery is empty
        if not hashes:
            hero = prop.get("hero_hashes")
            if hero and (hero.get("phash") or hero.get("whash") or hero.get("crop_hash")):
                hashes.append(
                    ImageHashes(
                        url=hero.get("url", ""),
                        phash=hero.get("phash"),
                        whash=hero.get("whash"),
                        crop_hash=hero.get("crop_hash"),
                    )
                )
        return hashes

    gallery_a = _load_gallery_hashes(a)
    gallery_b = _load_gallery_hashes(b)

    if not gallery_a or not gallery_b:
        return SignalResult(
            "gallery_images",
            fired=False,
            value=0.0,
            detail=f"no hashes: {len(gallery_a)} vs {len(gallery_b)} images",
        )

    match_count, pairs = count_gallery_matches(gallery_a, gallery_b)

    if match_count == 0:
        return SignalResult(
            "gallery_images",
            fired=True,
            value=0.0,
            detail=f"0 matches ({len(gallery_a)} vs {len(gallery_b)} images)",
        )

    # Graduated scoring
    if match_count == 1:
        value = 0.5
    elif match_count == 2:
        value = 0.85
    else:
        value = 1.0

    return SignalResult(
        "gallery_images",
        fired=True,
        value=value,
        detail=f"{match_count} matches ({len(gallery_a)} vs {len(gallery_b)} images)",
    )


def signal_gallery_embeddings(a: PropertyDict, b: PropertyDict) -> SignalResult:
    """Cross-compare gallery images using SSCD copy-detection embeddings.

    Uses pre-computed 512-dim L2-normalized embeddings stored in
    detail.gallery_embeddings by embed_snapshot.py. Cosine similarity
    threshold of 0.75 identifies copies despite crops, overlays,
    compression, and color changes.

    Graduated value based on match count (same scale as gallery_images):
      1 match  → 0.5  (could be coincidental building exterior)
      2 matches → 0.85 (strong evidence)
      3+ matches → 1.0  (very high confidence)
    """

    def _load_embeddings(prop: PropertyDict) -> list[np.ndarray]:
        detail = prop.get("detail", {}) or {}
        raw = detail.get("gallery_embeddings") or []
        embeddings = []
        for entry in raw:
            b64 = entry.get("embedding")
            if b64:
                try:
                    emb = np.frombuffer(base64.b64decode(b64), dtype=np.float32).copy()
                    if len(emb) == 512:
                        embeddings.append(emb)
                except Exception:
                    pass
        return embeddings

    emb_a = _load_embeddings(a)
    emb_b = _load_embeddings(b)

    if not emb_a or not emb_b:
        return SignalResult(
            "gallery_embeddings",
            fired=False,
            value=0.0,
            detail=f"no embeddings: {len(emb_a)} vs {len(emb_b)} images",
        )

    # Greedy 1:1 matching with cosine similarity threshold
    threshold = 0.75
    matched_b: set[int] = set()
    match_count = 0

    for ea in emb_a:
        best_j = -1
        best_sim = -1.0
        for j, eb in enumerate(emb_b):
            if j in matched_b:
                continue
            sim = float(np.dot(ea, eb))  # L2-normalized → dot = cosine
            if sim >= threshold and sim > best_sim:
                best_sim = sim
                best_j = j
        if best_j >= 0:
            matched_b.add(best_j)
            match_count += 1

    if match_count == 0:
        return SignalResult(
            "gallery_embeddings",
            fired=True,
            value=0.0,
            detail=f"0 matches ({len(emb_a)} vs {len(emb_b)} images)",
        )

    # Graduated scoring
    if match_count == 1:
        value = 0.5
    elif match_count == 2:
        value = 0.85
    else:
        value = 1.0

    return SignalResult(
        "gallery_embeddings",
        fired=True,
        value=value,
        detail=f"{match_count} matches ({len(emb_a)} vs {len(emb_b)} images)",
    )


# --------------------------------------------------------------------------- #
# Batch helpers
# --------------------------------------------------------------------------- #

# Signal functions that take only (a, b) — no extra kwargs
_SIMPLE_SIGNALS = [
    signal_full_postcode,
    signal_outcode,
    signal_coordinates,
    signal_street_name,
    signal_price,
    signal_fuzzy_address,
    signal_address_number,
    signal_title_similarity,
    signal_feature_overlap,
    signal_gallery_images,
    signal_gallery_embeddings,
]

# All signal names (for reference / iteration)
ALL_SIGNAL_NAMES = [fn.__name__.replace("signal_", "") for fn in _SIMPLE_SIGNALS] + [
    "description_tfidf",
    "description_semantic",
]


def compute_all_signals(
    a: PropertyDict,
    b: PropertyDict,
    *,
    vectorizer=None,
    description_embeddings: dict[str, np.ndarray] | None = None,
) -> SignalBundle:
    """Compute all signals for a property pair.

    Args:
        vectorizer: Pre-fitted TfidfVectorizer (corpus-level). If None,
            signal_description_tfidf falls back to pair-level fit.
        description_embeddings: Pre-computed sentence embeddings keyed by
            'source:source_id'. If None, signal_description_semantic
            lazy-loads the model and encodes on the fly.
    """
    bundle = SignalBundle()

    # Simple signals: (a, b) only
    for signal_fn in _SIMPLE_SIGNALS:
        bundle.signals.append(signal_fn(a, b))

    # Description TF-IDF: needs optional vectorizer
    bundle.signals.append(signal_description_tfidf(a, b, vectorizer=vectorizer))

    # Description semantic: needs optional embeddings
    bundle.signals.append(signal_description_semantic(a, b, embeddings=description_embeddings))

    return bundle
