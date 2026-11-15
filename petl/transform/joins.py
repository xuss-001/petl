from __future__ import absolute_import, division, print_function

import itertools
import math
from numbers import Number

from petl.comparison import Comparable, comparable_itemgetter
from petl.compat import next, string_types, text_type
from petl.errors import ArgumentError
from petl.transform.basics import cut, cutout, stack
from petl.transform.dedup import distinct
from petl.transform.sorts import sort
from petl.util.base import Table, asindices, data, header, rowgetter, rowgroupby


def natural_key(left, right):
    # determine key field or fields
    lhdr = header(left)
    lflds = list(map(str, lhdr))
    rhdr = header(right)
    rflds = list(map(str, rhdr))
    key = [f for f in lflds if f in rflds]
    assert len(key) > 0, 'no fields in common'
    if len(key) == 1:
        key = key[0]  # deal with singletons
    return key


def keys_from_args(left, right, key, lkey, rkey):

    if key is lkey is rkey is None:
        # no keys specified, attempt natural join
        lkey = rkey = natural_key(left, right)
    elif key is not None and lkey is rkey is None:
        # common key specified
        lkey = rkey = key
    elif key is None and lkey is not None and rkey is not None:
        # left and right keys specified
        pass
    else:
        raise ArgumentError(
            'bad key arguments: either specify key, or specify both lkey and '
            'rkey, or provide no key/lkey/rkey arguments at all (natural join)'
        )
    return lkey, rkey


def _nonkey_indices(hdr, indices):
    """Return non-key positions, including when keys use negative indexes.

    >>> _nonkey_indices(('id', 'value'), (-1,))
    [0]
    """
    key_indices = {i if i >= 0 else len(hdr) + i for i in indices}
    return [i for i in range(len(hdr)) if i not in key_indices]


def join(left, right, key=None, lkey=None, rkey=None, presorted=False,
         buffersize=None, tempdir=None, cache=True, lprefix=None, rprefix=None):
    """
    Perform an equi-join on the given tables. E.g.::

        >>> import petl as etl
        >>> table1 = [['id', 'colour'],
        ...           [1, 'blue'],
        ...           [2, 'red'],
        ...           [3, 'purple']]
        >>> table2 = [['id', 'shape'],
        ...           [1, 'circle'],
        ...           [3, 'square'],
        ...           [4, 'ellipse']]
        >>> table3 = etl.join(table1, table2, key='id')
        >>> table3
        +----+----------+----------+
        | id | colour   | shape    |
        +====+==========+==========+
        |  1 | 'blue'   | 'circle' |
        +----+----------+----------+
        |  3 | 'purple' | 'square' |
        +----+----------+----------+

        >>> # if no key is given, a natural join is tried
        ... table4 = etl.join(table1, table2)
        >>> table4
        +----+----------+----------+
        | id | colour   | shape    |
        +====+==========+==========+
        |  1 | 'blue'   | 'circle' |
        +----+----------+----------+
        |  3 | 'purple' | 'square' |
        +----+----------+----------+

        >>> # note behaviour if the key is not unique in either or both tables
        ... table5 = [['id', 'colour'],
        ...           [1, 'blue'],
        ...           [1, 'red'],
        ...           [2, 'purple']]
        >>> table6 = [['id', 'shape'],
        ...           [1, 'circle'],
        ...           [1, 'square'],
        ...           [2, 'ellipse']]
        >>> table7 = etl.join(table5, table6, key='id')
        >>> table7
        +----+----------+-----------+
        | id | colour   | shape     |
        +====+==========+===========+
        |  1 | 'blue'   | 'circle'  |
        +----+----------+-----------+
        |  1 | 'blue'   | 'square'  |
        +----+----------+-----------+
        |  1 | 'red'    | 'circle'  |
        +----+----------+-----------+
        |  1 | 'red'    | 'square'  |
        +----+----------+-----------+
        |  2 | 'purple' | 'ellipse' |
        +----+----------+-----------+

        >>> # compound keys are supported
        ... table8 = [['id', 'time', 'height'],
        ...           [1, 1, 12.3],
        ...           [1, 2, 34.5],
        ...           [2, 1, 56.7]]
        >>> table9 = [['id', 'time', 'weight'],
        ...           [1, 2, 4.5],
        ...           [2, 1, 6.7],
        ...           [2, 2, 8.9]]
        >>> table10 = etl.join(table8, table9, key=['id', 'time'])
        >>> table10
        +----+------+--------+--------+
        | id | time | height | weight |
        +====+======+========+========+
        |  1 |    2 |   34.5 |    4.5 |
        +----+------+--------+--------+
        |  2 |    1 |   56.7 |    6.7 |
        +----+------+--------+--------+

    If `presorted` is True, it is assumed that the data are already sorted by
    the given key, and the `buffersize`, `tempdir` and `cache` arguments are
    ignored. Otherwise, the data are sorted, see also the discussion of the
    `buffersize`, `tempdir` and `cache` arguments under the
    :func:`petl.transform.sorts.sort` function.

    Left and right tables with different key fields can be handled via the
    `lkey` and `rkey` arguments.

    .. versionchanged:: 1.7.16
        To ensure correct results for tables with uneven rows, tables will be
        squared up before joining to ensure correct results.

    """

    # TODO don't read data twice (occurs if using natural key)
    lkey, rkey = keys_from_args(left, right, key, lkey, rkey)
    return JoinView(left, right, lkey=lkey, rkey=rkey,
                    presorted=presorted, buffersize=buffersize, tempdir=tempdir,
                    cache=cache, lprefix=lprefix, rprefix=rprefix)


Table.join = join


class JoinView(Table):

    def __init__(self, left, right, lkey, rkey,
                 presorted=False, leftouter=False, rightouter=False,
                 missing=None, buffersize=None, tempdir=None, cache=True,
                 lprefix=None, rprefix=None):
        self.lkey = lkey
        self.rkey = rkey
        self.left = stack(left, missing=missing)
        self.right = stack(right, missing=missing)
        if not presorted:
            self.left = sort(self.left, lkey, buffersize=buffersize,
                             tempdir=tempdir, cache=cache)
            self.right = sort(self.right, rkey, buffersize=buffersize,
                              tempdir=tempdir, cache=cache)
        self.leftouter = leftouter
        self.rightouter = rightouter
        self.missing = missing
        self.lprefix = lprefix
        self.rprefix = rprefix

    def __iter__(self):
        return iterjoin(self.left, self.right, self.lkey, self.rkey,
                        leftouter=self.leftouter, rightouter=self.rightouter,
                        missing=self.missing, lprefix=self.lprefix,
                        rprefix=self.rprefix)


