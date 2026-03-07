from typing import cast, overload


@overload
def encoded_str(s: str) -> str: ...
@overload
def encoded_str(s: None) -> None: ...


def encoded_str(s: str | None) -> str | None:
    """Omit surrogate pairs."""
    if s is None:
        return None
    # This is bytes, but the python proto accepts that.
    return cast("str", s.encode("utf-8", errors="replace"))
