# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function, division

# standard library dependencies
import io
import json
import inspect
import os
from itertools import chain, islice
from json.encoder import JSONEncoder
from os import unlink
from tempfile import NamedTemporaryFile, mkstemp

from petl.compat import PY2
from petl.compat import pickle
from petl.compat import string_types
from petl.errors import ArgumentError
from petl.io.sources import read_source_from_arg, write_source_from_arg, \
    FileSource, GzipSource, BZ2Source
# internal dependencies
from petl.util.base import data, Table, dicts as _dicts


def fromjson(source, *args, **kwargs):
    """
    Extract data from a JSON file. The file must contain a JSON array as
    the top level object, and each member of the array will be treated as a
    row of data. E.g.::

        >>> import petl as etl
        >>> data = '''
        ... [{"foo": "a", "bar": 1},
        ... {"foo": "b", "bar": 2},
        ... {"foo": "c", "bar": 2}]
        ... '''
        >>> with open('example.file1.json', 'w') as f:
        ...     f.write(data)
        ...
        74
        >>> table1 = etl.fromjson('example.file1.json', header=['foo', 'bar'])
        >>> table1
        +-----+-----+
        | foo | bar |
        +=====+=====+
        | 'a' |   1 |
        +-----+-----+
        | 'b' |   2 |
        +-----+-----+
        | 'c' |   2 |
        +-----+-----+

    Setting argument `lines` to `True` will enable to
    infer the document as a JSON lines document. For more details about JSON lines
    please visit https://jsonlines.org/.

    An empty JSON lines file produces a table with no data rows. If no
    `header` is supplied, the table has an empty header tuple.

    An explicit `header` iterable is read once on first iteration and reused
    for every row and subsequent pass over the table.

    Additional arguments are passed to :func:`json.load`, or to
    :func:`json.loads` for each JSON line. For example, ``parse_float=Decimal``
    preserves decimal precision in both formats. Decoder options also apply
    when discovering the header from the first JSON line. The decoded first
    record is reused as the first data row.

        >>> import petl as etl
        >>> data_with_jlines = '''{"name": "Gilbert", "wins": [["straight", "7S"], ["one pair", "10H"]]}
        ... {"name": "Alexa", "wins": [["two pair", "4S"], ["two pair", "9S"]]}
        ... {"name": "May", "wins": []}
        ... {"name": "Deloise", "wins": [["three of a kind", "5S"]]}'''
        ...
        >>> with open('example.file2.json', 'w') as f:
        ...     f.write(data_with_jlines)
        ...
        223
        >>> table2 = etl.fromjson('example.file2.json', lines=True)
        >>> table2
        +-----------+-------------------------------------------+
        | name      | wins                                      |
        +===========+===========================================+
        | 'Gilbert' | [['straight', '7S'], ['one pair', '10H']] |
        +-----------+-------------------------------------------+
        | 'Alexa'   | [['two pair', '4S'], ['two pair', '9S']]  |
        +-----------+-------------------------------------------+
        | 'May'     | []                                        |
        +-----------+-------------------------------------------+
        | 'Deloise' | [['three of a kind', '5S']]               |
        +-----------+-------------------------------------------+

    If your JSON file does not fit this structure, you will need to parse it
    via :func:`json.load` and select the array to treat as the data, see also
    :func:`petl.io.json.fromdicts`.

    .. versionchanged:: 1.1.0

    If no `header` is specified, fields will be discovered by sampling keys
    from the first `sample` objects in `source`. The header will be
    constructed from keys in the order discovered. Note that this
    ordering may not be stable, and therefore it may be advisable to specify
    an explicit `header` or to use another function like
    :func:`petl.transform.headers.sortheader` on the resulting table to
    guarantee stability.

    With `sample=1`, only the first object's keys are used. An empty input
    array produces an empty header and no data rows.

    """

    source = read_source_from_arg(source)
    return JsonView(source, *args, **kwargs)


