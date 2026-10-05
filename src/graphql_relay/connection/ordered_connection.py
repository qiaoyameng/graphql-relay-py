import json
from typing import Any, Callable, List, Optional, Sequence, Tuple, Union

try:
    from typing import Protocol
except ImportError:  # Python < 3.8
    from typing_extensions import Protocol  # type: ignore

from ..utils.base64 import base64, unbase64
from .connection import (
    Connection,
    ConnectionArguments,
    ConnectionConstructor,
    ConnectionCursor,
    ConnectionType,
    Edge,
    EdgeConstructor,
    PageInfo,
    PageInfoConstructor,
)

__all__ = [
    "compare_ordering_keys",
    "connection_from_ordered_source",
    "cursor_to_ordering_key",
    "ordering_key_to_cursor",
    "IncompleteScanError",
    "NullsFirst",
    "OrderedDataSource",
    "OrderingKey",
]


"""An ordering key identifies a record by its sort position.

It consists of a tuple of sort key components and a unique key that
disambiguates records with equal sort key components. All values must be
JSON serializable so that they can be encoded into cursors.
"""
OrderingKey = Tuple[Tuple[Any, ...], Any]

"""Whether null values sort before or after all other values.

This can be a single boolean applied to all sort key components, or a
sequence of booleans with one entry per sort key component.
"""
NullsFirst = Union[bool, Sequence[bool]]


class OrderedDataSource(Protocol):
    """A data source that returns slices of an ordered result set.

    The result set is ordered by a composite sort key and a unique key.
    Slices are requested relative to an exclusive boundary ordering key,
    either in forward or in backward direction. Both fetch methods return
    their records in the public sort order, and both return fewer records
    than requested only when the respective end of the result set has been
    reached.
    """

    def fetch_forward(
        self, after: Optional[OrderingKey], limit: int
    ) -> List[Any]:
        """Return up to `limit` records strictly after the `after` key.

        If `after` is None, return records from the start of the result set.
        """
        ...

    def fetch_backward(
        self, before: Optional[OrderingKey], limit: int
    ) -> List[Any]:
        """Return up to `limit` records strictly before the `before` key.

        If `before` is None, return records from the end of the result set.
        The records are returned in the public sort order, not reversed.
        """
        ...


class IncompleteScanError(RuntimeError):
    """The scan limit was reached before the connection could be determined.

    Raised instead of returning a ``hasNextPage`` or ``hasPreviousPage``
    value (or an edge list) that is not backed by sufficient evidence.
    """


def _compare_values(a: Any, b: Any, null_first: bool) -> int:
    if a is None:
        return 0 if b is None else (-1 if null_first else 1)
    if b is None:
        return 1 if null_first else -1
    return (a > b) - (a < b)


def _nulls_first_flags(nulls_first: NullsFirst, length: int) -> Tuple[bool, ...]:
    if isinstance(nulls_first, bool):
        return (nulls_first,) * length
    flags = tuple(nulls_first)
    if not flags:
        raise ValueError("Argument 'nulls_first' must not be an empty sequence.")
    if len(flags) < length:
        flags += (flags[-1],) * (length - len(flags))
    return flags


def compare_ordering_keys(
    key_a: OrderingKey, key_b: OrderingKey, nulls_first: NullsFirst
) -> int:
    """Compare two ordering keys, honoring the given null ordering.

    Returns a negative number if `key_a` sorts before `key_b`, a positive
    number if it sorts after, and zero if both keys are equal. Records are
    ordered by their sort key components first; the unique key is used as
    the tiebreaker for equal sort key components.
    """
    components_a, unique_a = key_a
    components_b, unique_b = key_b
    length = max(len(components_a), len(components_b))
    flags = _nulls_first_flags(nulls_first, length + 1)
    for index in range(length):
        value_a = components_a[index] if index < len(components_a) else None
        value_b = components_b[index] if index < len(components_b) else None
        result = _compare_values(value_a, value_b, flags[index])
        if result:
            return result
    return _compare_values(unique_a, unique_b, flags[length])


PREFIX = "orderedconnection:"


def ordering_key_to_cursor(key: OrderingKey) -> ConnectionCursor:
    """Create the cursor string from an ordering key.

    The cursor encodes the sort key components and the unique key of a
    record, so it stays valid even when records are inserted or removed.
    """
    components, unique = key
    payload = json.dumps([list(components), unique], separators=(",", ":"))
    return base64(PREFIX + payload)


def cursor_to_ordering_key(cursor: ConnectionCursor) -> Optional[OrderingKey]:
    """Extract the ordering key from the cursor string.

    Returns None if the cursor does not contain a valid ordering key.
    """
    if not isinstance(cursor, str):
        return None
    payload = unbase64(cursor)
    if not payload.startswith(PREFIX):
        return None
    try:
        components, unique = json.loads(payload[len(PREFIX) :])
        return tuple(components), unique
    except (TypeError, ValueError):
        return None


# Batch size used when scanning without a limit on the number of results.
_UNBOUNDED_BATCH_SIZE = 100