def leftjoin(left, right, key=None, lkey=None, rkey=None, missing=None,
             presorted=False, buffersize=None, tempdir=None, cache=True,
             lprefix=None, rprefix=None):
    """
    Perform a left outer join on the given tables. E.g.::

        >>> import petl as etl
        >>> table1 = [['id', 'colour'],
        ...           [1, 'blue'],
        ...           [2, 'red'],
        ...           [3, 'purple']]
        >>> table2 = [['id', 'shape'],
        ...           [1, 'circle'],
        ...           [3, 'square'],
        ...           [4, 'ellipse']]
        >>> table3 = etl.leftjoin(table1, table2, key='id')
        >>> table3
        +----+----------+----------+
        | id | colour   | shape    |
        +====+==========+==========+
        |  1 | 'blue'   | 'circle' |
        +----+----------+----------+
        |  2 | 'red'    | None     |
        +----+----------+----------+
        |  3 | 'purple' | 'square' |
        +----+----------+----------+

    If `presorted` is True, it is assumed that the data are already sorted by
    the given key, and the `buffersize`, `tempdir` and `cache` arguments are
    ignored. Otherwise, the data are sorted, see also the discussion of the
    `buffersize`, `tempdir` and `cache` arguments under the
    :func:`petl.transform.sorts.sort` function.

    Left and right tables with different key fields can be handled via the
    `lkey` and `rkey` arguments.

    .. versionchanged:: 1.7.16
        To ensure correct results for tables with uneven rows, tables will be
        squared up before joining to ensure correct results.

    """

    # TODO don't read data twice (occurs if using natural key)
    lkey, rkey = keys_from_args(left, right, key, lkey, rkey)
    return JoinView(left, right, lkey=lkey, rkey=rkey,
                    presorted=presorted, leftouter=True, rightouter=False,
                    missing=missing, buffersize=buffersize, tempdir=tempdir,
                    cache=cache, lprefix=lprefix, rprefix=rprefix)


Table.leftjoin = leftjoin


def rightjoin(left, right, key=None, lkey=None, rkey=None, missing=None,
              presorted=False, buffersize=None, tempdir=None, cache=True,
              lprefix=None, rprefix=None):
    """
    Perform a right outer join on the given tables. E.g.::

        >>> import petl as etl
        >>> table1 = [['id', 'colour'],
        ...           [1, 'blue'],
        ...           [2, 'red'],
        ...           [3, 'purple']]
        >>> table2 = [['id', 'shape'],
        ...           [1, 'circle'],
        ...           [3, 'square'],
        ...           [4, 'ellipse']]
        >>> table3 = etl.rightjoin(table1, table2, key='id')
        >>> table3
        +----+----------+-----------+
        | id | colour   | shape     |
        +====+==========+===========+
        |  1 | 'blue'   | 'circle'  |
        +----+----------+-----------+
        |  3 | 'purple' | 'square'  |
        +----+----------+-----------+
        |  4 | None     | 'ellipse' |
        +----+----------+-----------+

    If `presorted` is True, it is assumed that the data are already sorted by
    the given key, and the `buffersize`, `tempdir` and `cache` arguments are
    ignored. Otherwise, the data are sorted, see also the discussion of the
    `buffersize`, `tempdir` and `cache` arguments under the
    :func:`petl.transform.sorts.sort` function.

    Left and right tables with different key fields can be handled via the
    `lkey` and `rkey` arguments.

    .. versionchanged:: 1.7.16
        To ensure correct results for tables with uneven rows, tables will be
        squared up before joining to ensure correct results.

    """

    # TODO don't read data twice (occurs if using natural key)
    lkey, rkey = keys_from_args(left, right, key, lkey, rkey)
    return JoinView(left, right, lkey=lkey, rkey=rkey,
                    presorted=presorted, leftouter=False, rightouter=True,
                    missing=missing, buffersize=buffersize,
                    tempdir=tempdir, cache=cache, lprefix=lprefix,
                    rprefix=rprefix)


Table.rightjoin = rightjoin


def outerjoin(left, right, key=None, lkey=None, rkey=None, missing=None,
              presorted=False, buffersize=None, tempdir=None, cache=True,
              lprefix=None, rprefix=None):
    """
    Perform a full outer join on the given tables. E.g.::

        >>> import petl as etl
        >>> table1 = [['id', 'colour'],
        ...           [1, 'blue'],
        ...           [2, 'red'],
        ...           [3, 'purple']]
        >>> table2 = [['id', 'shape'],
        ...           [1, 'circle'],
        ...           [3, 'square'],
        ...           [4, 'ellipse']]
        >>> table3 = etl.outerjoin(table1, table2, key='id')
        >>> table3
        +----+----------+-----------+
        | id | colour   | shape     |
        +====+==========+===========+
        |  1 | 'blue'   | 'circle'  |
        +----+----------+-----------+
        |  2 | 'red'    | None      |
        +----+----------+-----------+
        |  3 | 'purple' | 'square'  |
        +----+----------+-----------+
        |  4 | None     | 'ellipse' |
        +----+----------+-----------+

    If `presorted` is True, it is assumed that the data are already sorted by
    the given key, and the `buffersize`, `tempdir` and `cache` arguments are
    ignored. Otherwise, the data are sorted, see also the discussion of the
    `buffersize`, `tempdir` and `cache` arguments under the
    :func:`petl.transform.sorts.sort` function.

    Left and right tables with different key fields can be handled via the
    `lkey` and `rkey` arguments.

    .. versionchanged:: 1.7.16
        To ensure correct results for tables with uneven rows, tables will be
        squared up before joining to ensure correct results.

    """

    # TODO don't read data twice (occurs if using natural key)
    lkey, rkey = keys_from_args(left, right, key, lkey, rkey)
    return JoinView(left, right, lkey=lkey, rkey=rkey,
                    presorted=presorted, leftouter=True, rightouter=True,
                    missing=missing, buffersize=buffersize, tempdir=tempdir,
                    cache=cache, lprefix=lprefix, rprefix=rprefix)


Table.outerjoin = outerjoin