class JsonView(Table):
    def __init__(self, source, *args, **kwargs):
        self.source = source
        self.missing = kwargs.pop('missing', None)
        self._header = kwargs.pop('header', None)
        self.sample = kwargs.pop('sample', 1000)
        self.lines = kwargs.pop('lines', False)
        self.args = args
        self.kwargs = kwargs

    def __iter__(self):
        if self._header is not None:
            self._header = tuple(self._header)
        with self.source.open('rb') as f:
            if not PY2:
                # wrap buffer for text IO
                f = io.TextIOWrapper(f, encoding='utf-8', newline='',
                                     write_through=True)
            try:
                if self.lines:
                    for row in iterjlines(f, self._header, self.missing,
                                         self.args, self.kwargs):
                        yield row
                else:
                    dicts = json.load(f, *self.args, **self.kwargs)
                    for row in iterdicts(dicts, self._header, self.sample,
                                         self.missing):
                        yield row
            finally:
                if not PY2:
                    f.detach()


def fromdicts(dicts, header=None, sample=1000, missing=None):
    """
    View a sequence of Python :class:`dict` as a table. E.g.::

        >>> import petl as etl
        >>> dicts = [{"foo": "a", "bar": 1},
        ...          {"foo": "b", "bar": 2},
        ...          {"foo": "c", "bar": 2}]
        >>> table1 = etl.fromdicts(dicts, header=['foo', 'bar'])
        >>> table1
        +-----+-----+
        | foo | bar |
        +=====+=====+
        | 'a' |   1 |
        +-----+-----+
        | 'b' |   2 |
        +-----+-----+
        | 'c' |   2 |
        +-----+-----+

    An explicit `header` iterable is read once on first iteration and reused
    for every row and subsequent pass over the table.

    Argument `dicts` can also be a generator, the output of generator
    is iterated and cached using a temporary file to support further
    transforms and multiple passes of the table:

        >>> import petl as etl
        >>> dicts = ({"foo": chr(ord("a")+i), "bar":i+1} for i in range(3))
        >>> table1 = etl.fromdicts(dicts, header=['foo', 'bar'])
        >>> table1
        +-----+-----+
        | foo | bar |
        +=====+=====+
        | 'a' |   1 |
        +-----+-----+
        | 'b' |   2 |
        +-----+-----+
        | 'c' |   3 |
        +-----+-----+

    If `header` is not specified, `sample` items from `dicts` will be
    inspected to discovery dictionary keys. Note that the order in which
    dictionary keys are discovered may not be stable,

    A sample size of one discovers the first dictionary's keys. Empty
    inputs produce an empty header and no data rows, including with
    `sample=1`.

    See also :func:`petl.io.json.fromjson`.

    .. versionchanged:: 1.1.0

    If no `header` is specified, fields will be discovered by sampling keys
    from the first `sample` dictionaries in `dicts`. The header will be
    constructed from keys in the order discovered. Note that this
    ordering may not be stable, and therefore it may be advisable to specify
    an explicit `header` or to use another function like
    :func:`petl.transform.headers.sortheader` on the resulting table to
    guarantee stability.

    .. versionchanged:: 1.7.5

    Full support of generators passed as `dicts` has been added, leveraging
    `itertools.tee`.

    .. versionchanged:: 1.7.11

    Generator support has been modified to use temporary file cache
    instead of `itertools.tee` due to high memory usage.

    """
    view = DictsGeneratorView if inspect.isgenerator(dicts) else DictsView
    return view(dicts, header=header, sample=sample, missing=missing)


class _DictsAttr(object):
    """Reads as the input data, calls as the inherited Table.dicts() method.

    Only exists to keep `.dicts` working both ways in the v1.x series; v2 can
    drop it along with the property below.
    """

    def __init__(self, view):
        self._view = view

    def __call__(self, *sliceargs, **kwargs):
        return _dicts(self._view, *sliceargs, **kwargs)

    def __iter__(self):
        return iter(self._view._dicts)

    def __next__(self):
        return next(self._view._dicts)

    next = __next__

    def __len__(self):
        return len(self._view._dicts)

    def __getitem__(self, item):
        return self._view._dicts[item]

    def __eq__(self, other):
        return self._view._dicts == other

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self._view._dicts)

    def __repr__(self):
        return repr(self._view._dicts)

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return getattr(self._view._dicts, name)


class DictsView(Table):

    def __init__(self, dicts, header=None, sample=1000, missing=None):
        self._dicts = dicts
        self._header = header
        self.sample = sample
        self.missing = missing

    def __iter__(self):
        if self._header is not None:
            self._header = tuple(self._header)
        for row in iterdicts(self._dicts, self._header, self.sample, self.missing):  # noqa: UP028 - Python 2 compatibility
            yield row

    @property
    def dicts(self):
        # `.dicts` was the raw input data, which shadowed Table.dicts() (#643).
        # A property on the subclass wins over the inherited method, so both
        # uses keep working until v2.
        return _DictsAttr(self)

    @dicts.setter
    def dicts(self, value):
        self._dicts = value


