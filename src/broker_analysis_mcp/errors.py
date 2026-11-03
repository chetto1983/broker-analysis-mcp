"""Expected input/data failures shared without importing numerical or provider libraries."""


class DataError(ValueError):
    """The source returned nothing usable for the request."""


class SpecError(ValueError):
    """An indicator, expression or condition the user supplied is not valid."""
