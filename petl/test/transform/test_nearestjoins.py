from __future__ import absolute_import, division, print_function

import random

import pytest

import petl as etl
from petl.test.helpers import ieq


def _rows(table):
    return list(table)


def test_nearestjoin_directions():
    samples = (
        ('device', 'time', 'value'),
        ('a', 10, 1),
        ('a', 20, 2),
        ('b', 5, 3),
        ('b', 30, 4),
    )
    calibrations = (
        ('device', 'time', 'offset'),
        ('a', 8, 8.0),
        ('a', 15, 15.0),
        ('b', 1, 1.0),
        ('b', 10, 10.0),
    )

    backward = etl.nearestjoin(
        samples, calibrations, key='device', time='time',
        direction='backward', presorted=True
    )
    expect_backward = (
        ('device', 'time', 'value', 'device', 'time', 'offset'),
        ('a', 10, 1, 'a', 8, 8.0),
        ('a', 20, 2, 'a', 15, 15.0),
        ('b', 5, 3, 'b', 1, 1.0),
        ('b', 30, 4, 'b', 10, 10.0),
    )
    ieq(expect_backward, backward)

    forward = etl.nearestjoin(
        samples, calibrations, key='device', time='time',
        direction='forward', presorted=True
    )
    expect_forward = (
        ('device', 'time', 'value', 'device', 'time', 'offset'),
        ('a', 10, 1, 'a', 15, 15.0),
        ('a', 20, 2, None, None, None),
        ('b', 5, 3, 'b', 10, 10.0),
        ('b', 30, 4, None, None, None),
    )
    ieq(expect_forward, forward)

    nearest = etl.nearestjoin(
        samples, calibrations, key='device', time='time',
        direction='nearest', presorted=True
    )
    expect_nearest = (
        ('device', 'time', 'value', 'device', 'time', 'offset'),
        ('a', 10, 1, 'a', 8, 8.0),
        ('a', 20, 2, 'a', 15, 15.0),
        ('b', 5, 3, 'b', 1, 1.0),
        ('b', 30, 4, 'b', 10, 10.0),
    )
    ieq(expect_nearest, nearest)


def test_nearestjoin_device_isolation_compound_key():
    samples = (
        ('site', 'device', 'time', 'value'),
        ('north', 'd1', 10, 'n10'),
        ('north', 'd1', 20, 'n20'),
        ('south', 'd1', 10, 's10'),
        ('south', 'd1', 20, 's20'),
        ('north', 'd2', 10, 'x10'),
    )
    calibrations = (
        ('site', 'device', 'time', 'offset'),
        ('south', 'd1', 9, 's9'),
        ('north', 'd2', 11, 'x11'),
        ('north', 'd1', 15, 'n15'),
        ('south', 'd1', 19, 's19'),
    )

    actual = etl.nearestjoin(
        samples, calibrations, key=['site', 'device'], time='time',
        direction='backward', presorted=False
    )
    expect = (
        ('site', 'device', 'time', 'value', 'site', 'device', 'time',
         'offset'),
        ('north', 'd1', 10, 'n10', None, None, None, None),
        ('north', 'd1', 20, 'n20', 'north', 'd1', 15, 'n15'),
        ('north', 'd2', 10, 'x10', None, None, None, None),
        ('south', 'd1', 10, 's10', 'south', 'd1', 9, 's9'),
        ('south', 'd1', 20, 's20', 'south', 'd1', 19, 's19'),
    )
    ieq(expect, actual)


def test_nearestjoin_duplicate_times_and_nearest_tie():
    samples = (
        ('device', 'time'),
        ('a', 10),
    )
    calibrations = (
        ('device', 'time', 'label'),
        ('a', 9, 'previous'),
        ('a', 11, 'following'),
    )

    # Exactly in the middle: the preceding row is selected by rule.
    actual = etl.nearestjoin(
        samples, calibrations, key='device', time='time',
        direction='nearest', presorted=True
    )
    assert _rows(actual) == [
        ('device', 'time', 'device', 'time', 'label'),
        ('a', 10, 'a', 9, 'previous'),
    ]

    duplicate_calibrations = (
        ('device', 'time', 'label'),
        ('a', 10, 'first'),
        ('a', 10, 'second'),
    )
    expected = [
        ('device', 'time', 'device', 'time', 'label'),
        ('a', 10, 'a', 10, 'first'),
    ]
    for direction in ('backward', 'forward', 'nearest'):
        table = etl.nearestjoin(
            samples, duplicate_calibrations, key='device', time='time',
            direction=direction, presorted=True
        )
        assert _rows(table) == expected
        assert _rows(table) == expected