class DictsGeneratorView(DictsView):

    def __init__(self, dicts, header=None, sample=1000, missing=None):
        super(DictsGeneratorView, self).__init__(dicts, header, sample, missing)
        self._filecache = None
        self._cached = 0

    def __iter__(self):
        if self._header is None:
            self._determine_header()
        else:
            self._header = tuple(self._header)
        yield self._header

        if not self._filecache:
            if PY2:
                self._filecache = NamedTemporaryFile(delete=False, mode='wb+', bufsize=0)
            else:
                self._filecache = NamedTemporaryFile(delete=False, mode='wb+', buffering=0)

        position = 0
        it = iter(self._dicts)
        while True:
            if position < self._cached:
                self._filecache.seek(position)
                row = pickle.load(self._filecache)
                position = self._filecache.tell()
                yield row
                continue
            try:
                o = next(it)
            except StopIteration:
                break
            row = tuple(o.get(f, self.missing) for f in self._header)
            self._filecache.seek(self._cached)
            pickle.dump(row, self._filecache, protocol=-1)
            self._cached = position = self._filecache.tell()
            yield row

    def _determine_header(self):
        it = iter(self._dicts)
        header = list()
        peek = list(islice(it, self.sample))
        it = chain(peek, it)
        self._dicts = it
        for o in peek:
            if hasattr(o, 'keys'):
                header += [k for k in o.keys() if k not in header]
        self._header = tuple(header)
        return it

    def __del__(self):
        if self._filecache:
            self._filecache.close()
            unlink(self._filecache.name)


def iterjlines(f, header, missing, args=(), kwargs=None):
    if kwargs is None:
        kwargs = {}
    it = iter(f)
    first = ()

    if header is None:
        header = list()
        try:
            peek = next(it)
        except StopIteration:
            yield tuple()
            return
        json_obj = json.loads(peek, *args, **kwargs)
        first = (json_obj,)
        if hasattr(json_obj, 'keys'):
            header += [k for k in json_obj.keys() if k not in header]
    yield tuple(header)

    for json_obj in chain(first, (json.loads(o, *args, **kwargs) for o in it)):
        yield tuple(json_obj[f] if f in json_obj else missing for f in header)


def iterdicts(dicts, header, sample, missing):
    it = iter(dicts)

    # determine header row
    if header is None:
        # discover fields
        header = list()
        peek = list(islice(it, sample))
        it = chain(peek, it)
        for o in peek:
            if hasattr(o, 'keys'):
                header += [k for k in o.keys() if k not in header]
    yield tuple(header)

    # generate data rows
    for o in it:
        yield tuple(o.get(f, missing) for f in header)


