"""
Thin compatibility shim: makes rocksdict look like python-rocksdb.

Used by rocksdb_backend.py when `import rocksdb` fails but `rocksdict` is
available. Covers the exact API surface used by rocksdb_backend.py — no more.

python-rocksdb API → rocksdict API mapping:
  rocksdb.Options()                   → rocksdict.Options(raw_mode=True)
  rocksdb.ColumnFamilyOptions()       → rocksdict.Options(raw_mode=True)
  rocksdb.DB(path, opts, cf_descs)    → rocksdict.Rdict + CF handles
  rocksdb.WriteBatch()                → rocksdict.WriteBatch(raw_mode=True)
    batch.put((cf_handle, key), val)  → set_default_column_family + put
  rocksdb.WriteOptions(); wo.sync=T   → rocksdict.WriteOptions()
  db.write(wo, batch)                 → db.write(batch, wo)
  db.get((cf_handle, key))            → cf_rdict.get(key)
  db.iteritems(cf_handle)             → _ItemsIterator
  db.itervalues(cf_handle)            → _ValuesIterator
"""

from __future__ import annotations

from typing import Any, Iterator, List, Optional, Tuple

from rocksdict import (
    Options as _Options,
    Rdict,
    WriteBatch as _WriteBatch,
    WriteOptions as _WriteOptions,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

default_column_family_name = b"default"


# ---------------------------------------------------------------------------
# Types used for isinstance checks
# ---------------------------------------------------------------------------

class ColumnFamilyHandle:
    """Placeholder type — shim uses _CFHandle internally."""
    pass


# ---------------------------------------------------------------------------
# Options wrappers
# ---------------------------------------------------------------------------

class Options:
    def __init__(self) -> None:
        self._create_if_missing              = True
        self._create_missing_column_families = True

    # python-rocksdb uses attribute assignment; we intercept via __setattr__
    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)

    def _to_rocksdict(self) -> _Options:
        o = _Options(raw_mode=True)
        o.create_if_missing(self._create_if_missing)
        o.create_missing_column_families(self._create_missing_column_families)
        return o


class ColumnFamilyOptions:
    """Stub — all column families use the same default options."""
    pass


# ---------------------------------------------------------------------------
# WriteBatch wrapper
# ---------------------------------------------------------------------------

class WriteBatch:
    def __init__(self) -> None:
        self._ops: List[Tuple[bytes, bytes, bytes]] = []
        # Each op: (cf_name, key, value) — value=None means delete

    def put(self, key_with_cf: Tuple[Any, bytes], value: bytes) -> None:
        cf_handle, key = key_with_cf
        self._ops.append((cf_handle._name, key, value))

    def delete(self, key_with_cf: Tuple[Any, bytes]) -> None:
        cf_handle, key = key_with_cf
        self._ops.append((cf_handle._name, key, None))


# ---------------------------------------------------------------------------
# WriteOptions wrapper
# ---------------------------------------------------------------------------

class WriteOptions:
    def __init__(self) -> None:
        self.sync = True

    def _to_rocksdict(self) -> _WriteOptions:
        wo = _WriteOptions()
        # rocksdict WriteOptions has no .sync setter exposed; sync is implicit
        return wo


# ---------------------------------------------------------------------------
# Column family handle
# ---------------------------------------------------------------------------

class _CFHandle:
    """Thin wrapper around a per-CF Rdict sub-handle."""

    def __init__(self, name: bytes, rdict: Rdict) -> None:
        self._name   = name
        self._rdict  = rdict  # the Rdict object for this CF
        self._handle = rdict.get_column_family_handle(name.decode())


# ---------------------------------------------------------------------------
# Iterator wrappers
# ---------------------------------------------------------------------------

class _ItemsIterator:
    """
    Emulates python-rocksdb's iteritems() return type.
    Yields (key_bytes, value_bytes) tuples.
    After seek()/seek_to_first() it iterates forward.
    """

    def __init__(self, cf_handle: "_CFHandle") -> None:
        self._rdict = cf_handle._rdict
        self._it    = self._rdict.iter()
        self._reverse = False

    def seek(self, key: bytes) -> None:
        self._it = self._rdict.iter()
        self._it.seek(key)
        self._reverse = False

    def seek_to_first(self) -> None:
        self._it = self._rdict.iter()
        self._it.seek_to_first()
        self._reverse = False

    def seek_to_last(self) -> None:
        self._it = self._rdict.iter()
        self._it.seek_to_last()
        self._reverse = True

    def __iter__(self) -> Iterator[Tuple[bytes, bytes]]:
        it = self._it
        advance = it.prev if self._reverse else it.next
        while it.valid():
            k = it.key()
            v = it.value()
            advance()
            yield k, v


class _ValuesIterator:
    """
    Emulates python-rocksdb's itervalues() return type.
    Yields value_bytes.
    After seek_to_last() iterates in reverse (newest-first).
    """

    def __init__(self, cf_handle: "_CFHandle") -> None:
        self._rdict   = cf_handle._rdict
        self._it      = self._rdict.iter()
        self._reverse = False

    def seek_to_first(self) -> None:
        self._it = self._rdict.iter()
        self._it.seek_to_first()
        self._reverse = False

    def seek_to_last(self) -> None:
        self._it = self._rdict.iter()
        self._it.seek_to_last()
        self._reverse = True

    def seek(self, key: bytes) -> None:
        self._it = self._rdict.iter()
        self._it.seek(key)
        self._reverse = False

    def __iter__(self) -> Iterator[bytes]:
        it = self._it
        advance = it.prev if self._reverse else it.next
        while it.valid():
            v = it.value()
            advance()
            yield v


