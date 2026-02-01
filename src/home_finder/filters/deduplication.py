"""Property deduplication logic."""

from home_finder.logging import get_logger
from home_finder.models import Property

logger = get_logger(__name__)


class Deduplicator:
    """Deduplicate properties across platforms."""

    def __init__(self, *, enable_cross_platform: bool = False) -> None:
        """Initialize the deduplicator.

        Args:
            enable_cross_platform: If True, attempt to dedupe same property
                listed on multiple platforms (based on postcode + price + beds).
        """
        self.enable_cross_platform = enable_cross_platform

    def deduplicate(self, properties: list[Property]) -> list[Property]:
        """Remove duplicate properties.

        Args:
            properties: List of properties to deduplicate.

        Returns:
            List of unique properties.
        """
        if not properties:
            return []

        # First pass: dedupe by unique_id (same source + same ID)
        seen_unique_ids: dict[str, Property] = {}
        for prop in properties:
            if prop.unique_id in seen_unique_ids:
                existing = seen_unique_ids[prop.unique_id]
                # Keep the one seen first
                if prop.first_seen < existing.first_seen:
                    seen_unique_ids[prop.unique_id] = prop
            else:
                seen_unique_ids[prop.unique_id] = prop

        unique_by_id = list(seen_unique_ids.values())

        if not self.enable_cross_platform:
            logger.info(
                "deduplication_complete",
                original_count=len(properties),
                deduplicated_count=len(unique_by_id),
                cross_platform=False,
            )
            return unique_by_id

        # Second pass: cross-platform dedup based on postcode + price + bedrooms
        seen_signatures: dict[str, Property] = {}
        result: list[Property] = []

        for prop in unique_by_id:
            signature = self._get_cross_platform_signature(prop)

            if signature is None:
                # Can't generate signature (missing postcode), keep as unique
                result.append(prop)
                continue

            if signature in seen_signatures:
                existing = seen_signatures[signature]
                # Keep the one seen first
                if prop.first_seen < existing.first_seen:
                    seen_signatures[signature] = prop
                    # Replace in result
                    result = [p for p in result if p.unique_id != existing.unique_id]
                    result.append(prop)
            else:
                seen_signatures[signature] = prop
                result.append(prop)

        logger.info(
            "deduplication_complete",
            original_count=len(properties),
            after_unique_id=len(unique_by_id),
            deduplicated_count=len(result),
            cross_platform=True,
        )

        return result

    def _get_cross_platform_signature(self, prop: Property) -> str | None:
        """Generate a signature for cross-platform deduplication.

        Properties with the same postcode, price, and bedrooms are likely
        the same listing on different platforms.

        Args:
            prop: Property to generate signature for.

        Returns:
            Signature string, or None if signature can't be generated.
        """
        if not prop.postcode:
            return None

        # Normalize postcode (uppercase, single space)
        postcode = " ".join(prop.postcode.upper().split())

        return f"{postcode}:{prop.price_pcm}:{prop.bedrooms}"