def test_nearestjoin_maxdiff_inclusive_and_exact_time():
    samples = (
        ('device', 'time'),
        ('a', 10),
        ('a', 15),
        ('a', 20),
    )
    calibrations = (
        ('device', 'time', 'label'),
        ('a', 5, 'five-before'),
        ('a', 10, 'exact'),
        ('a', 20, 'exact-20'),
        ('a', 25, 'ten-after'),
    )

    actual = etl.nearestjoin(
        samples, calibrations, key='device', time='time',
        direction='backward', maxdiff=5, presorted=True
    )
    assert _rows(actual)[1:] == [
        ('a', 10, 'a', 10, 'exact'),
        ('a', 15, 'a', 10, 'exact'),
        ('a', 20, 'a', 20, 'exact-20'),
    ]

    # Equality at zero tolerance is a valid match.
    exact = etl.nearestjoin(
        (('device', 'time'), ('a', 10)),
        (('device', 'time', 'label'), ('a', 10, 'yes')),
        key='device', time='time', direction='forward', maxdiff=0,
        presorted=True
    )
    assert _rows(exact)[1:] == [('a', 10, 'a', 10, 'yes')]

    # A difference just over the limit is not a match.
    too_far = etl.nearestjoin(
        (('device', 'time'), ('a', 10)),
        (('device', 'time', 'label'), ('a', 15, 'no')),
        key='device', time='time', direction='forward', maxdiff=4,
        presorted=True
    )
    assert _rows(too_far)[1:] == [('a', 10, None, None, None)]


def test_nearestjoin_no_match_and_prefixes():
    samples = (
        ('device', 'time', 'value'),
        ('a', 10, 1),
        ('b', 10, 2),
    )
    calibrations = (
        ('device', 'time', 'value'),
        ('a', 1, 'cal-a'),
        ('c', 10, 'cal-c'),
    )

    actual = etl.nearestjoin(
        samples, calibrations, key='device', time='time',
        direction='nearest', maxdiff=1, presorted=False,
        lprefix='sample_', rprefix='cal_'
    )
    expect = (
        ('sample_device', 'sample_time', 'sample_value', 'cal_device',
         'cal_time', 'cal_value'),
        ('a', 10, 1, None, None, None),
        ('b', 10, 2, None, None, None),
    )
    ieq(expect, actual)


def test_nearestjoin_different_key_and_time_field_names():
    samples = (
        ('site', 'sensor', 'sample_time', 'value'),
        ('north', 's1', 10, 'sample'),
    )
    calibrations = (
        ('location', 'device', 'calibrated_at', 'offset'),
        ('north', 's1', 12, 0.5),
    )

    actual = etl.nearestjoin(
        samples, calibrations, lkey=['site', 'sensor'],
        rkey=['location', 'device'], ltime='sample_time',
        rtime='calibrated_at'
    )
    assert _rows(actual) == [
        ('site', 'sensor', 'sample_time', 'value', 'location', 'device',
         'calibrated_at', 'offset'),
        ('north', 's1', 10, 'sample', 'north', 's1', 12, 0.5),
    ]


def test_nearestjoin_empty_tables():
    left_empty = (('device', 'time', 'value'),)
    right = (('device', 'time', 'offset'), ('a', 1, 1.0))
    assert _rows(etl.nearestjoin(
        left_empty, right, key='device', time='time'
    )) == [('device', 'time', 'value', 'device', 'time', 'offset')]

    left = (('device', 'time', 'value'), ('a', 1, 1))
    right_empty = (('device', 'time', 'offset'),)
    assert _rows(etl.nearestjoin(
        left, right_empty, key='device', time='time'
    )) == [
        ('device', 'time', 'value', 'device', 'time', 'offset'),
        ('a', 1, 1, None, None, None),
    ]

    assert _rows(etl.nearestjoin(
        left_empty, right_empty, key='device', time='time'
    )) == [('device', 'time', 'value', 'device', 'time', 'offset')]


def test_nearestjoin_invalid_arguments_and_values():
    left = (('device', 'time'), ('a', 1))
    right = (('device', 'time', 'offset'), ('a', 2, 0.1))

    with pytest.raises(ValueError):
        etl.nearestjoin(left, right, key='device', maxdiff=-1)
    with pytest.raises(ValueError):
        etl.nearestjoin(left, right, key='device', maxdiff='one')
    with pytest.raises(ValueError):
        etl.nearestjoin(left, right, key='device', maxdiff=float('nan'))
    with pytest.raises(ValueError):
        etl.nearestjoin(left, right, key='device', direction='sideways')
    with pytest.raises(TypeError):
        etl.nearestjoin(left, right, key='device', unknown=True)
    with pytest.raises(ValueError):
        list(etl.nearestjoin(
            (('device', 'time'), ('a', 'not-a-number')),
            right, key='device'
        ))
    with pytest.raises(ValueError):
        list(etl.nearestjoin(
            left,
            (('device', 'time', 'offset'), ('a', None, 0.1)),
            key='device'
        ))
    with pytest.raises(ValueError):
        list(etl.nearestjoin(
            left,
            (('device', 'time', 'offset'), ('a', float('inf'), 0.1)),
            key='device'
        ))