def iterjoin(left, right, lkey, rkey, leftouter=False, rightouter=False,
             missing=None, lprefix=None, rprefix=None):
    lit = iter(left)
    rit = iter(right)

    lhdr = next(lit)
    rhdr = next(rit)

    # determine indices of the key fields in left and right tables
    lkind = asindices(lhdr, lkey)
    rkind = asindices(rhdr, rkey)

    # construct functions to extract key values from both tables
    lgetk = comparable_itemgetter(*lkind)
    rgetk = comparable_itemgetter(*rkind)

    # determine indices of non-key fields in the right table
    # (in the output, we only include key fields from the left table - we
    # don't want to duplicate fields)
    rvind = _nonkey_indices(rhdr, rkind)
    rgetv = rowgetter(*rvind)

    # determine the output fields
    if lprefix is None:
        outhdr = list(lhdr)
    else:
        outhdr = [(text_type(lprefix) + text_type(f)) for f in lhdr]
    if rprefix is None:
        outhdr.extend(rgetv(rhdr))
    else:
        outhdr.extend([(text_type(rprefix) + text_type(f)) for f in rgetv(rhdr)])
    yield tuple(outhdr)

    # define a function to join two groups of rows
    def joinrows(_lrowgrp, _rrowgrp):
        if _rrowgrp is None:
            for lrow in _lrowgrp:
                outrow = list(lrow)  # start with the left row
                # extend with missing values in place of the right row
                outrow.extend([missing] * len(rvind))
                yield tuple(outrow)
        elif _lrowgrp is None:
            for rrow in _rrowgrp:
                # start with missing values in place of the left row
                outrow = [missing] * len(lhdr)
                # set key values
                for li, ri in zip(lkind, rkind):
                    outrow[li] = rrow[ri]
                # extend with non-key values from the right row
                outrow.extend(rgetv(rrow))
                yield tuple(outrow)
        else:
            _rrowgrp = list(_rrowgrp)  # may need to iterate more than once
            for lrow in _lrowgrp:
                for rrow in _rrowgrp:
                    # start with the left row
                    outrow = list(lrow)
                    # extend with non-key values from the right row
                    outrow.extend(rgetv(rrow))
                    yield tuple(outrow)

    # construct group iterators for both tables
    lgit = itertools.groupby(lit, key=lgetk)
    rgit = itertools.groupby(rit, key=rgetk)
    lrowgrp = []
    rrowgrp = []

    # loop until *either* of the iterators is exhausted
    # initialise here to handle empty tables
    lkval, rkval = Comparable(None), Comparable(None)
    try:

        # pick off initial row groups
        lkval, lrowgrp = next(lgit)
        rkval, rrowgrp = next(rgit)

        while True:
            if lkval < rkval:
                if leftouter:
                    for row in joinrows(lrowgrp, None):
                        yield tuple(row)
                # advance left
                lkval, lrowgrp = next(lgit)
            elif lkval > rkval:
                if rightouter:
                    for row in joinrows(None, rrowgrp):
                        yield tuple(row)
                # advance right
                rkval, rrowgrp = next(rgit)
            else:
                for row in joinrows(lrowgrp, rrowgrp):
                    yield tuple(row)
                # advance both
                lkval, lrowgrp = next(lgit)
                rkval, rrowgrp = next(rgit)

    except StopIteration:
        pass

    # make sure any left rows remaining are yielded
    if leftouter:
        if lkval > rkval:
            # yield anything that got left hanging
            for row in joinrows(lrowgrp, None):
                yield tuple(row)
        # yield the rest
        for lkval, lrowgrp in lgit:
            for row in joinrows(lrowgrp, None):
                yield tuple(row)

    # make sure any right rows remaining are yielded
    if rightouter:
        if lkval < rkval:
            # yield anything that got left hanging
            for row in joinrows(None, rrowgrp):
                yield tuple(row)
        # yield the rest
        for rkval, rrowgrp in rgit:
            for row in joinrows(None, rrowgrp):
                yield tuple(row)


def crossjoin(*tables, **kwargs):
    """
    Form the cartesian product of the given tables. E.g.::

        >>> import petl as etl
        >>> table1 = [['id', 'colour'],
        ...           [1, 'blue'],
        ...           [2, 'red']]
        >>> table2 = [['id', 'shape'],
        ...           [1, 'circle'],
        ...           [3, 'square']]
        >>> table3 = etl.crossjoin(table1, table2)
        >>> table3
        +----+--------+----+----------+
        | id | colour | id | shape    |
        +====+========+====+==========+
        |  1 | 'blue' |  1 | 'circle' |
        +----+--------+----+----------+
        |  1 | 'blue' |  3 | 'square' |
        +----+--------+----+----------+
        |  2 | 'red'  |  1 | 'circle' |
        +----+--------+----+----------+
        |  2 | 'red'  |  3 | 'square' |
        +----+--------+----+----------+

    If `prefix` is `True` then field names in the output table header will be
    prefixed by the index of the input table.

    .. versionchanged:: 1.7.16
        To ensure correct results for tables with uneven rows, tables will be
        squared up before joining to ensure correct results.

    """

    return CrossJoinView(*tables, **kwargs)


Table.crossjoin = crossjoin


class CrossJoinView(Table):

    def __init__(self, *sources, **kwargs):
        self.prefix = kwargs.get('prefix', False)
        self.missing = kwargs.get('missing', None)
        self.sources = [stack(source, missing=self.missing) for source in sources]

    def __iter__(self):
        return itercrossjoin(self.sources, self.prefix)


def itercrossjoin(sources, prefix):

    # construct fields
    outhdr = list()
    for i, s in enumerate(sources):
        if prefix:
            # use one-based numbering
            outhdr.extend([text_type(i+1) + '_' + text_type(f) for f in header(s)])
        else:
            outhdr.extend(header(s))
    yield tuple(outhdr)

    datasrcs = [data(src) for src in sources]
    for prod in itertools.product(*datasrcs):
        outrow = list()
        for row in prod:
            outrow.extend(row)
        yield tuple(outrow)


def antijoin(left, right, key=None, lkey=None, rkey=None, presorted=False,
             buffersize=None, tempdir=None, cache=True):
    """
    Return rows from the `left` table where the key value does not occur in
    the `right` table. E.g.::

        >>> import petl as etl
        >>> table1 = [['id', 'colour'],
        ...           [0, 'black'],
        ...           [1, 'blue'],
        ...           [2, 'red'],
        ...           [4, 'yellow'],
        ...           [5, 'white']]
        >>> table2 = [['id', 'shape'],
        ...           [1, 'circle'],
        ...           [3, 'square']]
        >>> table3 = etl.antijoin(table1, table2, key='id')
        >>> table3
        +----+----------+
        | id | colour   |
        +====+==========+
        |  0 | 'black'  |
        +----+----------+
        |  2 | 'red'    |
        +----+----------+
        |  4 | 'yellow' |
        +----+----------+
        |  5 | 'white'  |
        +----+----------+

    If `presorted` is True, it is assumed that the data are already sorted by
    the given key, and the `buffersize`, `tempdir` and `cache` arguments are
    ignored. Otherwise, the data are sorted, see also the discussion of the
    `buffersize`, `tempdir` and `cache` arguments under the
    :func:`petl.transform.sorts.sort` function.

    Left and right tables with different key fields can be handled via the
    `lkey` and `rkey` arguments.

    """

    lkey, rkey = keys_from_args(left, right, key, lkey, rkey)
    return AntiJoinView(left=left, right=right, lkey=lkey, rkey=rkey,
                        presorted=presorted, buffersize=buffersize,
                        tempdir=tempdir, cache=cache)


Table.antijoin = antijoin


class AntiJoinView(Table):

    def __init__(self, left, right, lkey, rkey, presorted=False,
                 buffersize=None, tempdir=None, cache=True):
        if presorted:
            self.left = left
            self.right = right
        else:
            self.left = sort(left, lkey, buffersize=buffersize,
                             tempdir=tempdir, cache=cache)
            self.right = sort(right, rkey, buffersize=buffersize,
                              tempdir=tempdir, cache=cache)
        self.lkey = lkey
        self.rkey = rkey

    def __iter__(self):
        return iterantijoin(self.left, self.right, self.lkey, self.rkey)


