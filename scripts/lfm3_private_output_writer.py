"""Output boundary for LOCAL, fictional-only LFM3 custodian projection.

Requires owner-private parent directories; never follows symlinked ancestors
or final targets. Uses O_EXCL and inode-aware rollback on second-write error.
This is not a transactional filesystem, external custody, or authority to infer.
"""
from __future__ import annotations

import os
import stat

PACKET_MAX_BYTES = 2 * 1024 * 1024
RECEIPT_MAX_BYTES = 256 * 1024


def _target_dirfd(path: str) -> tuple[int, str]:
    if (type(path) is not str or not path.startswith('/') or '\x00' in path or
        not all(part not in ('.','..') for part in path.split('/'))):
        raise ValueError('noncanonical_absolute_output_path')
    parts = [part for part in path.split('/') if part]
    if len(parts) < 2 or not parts[-1]:
        raise ValueError('invalid_private_output_target')
    # Use O_PATH for ancestor directory descriptors. Some isolated hosts
    # deny read-opening '/' even though safe, non-reading path traversal is
    # allowed. Require O_PATH rather than silently following parent links.
    if not hasattr(os,'O_PATH') or not hasattr(os,'O_NOFOLLOW'):
        raise ValueError('required_safe_directory_descriptor_flags_missing')
    flags=os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW
    if hasattr(os,'O_CLOEXEC'):
        flags|=os.O_CLOEXEC
    fd=os.open('/',flags)
    # User-namespace hosts may map the system root owner to a UID other than
    # zero. Anchor that owner only to the verified '/' descriptor; never
    # accept arbitrary third-party directory owners based on a magic UID.
    root_stat=os.fstat(fd)
    system_uid=root_stat.st_uid
    if stat.S_IMODE(root_stat.st_mode)&0o022:
        os.close(fd)
        raise ValueError('untrusted_filesystem_root_permissions')
    try:
        for i,part in enumerate(parts[:-1]):
            nxt=os.open(part,flags,dir_fd=fd)
            os.close(fd)
            fd=nxt
            st=os.fstat(fd)
            m=stat.S_IMODE(st.st_mode)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid not in (system_uid,os.geteuid()):
                raise ValueError('untrusted_output_ancestor_owner')
            if i==len(parts)-2:
                # The final parent containing private gold/alias receipts must
                # be owned and traversable/readable only by the custodian.
                if (st.st_uid!=os.geteuid() or (m&0o777)!=0o700):
                    raise ValueError('output_parent_not_owner_private_0700')
            elif m & 0o022:
                # Only system-managed sticky /tmp-style ancestors are allowed.
                if not (st.st_uid==system_uid and (m&stat.S_ISVTX) and (m&0o777)==0o777):
                    raise ValueError('untrusted_writable_output_ancestor')
        # O_PATH cannot fsync a directory, but we need durable entries.
        # Reopen only the *verified final private parent*, relative to its
        # pinned O_PATH FD, and confirm it is the very same inode.
        directory_flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
        if hasattr(os,'O_CLOEXEC'):
            directory_flags|=os.O_CLOEXEC
        ready_fd=os.open('.',directory_flags,dir_fd=fd)
        try:
            old=os.fstat(fd);new=os.fstat(ready_fd)
            if (old.st_dev,old.st_ino)!=(new.st_dev,new.st_ino):
                raise ValueError('private_directory_identity_changed')
        except BaseException:
            os.close(ready_fd)
            raise
        os.close(fd)
        return ready_fd,parts[-1]
    except BaseException:
        os.close(fd)
        raise


def _absent(parent_fd: int, name: str):
    try:
        os.stat(name,dir_fd=parent_fd,follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ValueError('output_already_exists_no_overwrite')


def _owned_create(fd: int, name: str, body: bytes, created: list):
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW
    if hasattr(os,'O_CLOEXEC'):
        flags|=os.O_CLOEXEC
    file_fd=os.open(name,flags,0o600,dir_fd=fd)
    st=os.fstat(file_fd)
    # Track inode *immediately*: partial writes and failed fsyncs also need
    # rollback. Never unlink a file if its inode has been replaced.
    created.append((fd,name,st.st_dev,st.st_ino))
    with os.fdopen(file_fd,'wb') as stream:
        os.fchmod(stream.fileno(),0o600)
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def _rollback(created: list):
    for fd,name,device,inode in reversed(created):
        try:
            st=os.stat(name,dir_fd=fd,follow_symlinks=False)
            if (st.st_dev,st.st_ino)==(device,inode) and stat.S_ISREG(st.st_mode):
                os.unlink(name,dir_fd=fd)
                os.fsync(fd)
        except OSError:
            # A partial output is never approved by this exporter. A same-UID
            # actor may still modify its directory; escalate cleanup separately.
            pass


def write_private_projection_pair(packet_path: str,packet_body: str,
                                  receipt_path: str,receipt_body: str) -> None:
    """Create both private files only when both destinations are initially safe.

    On a normal exception, remove only *our own original inodes*. This cannot
    guarantee atomicity across crashes, power loss, separate mount points or
    a hostile same-UID writer. Neither output authorizes inference.
    """
    if type(packet_body) is not str or type(receipt_body) is not str:
        raise ValueError('invalid_output_body')
    packet=packet_body.encode('utf-8')
    receipt=receipt_body.encode('utf-8')
    if not 0<len(packet)<=PACKET_MAX_BYTES or not 0<len(receipt)<=RECEIPT_MAX_BYTES:
        raise ValueError('private_output_exceeds_fixed_byte_limit')
    pfd=None;rfd=None;created=[]
    try:
        pfd,pname=_target_dirfd(packet_path)
        rfd,rname=_target_dirfd(receipt_path)
        if os.fstat(pfd).st_dev==os.fstat(rfd).st_dev and os.fstat(pfd).st_ino==os.fstat(rfd).st_ino and pname==rname:
            raise ValueError('output_paths_not_distinct')
        _absent(pfd,pname)
        _absent(rfd,rname)
        _owned_create(pfd,pname,packet,created)
        _owned_create(rfd,rname,receipt,created)
        os.fsync(pfd)
        os.fsync(rfd)
    except (OSError,ValueError):
        _rollback(created)
        raise
    finally:
        if pfd is not None:os.close(pfd)
        if rfd is not None:os.close(rfd)
