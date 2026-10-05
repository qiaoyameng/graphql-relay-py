from functools import cmp_to_key

from pytest import raises

from graphql_relay import (
    compare_ordering_keys,
    connection_from_ordered_source,
    cursor_to_ordering_key,
    ordering_key_to_cursor,
    IncompleteScanError,
)
from graphql_relay.utils.base64 import base64


def rec(id, score):
    return {"id": id, "score": score}


sample_records = [
    rec(1, 10),
    rec(2, 20),
    rec(3, 20),
    rec(4, 30),
    rec(5, 40),
    rec(6, 40),
    rec(7, 50),
]


def sort_key(record):
    return record["score"]


def unique_key(record):
    return record["id"]


def composite_key(record):
    return record["a"], record["b"]


class RecordingOrderedSource:
    """In-memory ordered data source that records every fetch range."""

    def __init__(self, records, key_fn, id_fn, nulls_first):
        self._key_fn = key_fn
        self._id_fn = id_fn
        self._nulls_first = nulls_first
        self._records = sorted(records, key=cmp_to_key(self._compare_records))
        self.fetch_log = []

    def _key(self, record):
        components = self._key_fn(record)
        if not isinstance(components, tuple):
            if isinstance(components, list):
                components = tuple(components)
            else:
                components = (components,)
        return components, self._id_fn(record)

    def _compare_records(self, record_a, record_b):
        return compare_ordering_keys(
            self._key(record_a), self._key(record_b), self._nulls_first
        )

    def fetch_forward(self, after, limit):
        self.fetch_log.append(("forward", after, limit))
        start = 0
        if after is not None:
            start = len(self._records)
            for index, record in enumerate(self._records):
                key = self._key(record)
                if compare_ordering_keys(key, after, self._nulls_first) > 0:
                    start = index
                    break
        return self._records[start : start + limit]

    def fetch_backward(self, before, limit):
        self.fetch_log.append(("backward", before, limit))
        end = len(self._records)
        if before is not None:
            end = 0
            for index in range(len(self._records) - 1, -1, -1):
                key = self._key(self._records[index])
                if compare_ordering_keys(key, before, self._nulls_first) < 0:
                    end = index + 1
                    break
        return self._records[max(0, end - limit) : end]


def make_source(records, nulls_first=False):
    return RecordingOrderedSource(records, sort_key, unique_key, nulls_first)


def make_connection(source, args=None, **kwargs):
    kwargs.setdefault("sort_key", sort_key)
    kwargs.setdefault("unique_key", unique_key)
    kwargs.setdefault("nulls_first", False)
    return connection_from_ordered_source(source, args, **kwargs)


def ids(connection):
    return [edge.node["id"] for edge in connection.edges]


def cursor_of(score, id):
    return ordering_key_to_cursor(((score,), id))


