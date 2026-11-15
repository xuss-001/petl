from __future__ import absolute_import, print_function, division


# sort()
########

import petl as etl
table1 = [['foo', 'bar'],
          ['C', 2],
          ['A', 9],
          ['A', 6],
          ['F', 1],
          ['D', 10]]
table2 = etl.sort(table1, 'foo')
table2
# sorting by compound key is supported
table3 = etl.sort(table1, key=['foo', 'bar'])
table3
# if no key is specified, the default is a lexical sort
table4 = etl.sort(table1)
table4
# multiple fields can be sorted in different directions, with null placement
# independent of direction
bills = [['customer', 'amount'],
         ['B', 20],
         ['A', None],
         ['A', 5],
         ['B', None],
         ['A', 7]]
table5 = etl.sort(
    bills,
    key=[('customer', 'asc'), ('amount', 'desc', 'last')]
)
table5
# equivalent per-field option form
table6 = etl.sort(
    bills,
    key=['customer', 'amount'],
    reverse=[False, True],
    nulls_first=[True, False]
)
table6


# mergesort()
#############

import petl as etl
table1 = [['foo', 'bar'],
          ['A', 9],
          ['C', 2],
          ['D', 10],
          ['A', 6],
          ['F', 1]]
table2 = [['foo', 'bar'],
          ['B', 3],
          ['D', 10],
          ['A', 10],
          ['F', 4]]
table3 = etl.mergesort(table1, table2, key='foo')
table3.lookall()


# issorted()
############

import petl as etl
table1 = [['foo', 'bar', 'baz'],
          ['a', 1, True],
          ['b', 3, True],
          ['b', 2]]
etl.issorted(table1, key='foo')
etl.issorted(table1, key='bar')
etl.issorted(table1, key='foo', strict=True)
etl.issorted(table1, key='foo', reverse=True)
