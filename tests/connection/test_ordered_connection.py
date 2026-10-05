from bisect import bisect_left, bisect_right
from typing import NamedTuple, Optional

from pytest import raises

from graphql_relay import (
    connection_from_ordered_source,
    cursor_to_ordered_key,
    ordered_key_to_cursor,
    Connection,
    Edge,
    IncompleteScanError,
    PageInfo,
)


class Rec(NamedTuple):
    sort: Optional[int]
    uid: str
    name: str


def rec_key(rec):
    return (rec.sort, rec.uid)


def rec(sort, name):
    return Rec(sort=sort, uid=f"u-{name}", name=name)


def normalize(key, nulls_first=True):
    null_rank, value_rank = (0, 1) if nulls_first else (1, 0)
    return tuple(
        (null_rank,) if component is None else (value_rank, component)
        for component in key
    )


class RecordingOrderedSource:
    """In-memory ordered source that records every fetch range."""

    def __init__(self, records, nulls_first=True):
        self.nulls_first = nulls_first
        self._entries = sorted(
            ((normalize(rec_key(rec), nulls_first), rec) for rec in records),
            key=lambda entry: entry[0],
        )
        self.calls = []

    def fetch_forward(self, after_key, limit):
        self.calls.append(("forward", after_key, limit))
        keys = [entry[0] for entry in self._entries]
        start = 0
        if after_key is not None:
            start = bisect_right(keys, normalize(after_key, self.nulls_first))
        return [rec for _, rec in self._entries[start : start + limit]]

    def fetch_backward(self, before_key, limit):
        self.calls.append(("backward", before_key, limit))
        keys = [entry[0] for entry in self._entries]
        end = len(self._entries)
        if before_key is not None:
            end = bisect_left(keys, normalize(before_key, self.nulls_first))
        start = max(0, end - limit)
        return [rec for _, rec in reversed(self._entries[start:end])]


def names(connection):
    return [edge.node.name for edge in connection.edges]