def iterantijoin(left, right, lkey, rkey):
    lit = iter(left)
    rit = iter(right)

    lhdr = next(lit)
    rhdr = next(rit)
    yield tuple(lhdr)

    # determine indices of the key fields in left and right tables
    lkind = asindices(lhdr, lkey)
    rkind = asindices(rhdr, rkey)

    # construct functions to extract key values from both tables
    lgetk = comparable_itemgetter(*lkind)
    rgetk = comparable_itemgetter(*rkind)

    # construct group iterators for both tables
    lgit = itertools.groupby(lit, key=lgetk)
    rgit = itertools.groupby(rit, key=rgetk)
    lrowgrp = []

    # loop until *either* of the iterators is exhausted
    lkval, rkval = Comparable(None), Comparable(None)
    try:

        # pick off initial row groups
        lkval, lrowgrp = next(lgit)
        rkval, _ = next(rgit)

        while True:
            if lkval < rkval:
                for row in lrowgrp:
                    yield tuple(row)
                # advance left
                lkval, lrowgrp = next(lgit)
            elif lkval > rkval:
                # advance right
                rkval, _ = next(rgit)
            else:
                # advance both
                lkval, lrowgrp = next(lgit)
                rkval, _ = next(rgit)

    except StopIteration:
        pass

    # any left over?
    if lkval > rkval:
        # yield anything that got left hanging
        for row in lrowgrp:
            yield tuple(row)
    # and the rest...
    for lkval, lrowgrp in lgit:
        for row in lrowgrp:
            yield tuple(row)


def lookupjoin(left, right, key=None, lkey=None, rkey=None, missing=None,
               presorted=False, buffersize=None, tempdir=None, cache=True,
               lprefix=None, rprefix=None):
    """
    Perform a left join, but where the key is not unique in the right-hand
    table, arbitrarily choose the first row and ignore others. E.g.::

        >>> import petl as etl
        >>> table1 = [['id', 'color', 'cost'],
        ...           [1, 'blue', 12],
        ...           [2, 'red', 8],
        ...           [3, 'purple', 4]]
        >>> table2 = [['id', 'shape', 'size'],
        ...           [1, 'circle', 'big'],
        ...           [1, 'circle', 'small'],
        ...           [2, 'square', 'tiny'],
        ...           [2, 'square', 'big'],
        ...           [3, 'ellipse', 'small'],
        ...           [3, 'ellipse', 'tiny']]
        >>> table3 = etl.lookupjoin(table1, table2, key='id')
        >>> table3
        +----+----------+------+-----------+---------+
        | id | color    | cost | shape     | size    |
        +====+==========+======+===========+=========+
        |  1 | 'blue'   |   12 | 'circle'  | 'big'   |
        +----+----------+------+-----------+---------+
        |  2 | 'red'    |    8 | 'square'  | 'tiny'  |
        +----+----------+------+-----------+---------+
        |  3 | 'purple' |    4 | 'ellipse' | 'small' |
        +----+----------+------+-----------+---------+

    See also :func:`petl.transform.joins.leftjoin`.

    .. versionchanged:: 1.7.16
        To ensure correct results for tables with uneven rows, tables will be
        squared up before joining to ensure correct results.

    """

    lkey, rkey = keys_from_args(left, right, key, lkey, rkey)
    return LookupJoinView(left, right, lkey, rkey, presorted=presorted,
                          missing=missing, buffersize=buffersize,
                          tempdir=tempdir, cache=cache,
                          lprefix=lprefix, rprefix=rprefix)


Table.lookupjoin = lookupjoin


class LookupJoinView(Table):

    def __init__(self, left, right, lkey, rkey, presorted=False, missing=None,
                 buffersize=None, tempdir=None, cache=True,
                 lprefix=None, rprefix=None):
        self.left = stack(left, missing=missing)
        self.right = stack(right, missing=missing)
        if not presorted:
            self.left = sort(self.left, lkey, buffersize=buffersize,
                             tempdir=tempdir, cache=cache)
            self.right = sort(self.right, rkey, buffersize=buffersize,
                              tempdir=tempdir, cache=cache)
        self.lkey = lkey
        self.rkey = rkey
        self.missing = missing
        self.lprefix = lprefix
        self.rprefix = rprefix

    def __iter__(self):
        return iterlookupjoin(self.left, self.right, self.lkey, self.rkey,
                              missing=self.missing, lprefix=self.lprefix,
                              rprefix=self.rprefix)


def iterlookupjoin(left, right, lkey, rkey, missing=None, lprefix=None,
                   rprefix=None):
    lit = iter(left)
    rit = iter(right)

    lhdr = next(lit)
    rhdr = next(rit)

    # determine indices of the key fields in left and right tables
    lkind = asindices(lhdr, lkey)
    rkind = asindices(rhdr, rkey)

    # construct functions to extract key values from both tables
    lgetk = comparable_itemgetter(*lkind)
    rgetk = comparable_itemgetter(*rkind)

    # determine indices of non-key fields in the right table
    # (in the output, we only include key fields from the left table - we
    # don't want to duplicate fields)
    rvind = _nonkey_indices(rhdr, rkind)
    rgetv = rowgetter(*rvind)

    # determine the output fields
    if lprefix is None:
        outhdr = list(lhdr)
    else:
        outhdr = [(text_type(lprefix) + text_type(f)) for f in lhdr]
    if rprefix is None:
        outhdr.extend(rgetv(rhdr))
    else:
        outhdr.extend([(text_type(rprefix) + text_type(f)) for f in rgetv(rhdr)])
    yield tuple(outhdr)

    # define a function to join two groups of rows
    def joinrows(_lrowgrp, _rrowgrp):
        if _rrowgrp is None:
            for lrow in _lrowgrp:
                outrow = list(lrow)  # start with the left row
                # extend with missing values in place of the right row
                outrow.extend([missing] * len(rvind))
                yield tuple(outrow)
        else:
            rrow = next(iter(_rrowgrp))  # pick first arbitrarily
            for lrow in _lrowgrp:
                # start with the left row
                outrow = list(lrow)
                # extend with non-key values from the right row
                outrow.extend(rgetv(rrow))
                yield tuple(outrow)

    # construct group iterators for both tables
    lgit = itertools.groupby(lit, key=lgetk)
    rgit = itertools.groupby(rit, key=rgetk)
    lrowgrp = []

    # whether lrowgrp holds a group that has been picked off lgit but not yet
    # yielded; tracked explicitly because no key value can stand in for
    # "iterator exhausted" - None is itself a valid key
    lgrp_pending = False

    # loop until *either* of the iterators is exhausted
    try:

        # pick off initial row groups
        lkval, lrowgrp = next(lgit)
        lgrp_pending = True
        rkval, rrowgrp = next(rgit)

        while True:
            if lkval < rkval:
                for row in joinrows(lrowgrp, None):
                    yield tuple(row)
                # advance left
                lgrp_pending = False
                lkval, lrowgrp = next(lgit)
                lgrp_pending = True
            elif lkval > rkval:
                # advance right
                rkval, rrowgrp = next(rgit)
            else:
                for row in joinrows(lrowgrp, rrowgrp):
                    yield tuple(row)
                # advance both
                lgrp_pending = False
                lkval, lrowgrp = next(lgit)
                lgrp_pending = True
                rkval, rrowgrp = next(rgit)

    except StopIteration:
        pass

    # make sure any left rows remaining are yielded
    if lgrp_pending:
        # yield anything that got left hanging
        for row in joinrows(lrowgrp, None):
            yield tuple(row)
    # yield the rest
    for lkval, lrowgrp in lgit:
        for row in joinrows(lrowgrp, None):
            yield tuple(row)


