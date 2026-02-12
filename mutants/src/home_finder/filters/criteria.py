"""Property criteria filtering."""

from home_finder.logging import get_logger
from home_finder.models import Property, SearchCriteria

logger = get_logger(__name__)
from inspect import signature as _mutmut_signature
from typing import Annotated
from typing import Callable
from typing import ClassVar


MutantDict = Annotated[dict[str, Callable], "Mutant"]


def _mutmut_trampoline(orig, mutants, call_args, call_kwargs, self_arg = None):
    """Forward call to original or mutated function, depending on the environment"""
    import os
    mutant_under_test = os.environ['MUTANT_UNDER_TEST']
    if mutant_under_test == 'fail':
        from mutmut.__main__ import MutmutProgrammaticFailException
        raise MutmutProgrammaticFailException('Failed programmatically')      
    elif mutant_under_test == 'stats':
        from mutmut.__main__ import record_trampoline_hit
        record_trampoline_hit(orig.__module__ + '.' + orig.__name__)
        result = orig(*call_args, **call_kwargs)
        return result
    prefix = orig.__module__ + '.' + orig.__name__ + '__mutmut_'
    if not mutant_under_test.startswith(prefix):
        result = orig(*call_args, **call_kwargs)
        return result
    mutant_name = mutant_under_test.rpartition('.')[-1]
    if self_arg is not None:
        # call to a class method where self is not bound
        result = mutants[mutant_name](self_arg, *call_args, **call_kwargs)
    else:
        result = mutants[mutant_name](*call_args, **call_kwargs)
    return result


