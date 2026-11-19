"""File events on a case's private input mounts, loaded by its supervisor before the program starts."""

import ctypes
import errno
import mmap
import os
import struct
import time
import threading


class FileEvents(threading.local):
    """Reuse one queue per comparison worker. Each private mount has its own watch descriptors."""

    def __init__(self, resources):
        self.resources = resources
        self.fd = -1

    def descriptor(self, deadline):
        libc = ctypes.CDLL(None, use_errno=True)
        delay = .001
        while self.fd < 0:
            self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
            if self.fd >= 0:
                self.resources.callback(os.close, self.fd)
                break
            error = ctypes.get_errno()
            if error != errno.EMFILE or time.monotonic() >= deadline:
                raise OSError(error, "inotify_init1")
            time.sleep(delay)
            delay = min(.05, delay * 2)
        self.unmounted = None
        self.drain()  # discard events still queued since the preceding arm returned
        self.events = {}
        self.overflow = None
        self.unmounted = set()
        return self.fd

    def drain(self):
        while True:
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                return
            offset = 0
            while offset < len(data) and self.unmounted is not None:
                wd, mask, _, length = struct.unpack_from("iIII", data, offset)
                raw = data[offset + 16:offset + 16 + length].rstrip(b"\0")
                offset += 16 + length
                if mask & 0x2000:  # IN_UNMOUNT: every process that read through this watch has exited
                    self.unmounted.add(wd)
                elif mask & 0x4000 and self.overflow is None:
                    self.overflow = set(self.unmounted)
                elif raw and not mask & 0x40000000:
                    files = self.events.setdefault(wd, {})
                    first, extra = files.get(raw, (0, 0))
                    files[raw] = (first or mask if mask != 0x8 else first, extra | mask)

    def take(self, watches, mapped, links):
        self.drain()
        # Events lost after one of this arm's own mounts went were its teardown; an earlier arm's mount ends nothing.
        if self.overflow is not None and self.overflow.isdisjoint(map(int, watches)):
            return None, {}
        observed = {}
        for wd, (directory, root) in watches.items():
            for raw, (first, flags) in self.events.get(int(wd), {}).items():
                name = os.path.join(directory, os.fsdecode(raw))
                if first & (0x1 | 0x10) or not flags & (0x80 | 0x100) and (
                        name in mapped if mapped is not None else not first and flags & 0x8):
                    observed[name] = root
        targets = set(observed)
        if links:
            for path, root in observed.items():
                while path != root:
                    path = os.path.dirname(path)
                    targets.add(path)
        aliases = {name: [literal, root] for name, (literal, target, root) in links.items() if target in targets}
        return observed.items(), aliases


class CaseObserver:
    def __init__(self, folders, notify, named=()):
        self.folders = folders
        self.watches = {}
        self.mapped = set()
        self.links = {}
        libc = ctypes.CDLL(None, use_errno=True)
        os.set_inheritable(notify, False)
        directories, unknown = {}, set()
        named = set(named)
        for root in folders:
            pending = [root]
            while pending:
                directory = pending.pop()
                try:
                    entries = os.scandir(directory)
                except PermissionError:
                    if not os.access(directory, os.X_OK):
                        continue
                    raise
                with entries:
                    for entry in entries:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(entry.path)
                        else:
                            directories[directory] = root
                            if entry.path not in named:
                                unknown.add(directory)
                            if entry.is_symlink():
                                self.links[entry.path] = (os.readlink(entry.path), os.path.realpath(entry.path), root)
        # Only folders containing a preexisting file can supply a read. Register
        # after enumeration so the observer's own directory listings emit no events.
        for directory in (directories if self.links else unknown):
            root = directories[directory]
            wd = libc.inotify_add_watch(notify, os.fsencode(directory), 0x1 | 0x2 | 0x8 | 0x10 | 0x80 | 0x100)
            if wd < 0:
                raise OSError(ctypes.get_errno(), directory)
            self.watches[wd] = (directory, root)
        self.ring = self._mapping_ring(libc) if self.watches else None

    @staticmethod
    def _mapping_ring(libc):
        # PERF_COUNT_SW_DUMMY supplies inherited MMAP2 events without sampling instructions.
        # The worker already pins its process group to one CPU.
        try:
            number = ctypes.CDLL("libseccomp.so.2").seccomp_syscall_resolve_name(b"perf_event_open")
        except OSError:
            return None
        attr = bytearray(128)
        struct.pack_into("IIQ", attr, 0, 1, len(attr), 9)
        struct.pack_into("Q", attr, 40, (1 << 1) | (1 << 5) | (1 << 6) | (1 << 8) | (1 << 17) | (1 << 23))
        fd = libc.syscall(number, (ctypes.c_char * len(attr)).from_buffer(attr), 0, min(os.sched_getaffinity(0)), -1, 8)
        if fd < 0:
            return None
        try:
            return mmap.mmap(fd, mmap.PAGESIZE * 33)
        except OSError:
            return None
        finally:
            os.close(fd)

    def drain(self):
        if self.ring is None:
            return
        ring = self.ring
        head, tail, start, capacity = struct.unpack_from("QQQQ", ring, 1024)
        offset = tail % capacity
        size = head - tail
        data = ring[start + offset:start + min(capacity, offset + size)]
        if len(data) < size:
            data += ring[start:start + size - len(data)]
        offset = 0
        while offset < len(data):
            kind, _, length = struct.unpack_from("IHH", data, offset)
            if kind == 10 and struct.unpack_from("I", data, offset + 64)[0] & mmap.PROT_READ:
                name = os.fsdecode(data[offset + 72:offset + length].split(b"\0", 1)[0])
                if any(name.startswith(folder + os.sep) for folder in self.folders):
                    self.mapped.add(name)
            elif kind == 2:  # PERF_RECORD_LOST
                self.ring = None
            offset += length
        struct.pack_into("Q", ring, 1032, head)

    def finish(self):
        self.drain()
        return self.watches, sorted(self.mapped) if self.ring is not None else None, self.links