_MATCH_BACKWARD = 'backward'
_MATCH_FORWARD = 'forward'
_MATCH_NEAREST = 'nearest'

_MATCH_DIRECTION_ALIASES = {
    'backward': _MATCH_BACKWARD,
    'back': _MATCH_BACKWARD,
    'before': _MATCH_BACKWARD,
    'preceding': _MATCH_BACKWARD,
    'previous': _MATCH_BACKWARD,
    'prior': _MATCH_BACKWARD,
    'last': _MATCH_BACKWARD,
    'not_later': _MATCH_BACKWARD,
    'not_later_than': _MATCH_BACKWARD,
    'le': _MATCH_BACKWARD,
    'forward': _MATCH_FORWARD,
    'ahead': _MATCH_FORWARD,
    'after': _MATCH_FORWARD,
    'following': _MATCH_FORWARD,
    'next': _MATCH_FORWARD,
    'first': _MATCH_FORWARD,
    'not_earlier': _MATCH_FORWARD,
    'not_earlier_than': _MATCH_FORWARD,
    'ge': _MATCH_FORWARD,
    'nearest': _MATCH_NEAREST,
    'closest': _MATCH_NEAREST,
    'near': _MATCH_NEAREST,
    'either': _MATCH_NEAREST,
    'both': _MATCH_NEAREST,
}


class _TimeMatchError(ArgumentError, ValueError, TypeError):
    """Raised for invalid time matching arguments or time values.

    Inherits from :class:`petl.errors.ArgumentError`, :class:`ValueError` and
    :class:`TypeError` so callers can use either exception style.
    """


def _normalise_direction(direction):
    if direction is None:
        return _MATCH_NEAREST
    if not isinstance(direction, string_types):
        raise _TimeMatchError(
            'direction must be one of backward, forward or nearest'
        )
    key = direction.strip().lower().replace('-', '_').replace(' ', '_')
    try:
        return _MATCH_DIRECTION_ALIASES[key]
    except KeyError:
        raise _TimeMatchError(
            'direction must be one of backward, forward or nearest'
        )


def _is_nonfinite(value):
    if isinstance(value, float):
        return math.isnan(value) or math.isinf(value)
    try:
        if value.is_nan():
            return True
    except (AttributeError, TypeError):
        pass
    try:
        if value.is_infinite():
            return True
    except (AttributeError, TypeError):
        pass
    return False


def _validate_number(value, name):
    if isinstance(value, bool) or not isinstance(value, Number) \
            or isinstance(value, complex):
        raise _TimeMatchError('%s must be a number, got %r' % (name, value))
    if _is_nonfinite(value):
        raise _TimeMatchError('%s must be finite, got %r' % (name, value))
    return value


def _validate_maxdiff(maxdiff):
    if maxdiff is None:
        return None
    _validate_number(maxdiff, 'maxdiff')
    if maxdiff < 0:
        raise _TimeMatchError('maxdiff must be greater than or equal to zero')
    return maxdiff


def _as_field_list(fields):
    if fields is None:
        return None
    if isinstance(fields, (list, tuple)):
        return list(fields)
    return [fields]


def _as_time_field(fields):
    if isinstance(fields, (list, tuple)):
        if len(fields) != 1:
            raise _TimeMatchError('time field must identify exactly one field')
        return fields[0]
    return fields


def _without_time_field(device_key, time_field):
    if device_key is None:
        return None
    fields = _as_field_list(device_key)
    return [f for f in fields if f != time_field]


def _field_name(hdr, spec):
    indices = asindices(hdr, spec)
    if len(indices) != 1:
        raise _TimeMatchError('time field must identify exactly one field')
    return text_type(hdr[indices[0]])


def _natural_device_key(left, right, ltime, rtime):
    lhdr = header(left)
    rhdr = header(right)
    lflds = list(map(text_type, lhdr))
    rflds = list(map(text_type, rhdr))
    ltime_name = _field_name(lhdr, ltime)
    rtime_name = _field_name(rhdr, rtime)
    time_fields = {ltime_name, rtime_name}
    key = [f for f in lflds if f in rflds and f not in time_fields]
    if len(key) == 1:
        return key[0], key[0]
    return key, key


def _device_getter(indices):
    if not indices:
        # An empty device key means all rows belong to the same device.
        return lambda row: Comparable(())
    return comparable_itemgetter(*indices)


def _sort_key(device_key, time_field):
    key = _as_field_list(device_key)
    key.append(time_field)
    return key


def _getter_for_time(hdr, time_field, missing):
    indices = asindices(hdr, time_field)
    if len(indices) != 1:
        raise _TimeMatchError('time field must identify exactly one field')
    index = indices[0]

    def gettime(row):
        try:
            value = row[index]
        except IndexError:
            value = missing
        return _validate_number(value, 'time value')

    return gettime


def _validated_rows(rows, gettime):
    for row in rows:
        gettime(row)
        yield row


def _squared(table, missing):
    """Iterate a table once, trimming or padding rows to its header."""

    it = iter(table)
    try:
        hdr = tuple(next(it))
    except StopIteration:
        return
    n = len(hdr)
    yield hdr
    while True:
        try:
            row = next(it)
        except StopIteration:
            return
        outrow = tuple(row)
        if len(outrow) > n:
            outrow = outrow[:n]
        elif len(outrow) < n:
            outrow += (missing,) * (n - len(outrow))
        yield outrow


class _SquaredView(Table):

    def __init__(self, table, missing=None):
        self.table = table
        self.missing = missing

    def __iter__(self):
        return _squared(self.table, self.missing)


def _match_direction(ltime, prev_time, prev_row, next_time, next_row,
                     direction, maxdiff):
    """Return the matching right row, or None.

    ``prev_time`` is the latest distinct right-hand time less than or equal to
    ``ltime`` and ``prev_row`` is the first right row at that time.
    ``next_time`` is the earliest distinct right-hand time greater than
    ``ltime`` and ``next_row`` is the first right row at that time.

    Ties in the nearest direction are resolved in favour of the preceding
    (``prev_row``) record.
    """

    if prev_time is not None and prev_time == ltime:
        return prev_row

    if direction == _MATCH_BACKWARD:
        if prev_time is None:
            return None
        diff = ltime - prev_time
        if maxdiff is None or diff <= maxdiff:
            return prev_row
        return None

    if direction == _MATCH_FORWARD:
        if next_time is None:
            return None
        diff = next_time - ltime
        if maxdiff is None or diff <= maxdiff:
            return next_row
        return None

    # nearest
    if prev_time is None and next_time is None:
        return None
    elif prev_time is None:
        diff = next_time - ltime
        if maxdiff is None or diff <= maxdiff:
            return next_row
        return None
    elif next_time is None:
        diff = ltime - prev_time
        if maxdiff is None or diff <= maxdiff:
            return prev_row
        return None

    prev_diff = ltime - prev_time
    next_diff = next_time - ltime
    if prev_diff <= next_diff:
        if maxdiff is None or prev_diff <= maxdiff:
            return prev_row
    else:
        if maxdiff is None or next_diff <= maxdiff:
            return next_row
    return None


