# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function, division

import bz2
import gzip
import io
import json
import os

import pytest

import petl.io.json as json_module
from petl import fromjson, tojson, tojsonarrays
from petl.errors import ArgumentError
from petl.io.sources import BZ2Source, FileSource, GzipSource, MemorySource


TABLE = (('foo', 'bar'),
         ('a', 1),
         ('b', 2),
         ('章子怡', 3))

EXPECTED_OBJECTS = [
    {'foo': 'a', 'bar': 1},
    {'foo': 'b', 'bar': 2},
    {'foo': '章子怡', 'bar': 3},
]


def _read_json(path, lines=False):
    if lines:
        with open(path, encoding='utf-8') as f:
            return [json.loads(line) for line in f if line.strip()]
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _leftover_temps(directory, target):
    prefix = '.%s.' % os.path.basename(target)
    return [name for name in os.listdir(directory)
            if name.startswith(prefix)]


@pytest.mark.parametrize('lines', [False, True])
@pytest.mark.parametrize('stream', [False, True])
def test_tojson_stream_matches_default(tmpdir, lines, stream):
    path = str(tmpdir.join('records.json'))
    tojson(TABLE, path, lines=lines, stream=stream,
           sort_keys=True, ensure_ascii=False)
    assert _read_json(path, lines=lines) == EXPECTED_OBJECTS


@pytest.mark.parametrize('lines', [False, True])
@pytest.mark.parametrize('atomic', [False, True])
def test_tojson_encoding_options_and_unicode(tmpdir, lines, atomic):
    path = str(tmpdir.join('records.json'))
    options = {'sort_keys': True, 'ensure_ascii': False}
    if not lines:
        options['indent'] = 2
    tojson(TABLE, path, lines=lines, stream=True, atomic=atomic,
           **options)
    assert _read_json(path, lines=lines) == EXPECTED_OBJECTS
    with open(path, encoding='utf-8') as f:
        assert '章子怡' in f.read()


@pytest.mark.parametrize('output_header', [False, True])
@pytest.mark.parametrize('atomic', [False, True])
def test_tojsonarrays_stream_header(tmpdir, output_header, atomic):
    path = str(tmpdir.join('arrays.json'))
    tojsonarrays(TABLE, path, stream=True, atomic=atomic,
                 output_header=output_header, ensure_ascii=False)
    expected = [list(row) for row in (TABLE if output_header else TABLE[1:])]
    assert _read_json(path) == expected


def test_tojson_streaming_is_single_pass_per_row_encoding(monkeypatch, tmpdir):
    rows_read = []

    def table():
        yield ('id', 'value')
        for i in range(5000):
            rows_read.append(i)
            yield (i, 'x' * 100)

    encoded = []
    original_iterencode = json.JSONEncoder.iterencode

    def iterencode(self, o, *args, **kwargs):
        encoded.append(o)
        return original_iterencode(self, o, *args, **kwargs)

    monkeypatch.setattr(json.JSONEncoder, 'iterencode', iterencode)
    path = str(tmpdir.join('large.jsonl'))
    tojson(table(), path, lines=True, stream=True)

    assert rows_read == list(range(5000))
    assert len(encoded) == 5000
    assert all(isinstance(record, dict) for record in encoded)
    assert len(_read_json(path, lines=True)) == 5000


@pytest.mark.parametrize('suffix,opener', [
    ('', open),
    ('.gz', gzip.open),
    ('.bz2', bz2.open),
])
def test_tojson_atomic_stream_roundtrip_compressed(tmpdir, suffix, opener):
    path = str(tmpdir.join('records.json' + suffix))
    with opener(path, 'wb') as f:
        f.write(b'old contents')

    tojson(TABLE, path, stream=True, atomic=True, ensure_ascii=False)
    with opener(path, 'rt', encoding='utf-8') as f:
        assert json.load(f) == EXPECTED_OBJECTS
    assert list(fromjson(path)) == list(TABLE)


@pytest.mark.parametrize('source_class', [FileSource, GzipSource, BZ2Source])
def test_tojson_atomic_explicit_local_source(tmpdir, source_class):
    if source_class is FileSource:
        path = str(tmpdir.join('records.json'))
    elif source_class is GzipSource:
        path = str(tmpdir.join('records.json.gz'))
    else:
        path = str(tmpdir.join('records.json.bz2'))
    tojson(TABLE, source_class(path), stream=True, atomic=True)
    assert list(fromjson(path)) == list(TABLE)


