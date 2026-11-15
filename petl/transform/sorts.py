from __future__ import absolute_import, print_function, division


import os
import heapq
import errno
from tempfile import NamedTemporaryFile
import itertools
import logging
from collections import namedtuple
import operator
from petl.compat import pickle, next, text_type


import petl.config as config
from petl.comparison import Comparable, comparable_itemgetter
from petl.util.base import Table, asindices


logger = logging.getLogger(__name__)
warning = logger.warning
info = logger.info
debug = logger.debug


def _parse_sort_direction(value, name='sort direction'):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, text_type):
        value = value.strip().lower().replace('-', '_')
        if value in ('asc', 'ascending', 'ascend', 'a'):
            return False
        if value in ('desc', 'descending', 'descend', 'd'):
            return True
    elif isinstance(value, int):
        return value != 0
    raise ValueError('invalid %s: %r' % (name, value))


def _parse_null_placement(value, name='null placement'):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, text_type):
        value = value.strip().lower().replace('-', '_').replace(' ', '_')
        if value in ('first', 'nulls_first', 'nullsfirst'):
            return True
        if value in ('last', 'nulls_last', 'nullslast'):
            return False
    raise ValueError('invalid %s: %r' % (name, value))


def _parse_ascending(value, name='ascending'):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, text_type):
        value = value.strip().lower().replace('-', '_')
        if value in ('asc', 'ascending', 'ascend', 'a'):
            return True
        if value in ('desc', 'descending', 'descend', 'd'):
            return False
    raise ValueError('invalid %s: %r' % (name, value))


def _parse_nulls_last(value, name='nulls_last'):
    if isinstance(value, bool):
        return not value
    if value is None:
        return None
    return _parse_null_placement(value, name)


def _parse_sort_option(value, parser, name):
    if isinstance(value, (list, tuple)):
        return [parser(v, name) for v in value]
    return parser(value, name)


def _looks_like_sort_descriptor(value):
    if isinstance(value, dict):
        return 'field' in value or 'key' in value
    return isinstance(value, (list, tuple))


def _looks_like_sort_descriptor_list(value):
    if not isinstance(value, (list, tuple)):
        return False
    return all(_looks_like_sort_descriptor(item) for item in value)


def _parse_sort_descriptor(value):
    if isinstance(value, dict):
        if 'field' in value:
            field = value['field']
        elif 'key' in value:
            field = value['key']
        else:
            raise ValueError('sort rule is missing a field: %r' % (value,))

        direction = None
        if 'reverse' in value:
            direction = _parse_sort_direction(value['reverse'])
        elif 'direction' in value:
            direction = _parse_sort_direction(value['direction'])
        elif 'order' in value:
            direction = _parse_sort_direction(value['order'])
        elif 'ascending' in value:
            ascending = value['ascending']
            if ascending is not None:
                if not isinstance(ascending, bool):
                    raise ValueError(
                        'invalid ascending value: %r' % (ascending,)
                    )
                direction = not ascending

        nulls_first = None
        if 'nulls_first' in value:
            nulls_first = _parse_null_placement(value['nulls_first'])
        elif 'nulls_last' in value:
            nulls_first = _parse_nulls_last(value['nulls_last'])
        elif 'nulls' in value:
            nulls_first = _parse_null_placement(value['nulls'])
        elif 'na_position' in value:
            nulls_first = _parse_null_placement(value['na_position'])
        return field, direction, nulls_first

    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError('invalid sort rule: %r' % (value,))
    if len(value) > 3:
        raise ValueError('invalid sort rule: %r' % (value,))

    field = value[0]
    direction = None
    nulls_first = None
    if len(value) >= 2:
        direction = _parse_sort_direction(value[1])
    if len(value) == 3:
        nulls_first = _parse_null_placement(value[2])
    return field, direction, nulls_first


def _parse_sort_descriptors(key):
    if isinstance(key, dict):
        return [_parse_sort_descriptor(key)]
    if _looks_like_sort_descriptor_list(key):
        return [_parse_sort_descriptor(item) for item in key]
    return None