def _brute_force_backward(samples, calibrations, maxdiff=None):
    by_device = {}
    for row in calibrations[1:]:
        by_device.setdefault(row[0], []).append(row)

    out = [samples[0] + calibrations[0]]
    ordered_samples = sorted(samples[1:], key=lambda row: (row[0], row[1]))
    for row in ordered_samples:
        candidates = [
            r for r in by_device.get(row[0], []) if r[1] <= row[1]
        ]
        if candidates:
            candidate = max(candidates, key=lambda r: r[1])
            if maxdiff is None or row[1] - candidate[1] <= maxdiff:
                out.append(row + candidate)
                continue
        out.append(row + (None,) * (len(calibrations[0])))
    return out


def test_nearestjoin_large_unsorted_external_sort_and_presorted_equivalence():
    random.seed(1234)
    samples = [('device', 'time', 'value')]
    calibrations = [('device', 'time', 'offset')]
    for i in range(3000):
        device = 'device-%d' % (i % 7)
        samples.append((device, random.uniform(0, 1000), i))
    for i in range(4000):
        device = 'device-%d' % (i % 7)
        calibrations.append((device, random.uniform(0, 1000), i * 0.5))

    expect = _brute_force_backward(samples, calibrations, maxdiff=5)
    actual = list(etl.nearestjoin(
        samples, calibrations, key='device', time='time',
        direction='backward', maxdiff=5, buffersize=17, presorted=False
    ))
    assert actual == expect

    presorted_samples = etl.sort(samples, ['device', 'time'])
    presorted_calibrations = etl.sort(calibrations, ['device', 'time'])
    streamed = list(etl.nearestjoin(
        presorted_samples, presorted_calibrations, key='device', time='time',
        direction='backward', maxdiff=5, presorted=True
    ))
    assert streamed == expect


class OnePassTable(object):

    def __init__(self, rows):
        self._rows = iter(rows)
        self.consumed = 0
        self._iterated = False

    def __iter__(self):
        if self._iterated:
            raise RuntimeError('one-pass input iterated twice')
        self._iterated = True
        return self

    def __next__(self):
        row = next(self._rows)
        self.consumed += 1
        return row

    next = __next__


def test_nearestjoin_one_pass_presorted_input():
    left = OnePassTable([
        ('device', 'time', 'value'),
        ('a', 1, 10),
        ('a', 3, 30),
        ('b', 2, 20),
    ])
    right = OnePassTable([
        ('device', 'time', 'offset'),
        ('a', 2, 0.2),
        ('b', 1, 0.1),
    ])

    actual = list(etl.nearestjoin(
        left, right, key='device', time='time', direction='nearest',
        presorted=True
    ))
    assert actual == [
        ('device', 'time', 'value', 'device', 'time', 'offset'),
        ('a', 1, 10, 'a', 2, 0.2),
        ('a', 3, 30, 'a', 2, 0.2),
        ('b', 2, 20, 'b', 1, 0.1),
    ]
    assert left.consumed == 4
    assert right.consumed == 3

    # A natural device key can also be resolved without a second pass.
    left = OnePassTable([
        ('device', 'time', 'value'),
        ('a', 1, 10),
        ('b', 2, 20),
    ])
    right = OnePassTable([
        ('device', 'time', 'offset'),
        ('a', 2, 0.2),
        ('b', 1, 0.1),
    ])
    actual = list(etl.nearestjoin(
        left, right, time='time', direction='nearest', presorted=True
    ))
    assert actual[1:] == [
        ('a', 1, 10, 'a', 2, 0.2),
        ('b', 2, 20, 'b', 1, 0.1),
    ]
    assert left.consumed == 3
    assert right.consumed == 3


def test_nearestjoin_repeatable_result_iteration_and_table_method():
    left = etl.wrap([
        ('device', 'time', 'value'),
        ('a', 10, 1),
    ])
    right = etl.wrap([
        ('device', 'time', 'offset'),
        ('a', 9, 0.1),
        ('a', 11, 0.2),
    ])
    table = left.nearestjoin(
        right, key='device', time='time', direction='nearest'
    )
    first = list(table)
    second = list(table)
    assert first == second
    assert first[1:] == [('a', 10, 1, 'a', 9, 0.1)]


def test_existing_join_and_sort_still_work():
    table1 = (('id', 'colour'), (1, 'blue'), (2, 'red'))
    table2 = (('id', 'shape'), (1, 'circle'), (3, 'square'))
    assert list(etl.join(table1, table2, key='id')) == [
        ('id', 'colour', 'shape'),
        (1, 'blue', 'circle'),
    ]
    assert list(etl.sort(table1, 'id', reverse=True)) == [
        ('id', 'colour'),
        (2, 'red'),
        (1, 'blue'),
    ]
