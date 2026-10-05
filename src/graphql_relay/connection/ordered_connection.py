import json
from typing import Any, Callable, List, Optional, Sequence, Tuple

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
    "connection_from_ordered_source",
    "cursor_to_ordered_key",
    "ordered_key_to_cursor",
    "IncompleteScanError",
    "OrderedSource",
]

"""A composite sort key, ending in a unique tiebreaker component."""
OrderedKey = Tuple[Any, ...]


class IncompleteScanError(RuntimeError):
    """Raised when the scan limit is hit before pagination can be decided.

    When a ``max_scan`` limit is configured and the data source cannot be
    scanned far enough to determine ``hasNextPage``/``hasPreviousPage``
    with certainty, this error is raised instead of reporting a potentially
    wrong ``False`` value.
    """

    def __init__(self, max_scan: int, scanned: int) -> None:
        super().__init__(
            f"Scan limit of {max_scan} records was reached after scanning"
            f" {scanned} records before the page boundaries could be"
            " determined; refuse to report an unverified"
            " hasNextPage/hasPreviousPage value."
        )
        self.max_scan = max_scan
        self.scanned = scanned


class OrderedSource(Protocol):
    """A data source that serves slices of an ordered result set.

    The source orders records by a composite sort key that ends in a unique
    tiebreaker component. Both methods take an *exclusive* boundary key
    (``None`` means "from the very beginning/end") and return at most
    ``limit`` records. ``fetch_backward`` returns records in reverse
    (descending) order.
    """

    def fetch_forward(
        self, after_key: Optional[OrderedKey], limit: int
    ) -> Sequence[Any]:
        ...

    def fetch_backward(
        self, before_key: Optional[OrderedKey], limit: int
    ) -> Sequence[Any]:
        ...