class _SortValue(object):
    """Wrap a value so it can be ordered independently of other fields."""

    __slots__ = ('value', 'reverse')

    def __init__(self, value, reverse):
        self.value = Comparable(value)
        self.reverse = reverse

    def __lt__(self, other):
        if self.reverse:
            return self.value > other.value
        return self.value < other.value

    def __eq__(self, other):
        return isinstance(other, _SortValue) and self.value == other.value

    def __ne__(self, other):
        return not self == other

    def __le__(self, other):
        return self < other or self == other

    def __gt__(self, other):
        return not (self < other or self == other)

    def __ge__(self, other):
        return not (self < other)


def _make_sortkey(indices, reverse, nulls_first):
    fields = tuple(zip(indices, reverse, nulls_first))

    def getkey(row):
        key = []
        for index, descending, nulls_at_start in fields:
            try:
                value = row[index]
            except (IndexError, KeyError):
                value = None
            if value is None:
                nullrank = 0 if nulls_at_start else 1
            else:
                nullrank = 1 if nulls_at_start else 0
            key.append((nullrank, _SortValue(value, descending)))
        return tuple(key)

    return getkey


def _close_iterators(iterators):
    for iterator in iterators:
        close = getattr(iterator, 'close', None)
        if close is not None:
            close()


def sort(table, key=None, reverse=False, buffersize=None, tempdir=None,
         cache=True, nulls_first=None, nulls_last=None, **kwargs):
    """
    Sort the table. Field names or indices (from zero) can be used to specify
    the key. E.g.::

        >>> import petl as etl
        >>> table1 = [['foo', 'bar'],
        ...           ['C', 2],
        ...           ['A', 9],
        ...           ['A', 6],
        ...           ['F', 1],
        ...           ['D', 10]]
        >>> table2 = etl.sort(table1, 'foo')
        >>> table2
        +-----+-----+
        | foo | bar |
        +=====+=====+
        | 'A' |   9 |
        +-----+-----+
        | 'A' |   6 |
        +-----+-----+
        | 'C' |   2 |
        +-----+-----+
        | 'D' |  10 |
        +-----+-----+
        | 'F' |   1 |
        +-----+-----+

        >>> # sorting by compound key is supported
        ... table3 = etl.sort(table1, key=['foo', 'bar'])
        >>> table3
        +-----+-----+
        | foo | bar |
        +=====+=====+
        | 'A' |   6 |
        +-----+-----+
        | 'A' |   9 |
        +-----+-----+
        | 'C' |   2 |
        +-----+-----+
        | 'D' |  10 |
        +-----+-----+
        | 'F' |   1 |
        +-----+-----+

        >>> # if no key is specified, the default is a lexical sort
        ... table4 = etl.sort(table1)
        >>> table4
        +-----+-----+
        | foo | bar |
        +=====+=====+
        | 'A' |   6 |
        +-----+-----+
        | 'A' |   9 |
        +-----+-----+
        | 'C' |   2 |
        +-----+-----+
        | 'D' |  10 |
        +-----+-----+
        | 'F' |   1 |
        +-----+-----+

        >>> # sorting is stable, so fields can be sorted in different
        ... # directions by chaining sorts, starting with the last field
        ... table5 = etl.sort(table1, 'bar').sort('foo', reverse=True)
        >>> table5
        +-----+-----+
        | foo | bar |
        +=====+=====+
        | 'F' |   1 |
        +-----+-----+
        | 'D' |  10 |
        +-----+-----+
        | 'C' |   2 |
        +-----+-----+
        | 'A' |   6 |
        +-----+-----+
        | 'A' |   9 |
        +-----+-----+

    Multiple fields can be sorted in different directions in one pass. Use a
    list of sort rules, or pass per-field ``reverse`` and ``nulls_first``
    options. Null placement is independent of sort direction, and missing
    cells in short rows are treated as null. Sorting remains stable. E.g.::

        >>> table6 = [['customer', 'amount'],
        ...           ['B', 20],
        ...           ['A', None],
        ...           ['A', 5],
        ...           ['B', None],
        ...           ['A', 7]]
        >>> table7 = etl.sort(
        ...     table6,
        ...     key=[('customer', 'asc'), ('amount', 'desc', 'last')]
        ... )
        >>> table7
        +----------+--------+
        | customer | amount |
        +==========+========+
        | 'A'      |      7 |
        +----------+--------+
        | 'A'      |      5 |
        +----------+--------+
        | 'A'      | None   |
        +----------+--------+
        | 'B'      |     20 |
        +----------+--------+
        | 'B'      | None   |
        +----------+--------+

        The equivalent per-field option form is::

            etl.sort(table6, key=['customer', 'amount'],
                     reverse=[False, True],
                     nulls_first=[True, False])

        Each sort rule may be a ``(field, direction, nulls)`` tuple or a
        dictionary with ``field``, ``direction`` (or ``reverse``) and
        ``nulls_first`` (or ``nulls_last``) keys. Direction may be
        ``'asc'``/``'desc'`` or a boolean, where ``True`` means descending.
        Null placement may be ``'first'``/``'last'`` or a boolean, where
        ``True`` means first. Field names and indices follow the same rules as
        other PETL field selectors. When per-field rules are used and null
        placement is omitted, nulls are placed last. Calls using the original
        ``key`` and boolean ``reverse`` arguments retain their previous
        behaviour.

    The `buffersize` argument should be an `int` or `None`.

    If the number of rows in the table is less than `buffersize`, the table
    will be sorted in memory. Otherwise, the table is sorted in chunks of
    no more than `buffersize` rows, each chunk is written to a temporary file,
    and then a merge sort is performed on the temporary files.

    If `buffersize` is `None`, the value of
    `petl.config.sort_buffersize` will be used. By default this is
    set to 100000 rows, but can be changed, e.g.::

        >>> import petl.config
        >>> petl.config.sort_buffersize = 500000

    If `petl.config.sort_buffersize` is set to `None`, this forces
    all sorting to be done entirely in memory.

    By default the results of the sort will be cached, and so a second pass over
    the sorted table will yield rows from the cache and will not repeat the
    sort operation. To turn off caching, set the `cache` argument to `False`.

    """

    sortkeys = kwargs.pop('sortkeys', None)
    if sortkeys is not None:
        if key is not None:
            raise ValueError('sortkeys and key cannot both be specified')
        key = sortkeys

    directions = kwargs.pop('directions', None)
    order = kwargs.pop('order', None)
    ascending = kwargs.pop('ascending', None)
    direction_aliases = [v for v in (directions, order, ascending)
                         if v is not None]
    if len(direction_aliases) > 1:
        raise ValueError(
            'only one of directions, order and ascending may be specified'
        )
    if direction_aliases:
        if reverse not in (False, None):
            raise ValueError(
                'reverse cannot be combined with directions, order or '
                'ascending'
            )
        if ascending is not None:
            reverse = _parse_sort_option(
                ascending, _parse_ascending, 'ascending'
            )
            if isinstance(reverse, list):
                reverse = [not value for value in reverse]
            else:
                reverse = not reverse
        else:
            reverse = _parse_sort_option(
                direction_aliases[0], _parse_sort_direction, 'direction'
            )

    nulls = kwargs.pop('nulls', None)
    na_position = kwargs.pop('na_position', None)
    null_aliases = [v for v in (nulls, na_position) if v is not None]
    if len(null_aliases) > 1:
        raise ValueError('only one of nulls and na_position may be specified')
    if nulls_first is not None and nulls_last is not None:
        raise ValueError('nulls_first and nulls_last cannot both be specified')
    if null_aliases:
        if nulls_first is not None or nulls_last is not None:
            raise ValueError(
                'nulls or na_position cannot be combined with nulls_first '
                'or nulls_last'
            )
        nulls_first = null_aliases[0]

    if kwargs:
        unexpected = sorted(kwargs)[0]
        raise TypeError(
            "sort() got an unexpected keyword argument %r" % unexpected
        )

    return SortView(table, key=key, reverse=reverse, buffersize=buffersize,
                    tempdir=tempdir, cache=cache,
                    nulls_first=nulls_first, nulls_last=nulls_last)


