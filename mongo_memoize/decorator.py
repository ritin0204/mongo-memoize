# -*- coding: utf-8 -*-

from __future__ import absolute_import, print_function

import pymongo
from functools import wraps
import hashlib

from mongo_memoize.key_generator import PickleMD5KeyGenerator
from mongo_memoize.serializer import PickleSerializer


class Memoizer(object):

    def __init__(self, db_name='mongo_memoize', mongo_client=None, host='localhost', port=27017, collection_name=None,
                 prefix='memoize', capped=False, capped_size=100000000, capped_max=None,
                 connection_options={}, key_generator=None, serializer=None, verbose=False):

        self.serializer = serializer
        if not self.serializer:
            self.serializer = PickleSerializer()

        self.key_generator = key_generator
        if not self.key_generator:
            self.key_generator = PickleMD5KeyGenerator()

        self.host = host
        self.port = port
        self.connection_options = connection_options
        self.db_name = db_name

        self.collection_name = collection_name
        self.prefix = prefix
        self.capped = capped
        self.capped_size = capped_size
        self.capped_max = capped_max
        self.verbose = verbose

        self.db_conn = mongo_client
        self.db = None
        self.is_connected = False
        self.external_db_conn = True if mongo_client else False

    def connect(self):
        if not self.external_db_conn:
            self.db_conn = pymongo.MongoClient(
                self.host, self.port, *self.connection_options)
        self.db = self.db_conn[self.db_name]
        self.is_connected = True

    def disconnect(self):
        if not self.external_db_conn:
            try:
                self.db_conn.close()
            except AttributeError:
                self.db_conn = None
            self.is_connected = False

    def get_col_name(self, func):
        if self.collection_name:
            col_name = self.collection_name
        else:
            func_module_encoded = func.__module__.encode('utf-8')
            col_name = '%s_%s_%s' % (self.prefix, func.__name__, hashlib.md5(
                func_module_encoded).hexdigest())
        return col_name

    def initialize_col(self, func):
        self.connect()
        col_name = self.get_col_name(func)

        if self.capped:
            if col_name not in self.db.list_collection_names():
                assert self.capped_size > 0, 'The size of the capped collection is required.'

                capped_args = dict()
                capped_args['size'] = self.capped_size
                if self.capped_max:
                    capped_args['max'] = self.capped_max

                self.db.create_collection(col_name, capped=True, **capped_args)

        cache_col = self.db[col_name]
        cache_col.create_index('key', unique=True)
        return cache_col

    def memoize(self):
        """A decorator that caches results of the function in MongoDB.

        Usage:

            >>> from mongo_memoize import memoize
            >>> @memoize()
            ... def some_function():
            ...     pass
            ...

        :param str db_name: MongoDB database name.
        :param str host: MongoDB host name.
        :param int port: MongoDB port.
        :param str collection_name: MongoDB collection name. If not specified, the
            collection name is generated automatically using the prefix, the module
            name, and the function name.
        :param str prefix: Prefix of the MongoDB collection name. This argument is
            only valid when the collection_name argument is not specified.
        :param bool capped: Whether to use the capped collection.
        :param int capped_size: The maximum size of the capped collection in bytes.
        :param int capped_max: The maximum number of items in the capped collection.
        :param dict connection_options: Additional parameters for establishing
            MongoDB connection.
        :param key_generator: Key generator instance.
            :class:`PickleMD5KeyGenerator <mongo_memoize.PickleMD5KeyGenerator>` is used by default.
        :param serializer: Serializer instance.
            :class:`PickleSerializer <mongo_memoize.PickleSerializer>` is used by default.
        """

        def decorator(func):

            cache_col = self.initialize_col(func)

            @wraps(func)
            def wrapped_func(*args, **kwargs):
                if not self.is_connected:
                    self.connect()

                cache_key = self.key_generator(args, kwargs)

                cached_obj = cache_col.find_one(dict(key=cache_key))
                if cached_obj:
                    if self.verbose:
                        print("Cache hit: {} ___ {}".format(args, kwargs))
                    return self.serializer.deserialize(cached_obj['result'])

                if self.verbose:
                    print("Cache miss: {} ___ {}".format(args, kwargs))

                ret = func(*args, **kwargs)
                cache_col.update(
                    {'key': cache_key},
                    {
                        '$set': {
                            'result': self.serializer.serialize(ret),
                            'args': str(args),
                            'kwargs': str(kwargs)
                        }
                    },
                    upsert=True
                )

                return ret

            return wrapped_func

        return decorator

    @staticmethod
    def normalize_args_list(arg_list, kwarg_list):
        if arg_list is None and kwarg_list is None:
            return list()

        if arg_list is None:
            arg_list = [() for _ in kwarg_list]

        if kwarg_list is None:
            if len(arg_list) and isinstance(arg_list[0], dict):
                raise ValueError("Dictionary provided in arg_list.  If only kwarg_list is to be provide it, "
                                 "provide it as a named argument.")
            kwarg_list = [dict() for _ in arg_list]

        return arg_list, kwarg_list

    def get_key_list(self, arg_list, kwarg_list):
        arg_list, kwarg_list = self.normalize_args_list(arg_list, kwarg_list)
        key_list = [self.key_generator(args, kwargs)
                    for args, kwargs in zip(arg_list, kwarg_list)]
        return key_list

    def batch_check_keys(self, func, key_list):
        """Check lists of args and kwargs to see if they've been calculated."""
        cache_col = self.initialize_col(func)
        key_found = [bool(cache_col.find_one(dict(key=cache_key)))
                     for cache_key in key_list]
        self.disconnect()
        return key_found