# ---------------------------------------------------------------------------
# DB wrapper — main entry point
# ---------------------------------------------------------------------------

class DB:
    """
    Wraps rocksdict.Rdict to emulate python-rocksdb's DB class.

    Usage (mirrors python-rocksdb):
        opts = rocksdb.Options(); opts.create_if_missing = True
        cf_opts = rocksdb.ColumnFamilyOptions()
        cf_descs = [(b"default", cf_opts), (b"runs", cf_opts), ...]
        db, cf_handles = rocksdb.DB(path, opts, column_families=cf_descs)
    """

    def __new__(cls,
                path: str,
                opts: Options,
                column_families: Optional[List[Tuple[bytes, Any]]] = None):  # type: ignore
        instance = object.__new__(cls)
        instance._init(path, opts, column_families or [])
        return instance

    def _init(self,
              path: str,
              opts: Options,
              column_families: List[Tuple[bytes, Any]]) -> None:
        rd_opts = opts._to_rocksdict()

        # Build column family dict for rocksdict (exclude "default" from extras)
        cf_rd_opts = _Options(raw_mode=True)
        cf_rd_opts.create_if_missing(True)
        cf_rd_opts.create_missing_column_families(True)

        cf_dict = {}
        cf_names_in_order = []
        for name, _ in column_families:
            if isinstance(name, bytes):
                name_str = name.decode()
            else:
                name_str = name
                name = name.encode()
            if name_str == "default":
                cf_names_in_order.append(name)
                continue
            cf_dict[name_str] = cf_rd_opts
            cf_names_in_order.append(name)

        self._rdict = Rdict(path, options=rd_opts,
                            column_families=cf_dict if cf_dict else None)
        self._cf_handles: dict[bytes, _CFHandle] = {}

        # Build handles for all CFs including default
        for name in cf_names_in_order:
            name_str = name.decode() if isinstance(name, bytes) else name
            name_b   = name if isinstance(name, bytes) else name.encode()
            try:
                if name_str == "default":
                    cf_rdict = self._rdict
                else:
                    cf_rdict = self._rdict.get_column_family(name_str)
            except Exception:
                cf_rdict = self._rdict
            self._cf_handles[name_b] = _CFHandle(name_b, cf_rdict)

    def _resolve_cf(self, cf_handle: Any) -> _CFHandle:
        if isinstance(cf_handle, _CFHandle):
            return cf_handle
        if isinstance(cf_handle, bytes):
            return self._cf_handles.get(cf_handle, self._cf_handles[b"default"])
        return self._cf_handles[b"default"]

    def get(self, key_with_cf: Tuple[Any, bytes]) -> Optional[bytes]:
        cf_handle, key = key_with_cf
        cf = self._resolve_cf(cf_handle)
        try:
            return cf._rdict.get(key)
        except Exception:
            return None

    def write(self, write_options: WriteOptions, batch: WriteBatch) -> None:
        wo = write_options._to_rocksdict()
        rd_batch = _WriteBatch(raw_mode=True)
        for cf_name, key, value in batch._ops:
            handle_obj = self._cf_handles.get(cf_name)
            if handle_obj is None:
                continue
            rd_batch.set_default_column_family(handle_obj._handle)
            if value is None:
                rd_batch.delete(key)
            else:
                rd_batch.put(key, value)
        self._rdict.write(rd_batch, wo)

    def iteritems(self, cf_handle: Any) -> _ItemsIterator:
        return _ItemsIterator(self._resolve_cf(cf_handle))

    def itervalues(self, cf_handle: Any) -> _ValuesIterator:
        return _ValuesIterator(self._resolve_cf(cf_handle))

    def compact_range(self, *args: Any, **kwargs: Any) -> None:
        pass

    def close(self) -> None:
        # rocksdict keeps the LOCK file open until ALL Rdict and ColumnFamily
        # instances are garbage-collected. Explicitly delete everything.
        for handle in list(self._cf_handles.values()):
            try:
                del handle._handle
            except Exception:
                pass
            try:
                if handle._rdict is not self._rdict:
                    handle._rdict.close()
            except Exception:
                pass
            try:
                del handle._rdict
            except Exception:
                pass
        self._cf_handles.clear()
        try:
            self._rdict.close()
        except Exception:
            pass
        try:
            del self._rdict
        except Exception:
            pass
        import gc
        gc.collect()


# ---------------------------------------------------------------------------
# Module-level shim: make `DB(...)` return (db, [handles]) like python-rocksdb
# ---------------------------------------------------------------------------

_OriginalDB = DB


class _DBFactory:
    """
    Callable that returns (DB, [cf_handles]) to match python-rocksdb's
    `db, cf_handles = rocksdb.DB(path, opts, column_families=[...])`.
    """

    def __call__(self, path: str, opts: Options,
                 column_families: Optional[List[Tuple[bytes, Any]]] = None,
                 **_: Any) -> Tuple["DB", List[_CFHandle]]:
        db = _OriginalDB(path, opts, column_families or [])
        cf_names = [name for name, _ in (column_families or [])]
        handles  = [db._cf_handles.get(
                        n if isinstance(n, bytes) else n.encode(),
                        list(db._cf_handles.values())[0])
                    for n in cf_names]
        return db, handles


DB = _DBFactory()  # type: ignore