Table.sort = sort


def _iterchunk(fn):
    # reopen so iterators from file cache are independent
    debug('iterchunk, opening %s' % fn)
    with open(fn, 'rb') as f:
        try:
            while True:
                yield pickle.load(f)
        except EOFError:
            pass
    debug('end of iterchunk, closed %s' % fn)


class _Keyed(namedtuple('Keyed', ['key', 'obj'])):
    #  Override default behavior of namedtuple comparisons, only keys need to be compared for heapmerge
    def __eq__(self, other):
        return self.key == other.key

    def __lt__(self, other):
        return self.key < other.key

    def __le__(self, other):
        return self.key <= other.key

    def __ne__(self, other):
        return self.key != other.key

    def __gt__(self, other):
        return self.key > other.key

    def __ge__(self, other):
        return self.key >= other.key


def _heapqmergesorted(key=None, *iterables):
    """Return a single iterator over the given iterables, sorted by the
    given `key` function, assuming the input iterables are already sorted by
    the same function. (I.e., the merge part of a general merge sort.) Uses
    :func:`heapq.merge` for the underlying implementation."""

    if key is None:
        keyed_iterables = iterables
        for element in heapq.merge(*keyed_iterables):
            yield element
    else:
        keyed_iterables = [(_Keyed(key(obj), obj) for obj in iterable)
                           for iterable in iterables]
        for element in heapq.merge(*keyed_iterables):
            yield element.obj