def connection_from_ordered_source(
    source: OrderedDataSource,
    args: Optional[ConnectionArguments] = None,
    *,
    sort_key: Callable[[Any], Any],
    unique_key: Callable[[Any], Any],
    nulls_first: NullsFirst,
    is_visible: Optional[Callable[[Any], bool]] = None,
    max_scan: Optional[int] = None,
    connection_type: ConnectionConstructor = Connection,
    edge_type: EdgeConstructor = Edge,
    page_info_type: PageInfoConstructor = PageInfo,
) -> ConnectionType:
    """Create a connection object from an ordered data source.

    Different from `connection_from_array`, this function does not need the
    whole result set. Instead, it fetches slices from an `OrderedDataSource`
    that is ordered by a composite sort key (given as `sort_key`) and a
    unique tiebreaker (given as `unique_key`). The `after` and `before`
    arguments are treated as exclusive boundaries. Cursors are built from
    the ordering keys of the records and do not depend on array offsets.

    Since the records may be filtered by the `is_visible` permission check
    after fetching, fetching `first + 1` records is not always sufficient;
    the source is queried repeatedly until enough visible records have been
    found or the end of the result set has been reached. When `last` is
    used, the source is read backwards, but the resulting edges are still
    returned in the public sort order.

    The `nulls_first` argument is mandatory and determines whether null
    sort key components sort before or after all other values. Pass a
    single boolean or one boolean per sort key component.

    The `max_scan` argument limits the total number of underlying records
    that may be inspected. If the limit is reached while there is not
    enough evidence to determine the edges, `hasNextPage` or
    `hasPreviousPage`, an `IncompleteScanError` is raised instead of
    reporting a value that might be wrong.
    """
    args = args or {}
    before = args.get("before")
    after = args.get("after")
    first = args.get("first")
    last = args.get("last")

    if isinstance(first, int):
        if first < 0:
            raise ValueError("Argument 'first' must be a non-negative integer.")
    else:
        first = None
    if isinstance(last, int):
        if last < 0:
            raise ValueError("Argument 'last' must be a non-negative integer.")
    else:
        last = None
    if max_scan is not None and max_scan < 1:
        raise ValueError("Argument 'max_scan' must be a positive integer.")

    after_key = _require_ordering_key(after, "after")
    before_key = _require_ordering_key(before, "before")

    def ordering_key(record: Any) -> OrderingKey:
        components = sort_key(record)
        if not isinstance(components, tuple):
            if isinstance(components, list):
                components = tuple(components)
            else:
                components = (components,)
        return components, unique_key(record)

    scanned = 0

    def scan(
        backwards: bool,
        start_key: Optional[OrderingKey],
        stop_key: Optional[OrderingKey],
        need: Optional[int],
    ) -> Tuple[List[Tuple[OrderingKey, Any]], bool]:
        """Scan visible records, starting after/before the given key.

        Returns the collected (ordering key, record) pairs in public sort
        order, and whether the scan provably reached the end of the window.
        Raises `IncompleteScanError` if the scan limit is reached first.
        """
        nonlocal scanned
        collected: List[Tuple[OrderingKey, Any]] = []
        boundary = start_key
        exhausted = False
        while need is None or len(collected) < need:
            request = (
                need - len(collected)
                if need is not None
                else _UNBOUNDED_BATCH_SIZE
            )
            if max_scan is not None:
                remaining = max_scan - scanned
                if remaining <= 0:
                    direction = "backward" if backwards else "forward"
                    raise IncompleteScanError(
                        f"Scan limit of {max_scan} records was reached while"
                        f" scanning {direction}; the connection cannot be"
                        " determined without inspecting more records."
                    )
                request = min(request, remaining)
            if backwards:
                batch = source.fetch_backward(boundary, request)
            else:
                batch = source.fetch_forward(boundary, request)
            scanned += len(batch)
            if not batch:
                exhausted = True
                break
            if len(batch) < request:
                exhausted = True
            stop = exhausted
            for record in reversed(batch) if backwards else batch:
                key = ordering_key(record)
                if stop_key is not None:
                    order = compare_ordering_keys(key, stop_key, nulls_first)
                    if order <= 0 if backwards else order >= 0:
                        exhausted = stop = True
                        break
                boundary = key
                if is_visible is None or is_visible(record):
                    collected.append((key, record))
                    if need is not None and len(collected) >= need:
                        stop = True
                        break
            if stop:
                break
        if backwards:
            collected.reverse()
        return collected, exhausted

    if first is None and last is None:
        selected, _exhausted = scan(False, after_key, before_key, None)
        has_next_page = has_previous_page = False
    elif last is None:
        assert first is not None
        found, exhausted = scan(False, after_key, before_key, first + 1)
        has_next_page = not exhausted
        has_previous_page = False
        selected = found[:first]
    elif first is None:
        found, exhausted = scan(True, before_key, after_key, last + 1)
        has_previous_page = not exhausted
        has_next_page = False
        selected = found[max(0, len(found) - last) :]
    else:
        found, exhausted = scan(False, after_key, before_key, first + 1)
        has_next_page = not exhausted
        selected = found[:first]
        if len(selected) > last:
            selected = selected[len(selected) - last :]
            has_previous_page = True
        else:
            start_key = selected[0][0] if selected else before_key
            extra, _ignored = scan(True, start_key, after_key, 1)
            has_previous_page = bool(extra)

    edges = [
        edge_type(node=record, cursor=ordering_key_to_cursor(key))
        for key, record in selected
    ]

    first_edge_cursor = edges[0].cursor if edges else None
    last_edge_cursor = edges[-1].cursor if edges else None

    return connection_type(
        edges=edges,
        pageInfo=page_info_type(
            startCursor=first_edge_cursor,
            endCursor=last_edge_cursor,
            hasPreviousPage=has_previous_page,
            hasNextPage=has_next_page,
        ),
    )


def _require_ordering_key(
    cursor: Optional[ConnectionCursor], argument: str
) -> Optional[OrderingKey]:
    if cursor is None:
        return None
    key = cursor_to_ordering_key(cursor)
    if key is None:
        raise ValueError(f"Argument '{argument}' contains an invalid cursor.")
    return key
