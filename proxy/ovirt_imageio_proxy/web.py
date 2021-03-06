# ovirt-imageio-daemon
# Copyright (C) 2015-2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from __future__ import absolute_import

import collections
import json
import logging
import re
import time

from contextlib import contextmanager

from six.moves import http_client

import webob

from webob.exc import (
    HTTPBadRequest,
    HTTPException,
    HTTPMethodNotAllowed,
    HTTPNotFound,
    HTTPInternalServerError,
)

log = logging.getLogger("web")


class RequestInfo(object):
    """
    Keep request information for logging.
    """

    def __init__(self, request):
        self.client_addr = client_address(request)
        self.method = request.method
        self.path = request.path

    def __str__(self):
        return "[%s] %s %s" % (self.client_addr, self.method, self.path)


class ResponseInfo(object):
    """
    Keep response information for logging.
    """

    def __init__(self, response):
        self.status_code = response.status_code
        self.content_length = response.content_length

    def __str__(self):
        return "[%s] %s" % (self.status_code, self.content_length)


class LoggingAppIter(object):
    """
    WSGI app_iter logging a FINISH log with request and response info.
    """

    def __init__(self, app_iter, req, res, clock):
        self.app_iter = app_iter
        self.req = req
        self.res = res
        self.clock = clock

    def __iter__(self):
        return iter(self.app_iter)

    def close(self):
        if hasattr(self.app_iter, "close"):
            self.app_iter.close()
        self.clock.stop("request")
        log.debug("FINISH %s %s %s", self.req, self.res, self.clock)


class Timer(object):

    def __init__(self, name):
        self.name = name
        self.total = 0.0
        self.count = 0
        self.started = None


class Clock(object):
    """
    Measure time for complex flows.

    This clock is useful for timing complex flows, when you want to record
    multiple timings for a single flow. For example, the total time, and the
    time of each step in the flow.

    This is similar to MoinMoin.util.clock.Clock:
    https://bitbucket.org/thomaswaldmann/moin-2.0/src/

    And vdsm.common.time.Clock:
    https://github.com/oVirt/vdsm/blob/master/lib/vdsm/common/time.py#L45

    Usage::

        clock = time.Clock()
        ...
        clock.start("total")
        ...
        clock.start("read")
        clock.stop("read")
        clock.start("write")
        clock.stop("write")
        clock.start("read")
        clock.stop("read")
        clock.start("write")
        clock.stop("write")
        ...
        clock.start("sync")
        clock.stop("sync")
        ...
        clock.stop("total")
        log.info("times=%s", clock)

    """

    def __init__(self):
        # Keep insertion order for nicer output in __repr__.
        self._timers = collections.OrderedDict()

    def start(self, name):
        t = self._timers.get(name)
        if t is None:
            t = self._timers[name] = Timer(name)

        if t.started is not None:
            raise RuntimeError("Timer %r is running" % name)

        t.started = time.time()
        t.count += 1

    def stop(self, name):
        t = self._timers.get(name)
        if t is None:
            raise RuntimeError("No such timer %r" % name)

        if t.started is None:
            raise RuntimeError("Timer %r is not running" % name)

        elapsed = time.time() - t.started
        t.total += elapsed
        t.started = None

        return elapsed

    @contextmanager
    def run(self, name):
        self.start(name)
        try:
            yield
        finally:
            self.stop(name)

    def __repr__(self):
        now = time.time()
        timers = []
        for t in self._timers.values():
            if t.started is not None:
                total = now - t.started
            else:
                total = t.total
            timers.append("%s=%.6f/%d" % (t.name, total, t.count))
        return "[%s]" % ", ".join(timers)