def _iter_matched_device(lgroup, rgroup, getltime, getrtime, direction,
                         maxdiff, missing, nright):
    prev_time = None
    prev_row = None

    rtime_groups = itertools.groupby(rgroup, key=getrtime)

    def load_next_group():
        try:
            group_time, group = next(rtime_groups)
            return group_time, next(group)
        except StopIteration:
            return None, None

    next_time, next_row = load_next_group()

    for lrow in lgroup:
        ltime = getltime(lrow)

        while next_time is not None and next_time <= ltime:
            prev_time = next_time
            prev_row = next_row
            next_time, next_row = load_next_group()

        rrow = _match_direction(
            ltime, prev_time, prev_row, next_time, next_row,
            direction, maxdiff
        )
        if rrow is None:
            yield tuple(lrow) + (missing,) * nright
        else:
            yield tuple(lrow) + tuple(rrow)


def nearestjoin(left, right, key=None, lkey=None, rkey=None, ltime=None,
                rtime=None, time=None, direction='nearest', maxdiff=None,
                missing=None, presorted=False, buffersize=None, tempdir=None,
                cache=True, lprefix=None, rprefix=None, **kwargs):
    """
    Match each row from `left` with at most one row from `right` by device and
    by numerical time. E.g.::

        >>> import petl as etl
        >>> samples = (('device', 'time', 'value'),
        ...            ('a', 10, 1.2),
        ...            ('a', 12, 1.3),
        ...            ('b', 5, 2.1))
        >>> calibrations = (('device', 'time', 'offset'),
        ...                 ('a', 9, 0.1),
        ...                 ('a', 13, 0.3),
        ...                 ('b', 7, 0.2))
        >>> table = etl.nearestjoin(samples, calibrations, key='device',
        ...                         ltime='time', rtime='time',
        ...                         direction='nearest',
        ...                         lprefix='sample_', rprefix='cal_')
        >>> table
        +---------------+-------------+--------------+------------+----------+------------+
        | sample_device | sample_time | sample_value | cal_device | cal_time | cal_offset |
        +===============+=============+==============+============+==========+============+
        | 'a'           |          10 |          1.2 | 'a'        |        9 |        0.1 |
        +---------------+-------------+--------------+------------+----------+------------+
        | 'a'           |          12 |          1.3 | 'a'        |       13 |        0.3 |
        +---------------+-------------+--------------+------------+----------+------------+
        | 'b'           |           5 |          2.1 | 'b'        |        7 |        0.2 |
        +---------------+-------------+--------------+------------+----------+------------+
        <BLANKLINE>

    This is a left outer join: every row from `left` is emitted exactly once.
    When no suitable row from `right` is found, the right-hand fields are
    filled with `missing`. All fields from both input tables are preserved.
    Use `lprefix` and/or `rprefix` to distinguish fields with the same name,
    e.g., ``lprefix='sample_'`` and ``rprefix='cal_'``.

    The device is identified by one or more fields. Use `key` when both tables
    use the same device fields, or `lkey` and `rkey` when they differ. If no
    device key is given, fields common to both headers (excluding the time
    fields) are used. The time fields can be selected via `ltime` and `rtime`;
    `time` can be used as a shorthand when both are called the same. The time
    fields default to ``'time'``.

    `direction` controls which right-hand row is selected:

    * ``'backward'`` (also ``'preceding'``, ``'prior'``): the nearest row with
      a time less than or equal to the sample time;
    * ``'forward'`` (also ``'following'``, ``'next'``): the nearest row with a
      time greater than or equal to the sample time;
    * ``'nearest'`` (the default; also ``'closest'``): the row with the
      smallest absolute time difference, considering both sides.

    `maxdiff` is an optional inclusive upper bound on the absolute time
    difference. A difference exactly equal to `maxdiff` is accepted. There is
    no upper bound when `maxdiff` is None.

    **Tie-breaking.** If several right-hand rows share the same time, the
    first one in the (stable) sorted right-hand input is used. If a sample is
    exactly midway between a preceding and a following right-hand row, the
    preceding row is used. These rules make matching deterministic for the
    same input.

    If `presorted` is True, both inputs must already be sorted by device and
    time. Matching is then performed in a single pass over each input, using
    memory proportional to a few rows rather than the size of a device group
    or the right-hand table. If `presorted` is False, both inputs are sorted
    first using :func:`petl.transform.sorts.sort`, which can spill to disk via
    `buffersize`, `tempdir` and `cache`, so unsorted inputs larger than memory
    are also supported. Sorted and unsorted inputs produce the same match
    results; the output is ordered by the sorted left-hand input.

    Existing joins and sorts are unaffected. This function is also available
    as a table method, e.g., ``samples.nearestjoin(calibrations, ...)``.

    .. versionadded:: 1.7.33
    """

    # Accept a few naturally spelled aliases without cluttering the main
    # signature. Unknown keyword arguments are reported as usual.
    if 'timefield' in kwargs:
        time = kwargs.pop('timefield')
    if 'time_field' in kwargs:
        time = kwargs.pop('time_field')
    if 'ltimefield' in kwargs:
        ltime = kwargs.pop('ltimefield')
    if 'rtimefield' in kwargs:
        rtime = kwargs.pop('rtimefield')
    if 'left_time' in kwargs:
        ltime = kwargs.pop('left_time')
    if 'right_time' in kwargs:
        rtime = kwargs.pop('right_time')
    if 'l_time' in kwargs:
        ltime = kwargs.pop('l_time')
    if 'r_time' in kwargs:
        rtime = kwargs.pop('r_time')
    if 'ltimestamp' in kwargs:
        ltime = kwargs.pop('ltimestamp')
    if 'rtimestamp' in kwargs:
        rtime = kwargs.pop('rtimestamp')
    if 'how' in kwargs:
        direction = kwargs.pop('how')
    if 'mode' in kwargs:
        direction = kwargs.pop('mode')
    if 'method' in kwargs:
        direction = kwargs.pop('method')
    if 'match' in kwargs:
        direction = kwargs.pop('match')
    if 'tolerance' in kwargs:
        maxdiff = kwargs.pop('tolerance')
    if 'max_diff' in kwargs:
        maxdiff = kwargs.pop('max_diff')
    if 'max_time_diff' in kwargs:
        maxdiff = kwargs.pop('max_time_diff')
    if 'max_difference' in kwargs:
        maxdiff = kwargs.pop('max_difference')
    if 'max_time_difference' in kwargs:
        maxdiff = kwargs.pop('max_time_difference')
    if 'max_delta' in kwargs:
        maxdiff = kwargs.pop('max_delta')
    if 'max_time_delta' in kwargs:
        maxdiff = kwargs.pop('max_time_delta')
    if 'max_time_gap' in kwargs:
        maxdiff = kwargs.pop('max_time_gap')
    if 'max_gap' in kwargs:
        maxdiff = kwargs.pop('max_gap')
    if 'time_tolerance' in kwargs:
        maxdiff = kwargs.pop('time_tolerance')
    if 'on' in kwargs and key is None:
        key = kwargs.pop('on')
    if 'device' in kwargs and key is None:
        key = kwargs.pop('device')
    if 'device_key' in kwargs and key is None:
        key = kwargs.pop('device_key')
    if 'keys' in kwargs and key is None:
        key = kwargs.pop('keys')
    if 'left_key' in kwargs and lkey is None:
        lkey = kwargs.pop('left_key')
    if 'right_key' in kwargs and rkey is None:
        rkey = kwargs.pop('right_key')
    if 'left_prefix' in kwargs and lprefix is None:
        lprefix = kwargs.pop('left_prefix')
    if 'right_prefix' in kwargs and rprefix is None:
        rprefix = kwargs.pop('right_prefix')
    if kwargs:
        unexpected = next(iter(kwargs))
        raise TypeError(
            "nearestjoin() got an unexpected keyword argument '%s'"
            % unexpected
        )

    if time is not None:
        if ltime is None:
            ltime = time
        if rtime is None:
            rtime = time
    if ltime is None:
        ltime = 'time'
    if rtime is None:
        rtime = 'time'
    ltime = _as_time_field(ltime)
    rtime = _as_time_field(rtime)

    if key is not None and lkey is None and rkey is None:
        lkey = rkey = key
    elif key is None and lkey is not None and rkey is not None:
        pass
    elif key is lkey is rkey is None:
        # Resolved lazily from the headers, so that presorted inputs can be
        # consumed in a single pass.
        pass
    else:
        raise ArgumentError(
            'bad key arguments: either specify key, or specify both lkey and '
            'rkey, or provide no key/lkey/rkey arguments at all'
        )

    # Be forgiving if callers include the time field in the device key; the
    # time is always handled separately by this function.
    lkey = _without_time_field(lkey, ltime)
    rkey = _without_time_field(rkey, rtime)

    direction = _normalise_direction(direction)
    maxdiff = _validate_maxdiff(maxdiff)

    return NearestJoinView(
        left, right, lkey=lkey, rkey=rkey, ltime=ltime, rtime=rtime,
        direction=direction, maxdiff=maxdiff, missing=missing,
        presorted=presorted, buffersize=buffersize, tempdir=tempdir,
        cache=cache, lprefix=lprefix, rprefix=rprefix
    )