def memoize(
        db_name='mongo_memoize', mongo_client=None, host='localhost', port=27017, collection_name=None,
        prefix='memoize', capped=False, capped_size=100000000, capped_max=None,
        connection_options={}, key_generator=None, serializer=None, verbose=False
):
    """A decorator that caches results of the function in MongoDB.

    Usage:

        >>> from mongo_memoize import memoize
        >>> @memoize()
        ... def some_function():
        ...     pass
        ...

    :param str db_name: MongoDB database name.
    :param MongoClient mongo_client: MongoDB database connection as PyMongo Client
    :param str host: MongoDB host name.
    :param int port: MongoDB port.
    :param str collection_name: MongoDB collection name. If not specified, the
        collection name is generated automatically using the prefix, the module
        name, and the function name.
    :param str prefix: Prefix of the MongoDB collection name. This argument is
        only valid when the collection_name argument is not specified.
    :param bool capped: Whether to use the capped collection.
    :param int capped_size: The maximum size of the capped collection in bytes.
    :param int capped_max: The maximum number of items in the capped collection.
    :param dict connection_options: Additional parameters for establishing
        MongoDB connection.
    :param key_generator: Key generator instance.
        :class:`PickleMD5KeyGenerator <mongo_memoize.PickleMD5KeyGenerator>` is used by default.
    :param serializer: Serializer instance.
        :class:`PickleSerializer <mongo_memoize.PickleSerializer>` is used by default.
    """

    def decorator(func):

        memoizer = Memoizer(db_name, mongo_client, host, port, collection_name,
                            prefix, capped, capped_size, capped_max,
                            connection_options, key_generator, serializer, verbose)

        memoizer.connect()
        cache_col = memoizer.initialize_col(func)

        @wraps(func)
        def wrapped_func(*args, **kwargs):
            cache_key = memoizer.key_generator(args, kwargs)

            cached_obj = cache_col.find_one(dict(key=cache_key))
            if cached_obj:
                if verbose:
                    print("Cache hit: {} ___ {}".format(args, kwargs))
                return memoizer.serializer.deserialize(cached_obj['result'])

            if verbose:
                print("Cache miss: {} ___ {}".format(args, kwargs))

            ret = func(*args, **kwargs)
            cache_col.update_one(
                {'key': cache_key},
                {
                    '$set': {
                        'result': memoizer.serializer.serialize(ret),
                        'args': str(args),
                        'kwargs': str(kwargs)
                    }
                },
                upsert=True
            )

            return ret

        return wrapped_func

    return decorator