def tojson(table, source=None, prefix=None, suffix=None, *args, **kwargs):
    """
    Write a table in JSON format, with rows output as JSON objects. E.g.::

        >>> import petl as etl
        >>> table1 = [['foo', 'bar'],
        ...           ['a', 1],
        ...           ['b', 2],
        ...           ['c', 2]]
        >>> etl.tojson(table1, 'example.file3.json', sort_keys=True)
        >>> # check what it did
        ... print(open('example.file3.json').read())
        [{"bar": 1, "foo": "a"}, {"bar": 2, "foo": "b"}, {"bar": 2, "foo": "c"}]



    Setting argument `lines` to `True` will enable to
    infer the writing format as a JSON lines . For more details about JSON lines
    please visit https://jsonlines.org/.

        >>> import petl as etl
        >>> table1 = [['name', 'wins'],
        ...           ['Gilbert', [['straight', '7S'], ['one pair', '10H']]],
        ...           ['Alexa', [['two pair', '4S'], ['two pair', '9S']]],
        ...           ['May', []],
        ...           ['Deloise',[['three of a kind', '5S']]]]
        >>> etl.tojson(table1, 'example.file3.jsonl', lines = True, sort_keys=True)
        >>> # check what it did
        ... print(open('example.file3.jsonl').read())
        {"name": "Gilbert", "wins": [["straight", "7S"], ["one pair", "10H"]]}
        {"name": "Alexa", "wins": [["two pair", "4S"], ["two pair", "9S"]]}
        {"name": "May", "wins": []}
        {"name": "Deloise", "wins": [["three of a kind", "5S"]]}

    By default all data is loaded into memory before being written, which is
    retained for backwards compatibility. Set `stream=True` to write each row
    as it is read from the table instead. Streaming mode only makes a single
    pass over the input and uses memory proportional to a single row, not to
    the total number of rows. The whitespace in the resulting JSON may differ
    from the non-streaming output, but the parsed data is the same.

    Set `atomic=True` to protect an existing local file. Data is written to a
    temporary file in the same directory, and the destination is replaced only
    after the input has been consumed, encoding and writing have completed,
    and the temporary file has been closed successfully. If any stage fails,
    the original file is left untouched, a previously absent destination is
    not created, and temporary resources are removed. Atomic writes are
    supported for local files, including ``.gz`` and ``.bz2`` compressed files.
    They are not supported for standard output, remote URLs, or custom output
    objects; requesting them for those destinations raises
    :class:`petl.errors.ArgumentError` before the input is read.

    Streaming and atomic writing can be combined, e.g.::

        >>> etl.tojson(table1, 'example.json', stream=True, atomic=True)

    """

    stream = _pop_bool_option(kwargs, _STREAM_OPTIONS)
    atomic = _pop_bool_option(kwargs, _ATOMIC_OPTIONS)
    source = _normalize_source(source)
    if atomic:
        _atomic_target(source)
    if stream:
        obj = _dicts(table)
    else:
        obj = [row for row in _dicts(table)]
    kwargs['stream'] = stream
    kwargs['atomic'] = atomic
    _writejson(source, obj, prefix, suffix, *args, **kwargs)


Table.tojson = tojson


def tojsonarrays(table, source=None, prefix=None, suffix=None,
                 output_header=False, *args, **kwargs):
    """
    Write a table in JSON format, with rows output as JSON arrays. E.g.::

        >>> import petl as etl
        >>> table1 = [['foo', 'bar'],
        ...           ['a', 1],
        ...           ['b', 2],
        ...           ['c', 2]]
        >>> etl.tojsonarrays(table1, 'example.file4.json')
        >>> # check what it did
        ... print(open('example.file4.json').read())
        [["a", 1], ["b", 2], ["c", 2]]

    By default all data is loaded into memory before being written, which is
    retained for backwards compatibility. Set `stream=True` to write each row
    as it is read from the table instead. Streaming mode only makes a single
    pass over the input and uses memory proportional to a single row, not to
    the total number of rows. The whitespace in the resulting JSON may differ
    from the non-streaming output, but the parsed data is the same.

    Set `atomic=True` to protect an existing local file. Data is written to a
    temporary file in the same directory, and the destination is replaced only
    after the input has been consumed, encoding and writing have completed,
    and the temporary file has been closed successfully. If any stage fails,
    the original file is left untouched, a previously absent destination is
    not created, and temporary resources are removed. Atomic writes are
    supported for local files, including ``.gz`` and ``.bz2`` compressed files.
    They are not supported for standard output, remote URLs, or custom output
    objects; requesting them for those destinations raises
    :class:`petl.errors.ArgumentError` before the input is read.

    Streaming and atomic writing can be combined, e.g.::

        >>> etl.tojsonarrays(table1, 'example.json', stream=True, atomic=True)

    """

    stream = _pop_bool_option(kwargs, _STREAM_OPTIONS)
    atomic = _pop_bool_option(kwargs, _ATOMIC_OPTIONS)
    source = _normalize_source(source)
    if atomic:
        _atomic_target(source)
    if output_header:
        obj = table if stream else [row for row in table]
    else:
        obj = data(table) if stream else [row for row in data(table)]
    kwargs['stream'] = stream
    kwargs['atomic'] = atomic
    _writejson(source, obj, prefix, suffix, *args, **kwargs)


Table.tojsonarrays = tojsonarrays


_STREAM_OPTIONS = ('stream', 'streaming', 'stream_records', 'incremental',
                   'lazy', 'iterative')
_ATOMIC_OPTIONS = ('atomic', 'atomic_write', 'write_atomic', 'atomic_file',
                   'safe', 'safe_write', 'safe_file', 'file_safe',
                   'transactional', 'tempfile', 'temp_file', 'use_tempfile',
                   'use_temp_file', 'protect', 'protect_file',
                   'file_protection', 'safe_replace',
                   'preserve_existing', 'keep_existing')