Table.nearestjoin = nearestjoin


class NearestJoinView(Table):

    def __init__(self, left, right, lkey, rkey, ltime, rtime, direction,
                 maxdiff, missing=None, presorted=False, buffersize=None,
                 tempdir=None, cache=True, lprefix=None, rprefix=None):
        self.left = left
        self.right = right
        self.lkey = lkey
        self.rkey = rkey
        self.ltime = ltime
        self.rtime = rtime
        self.direction = direction
        self.maxdiff = maxdiff
        self.missing = missing
        self.presorted = presorted
        self.buffersize = buffersize
        self.tempdir = tempdir
        self._cache = cache
        self.lprefix = lprefix
        self.rprefix = rprefix
        if not presorted and lkey is not None and rkey is not None:
            # Create the sort views once so their default cache can be reused
            # across repeated iterations.
            self._prepared_left = sort(
                _SquaredView(left, missing), _sort_key(lkey, ltime),
                buffersize=buffersize, tempdir=tempdir, cache=cache
            )
            self._prepared_right = sort(
                _SquaredView(right, missing), _sort_key(rkey, rtime),
                buffersize=buffersize, tempdir=tempdir, cache=cache
            )
        else:
            self._prepared_left = None
            self._prepared_right = None

    def __len__(self):
        # The result can be produced from a single-pass input, so its length
        # is not known without consuming it. Let list()/tuple() fall back to
        # iteration rather than probing the source an extra time.
        raise TypeError('nearestjoin result has no known length')

    def __length_hint__(self):
        return 0

    def __iter__(self):
        lkey = self.lkey
        rkey = self.rkey

        if self._prepared_left is not None:
            left = self._prepared_left
            right = self._prepared_right
        else:
            left = _SquaredView(self.left, self.missing)
            right = _SquaredView(self.right, self.missing)

            if lkey is None or rkey is None:
                if self.presorted:
                    # Let the iterator resolve the natural key from the
                    # headers while it already has the single-pass iterators
                    # open.
                    pass
                else:
                    lkey, rkey = _natural_device_key(
                        left, right, self.ltime, self.rtime
                    )

            if not self.presorted:
                left = sort(
                    left, _sort_key(lkey, self.ltime),
                    buffersize=self.buffersize, tempdir=self.tempdir,
                    cache=self._cache
                )
                right = sort(
                    right, _sort_key(rkey, self.rtime),
                    buffersize=self.buffersize, tempdir=self.tempdir,
                    cache=self._cache
                )

        return iternearestjoin(
            left, right, lkey=lkey, rkey=rkey, ltime=self.ltime,
            rtime=self.rtime, direction=self.direction, maxdiff=self.maxdiff,
            missing=self.missing, lprefix=self.lprefix, rprefix=self.rprefix
        )