def _shortlistmergesorted(key=None, reverse=False, *iterables):
    """Return a single iterator over the given iterables, sorted by the
    given `key` function, assuming the input iterables are already sorted by
    the same function. (I.e., the merge part of a general merge sort.) Uses
    :func:`min` (or :func:`max` if ``reverse=True``) for the underlying
    implementation."""

    if reverse:
        op = max
    else:
        op = min
    if key is not None:
        opkwargs = {'key': key}
    else:
        opkwargs = dict()
    # populate initial shortlist
    # (remember some iterables might be empty)
    iterators = list()
    shortlist = list()
    for iterable in iterables:
        it = iter(iterable)
        try:
            first = next(it)
            iterators.append(it)
            shortlist.append(first)
        except StopIteration:
            pass
    # do the mergesort
    while iterators:
        nxt = op(shortlist, **opkwargs)
        yield nxt
        nextidx = shortlist.index(nxt)
        try:
            shortlist[nextidx] = next(iterators[nextidx])
        except StopIteration:
            del shortlist[nextidx]
            del iterators[nextidx]


def _mergesorted(key=None, reverse=False, *iterables):
    # N.B., I've used heapq for normal merge sort and shortlist merge sort for
    # reverse merge sort because I've assumed that heapq.merge is faster and
    # so is preferable but it doesn't support reverse sorting so the shortlist
    # merge sort has to be used for reverse sorting. Some casual profiling
    # suggests there isn't much between the two in terms of speed, but might be
    # worth profiling more carefully

    if reverse:
        return _shortlistmergesorted(key, True, *iterables)
    else:
        return _heapqmergesorted(key, *iterables)


class _SortIterator(object):
    __slots__ = ('view', 'iterator', '_exhausted')

    def __init__(self, view):
        self.view = view
        self.iterator = None
        self._exhausted = False

    def __iter__(self):
        return self

    def __next__(self):
        if self._exhausted:
            raise StopIteration
        if self.iterator is None:
            self.iterator = self.view._iter()
        try:
            return next(self.iterator)
        except StopIteration:
            self.close()
            raise

    next = __next__

    def close(self):
        iterator = self.iterator
        self.iterator = None
        self.view = None
        self._exhausted = True
        if iterator is not None:
            close = getattr(iterator, 'close', None)
            if close is not None:
                close()