def describe_connection_from_ordered_source():
    def describe_forward_pagination():
        def returns_all_records_without_arguments():
            source = RecordingOrderedSource([rec(1, "A"), rec(2, "B"), rec(3, "C")])
            c = connection_from_ordered_source(source, {}, key=rec_key)
            assert names(c) == ["A", "B", "C"]
            assert c.pageInfo == PageInfo(
                startCursor=ordered_key_to_cursor((1, "u-A")),
                endCursor=ordered_key_to_cursor((3, "u-C")),
                hasPreviousPage=False,
                hasNextPage=False,
            )

        def respects_first():
            source = RecordingOrderedSource(
                [rec(1, "A"), rec(2, "B"), rec(3, "C"), rec(4, "D")]
            )
            c = connection_from_ordered_source(source, dict(first=2), key=rec_key)
            assert names(c) == ["A", "B"]
            assert c.pageInfo.hasNextPage is True
            assert c.pageInfo.hasPreviousPage is False

        def respects_an_overly_large_first():
            source = RecordingOrderedSource([rec(1, "A"), rec(2, "B")])
            c = connection_from_ordered_source(source, dict(first=10), key=rec_key)
            assert names(c) == ["A", "B"]
            assert c.pageInfo.hasNextPage is False

        def rejects_a_negative_first():
            source = RecordingOrderedSource([rec(1, "A")])
            with raises(ValueError):
                connection_from_ordered_source(source, dict(first=-1), key=rec_key)

        def only_fetches_as_many_records_as_needed():
            source = RecordingOrderedSource(
                [rec(1, "A"), rec(2, "B"), rec(3, "C"), rec(4, "D")]
            )
            connection_from_ordered_source(source, dict(first=2), key=rec_key)
            # first + 1 records are enough to prove hasNextPage
            assert source.calls == [("forward", None, 3)]

    def describe_permission_filtering():
        def skips_consecutive_invisible_records_before_applying_first():
            source = RecordingOrderedSource(
                [
                    rec(1, "A"),
                    rec(2, "h1"),
                    rec(3, "h2"),
                    rec(4, "h3"),
                    rec(5, "B"),
                    rec(6, "C"),
                ]
            )
            visible = {"A", "B", "C"}
            c = connection_from_ordered_source(
                source,
                dict(first=2),
                key=rec_key,
                is_visible=lambda r: r.name in visible,
            )
            assert names(c) == ["A", "B"]
            assert c.pageInfo.hasNextPage is True

        def resumes_scanning_from_the_last_scanned_record():
            source = RecordingOrderedSource(
                [
                    rec(1, "A"),
                    rec(2, "h1"),
                    rec(3, "h2"),
                    rec(4, "B"),
                    rec(5, "C"),
                ]
            )
            visible = {"A", "B", "C"}
            c = connection_from_ordered_source(
                source,
                dict(first=2),
                key=rec_key,
                is_visible=lambda r: r.name in visible,
                batch_size=2,
            )
            assert names(c) == ["A", "B"]
            assert c.pageInfo.hasNextPage is True
            # each fetch resumes right after the last *scanned* record,
            # so the visible record B directly after the hidden ones is
            # not skipped
            assert source.calls == [
                ("forward", None, 2),
                ("forward", (2, "u-h1"), 2),
                ("forward", (4, "u-B"), 2),
            ]

        def detects_the_end_when_invisible_records_tail_the_source():
            source = RecordingOrderedSource(
                [rec(1, "A"), rec(2, "h1"), rec(3, "h2"), rec(4, "h3")]
            )
            c = connection_from_ordered_source(
                source,
                dict(first=1),
                key=rec_key,
                is_visible=lambda r: r.name == "A",
            )
            assert names(c) == ["A"]
            assert c.pageInfo.hasNextPage is False

    def describe_exclusive_boundaries():
        def after_excludes_the_boundary_record():
            source = RecordingOrderedSource(
                [rec(1, "A"), rec(2, "B"), rec(3, "C"), rec(4, "D")]
            )
            after = ordered_key_to_cursor((2, "u-B"))
            c = connection_from_ordered_source(source, dict(after=after), key=rec_key)
            assert names(c) == ["C", "D"]

        def before_excludes_the_boundary_record():
            source = RecordingOrderedSource(
                [rec(1, "A"), rec(2, "B"), rec(3, "C"), rec(4, "D")]
            )
            before = ordered_key_to_cursor((3, "u-C"))
            c = connection_from_ordered_source(
                source, dict(before=before), key=rec_key
            )
            assert names(c) == ["A", "B"]

        def includes_the_visible_record_right_after_the_boundary():
            source = RecordingOrderedSource(
                [rec(1, "A"), rec(2, "h1"), rec(3, "B")]
            )
            visible = {"A", "B"}
            after = ordered_key_to_cursor((2, "u-h1"))
            c = connection_from_ordered_source(
                source,
                dict(after=after, first=1),
                key=rec_key,
                is_visible=lambda r: r.name in visible,
            )
            assert names(c) == ["B"]
            assert c.pageInfo.hasNextPage is False

        def includes_the_visible_record_right_before_the_boundary():
            source = RecordingOrderedSource(
                [rec(1, "A"), rec(2, "B"), rec(3, "h1")]
            )
            visible = {"A", "B"}
            before = ordered_key_to_cursor((3, "u-h1"))
            c = connection_from_ordered_source(
                source,
                dict(before=before, last=1),
                key=rec_key,
                is_visible=lambda r: r.name in visible,
            )
            assert names(c) == ["B"]
            assert c.pageInfo.hasPreviousPage is True

        def rejects_an_invalid_cursor():
            source = RecordingOrderedSource([rec(1, "A")])
            with raises(ValueError):
                connection_from_ordered_source(
                    source, dict(after="garbage"), key=rec_key
                )

    def describe_equal_sort_values():
        def paginates_through_ties_via_the_unique_key():
            source = RecordingOrderedSource(
                [rec(5, "A"), rec(5, "B"), rec(5, "C"), rec(5, "D")]
            )
            first_page = connection_from_ordered_source(
                source, dict(first=2), key=rec_key
            )
            assert names(first_page) == ["A", "B"]
            assert first_page.pageInfo.hasNextPage is True

            after = first_page.pageInfo.endCursor
            second_page = connection_from_ordered_source(
                source, dict(after=after, first=2), key=rec_key
            )
            assert names(second_page) == ["C", "D"]
            assert second_page.pageInfo.hasNextPage is False

            third_page = connection_from_ordered_source(
                source, dict(after=second_page.pageInfo.endCursor, first=2),
                key=rec_key,
            )
            assert names(third_page) == []
            assert third_page.pageInfo.hasNextPage is False

        def does_not_skip_or_repeat_records_at_batch_boundaries():
            source = RecordingOrderedSource(
                [rec(5, "A"), rec(5, "B"), rec(5, "C")]
            )
            seen = []
            after = None
            for _ in range(3):
                page = connection_from_ordered_source(
                    source, dict(after=after, first=1), key=rec_key
                )
                seen.extend(names(page))
                after = page.pageInfo.endCursor
            assert seen == ["A", "B", "C"]

    def describe_backward_pagination():
        def respects_last_and_keeps_public_order():
            source = RecordingOrderedSource(
                [rec(1, "A"), rec(2, "B"), rec(3, "C"), rec(4, "D")]
            )
            c = connection_from_ordered_source(source, dict(last=2), key=rec_key)
            assert names(c) == ["C", "D"]
            assert c.pageInfo.hasPreviousPage is True
            assert c.pageInfo.hasNextPage is False
            assert source.calls == [("backward", None, 3)]

        def respects_last_with_a_before_cursor():
            source = RecordingOrderedSource(
                [rec(1, "A"), rec(2, "B"), rec(3, "C"), rec(4, "D")]
            )
            before = ordered_key_to_cursor((4, "u-D"))
            c = connection_from_ordered_source(
                source, dict(before=before, last=2), key=rec_key
            )
            assert names(c) == ["B", "C"]
            assert c.pageInfo.hasPreviousPage is True

        def rejects_a_negative_last():
            source = RecordingOrderedSource([rec(1, "A")])
            with raises(ValueError):
                connection_from_ordered_source(source, dict(last=-1), key=rec_key)

        def stops_at_the_after_boundary():
            source = RecordingOrderedSource(
                [rec(1, "A"), rec(2, "B"), rec(3, "C"), rec(4, "D")]
            )
            after = ordered_key_to_cursor((1, "u-A"))
            c = connection_from_ordered_source(
                source, dict(after=after, last=5), key=rec_key
            )
            assert names(c) == ["B", "C", "D"]
            assert c.pageInfo.hasPreviousPage is False

    def describe_bidirectional_pagination():
        def pages_forward_and_then_backward_to_the_start():
            records = [rec(i, name) for i, name in enumerate("ABCDE", start=1)]
            source = RecordingOrderedSource(records)

            page1 = connection_from_ordered_source(source, dict(first=2), key=rec_key)
            assert names(page1) == ["A", "B"]

            page2 = connection_from_ordered_source(
                source,
                dict(after=page1.pageInfo.endCursor, first=2),
                key=rec_key,
            )
            assert names(page2) == ["C", "D"]
            assert page2.pageInfo.hasNextPage is True

            back = connection_from_ordered_source(
                source,
                dict(before=page2.pageInfo.startCursor, last=2),
                key=rec_key,
            )
            assert names(back) == ["A", "B"]
            assert back.pageInfo.hasPreviousPage is False

        def applies_last_after_first():
            source = RecordingOrderedSource(
                [rec(i, name) for i, name in enumerate("ABCDE", start=1)]
            )
            c = connection_from_ordered_source(
                source, dict(first=3, last=1), key=rec_key
            )
            assert names(c) == ["C"]
            assert c.pageInfo.hasPreviousPage is True
            assert c.pageInfo.hasNextPage is True

    def describe_scan_limit():
        def raises_an_incomplete_scan_error_instead_of_a_false_has_next_page():
            source = RecordingOrderedSource(
                [rec(1, "A")]
                + [rec(i, f"h{i}") for i in range(2, 7)]
                + [rec(7, "B")]
            )
            with raises(IncompleteScanError) as excinfo:
                connection_from_ordered_source(
                    source,
                    dict(first=2),
                    key=rec_key,
                    is_visible=lambda r: r.name in {"A", "B"},
                    max_scan=4,
                )
            assert excinfo.value.max_scan == 4
            assert excinfo.value.scanned == 4

        def raises_an_incomplete_scan_error_for_backward_pagination():
            source = RecordingOrderedSource(
                [rec(1, "A")]
                + [rec(i, f"h{i}") for i in range(2, 7)]
                + [rec(7, "B")]
            )
            with raises(IncompleteScanError):
                connection_from_ordered_source(
                    source,
                    dict(last=2),
                    key=rec_key,
                    is_visible=lambda r: r.name in {"A", "B"},
                    max_scan=3,
                )

        def succeeds_when_the_scan_budget_is_sufficient():
            source = RecordingOrderedSource(
                [rec(1, "A"), rec(2, "h1"), rec(3, "B")]
            )
            c = connection_from_ordered_source(
                source,
                dict(first=2),
                key=rec_key,
                is_visible=lambda r: r.name != "h1",
                max_scan=4,
            )
            assert names(c) == ["A", "B"]
            assert c.pageInfo.hasNextPage is False

    def describe_null_ordering():
        def orders_nulls_first_when_requested():
            source = RecordingOrderedSource(
                [rec(2, "B"), rec(None, "N"), rec(1, "A")], nulls_first=True
            )
            c = connection_from_ordered_source(
                source, {}, key=rec_key, nulls_first=True
            )
            assert names(c) == ["N", "A", "B"]

        def orders_nulls_last_when_requested():
            source = RecordingOrderedSource(
                [rec(2, "B"), rec(None, "N"), rec(1, "A")], nulls_first=False
            )
            c = connection_from_ordered_source(
                source, {}, key=rec_key, nulls_first=False
            )
            assert names(c) == ["A", "B", "N"]


        def paginates_across_null_boundaries():
            source = RecordingOrderedSource(
                [rec(None, "N"), rec(1, "A"), rec(2, "B")], nulls_first=False
            )
            page1 = connection_from_ordered_source(
                source, dict(first=2), key=rec_key, nulls_first=False
            )
            assert names(page1) == ["A", "B"]
            page2 = connection_from_ordered_source(
                source,
                dict(after=page1.pageInfo.endCursor, first=2),
                key=rec_key,
                nulls_first=False,
            )
            assert names(page2) == ["N"]
            assert page2.pageInfo.hasNextPage is False

    def describe_cursors():
        def round_trips_a_composite_key_with_a_null_component():
            cursor = ordered_key_to_cursor((None, "u-N"))
            assert cursor_to_ordered_key(cursor) == (None, "u-N")

        def does_not_depend_on_array_offsets():
            source = RecordingOrderedSource(
                [rec(10, "A"), rec(20, "B"), rec(30, "C")]
            )
            c = connection_from_ordered_source(source, {}, key=rec_key)
            assert isinstance(c, Connection)
            assert c.edges[1] == Edge(
                node=Rec(sort=20, uid="u-B", name="B"),
                cursor=ordered_key_to_cursor((20, "u-B")),
            )
            # the cursor keeps pointing at B even if earlier rows vanish
            shrunk = RecordingOrderedSource([rec(20, "B"), rec(30, "C")])
            after_b = connection_from_ordered_source(
                shrunk, dict(after=c.edges[1].cursor), key=rec_key
            )
            assert names(after_b) == ["C"]