def iternearestjoin(left, right, lkey, rkey, ltime, rtime, direction,
                    maxdiff, missing=None, lprefix=None, rprefix=None):
    lit = iter(left)
    rit = iter(right)

    try:
        lhdr = next(lit)
    except StopIteration:
        return
    try:
        rhdr = next(rit)
    except StopIteration:
        rhdr = ()

    lflds = list(map(text_type, lhdr))
    rflds = list(map(text_type, rhdr))

    if not lflds:
        # No sample fields and therefore no sample rows to emit.
        yield tuple()
        return

    if not rflds:
        # The right-hand table has no fields, so every sample is unmatched and
        # there are no calibration fields to append.
        if lprefix is None:
            outhdr = list(lflds)
        else:
            outhdr = [text_type(lprefix) + f for f in lflds]
        yield tuple(outhdr)
        lgettime = _getter_for_time(lhdr, ltime, missing)
        for lrow in lit:
            lgettime(lrow)
            yield tuple(lrow)
        return

    if lkey is None or rkey is None:
        lkey, rkey = _natural_device_key(
            (lhdr,), (rhdr,), ltime, rtime
        )

    lkind = asindices(lhdr, lkey)
    rkind = asindices(rhdr, rkey)
    lgettime = _getter_for_time(lhdr, ltime, missing)
    rgettime = _getter_for_time(rhdr, rtime, missing)
    lgetdev = _device_getter(lkind)
    rgetdev = _device_getter(rkind)

    if lprefix is None:
        outhdr = list(lflds)
    else:
        outhdr = [text_type(lprefix) + f for f in lflds]
    if rprefix is None:
        outhdr.extend(rflds)
    else:
        outhdr.extend([text_type(rprefix) + f for f in rflds])
    yield tuple(outhdr)

    lit = _validated_rows(lit, lgettime)
    rit = _validated_rows(rit, rgettime)
    lgroups = itertools.groupby(lit, key=lgetdev)
    rgroups = itertools.groupby(rit, key=rgetdev)

    try:
        rdev, rgroup = next(rgroups)
    except StopIteration:
        rdev, rgroup = None, None

    for ldev, lgroup in lgroups:
        while rgroup is not None and rdev < ldev:
            try:
                rdev, rgroup = next(rgroups)
            except StopIteration:
                rdev, rgroup = None, None

        if rgroup is not None and rdev == ldev:
            for outrow in _iter_matched_device(
                    lgroup, rgroup, lgettime, rgettime, direction, maxdiff,
                    missing, len(rflds)):
                yield outrow
            try:
                rdev, rgroup = next(rgroups)
            except StopIteration:
                rdev, rgroup = None, None
        else:
            for lrow in lgroup:
                # validate the sample time even when there cannot be a match
                lgettime(lrow)
                yield tuple(lrow) + (missing,) * len(rflds)


# Common aliases. ``asofjoin`` follows the traditional as-of convention and
# defaults to looking backwards, while the others share nearestjoin's default.
def asofjoin(left, right, key=None, lkey=None, rkey=None, ltime=None,
             rtime=None, time=None, direction='backward', maxdiff=None,
             missing=None, presorted=False, buffersize=None, tempdir=None,
             cache=True, lprefix=None, rprefix=None, **kwargs):
    """As-of join: like :func:`nearestjoin`, defaulting to ``direction='backward'``."""
    return nearestjoin(
        left, right, key=key, lkey=lkey, rkey=rkey, ltime=ltime, rtime=rtime,
        time=time, direction=direction, maxdiff=maxdiff, missing=missing,
        presorted=presorted, buffersize=buffersize, tempdir=tempdir,
        cache=cache, lprefix=lprefix, rprefix=rprefix, **kwargs
    )


def timejoin(left, right, *args, **kwargs):
    """Alias for :func:`nearestjoin`."""
    return nearestjoin(left, right, *args, **kwargs)


def closestjoin(left, right, *args, **kwargs):
    """Alias for :func:`nearestjoin`."""
    return nearestjoin(left, right, *args, **kwargs)


def matchjoin(left, right, *args, **kwargs):
    """Alias for :func:`nearestjoin`."""
    return nearestjoin(left, right, *args, **kwargs)


def joinnearest(left, right, *args, **kwargs):
    """Alias for :func:`nearestjoin`."""
    return nearestjoin(left, right, *args, **kwargs)


def asof(left, right, *args, **kwargs):
    """Alias for :func:`asofjoin`."""
    return asofjoin(left, right, *args, **kwargs)


Table.asofjoin = asofjoin
Table.timejoin = timejoin
Table.closestjoin = closestjoin
Table.matchjoin = matchjoin
Table.joinnearest = joinnearest
Table.asof = asof


def unjoin(table, value, key=None, autoincrement=(1, 1), presorted=False,
           buffersize=None, tempdir=None, cache=True):
    """
    Split a table into two tables by reversing an inner join. E.g.::

        >>> import petl as etl
        >>> # join key is present in the table
        ... table1 = (('foo', 'bar', 'baz'),
        ...           ('A', 1, 'apple'),
        ...           ('B', 1, 'apple'),
        ...           ('C', 2, 'orange'))
        >>> table2, table3 = etl.unjoin(table1, 'baz', key='bar')
        >>> table2
        +-----+-----+
        | foo | bar |
        +=====+=====+
        | 'A' |   1 |
        +-----+-----+
        | 'B' |   1 |
        +-----+-----+
        | 'C' |   2 |
        +-----+-----+

        >>> table3
        +-----+----------+
        | bar | baz      |
        +=====+==========+
        |   1 | 'apple'  |
        +-----+----------+
        |   2 | 'orange' |
        +-----+----------+

        >>> # an integer join key can also be reconstructed
        ... table4 = (('foo', 'bar'),
        ...           ('A', 'apple'),
        ...           ('B', 'apple'),
        ...           ('C', 'orange'))
        >>> table5, table6 = etl.unjoin(table4, 'bar')
        >>> table5
        +-----+--------+
        | foo | bar_id |
        +=====+========+
        | 'A' |      1 |
        +-----+--------+
        | 'B' |      1 |
        +-----+--------+
        | 'C' |      2 |
        +-----+--------+

        >>> table6
        +----+----------+
        | id | bar      |
        +====+==========+
        |  1 | 'apple'  |
        +----+----------+
        |  2 | 'orange' |
        +----+----------+

    The `autoincrement` parameter controls how an integer join key is
    reconstructed, and should be a tuple of (`start`, `step`).

    """

    if key is None:
        # first sort the table by the value field
        if presorted:
            tbl_sorted = table
        else:
            tbl_sorted = sort(table, value, buffersize=buffersize,
                              tempdir=tempdir, cache=cache)
        # on the left, return the original table but with the value field
        # replaced by an incrementing integer
        left = ConvertToIncrementingCounterView(tbl_sorted, value,
                                                autoincrement)
        # on the right, return a new table with distinct values from the
        # given field
        right = EnumerateDistinctView(tbl_sorted, value, autoincrement)
    else:
        # on the left, return distinct rows from the original table
        # with the value field cut out
        left = distinct(cutout(table, value))
        # on the right, return distinct rows from the original table
        # with all fields but the key and value cut out
        right = distinct(cut(table, key, value))
    return left, right


class ConvertToIncrementingCounterView(Table):

    def __init__(self, tbl, value, autoincrement):
        self.table = tbl
        self.value = value
        self.autoincrement = autoincrement

    def __iter__(self):
        it = iter(self.table)
        hdr = next(it)
        table = itertools.chain([hdr], it)
        value = self.value
        vidx = hdr.index(value)
        outhdr = list(hdr)
        outhdr[vidx] = '%s_id' % value
        yield tuple(outhdr)
        offset, multiplier = self.autoincrement
        for n, (_, group) in enumerate(rowgroupby(table, value)):
            for row in group:
                outrow = list(row)
                outrow[vidx] = (n * multiplier) + offset
                yield tuple(outrow)


Table.unjoin = unjoin


class EnumerateDistinctView(Table):

    def __init__(self, tbl, value, autoincrement):
        self.table = tbl
        self.value = value
        self.autoincrement = autoincrement

    def __iter__(self):
        offset, multiplier = self.autoincrement
        yield ('id', self.value)
        for n, (v, _) in enumerate(rowgroupby(self.table, self.value)):
            yield ((n * multiplier) + offset, v)