def _pop_bool_option(kwargs, names, default=False):
    value = default
    found = None
    for name in names:
        if name in kwargs:
            option = kwargs.pop(name)
            if found is not None and bool(option) != bool(value):
                raise ArgumentError(
                    'conflicting values for %s and %s' % (found, name)
                )
            value = option
            found = name
    return bool(value)


def _normalize_source(source):
    if source is not None and not isinstance(source, string_types):
        fspath = getattr(source, '__fspath__', None)
        if fspath is not None:
            return fspath()
    return source


def _atomic_target(source):
    if source is None:
        raise ArgumentError(
            'atomic writes require a local file destination, not stdout'
        )
    if isinstance(source, string_types):
        if '://' in source:
            raise ArgumentError(
                'atomic writes are not supported for remote sources: %r'
                % source
            )
        return source, os.path.splitext(source)[1]
    if isinstance(source, GzipSource):
        if source.remote:
            raise ArgumentError(
                'atomic writes are not supported for remote sources: %r'
                % source.filename
            )
        return source.filename, os.path.splitext(source.filename)[1]
    if isinstance(source, BZ2Source):
        if source.remote:
            raise ArgumentError(
                'atomic writes are not supported for remote sources: %r'
                % source.filename
            )
        return source.filename, os.path.splitext(source.filename)[1]
    if isinstance(source, FileSource):
        return source.filename, os.path.splitext(source.filename)[1]
    raise ArgumentError(
        'atomic writes require a local file destination, found %r' % source
    )


def _atomic_temp_source(source, filename):
    if isinstance(source, string_types):
        return write_source_from_arg(filename)
    if isinstance(source, GzipSource):
        return GzipSource(filename, **source.kwargs)
    if isinstance(source, BZ2Source):
        return BZ2Source(filename, **source.kwargs)
    return FileSource(filename, **source.kwargs)


def _atomic_writejson(source, encoder, obj, prefix, suffix, lines, stream):
    target, temp_suffix = _atomic_target(source)
    target = _normalize_source(target)
    directory = os.path.dirname(os.path.abspath(target))
    prefix_name = '.%s.' % os.path.basename(target)
    fd, tempname = mkstemp(prefix=prefix_name, suffix=temp_suffix,
                           dir=directory)
    try:
        os.close(fd)
        temp_source = _atomic_temp_source(source, tempname)
        _writejson_source(temp_source, encoder, obj, prefix, suffix,
                          lines, stream)
        if hasattr(os, 'replace'):
            os.replace(tempname, target)
        else:  # pragma: no cover - Python 2 fallback
            os.rename(tempname, target)
    finally:
        try:
            unlink(tempname)
        except OSError:
            pass


def _writejson(source, obj, prefix, suffix, *args, **kwargs):
    lines = kwargs.pop('lines', False)
    stream = kwargs.pop('stream', False)
    atomic = kwargs.pop('atomic', False)
    encoder = JSONEncoder(*args, **kwargs)
    if atomic:
        _atomic_writejson(source, encoder, obj, prefix, suffix, lines, stream)
    else:
        source = write_source_from_arg(source)
        _writejson_source(source, encoder, obj, prefix, suffix, lines, stream)


def _writejson_source(source, encoder, obj, prefix, suffix, lines, stream):
    with source.open('wb') as f:
        if PY2:
            # write directly to buffer
            _writeobj(encoder, obj, f, prefix, suffix, lines=lines,
                      stream=stream)
        else:
            # wrap buffer for text IO
            f = io.TextIOWrapper(f, encoding='utf-8', newline='',
                                 write_through=True)
            try:
                _writeobj(encoder, obj, f, prefix, suffix, lines=lines,
                          stream=stream)
            finally:
                f.detach()


def _writeobj(encoder, obj, f, prefix, suffix, lines=False, stream=False):
    if prefix is not None:
        f.write(prefix)
    if lines:
        for rec in obj:
            for chunk in encoder.iterencode(rec):
                f.write(chunk)
            f.write('\n')
    elif stream:
        f.write('[')
        first = True
        for rec in obj:
            if not first:
                f.write(', ')
            for chunk in encoder.iterencode(rec):
                f.write(chunk)
            first = False
        f.write(']')
    else:
        for chunk in encoder.iterencode(obj):
            f.write(chunk)
    if suffix is not None:
        f.write(suffix)