class CriteriaFilter:
    """Filter properties by search criteria (price, bedrooms)."""

    def xǁCriteriaFilterǁ__init____mutmut_orig(self, criteria: SearchCriteria) -> None:
        """Initialize the criteria filter.

        Args:
            criteria: Search criteria to filter by.
        """
        self.criteria = criteria

    def xǁCriteriaFilterǁ__init____mutmut_1(self, criteria: SearchCriteria) -> None:
        """Initialize the criteria filter.

        Args:
            criteria: Search criteria to filter by.
        """
        self.criteria = None
    
    xǁCriteriaFilterǁ__init____mutmut_mutants : ClassVar[MutantDict] = {
    'xǁCriteriaFilterǁ__init____mutmut_1': xǁCriteriaFilterǁ__init____mutmut_1
    }
    
    def __init__(self, *args, **kwargs):
        result = _mutmut_trampoline(object.__getattribute__(self, "xǁCriteriaFilterǁ__init____mutmut_orig"), object.__getattribute__(self, "xǁCriteriaFilterǁ__init____mutmut_mutants"), args, kwargs, self)
        return result 
    
    __init__.__signature__ = _mutmut_signature(xǁCriteriaFilterǁ__init____mutmut_orig)
    xǁCriteriaFilterǁ__init____mutmut_orig.__name__ = 'xǁCriteriaFilterǁ__init__'

    def xǁCriteriaFilterǁfilter_properties__mutmut_orig(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            "criteria_filter_complete",
            total_properties=len(properties),
            matching=len(matching),
            min_price=self.criteria.min_price,
            max_price=self.criteria.max_price,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_1(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = None

        logger.info(
            "criteria_filter_complete",
            total_properties=len(properties),
            matching=len(matching),
            min_price=self.criteria.min_price,
            max_price=self.criteria.max_price,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_2(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(None)]

        logger.info(
            "criteria_filter_complete",
            total_properties=len(properties),
            matching=len(matching),
            min_price=self.criteria.min_price,
            max_price=self.criteria.max_price,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_3(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            None,
            total_properties=len(properties),
            matching=len(matching),
            min_price=self.criteria.min_price,
            max_price=self.criteria.max_price,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_4(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            "criteria_filter_complete",
            total_properties=None,
            matching=len(matching),
            min_price=self.criteria.min_price,
            max_price=self.criteria.max_price,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_5(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            "criteria_filter_complete",
            total_properties=len(properties),
            matching=None,
            min_price=self.criteria.min_price,
            max_price=self.criteria.max_price,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_6(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            "criteria_filter_complete",
            total_properties=len(properties),
            matching=len(matching),
            min_price=None,
            max_price=self.criteria.max_price,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_7(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            "criteria_filter_complete",
            total_properties=len(properties),
            matching=len(matching),
            min_price=self.criteria.min_price,
            max_price=None,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_8(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            "criteria_filter_complete",
            total_properties=len(properties),
            matching=len(matching),
            min_price=self.criteria.min_price,
            max_price=self.criteria.max_price,
            min_bedrooms=None,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_9(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            "criteria_filter_complete",
            total_properties=len(properties),
            matching=len(matching),
            min_price=self.criteria.min_price,
            max_price=self.criteria.max_price,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=None,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_10(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            total_properties=len(properties),
            matching=len(matching),
            min_price=self.criteria.min_price,
            max_price=self.criteria.max_price,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_11(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            "criteria_filter_complete",
            matching=len(matching),
            min_price=self.criteria.min_price,
            max_price=self.criteria.max_price,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_12(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            "criteria_filter_complete",
            total_properties=len(properties),
            min_price=self.criteria.min_price,
            max_price=self.criteria.max_price,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_13(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            "criteria_filter_complete",
            total_properties=len(properties),
            matching=len(matching),
            max_price=self.criteria.max_price,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_14(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            "criteria_filter_complete",
            total_properties=len(properties),
            matching=len(matching),
            min_price=self.criteria.min_price,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_15(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            "criteria_filter_complete",
            total_properties=len(properties),
            matching=len(matching),
            min_price=self.criteria.min_price,
            max_price=self.criteria.max_price,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_16(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            "criteria_filter_complete",
            total_properties=len(properties),
            matching=len(matching),
            min_price=self.criteria.min_price,
            max_price=self.criteria.max_price,
            min_bedrooms=self.criteria.min_bedrooms,
            )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_17(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            "XXcriteria_filter_completeXX",
            total_properties=len(properties),
            matching=len(matching),
            min_price=self.criteria.min_price,
            max_price=self.criteria.max_price,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching

    def xǁCriteriaFilterǁfilter_properties__mutmut_18(self, properties: list[Property]) -> list[Property]:
        """Filter properties by criteria.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties matching the criteria.
        """
        matching = [p for p in properties if self.criteria.matches_property(p)]

        logger.info(
            "CRITERIA_FILTER_COMPLETE",
            total_properties=len(properties),
            matching=len(matching),
            min_price=self.criteria.min_price,
            max_price=self.criteria.max_price,
            min_bedrooms=self.criteria.min_bedrooms,
            max_bedrooms=self.criteria.max_bedrooms,
        )

        return matching
    
    xǁCriteriaFilterǁfilter_properties__mutmut_mutants : ClassVar[MutantDict] = {
    'xǁCriteriaFilterǁfilter_properties__mutmut_1': xǁCriteriaFilterǁfilter_properties__mutmut_1, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_2': xǁCriteriaFilterǁfilter_properties__mutmut_2, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_3': xǁCriteriaFilterǁfilter_properties__mutmut_3, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_4': xǁCriteriaFilterǁfilter_properties__mutmut_4, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_5': xǁCriteriaFilterǁfilter_properties__mutmut_5, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_6': xǁCriteriaFilterǁfilter_properties__mutmut_6, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_7': xǁCriteriaFilterǁfilter_properties__mutmut_7, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_8': xǁCriteriaFilterǁfilter_properties__mutmut_8, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_9': xǁCriteriaFilterǁfilter_properties__mutmut_9, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_10': xǁCriteriaFilterǁfilter_properties__mutmut_10, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_11': xǁCriteriaFilterǁfilter_properties__mutmut_11, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_12': xǁCriteriaFilterǁfilter_properties__mutmut_12, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_13': xǁCriteriaFilterǁfilter_properties__mutmut_13, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_14': xǁCriteriaFilterǁfilter_properties__mutmut_14, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_15': xǁCriteriaFilterǁfilter_properties__mutmut_15, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_16': xǁCriteriaFilterǁfilter_properties__mutmut_16, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_17': xǁCriteriaFilterǁfilter_properties__mutmut_17, 
        'xǁCriteriaFilterǁfilter_properties__mutmut_18': xǁCriteriaFilterǁfilter_properties__mutmut_18
    }
    
    def filter_properties(self, *args, **kwargs):
        result = _mutmut_trampoline(object.__getattribute__(self, "xǁCriteriaFilterǁfilter_properties__mutmut_orig"), object.__getattribute__(self, "xǁCriteriaFilterǁfilter_properties__mutmut_mutants"), args, kwargs, self)
        return result 
    
    filter_properties.__signature__ = _mutmut_signature(xǁCriteriaFilterǁfilter_properties__mutmut_orig)
    xǁCriteriaFilterǁfilter_properties__mutmut_orig.__name__ = 'xǁCriteriaFilterǁfilter_properties'