def connection_from_ordered_source(
    source: OrderedSource,
    args: Optional[ConnectionArguments] = None,
    *,
    key: Callable[[Any], OrderedKey],
    is_visible: Optional[Callable[[Any], bool]] = None,
    nulls_first: bool = True,
    batch_size: Optional[int] = None,
    max_scan: Optional[int] = None,
    connection_type: ConnectionConstructor = Connection,
    edge_type: EdgeConstructor = Edge,
    page_info_type: PageInfoConstructor = PageInfo,
) -> ConnectionType:
    """Create a connection object from an ordered data source.

    Unlike `connection_from_array`, this function never materializes the
    whole result set. Instead, it pulls slices from `source`, which must
    order records by the composite sort key returned by `key` (whose last
    component must be a unique tiebreaker) and support fetching forward and
    backward from exclusive boundary keys.

    Records are passed through the optional `is_visible` permission filter
    before `first`/`last` are applied, so more than `first + 1` underlying
    records may be scanned. `after`/`before` cursors are exclusive
    boundaries that encode the composite key of a record, so they stay
    stable even when records are inserted or removed. Components that are
    `None` are ordered according to `nulls_first`, which the caller must
    set to match the ordering of the data source.

    When `max_scan` is set, at most that many underlying records are
    scanned. If the limit is reached before `hasNextPage`/`hasPreviousPage`
    can be determined with certainty, an `IncompleteScanError` is raised
    instead of reporting an unverified `False`.
    """
    args = args or {}
    before = args.get("before")
    after = args.get("after")
    first = args.get("first")
    last = args.get("last")

    if isinstance(first, int) and first < 0:
        raise ValueError("Argument 'first' must be a non-negative integer.")
    if isinstance(last, int) and last < 0:
        raise ValueError("Argument 'last' must be a non-negative integer.")

    after_key = _decode_boundary(after, "after")
    before_key = _decode_boundary(before, "before")

    visible = is_visible if is_visible is not None else lambda record: True

    if isinstance(last, int) and not isinstance(first, int):
        # Backward pagination: scan in reverse from the `before` boundary,
        # stop at the `after` boundary, then present edges in public order.
        collected = _scan(
            source,
            key=key,
            visible=visible,
            nulls_first=nulls_first,
            forward=False,
            start_key=before_key,
            stop_key=after_key,
            needed=last + 1,
            batch_size=batch_size,
            max_scan=max_scan,
        )
        has_previous_page = len(collected) > last
        page = collected[:last]
        page.reverse()
        has_next_page = False
    else:
        # Forward pagination: scan from the `after` boundary and stop at
        # the `before` boundary.
        needed = first + 1 if isinstance(first, int) else None
        collected = _scan(
            source,
            key=key,
            visible=visible,
            nulls_first=nulls_first,
            forward=True,
            start_key=after_key,
            stop_key=before_key,
            needed=needed,
            batch_size=batch_size,
            max_scan=max_scan,
        )
        has_next_page = isinstance(first, int) and len(collected) > first
        page = collected[:first] if isinstance(first, int) else collected
        has_previous_page = False
        if isinstance(last, int) and len(page) > last:
            page = page[len(page) - last :]
            has_previous_page = True

    edges = [
        edge_type(node=record, cursor=ordered_key_to_cursor(record_key))
        for record_key, record in page
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


def _decode_boundary(
    cursor: Optional[ConnectionCursor], name: str
) -> Optional[OrderedKey]:
    if cursor is None:
        return None
    boundary_key = cursor_to_ordered_key(cursor)
    if boundary_key is None:
        raise ValueError(f"Argument '{name}' is not a valid cursor: {cursor!r}")
    return boundary_key


def _normalize_key(key: OrderedKey, nulls_first: bool) -> Tuple:
    """Make a composite key comparable, placing `None` components explicitly.

    Each component is replaced by a tuple whose first element ranks `None`
    below (``nulls_first=True``) or above (``nulls_first=False``) all other
    values, so keys never compare `None` against non-`None` values.
    """
    null_rank, value_rank = (0, 1) if nulls_first else (1, 0)
    return tuple(
        (null_rank,) if component is None else (value_rank, component)
        for component in key
    )


def _scan(
    source: OrderedSource,
    *,
    key: Callable[[Any], OrderedKey],
    visible: Callable[[Any], bool],
    nulls_first: bool,
    forward: bool,
    start_key: Optional[OrderedKey],
    stop_key: Optional[OrderedKey],
    needed: Optional[int],
    batch_size: Optional[int],
    max_scan: Optional[int],
) -> List[Tuple[OrderedKey, Any]]:
    """Collect up to `needed` visible records from `source`.

    Returns a list of ``(key, record)`` pairs in scan order (ascending when
    scanning forward, descending when scanning backward). Scanning resumes
    from the key of the last *scanned* record, so invisible records are
    skipped without gaps and records sharing the same sort components are
    still traversed exactly once via the unique tiebreaker.
    """
    collected: List[Tuple[OrderedKey, Any]] = []
    scanned = 0
    cursor_key = start_key
    stop = _normalize_key(stop_key, nulls_first) if stop_key is not None else None
    fetch = source.fetch_forward if forward else source.fetch_backward
    exhausted = False

    while not exhausted and (needed is None or len(collected) < needed):
        if max_scan is not None and scanned >= max_scan:
            raise IncompleteScanError(max_scan, scanned)
        if batch_size is not None:
            limit = batch_size
        elif needed is not None:
            limit = needed - len(collected)
        else:
            limit = 100
        if max_scan is not None:
            limit = min(limit, max_scan - scanned)
        batch = fetch(cursor_key, limit)
        if not batch:
            break
        scanned += len(batch)
        for record in batch:
            record_key = key(record)
            if stop is not None:
                normalized = _normalize_key(record_key, nulls_first)
                if (forward and normalized >= stop) or (
                    not forward and normalized <= stop
                ):
                    exhausted = True
                    break
            cursor_key = record_key
            if visible(record):
                collected.append((record_key, record))
        if len(batch) < limit:
            exhausted = True

    return collected


PREFIX = "orderedconnection:"


def ordered_key_to_cursor(key: OrderedKey) -> ConnectionCursor:
    """Create the cursor string from a composite sort key."""
    return base64(PREFIX + json.dumps(list(key), separators=(",", ":")))


def cursor_to_ordered_key(cursor: ConnectionCursor) -> Optional[OrderedKey]:
    """Extract the composite sort key from the cursor string."""
    if not isinstance(cursor, str):
        return None
    raw = unbase64(cursor)
    if not raw.startswith(PREFIX):
        return None
    try:
        value = json.loads(raw[len(PREFIX) :])
    except ValueError:
        return None
    if not isinstance(value, list):
        return None
    return tuple(value)