class SortView(Table):
    def __init__(self, source, key=None, reverse=False, buffersize=None,
                 tempdir=None, cache=True, nulls_first=None,
                 nulls_last=None):
        self.source = source
        self.key = key
        self.reverse = reverse
        self._descriptors = _parse_sort_descriptors(key)
        reverse_option = _parse_sort_option(
            reverse, _parse_sort_direction, 'reverse'
        )
        if reverse_option is None:
            reverse_option = False
        self._reverse_option = reverse_option

        if nulls_first is not None and nulls_last is not None:
            raise ValueError(
                'nulls_first and nulls_last cannot both be specified'
            )
        if nulls_first is not None:
            self._nulls_first_option = _parse_sort_option(
                nulls_first, _parse_null_placement, 'nulls_first'
            )
        elif nulls_last is not None:
            self._nulls_first_option = _parse_sort_option(
                nulls_last, _parse_nulls_last, 'nulls_last'
            )
        else:
            self._nulls_first_option = None

        self._enhanced = (
            self._descriptors is not None
            or not isinstance(reverse, (bool, type(None)))
            or nulls_first is not None
            or nulls_last is not None
        )
        if self._descriptors is not None:
            self._field_count = len(self._descriptors)
        elif key is None:
            self._field_count = None
        elif isinstance(key, (list, tuple)):
            self._field_count = len(key)
        else:
            self._field_count = 1
        if self._field_count == 0:
            raise ValueError('no fields selected for sorting')
        self._validate_option_count(
            self._reverse_option, 'reverse'
        )
        self._validate_option_count(
            self._nulls_first_option, 'nulls_first'
        )
        self._mergereverse = (
            reverse_option if isinstance(reverse_option, bool) else False
        )
        if buffersize is None:
            self.buffersize = config.sort_buffersize
        else:
            self.buffersize = buffersize
        self.tempdir = tempdir
        self._cache = cache
        self._hdrcache = None
        self._memcache = None
        self._filecache = None
        self._getkey = None

    def _validate_option_count(self, value, name):
        if (
            isinstance(value, list)
            and self._field_count is not None
            and len(value) != self._field_count
        ):
            raise ValueError(
                '%s has %d values but %d sort fields were selected'
                % (name, len(value), self._field_count)
            )

    def clearcache(self):
        debug('clear cache')
        filecache = self._filecache
        self._filecache = None
        if filecache:
            for wrapper in filecache:
                wrapper.delete()
        self._hdrcache = None
        self._memcache = None
        self._getkey = None

    def __iter__(self):
        return _SortIterator(self)

    def _iter(self):
        source = self.source
        key = self.key
        reverse = self.reverse
        if self._cache and self._memcache is not None:
            return self._iterfrommemcache()
        elif self._cache and self._filecache is not None:
            return self._iterfromfilecache()
        else:
            return self._iternocache(source, key, reverse)

    def _iterfrommemcache(self):
        debug('iterate from memory cache')
        yield tuple(self._hdrcache)
        for row in self._memcache:
            yield tuple(row)

    def _iterfromfilecache(self):
        # create a reference to the filecache here, so cleanup happens in the
        # correct order
        filecache = self._filecache
        filenames = list(map(operator.attrgetter('name'), filecache))
        debug('iterate from file cache: %r', filenames)
        yield tuple(self._hdrcache)
        chunkiters = [_iterchunk(fn) for fn in filenames]
        rows = _mergesorted(self._getkey, self._mergereverse, *chunkiters)
        try:
            for row in rows:
                yield tuple(row)
        finally:
            debug('attempt cleanup from generator')
            # N.B., need to ensure that any open files are closed **before**
            # temporary files are deleted, as deletion will fail on Windows
            # if file is in use (i.e., still open)
            _close_iterators(chunkiters)
            close = getattr(rows, 'close', None)
            if close is not None:
                close()
            del filecache
            debug('exiting generator')

    def _spread_option(self, value, count, name):
        if isinstance(value, list):
            if len(value) != count:
                raise ValueError(
                    '%s has %d values but %d sort fields were selected'
                    % (name, len(value), count)
                )
            return list(value)
        return [value] * count

    def _resolve_enhanced_getkey(self, hdr):
        if self._descriptors is not None:
            fields = [descriptor[0] for descriptor in self._descriptors]
            indices = asindices(hdr, tuple(fields))
        elif self.key is not None:
            indices = asindices(hdr, self.key)
        else:
            indices = list(range(len(hdr)))

        count = len(indices)
        if count == 0:
            raise ValueError('no fields selected for sorting')

        reverse = self._spread_option(
            self._reverse_option, count, 'reverse'
        )
        nulls_first = self._spread_option(
            self._nulls_first_option, count, 'nulls_first'
        )

        if self._descriptors is not None:
            for i, descriptor in enumerate(self._descriptors):
                if descriptor[1] is not None:
                    reverse[i] = descriptor[1]
                if descriptor[2] is not None:
                    nulls_first[i] = descriptor[2]

        reverse = [False if value is None else value for value in reverse]
        nulls_first = [
            False if value is None else value for value in nulls_first
        ]
        return _make_sortkey(indices, reverse, nulls_first)

    def _iternocache(self, source, key, reverse):
        debug('iterate without cache')
        self.clearcache()
        it = iter(source)

        try:
            hdr = next(it)
        except StopIteration:
            if key is None:
                return  # nothing to do on a table without headers
            hdr = []
        yield tuple(hdr)

        if self._enhanced:
            getkey = self._resolve_enhanced_getkey(hdr)
            reverse = False
        else:
            if key is not None:
                # convert field selection into field indices
                indices = asindices(hdr, key)
            else:
                indices = range(len(hdr))
            if len(indices) == 0:
                raise ValueError('no fields selected for sorting')
            # now use field indices to construct a _getkey function
            getkey = comparable_itemgetter(*indices)
            reverse = self._reverse_option
        self._mergereverse = reverse

        # TODO support native comparison

        # initialise the first chunk
        rows = list(itertools.islice(it, 0, self.buffersize))
        rows.sort(key=getkey, reverse=reverse)

        # have we exhausted the source iterator?
        if self.buffersize is None or len(rows) < self.buffersize:
            # yes, table fits within sort buffer

            if self._cache:
                debug('caching mem')
                self._hdrcache = hdr
                self._memcache = rows
                # actually not needed to iterate from memcache
                self._getkey = getkey

            for row in rows:
                yield tuple(row)

        else:
            # no, table is too big, need to sort in chunks

            chunkfiles = []
            cached = False
            try:
                while rows:

                    # dump the chunk
                    with NamedTemporaryFile(dir=self.tempdir, delete=False,
                                            mode='wb') as f:
                        # N.B., we **don't** want the file to be deleted on
                        # close, but we **do** want the file to be deleted when
                        # self is garbage collected, or when the program exits.
                        # When all references to the wrapper are gone, the file
                        # should get deleted.
                        wrapper = _NamedTempFileDeleteOnGC(f.name)
                        debug('created temporary chunk file %s' % f.name)
                        for row in rows:
                            pickle.dump(row, f, protocol=-1)
                        f.flush()
                        chunkfiles.append(wrapper)

                    # grab the next chunk
                    rows = list(itertools.islice(it, 0, self.buffersize))
                    rows.sort(key=getkey, reverse=reverse)

                if self._cache:
                    debug('caching files')
                    self._hdrcache = hdr
                    self._filecache = chunkfiles
                    self._getkey = getkey
                    cached = True

                chunkiters = [_iterchunk(f.name) for f in chunkfiles]
                merged = _mergesorted(getkey, reverse, *chunkiters)
                try:
                    for row in merged:
                        yield tuple(row)
                finally:
                    _close_iterators(chunkiters)
                    close = getattr(merged, 'close', None)
                    if close is not None:
                        close()
            finally:
                if not cached:
                    for wrapper in chunkfiles:
                        wrapper.delete()