def test_tojson_atomic_preserves_existing_on_input_failure(tmpdir):
    path = str(tmpdir.join('records.json'))
    with open(path, 'w') as f:
        f.write('last good file')

    def table():
        yield ('foo', 'bar')
        yield ('a', 1)
        raise RuntimeError('input failed')

    with pytest.raises(RuntimeError, match='input failed'):
        tojson(table(), path, stream=True, atomic=True)

    assert open(path).read() == 'last good file'
    assert _leftover_temps(str(tmpdir), path) == []


def test_tojson_atomic_absent_target_on_input_failure(tmpdir):
    path = str(tmpdir.join('records.json'))

    def table():
        yield ('foo', 'bar')
        yield ('a', 1)
        raise RuntimeError('input failed')

    with pytest.raises(RuntimeError, match='input failed'):
        tojson(table(), path, stream=True, atomic=True)

    assert not os.path.exists(path)
    assert _leftover_temps(str(tmpdir), path) == []


def test_tojson_atomic_preserves_existing_on_encoding_failure(tmpdir):
    path = str(tmpdir.join('records.json'))
    with open(path, 'w') as f:
        f.write('last good file')

    with pytest.raises(TypeError):
        tojson([('foo',), (object(),)], path, stream=True, atomic=True)

    assert open(path).read() == 'last good file'
    assert _leftover_temps(str(tmpdir), path) == []


def test_tojson_atomic_preserves_existing_on_write_failure(
        monkeypatch, tmpdir):
    path = str(tmpdir.join('records.json'))
    with open(path, 'w') as f:
        f.write('last good file')

    def fail_write(encoder, obj, f, prefix, suffix, lines=False, stream=False):
        f.write('partial output')
        f.flush()
        raise OSError('write failed')

    monkeypatch.setattr(json_module, '_writeobj', fail_write)
    with pytest.raises(OSError, match='write failed'):
        tojson(TABLE, path, stream=True, atomic=True)

    assert open(path).read() == 'last good file'
    assert _leftover_temps(str(tmpdir), path) == []


def test_tojson_atomic_preserves_existing_on_close_failure(
        monkeypatch, tmpdir):
    path = str(tmpdir.join('records.json'))
    with open(path, 'w') as f:
        f.write('last good file')

    class CloseFailsSource(object):
        def __init__(self, filename):
            self.filename = filename

        def open(self, mode='wb'):
            return CloseFailsFile(io.open(self.filename, mode))

    class CloseFailsFile(object):
        def __init__(self, inner):
            self.inner = inner

        def __enter__(self):
            return self.inner

        def __exit__(self, exc_type, exc_value, traceback):
            self.inner.close()
            raise OSError('close failed')

    monkeypatch.setattr(
        json_module, '_atomic_temp_source',
        lambda source, filename: CloseFailsSource(filename)
    )
    with pytest.raises(OSError, match='close failed'):
        tojson(TABLE, path, stream=True, atomic=True)

    assert open(path).read() == 'last good file'
    assert _leftover_temps(str(tmpdir), path) == []


class _Sink(object):
    def __init__(self):
        self.opened = False

    def open(self, mode):
        self.opened = True
        raise AssertionError('sink should not be opened')


@pytest.mark.parametrize('source_kind', ['stdout', 'remote', 'custom'])
def test_tojson_atomic_rejects_nonlocal_before_reading(source_kind):
    read = []

    def table():
        read.append(True)
        yield ('foo',)
        yield ('a',)

    if source_kind == 'stdout':
        source = None
    elif source_kind == 'remote':
        source = 'https://example.com/records.json'
    else:
        source = _Sink()

    with pytest.raises(ArgumentError):
        tojson(table(), source, stream=True, atomic=True)

    assert read == []
    if isinstance(source, _Sink):
        assert source.opened is False


def test_tojson_prefix_suffix_stream_atomic(tmpdir):
    path = str(tmpdir.join('wrapped.json'))
    tojson(TABLE, path, prefix='{"records": ', suffix='}',
           stream=True, atomic=True, ensure_ascii=False)
    with open(path, encoding='utf-8') as f:
        assert json.load(f) == {'records': EXPECTED_OBJECTS}


def test_tojson_memory_source_default_is_unchanged():
    sink = MemorySource()
    tojson(TABLE, sink)
    assert json.loads(sink.getvalue().decode('utf-8')) == EXPECTED_OBJECTS