def describe_connection_from_ordered_source():
    def describe_basic_pagination():
        def returns_all_records_without_arguments():
            source = make_source(sample_records)
            c = make_connection(source)
            assert ids(c) == [1, 2, 3, 4, 5, 6, 7]
            assert c.pageInfo.hasNextPage is False
            assert c.pageInfo.hasPreviousPage is False
            assert c.pageInfo.startCursor == c.edges[0].cursor
            assert c.pageInfo.endCursor == c.edges[-1].cursor
            assert len(source.fetch_log) == 1

        def respects_first():
            source = make_source(sample_records)
            c = make_connection(source, dict(first=2))
            assert ids(c) == [1, 2]
            assert c.pageInfo.hasNextPage is True
            assert c.pageInfo.hasPreviousPage is False
            # fetches exactly first + 1 records, not the whole list
            assert source.fetch_log == [("forward", None, 3)]

        def respects_first_and_after_as_exclusive_boundary():
            source = make_source(sample_records)
            c = make_connection(source, dict(first=2, after=cursor_of(20, 2)))
            # the boundary record itself is excluded, but the record with
            # the same sort value right after the boundary is included
            assert ids(c) == [3, 4]
            assert c.pageInfo.hasNextPage is True
            assert source.fetch_log == [("forward", ((20,), 2), 3)]

        def respects_last_and_before_as_exclusive_boundary():
            source = make_source(sample_records)
            c = make_connection(source, dict(last=2, before=cursor_of(40, 5)))
            assert ids(c) == [3, 4]
            assert c.pageInfo.hasPreviousPage is True
            assert c.pageInfo.hasNextPage is False
            assert source.fetch_log == [("backward", ((40,), 5), 3)]

        def returns_backward_results_in_public_order():
            c = make_connection(make_source(sample_records), dict(last=3))
            assert ids(c) == [5, 6, 7]
            assert c.pageInfo.hasPreviousPage is True
            assert c.pageInfo.hasNextPage is False

        def respects_after_and_before_together():
            c = make_connection(
                make_source(sample_records),
                dict(first=10, after=cursor_of(20, 2), before=cursor_of(40, 6)),
            )
            assert ids(c) == [3, 4, 5]
            assert c.pageInfo.hasNextPage is False

        def respects_first_zero():
            c = make_connection(make_source(sample_records), dict(first=0))
            assert c.edges == []
            assert c.pageInfo.hasNextPage is True
            assert c.pageInfo.startCursor is None
            assert c.pageInfo.endCursor is None

        def respects_first_and_last_together():
            c = make_connection(
                make_source(sample_records), dict(first=3, last=2)
            )
            assert ids(c) == [2, 3]
            assert c.pageInfo.hasNextPage is True
            assert c.pageInfo.hasPreviousPage is True

        def determines_previous_page_for_first_and_last_by_scanning():
            c = make_connection(
                make_source(sample_records), dict(first=2, last=2)
            )
            assert ids(c) == [1, 2]
            assert c.pageInfo.hasNextPage is True
            assert c.pageInfo.hasPreviousPage is False

        def respects_after_boundary_when_scanning_for_previous_page():
            c = make_connection(
                make_source(sample_records),
                dict(first=2, last=2, after=cursor_of(10, 1)),
            )
            assert ids(c) == [2, 3]
            assert c.pageInfo.hasNextPage is True
            # no visible records exist between the after boundary and id 2
            assert c.pageInfo.hasPreviousPage is False

        def handles_first_zero_with_last():
            c = make_connection(
                make_source(sample_records), dict(first=0, last=1)
            )
            assert c.edges == []
            assert c.pageInfo.hasNextPage is True
            assert c.pageInfo.hasPreviousPage is True

        def handles_empty_source():
            c = make_connection(make_source([]), dict(first=2))
            assert c.edges == []
            assert c.pageInfo.hasNextPage is False
            assert c.pageInfo.hasPreviousPage is False
            assert c.pageInfo.startCursor is None
            assert c.pageInfo.endCursor is None

        def handles_first_larger_than_result_set():
            c = make_connection(make_source(sample_records), dict(first=10))
            assert ids(c) == [1, 2, 3, 4, 5, 6, 7]
            assert c.pageInfo.hasNextPage is False

        def treats_non_integer_first_and_last_as_missing():
            c = make_connection(
                make_source(sample_records), dict(first="2", last="1")
            )
            assert ids(c) == [1, 2, 3, 4, 5, 6, 7]

        def accepts_list_sort_keys():
            source = RecordingOrderedSource(
                sample_records,
                lambda record: [record["score"]],
                unique_key,
                False,
            )
            c = connection_from_ordered_source(
                source,
                dict(first=2),
                sort_key=lambda record: [record["score"]],
                unique_key=unique_key,
                nulls_first=False,
            )
            assert ids(c) == [1, 2]

    def describe_permission_filtering():
        def skips_invisible_records():
            source = make_source(sample_records)
            hidden = {2, 3, 5}
            c = make_connection(
                source,
                dict(first=2),
                is_visible=lambda record: record["id"] not in hidden,
            )
            assert ids(c) == [1, 4]
            assert c.pageInfo.hasNextPage is True
            # keeps fetching until enough visible records are found
            assert source.fetch_log == [
                ("forward", None, 3),
                ("forward", ((20,), 3), 2),
                ("forward", ((40,), 5), 1),
            ]

        def skips_consecutive_invisible_records():
            source = make_source(sample_records)
            hidden = {2, 3, 4, 5}
            c = make_connection(
                source,
                dict(first=1),
                is_visible=lambda record: record["id"] not in hidden,
            )
            assert ids(c) == [1]
            assert c.pageInfo.hasNextPage is True
            assert source.fetch_log == [
                ("forward", None, 2),
                ("forward", ((20,), 2), 1),
                ("forward", ((20,), 3), 1),
                ("forward", ((30,), 4), 1),
                ("forward", ((40,), 5), 1),
            ]

        def returns_empty_when_all_records_are_invisible():
            c = make_connection(
                make_source(sample_records),
                dict(first=2),
                is_visible=lambda record: False,
            )
            assert c.edges == []
            assert c.pageInfo.hasNextPage is False
            assert c.pageInfo.hasPreviousPage is False

        def filters_during_backward_pagination():
            source = make_source(sample_records)
            hidden = {6}
            c = make_connection(
                source,
                dict(last=2),
                is_visible=lambda record: record["id"] not in hidden,
            )
            # edges are still returned in public sort order
            assert ids(c) == [5, 7]
            assert c.pageInfo.hasPreviousPage is True
            assert c.pageInfo.hasNextPage is False

        def filters_invisible_record_at_the_boundary():
            # the record at the after boundary is invisible; the scan must
            # still resume strictly after its ordering key
            source = make_source(sample_records)
            hidden = {2}
            c = make_connection(
                source,
                dict(first=2, after=cursor_of(20, 2)),
                is_visible=lambda record: record["id"] not in hidden,
            )
            assert ids(c) == [3, 4]

    def describe_equal_sort_values():
        def paginates_forward_through_ties():
            source = make_source(sample_records)
            page1 = make_connection(source, dict(first=2))
            assert ids(page1) == [1, 2]
            page2 = make_connection(
                source, dict(first=2, after=page1.pageInfo.endCursor)
            )
            assert ids(page2) == [3, 4]
            page3 = make_connection(
                source, dict(first=2, after=page2.pageInfo.endCursor)
            )
            assert ids(page3) == [5, 6]
            page4 = make_connection(
                source, dict(first=2, after=page3.pageInfo.endCursor)
            )
            assert ids(page4) == [7]
            assert page4.pageInfo.hasNextPage is False

        def paginates_backward_through_ties():
            source = make_source(sample_records)
            page1 = make_connection(
                source, dict(last=1, before=cursor_of(30, 4))
            )
            assert ids(page1) == [3]
            page2 = make_connection(
                source, dict(last=1, before=page1.pageInfo.startCursor)
            )
            assert ids(page2) == [2]
            page3 = make_connection(
                source, dict(last=1, before=page2.pageInfo.startCursor)
            )
            assert ids(page3) == [1]
            page4 = make_connection(
                source, dict(last=1, before=page3.pageInfo.startCursor)
            )
            assert page4.edges == []
            assert page4.pageInfo.hasPreviousPage is False

        def does_not_skip_visible_record_with_same_sort_value_at_boundary():
            # id 3 has the same sort value as the boundary record id 2
            c = make_connection(
                make_source(sample_records), dict(first=1, after=cursor_of(20, 2))
            )
            assert ids(c) == [3]
            # and backwards: id 2 has the same sort value as boundary id 3
            c = make_connection(
                make_source(sample_records), dict(last=1, before=cursor_of(20, 3))
            )
            assert ids(c) == [2]

    def describe_scan_limit():
        def raises_when_forward_evidence_is_missing():
            hidden = {2, 3, 4, 5, 6, 7}
            with raises(IncompleteScanError) as exc_info:
                make_connection(
                    make_source(sample_records),
                    dict(first=1),
                    is_visible=lambda record: record["id"] not in hidden,
                    max_scan=2,
                )
            assert "Scan limit of 2" in str(exc_info.value)

        def raises_when_backward_evidence_is_missing():
            hidden = {1, 2, 3, 4, 5, 6}
            with raises(IncompleteScanError):
                make_connection(
                    make_source(sample_records),
                    dict(last=1),
                    is_visible=lambda record: record["id"] not in hidden,
                    max_scan=2,
                )

        def raises_when_edges_are_incomplete_without_first_or_last():
            with raises(IncompleteScanError):
                make_connection(make_source(sample_records), max_scan=3)

        def succeeds_when_limit_exactly_suffices():
            c = make_connection(
                make_source(sample_records), dict(first=1), max_scan=2
            )
            assert ids(c) == [1]
            assert c.pageInfo.hasNextPage is True

        def succeeds_when_source_is_exhausted_within_limit():
            c = make_connection(
                make_source(sample_records), dict(first=10), max_scan=8
            )
            assert ids(c) == [1, 2, 3, 4, 5, 6, 7]
            assert c.pageInfo.hasNextPage is False

        def rejects_non_positive_max_scan():
            with raises(ValueError) as exc_info:
                make_connection(make_source(sample_records), max_scan=0)
            assert "'max_scan'" in str(exc_info.value)

    def describe_null_ordering():
        records_with_nulls = [
            rec(1, None),
            rec(2, 10),
            rec(3, None),
            rec(4, 5),
        ]

        def orders_nulls_first_when_requested():
            c = make_connection(
                make_source(records_with_nulls, nulls_first=True),
                nulls_first=True,
            )
            assert ids(c) == [1, 3, 4, 2]

        def orders_nulls_last_when_requested():
            c = make_connection(
                make_source(records_with_nulls, nulls_first=False),
                nulls_first=False,
            )
            assert ids(c) == [4, 2, 1, 3]

        def paginates_across_the_null_boundary():
            source = make_source(records_with_nulls, nulls_first=False)
            page1 = make_connection(source, dict(first=2), nulls_first=False)
            assert ids(page1) == [4, 2]
            assert page1.pageInfo.hasNextPage is True
            page2 = make_connection(
                source,
                dict(first=2, after=page1.pageInfo.endCursor),
                nulls_first=False,
            )
            assert ids(page2) == [1, 3]
            assert page2.pageInfo.hasNextPage is False

        def requires_nulls_first_to_be_specified():
            with raises(TypeError):
                connection_from_ordered_source(
                    make_source(sample_records),
                    sort_key=sort_key,
                    unique_key=unique_key,
                )

    def describe_composite_sort_keys():
        composite_records = [
            {"id": 1, "a": 1, "b": 2},
            {"id": 2, "a": 1, "b": 1},
            {"id": 3, "a": 2, "b": None},
            {"id": 4, "a": None, "b": 1},
        ]

        def sorts_by_multiple_components_with_per_component_null_ordering():
            source = RecordingOrderedSource(
                composite_records, composite_key, unique_key, (True, False)
            )
            c = connection_from_ordered_source(
                source,
                sort_key=composite_key,
                unique_key=unique_key,
                nulls_first=(True, False),
            )
            assert ids(c) == [4, 2, 1, 3]

        def paginates_with_composite_keys():
            source = RecordingOrderedSource(
                composite_records, composite_key, unique_key, (True, False)
            )
            page1 = connection_from_ordered_source(
                source,
                dict(first=2),
                sort_key=composite_key,
                unique_key=unique_key,
                nulls_first=(True, False),
            )
            assert ids(page1) == [4, 2]
            page2 = connection_from_ordered_source(
                source,
                dict(first=2, after=page1.pageInfo.endCursor),
                sort_key=composite_key,
                unique_key=unique_key,
                nulls_first=(True, False),
            )
            assert ids(page2) == [1, 3]
            assert page2.pageInfo.hasNextPage is False

        def compare_ordering_keys_honors_null_ordering():
            assert compare_ordering_keys(((None,), 1), ((1,), 1), True) < 0
            assert compare_ordering_keys(((None,), 1), ((1,), 1), False) > 0
            assert compare_ordering_keys(((None,), 1), ((None,), 2), True) < 0
            assert compare_ordering_keys(((1,), 1), ((1,), 2), False) < 0
            assert compare_ordering_keys(((1,), 2), ((1,), 1), False) > 0
            assert compare_ordering_keys(((1,), 1), ((1,), 1), False) == 0
            key_a = ((1, None), 1)
            key_b = ((1, 2), 1)
            assert compare_ordering_keys(key_a, key_b, (False, True)) < 0
            assert compare_ordering_keys(key_a, key_b, (False, False)) > 0

        def compare_ordering_keys_rejects_empty_nulls_first():
            with raises(ValueError):
                compare_ordering_keys(((1,), 1), ((1,), 2), ())

    def describe_cursors():
        def roundtrips_ordering_keys():
            key = ((20, None), "x")
            assert cursor_to_ordering_key(ordering_key_to_cursor(key)) == key

        def does_not_depend_on_array_positions():
            source = make_source(sample_records)
            c = make_connection(source, dict(first=4))
            cursor = c.edges[-1].cursor
            assert cursor_to_ordering_key(cursor) == ((30,), 4)
            # removing a record before the cursor must not move it
            smaller_source = make_source(sample_records[1:])
            c = make_connection(smaller_source, dict(first=2, after=cursor))
            assert ids(c) == [5, 6]

        def rejects_invalid_cursors():
            source = make_source(sample_records)
            with raises(ValueError):
                make_connection(source, dict(after="not-a-cursor"))
            with raises(ValueError):
                make_connection(source, dict(before="YXJyYXljb25uZWN0aW9uOjE="))
            with raises(ValueError):
                make_connection(source, dict(after=base64("orderedconnection:{")))
            with raises(ValueError):
                make_connection(source, dict(after=base64("orderedconnection:5")))
            with raises(ValueError):
                make_connection(source, dict(after=123))

        def rejects_negative_first_and_last():
            source = make_source(sample_records)
            with raises(ValueError) as exc_info:
                make_connection(source, dict(first=-1))
            assert "'first'" in str(exc_info.value)
            with raises(ValueError) as exc_info:
                make_connection(source, dict(last=-1))
            assert "'last'" in str(exc_info.value)
