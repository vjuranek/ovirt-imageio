# ovirt-imageio
# Copyright (C) 2015-2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from ovirt_imageio._internal import errors


def test_str():
    e = errors.PartialContent(50, 42)
    assert str(e) == "Requested 50 bytes, available 42 bytes"
