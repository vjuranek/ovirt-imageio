# ovirt-imageio
# Copyright (C) 2019 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import json
import logging
import subprocess

log = logging.getLogger("qemu_img")


class ContentMismatch(Exception):
    """
    Raised when compared images differ.
    """


class OpenImageError(Exception):
    """
    Raised when image cannot be opened, for example when specified format does
    not match the actual image format.
    """


def create(path, fmt, size=None, backing=None):
    cmd = ["qemu-img", "create", "-f", fmt]
    if backing:
        cmd.extend(("-b", backing))
    cmd.append(path)
    if size is not None:
        cmd.append(str(size))
    subprocess.check_call(cmd)


def convert(src, dst, src_fmt, dst_fmt, progress=False, compressed=False):
    cmd = ["qemu-img", "convert", "-f", src_fmt, "-O", dst_fmt]
    if progress:
        cmd.append("-p")
    if compressed:
        cmd.append("-c")
    cmd.extend((src, dst))
    subprocess.check_call(cmd)


def unsafe_rebase(path, backing):
    cmd = ["qemu-img", "rebase", "-u", "-b", backing, path]
    subprocess.check_call(cmd)


def info(path):
    cmd = ["qemu-img", "info", "--output", "json", path]
    out = subprocess.check_output(cmd)
    return json.loads(out.decode("utf-8"))


def measure(path, out_fmt):
    cmd = ["qemu-img", "measure", "--output", "json", "-O", out_fmt, path]
    out = subprocess.check_output(cmd)
    return json.loads(out.decode("utf-8"))


def compare(a, b, format1=None, format2=None, strict=False):
    cmd = ["qemu-img", "compare"]

    if strict:
        cmd.append("-s")

    if format1:
        cmd.append("-f")
        cmd.append(format1)

    if format2:
        cmd.append("-F")
        cmd.append(format2)

    cmd.append(a)
    cmd.append(b)

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate()
    if p.returncode == 0:
        return
    elif p.returncode == 1:
        raise ContentMismatch(out.decode("utf-8"))
    elif p.returncode == 2:
        raise OpenImageError(out.decode("utf-8"))
    else:
        raise RuntimeError(err.decode("utf-8"))