class _NamedTempFileDeleteOnGC(object):

    def __init__(self, name):
        self.name = name
        self._deleted = False

    def delete(self, unlink=os.unlink, log=logger.debug):
        if self._deleted:
            return
        name = self.name
        try:
            log('deleting %s' % name)
            unlink(name)
        except Exception as e:
            if getattr(e, 'errno', None) == errno.ENOENT:
                self._deleted = True
            else:
                log('exception deleting %s: %s' % (name, e))
        else:
            self._deleted = True
            log('deleted %s' % name)

    def __del__(self):
        try:
            self.delete()
        except Exception:
            pass

    def __str__(self):
        return self.name

    def __repr__(self):
        return self.name


def mergesort(*tables, **kwargs):
    """
    Combine multiple input tables into one sorted output table. E.g.::

        >>> import petl as etl
        >>> table1 = [['foo', 'bar'],
        ...           ['A', 9],
        ...           ['C', 2],
        ...           ['D', 10],
        ...           ['A', 6],
        ...           ['F', 1]]
        >>> table2 = [['foo', 'bar'],
        ...           ['B', 3],
        ...           ['D', 10],
        ...           ['A', 10],
        ...           ['F', 4]]
        >>> table3 = etl.mergesort(table1, table2, key='foo')
        >>> table3.lookall()
        +-----+-----+
        | foo | bar |
        +=====+=====+
        | 'A' |   9 |
        +-----+-----+
        | 'A' |   6 |
        +-----+-----+
        | 'A' |  10 |
        +-----+-----+
        | 'B' |   3 |
        +-----+-----+
        | 'C' |   2 |
        +-----+-----+
        | 'D' |  10 |
        +-----+-----+
        | 'D' |  10 |
        +-----+-----+
        | 'F' |   1 |
        +-----+-----+
        | 'F' |   4 |
        +-----+-----+

    If the input tables are already sorted by the given key, give
    ``presorted=True`` as a keyword argument.

    This function is equivalent to concatenating the input tables using
    :func:`cat` then sorting, however this function will typically be more
    efficient, especially if the input tables are presorted.

    Keyword arguments:

    key : string or tuple of strings, optional
        Field name or tuple of fields to sort by (defaults to `None` lexical
        sort)
    reverse : bool, optional
        `True` if sort in reverse (descending) order (defaults to `False`)
    presorted : bool, optional
        `True` if inputs are already sorted by the given key (defaults to
        `False`)
    missing : object
        Value to fill with when input tables have different fields (defaults to
        `None`)
    header : sequence of strings, optional
        Specify a fixed header for the output table
    buffersize : int, optional
        Limit the number of rows in memory per input table when inputs are not
        presorted

    """

    return MergeSortView(tables, **kwargs)


