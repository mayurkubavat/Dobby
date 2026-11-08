"""HAL errors that fail closed instead of looking successful."""


class CapabilityUnavailable(RuntimeError):
    """The active backend does not implement this operation."""


class CassetteMismatch(RuntimeError):
    """A replayed action did not match the next cassette transition."""

    def __init__(self, expected: object, actual: object) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"cassette mismatch: expected {expected!r}, got {actual!r}")