class Application(object):
    ALLOWED_METHODS = frozenset(['GET', 'PUT', 'PATCH', 'POST',
                                 'DELETE', 'OPTIONS', 'HEAD'])
    """
    WSGI application dispatching requests based on path and method to request
    handlers.
    """

    def __init__(self, config, routes):
        self.config = config
        self.routes = [(re.compile(pattern), cls) for pattern, cls in routes]

    def __call__(self, env, start_response):
        clock = Clock()
        clock.start("request")
        request = webob.Request(env)
        req = RequestInfo(request)
        log.debug("START %s", req)
        try:
            resp = self.dispatch(request, clock)
        except Exception as e:
            if not isinstance(e, HTTPException):
                # Don't expose internal errors to client.
                e = HTTPInternalServerError(detail="Internal server error")
            resp = error_response(e)
            self.log_error(req, resp, e, clock)
            return resp(env, start_response)
        else:
            app_iter = resp(env, start_response)
            res = ResponseInfo(resp)
            return LoggingAppIter(app_iter, req, res, clock)

    def dispatch(self, request, clock):
        if request.method not in self.ALLOWED_METHODS:
            raise HTTPMethodNotAllowed("Invalid method %r" %
                                       request.method)
        path = request.path_info
        for route, handler_class in self.routes:
            match = route.match(path)
            if match:
                handler = handler_class(self.config, request, clock=clock)
                try:
                    method = getattr(handler, request.method.lower())
                except AttributeError:
                    raise HTTPMethodNotAllowed(
                        "Method %r not defined for %r" %
                        (request.method, path))
                else:
                    request.path_info_pop()
                    return method(*match.groups())
        raise HTTPNotFound("No handler for %r" % path)

    def log_error(self, req, resp, error, clock):
        clock.stop("request")
        # Show exceptions only for internal errors (bugs in proxy), and warn
        # about anthing else (client error).
        meth = log.exception if resp.status_code >= 500 else log.warning
        meth("ERROR %s [%s] %s %s", req, resp.status_code, error, clock)


def error_response(e):
    """
    Return WSGI application for sending error response using JSON format.
    """
    body = e.detail.encode("utf-8")
    return webob.Response(status=e.code,
                          body=body,
                          content_type="text/plain",
                          content_length=len(body))


def response(status=http_client.OK, payload=None, **kwargs):
    """
    Return WSGI application for sending response in JSON format.
    """
    body = json.dumps(payload) if payload else ""
    return webob.Response(status=status,
                          body=body,
                          content_type="application/json",
                          content_length=len(body),
                          **kwargs)


def content_range(request):
    """
    Helper for parsing Content-Range header in request.

    WebOb support parsing of Content-Range header, but do not expose this
    header in webob.Request.
    """
    try:
        header = request.headers["content-range"]
    except KeyError:
        return webob.byterange.ContentRange(None, None, None)
    content_range = webob.byterange.ContentRange.parse(header)
    if content_range is None:
        raise HTTPBadRequest("Invalid content-range: %r" % header)
    return content_range


def client_address(request):
    # Our WSGI server set REMOTE_HOST only if different from REMOTE_ADDR.
    return request.environ.get("REMOTE_HOST") or request.remote_addr


class CappedStream(object):
    """
    Stream limiting the amount of read data.

    This is a readonly file-like object limiting the amount of data read from
    the underlying stream. This is required when using http pipelining, and
    useful to avoid resurces exhaustion.

    The read() method will return empty string once the max_bytes bytes was
    read from the stream.

    Provides __iter__() method to make requests.Request use streaming.
    """

    def __init__(self, input_stream, max_bytes, buffer_size=1024**2):
        """
        Initialize a CappedStream.

        Arguments:
          input_stream (reader): An object implemneting read().
          max_bytes (int): maximum number of bytes to read from input_stream
          buffer_size (int): maximum number of bytes read will return
        """
        self.input_stream = input_stream
        self.max_bytes = max_bytes
        self.buffer_size = buffer_size
        self.bytes_read = 0

    def __iter__(self):
        while True:
            chunk = self.read(self.buffer_size)
            if not chunk:
                return
            yield chunk

    def read(self, size=None):
        if size is None:
            size = self.buffer_size
        to_read = min(size, self.max_bytes - self.bytes_read)
        chunk = self.input_stream.read(to_read)
        self.bytes_read += len(chunk)
        return chunk