Table.mergesort = mergesort


class MergeSortView(Table):
    def __init__(self, tables, key=None, reverse=False, presorted=False,
                 missing=None, header=None, buffersize=None, tempdir=None,
                 cache=True):
        self.key = key
        if presorted:
            self.tables = tables
        else:
            self.tables = [sort(t, key=key, reverse=reverse,
                                buffersize=buffersize, tempdir=tempdir,
                                cache=cache)
                           for t in tables]
        self.missing = missing
        self._header = header
        self.reverse = reverse

    def __iter__(self):
        return itermergesort(self.tables, self.key, self._header, self.missing,
                             self.reverse)


def itermergesort(sources, key, header, missing, reverse):
    # first need to standardise headers of all input tables
    # borrow this from itercat - TODO remove code smells

    its = [iter(t) for t in sources]
    src_hdrs = []
    for it in its:
        try:
            src_hdrs.append(next(it))
        except StopIteration:
            src_hdrs.append([])

    if header is None:
        # determine output fields by gathering all fields found in the sources
        outhdr = list()
        for hdr in src_hdrs:
            for f in list(map(text_type, hdr)):
                if f not in outhdr:
                    # add any new fields as we find them
                    outhdr.append(f)
    else:
        # predetermined output fields
        outhdr = header
    yield tuple(outhdr)

    def _standardisedata(it, hdr, ofs):
        flds = list(map(text_type, hdr))
        # now construct and yield the data rows
        for _row in it:
            try:
                # should be quickest to do this way
                yield tuple(_row[flds.index(fo)] if fo in flds else missing
                            for fo in ofs)
            except IndexError:
                # handle short rows
                outrow = [missing] * len(ofs)
                for i, fi in enumerate(flds):
                    try:
                        outrow[ofs.index(fi)] = _row[i]
                    except IndexError:
                        pass  # be relaxed about short rows
                yield tuple(outrow)

    # wrap all iterators to standardise fields
    sits = [_standardisedata(it, hdr, outhdr)
            for hdr, it in zip(src_hdrs, its)]

    # now determine key function
    getkey = None
    if key is not None:
        # convert field selection into field indices
        indices = asindices(outhdr, key)
        # now use field indices to construct a _getkey function
        # N.B., this will probably raise an exception on short rows
        getkey = comparable_itemgetter(*indices)

    # OK, do the merge sort
    for row in _shortlistmergesorted(getkey, reverse, *sits):
        yield row


def issorted(table, key=None, reverse=False, strict=False):
    """
    Return True if the table is ordered (i.e., sorted) by the given key. E.g.::

        >>> import petl as etl
        >>> table1 = [['foo', 'bar', 'baz'],
        ...           ['a', 1, True],
        ...           ['b', 3, True],
        ...           ['b', 2]]
        >>> etl.issorted(table1, key='foo')
        True
        >>> etl.issorted(table1, key='bar')
        False
        >>> etl.issorted(table1, key='foo', strict=True)
        False
        >>> etl.issorted(table1, key='foo', reverse=True)
        False

    """

    # determine the operator to use when comparing rows
    if reverse and strict:
        op = operator.lt
    elif reverse and not strict:
        op = operator.le
    elif strict:
        op = operator.gt
    else:
        op = operator.ge

    it = iter(table)
    try:
        flds = [text_type(f) for f in next(it)]
    except StopIteration:
        flds = []
    if key is not None:
        getkey = comparable_itemgetter(*asindices(flds, key))
    try:
        prev = next(it)
    except StopIteration:
        return True  # no data rows
    if key is None:
        for curr in it:
            if not op(curr, prev):
                return False
            prev = curr
    else:
        prevkey = getkey(prev)
        for curr in it:
            currkey = getkey(curr)
            if not op(currkey, prevkey):
                return False
            prevkey = currkey
    return True


Table.issorted = issorted
