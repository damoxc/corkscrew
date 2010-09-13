#
# corkscrew/jsonrpc.py
#
# Copyright (C) 2010 Damien Churchill <damoxc@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.    See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.    If not, write to:
#   The Free Software Foundation, Inc.,
#   51 Franklin Street, Fifth Floor
#   Boston, MA    02110-1301, USA.
#

import logging

from types import FunctionType
from twisted.internet.defer import Deferred, DeferredList
from twisted.web import http, resource, server

from corkscrew.errors import AuthError, JsonError

# predefine values so we can use lazy loading
AUTH_LEVEL_DEFAULT = None

def export(auth_level=AUTH_LEVEL_DEFAULT):
    """
    Decorator function to register an object's method as a RPC. The object
    will need to be registered with a `:class:JsonRpc` to be effective.

    :param func: the function to export
    :type func: function
    :keyword auth_level: the auth level required to call this method
    :type auth_level: int

    """
    global AUTH_LEVEL_DEFAULT
    if AUTH_LEVEL_DEFAULT is None:
        from corkscrew.auth import AUTH_LEVEL_DEFAULT

    def wrap(func, *args, **kwargs):
        func._json_export = True
        func._json_auth_level = auth_level
        return func

    if type(auth_level) is FunctionType:
        func = auth_level
        auth_level = AUTH_LEVEL_DEFAULT
        return wrap(func)
    else:
        return wrap

from corkscrew.auth import Auth
from corkscrew.common import json, compress

log = logging.getLogger(__name__)

class JsonRpc(resource.Resource):
    """
    A Twisted Web resource that exposes a JSON-RPC interface for web clients \
    to use.
    """

    def __init__(self, auth=False):
        resource.Resource.__init__(self)
        self.methods = {}
        if auth:
            self.auth = Auth()
            self.register_object(self.auth)
        else:
            self.auth = None

    def get_methods(self):
        return list(self.methods)

    def exec_method(self, method, params, request):
        """
        Handles executing all local methods.
        """
        if method == "system.listMethods":
            return self.get_methods()
        elif method in self.methods:
            # This will eventually process methods that the server adds
            # and any plugins.
            meth = self.methods[method]
            meth.func_globals['__request__'] = request
            if self.auth:
                self.auth.check_request(request, meth)
            return meth(*params)
        raise JSONException("Unknown method")

    def has_method(self, method):
        """
        Checks to see if we can handle the specified method.

        :param method: The method name
        :type method: str
        :returns: True or False
        :rtype: bool
        """
        return method == 'system.listMethods' or method in self.methods

    def handle_request(self, request):
        """
        Takes some json data as a string and attempts to decode it, and process
        the rpc object that should be contained, returning a deferred for all
        procedure calls and the request id.
        """
        try:
            request.json = json.loads(request.json)
        except ValueError:
            raise JSONException("JSON not decodable")
        
        if "method" not in request.json or "id" not in request.json or \
           "params" not in request.json:
            raise JSONException("Invalid JSON request")

        method, params = request.json['method'], request.json['params']
        request.request_id = request.json['id']

        if self.has_method(method):
            try:
                return self.exec_method(method, params, request)
            except AuthError:
                raise JsonError(1, 'Not authenticated')
            except Exception as e:
                log.error("Error calling method `%s`", method)
                log.exception(e)
                raise JsonError(3, e.message)
        else:
            raise JsonError(2, 'Unknown method')

    def on_json_request(self, request):
        """
        Handler to take the json data as a string and pass it on to the
        _handle_request method for further processing.
        """
        log.debug("json-request: %s", request.json)
        response = {
            'result': None,
            'error':  None,
            'id':     None
        }

        # Pass the JSON-RPC request off to be handled
        try:
            result = self.handle_request(request)
        except JsonError as e:
            response['error'] = {'message': e.message, 'code': e.code}
        except JSONException as e:
            log.exception(e)
            return
        
        # Store the request id in the response dict
        response['id'] = request.request_id

        # Check to see if we have a Deferred and change our behaviour if so
        if isinstance(result, Deferred):
            result.addCallback(self.on_got_result, request, response)
            result.addErrback(self.on_err_result, request, response)
            return d
        else:
            response['result'] = result
            return self.send_response(request, response)

    def on_got_result(self, result, request, response):
        """
        Sends the result of a RPC call that returned a Deferred.
        """
        response['result'] = result
        return self.send_response(request, response)

    def on_err_result(self, failure, request, response):
        """
        Handles any failures that occured while making a RPC call.
        """
        response['error'] = failure.value.args[0] if failure.value.args else ''
        return self.send_response(request, response)

    def on_json_request_failed(self, reason, request):
        """
        Errback handler to return a HTTP code of 500.
        """
        log.exception(reason)
        request.setResponseCode(http.INTERNAL_SERVER_ERROR)
        return ""

    def send_response(self, request, response):
        request.setHeader("content-type", "application/x-json")
        request.write(compress(json.dumps(response), request))
        request.finish()

    def render(self, request):
        """
        Handles all the POST requests made to the JsonRpc resource.
        """

        if request.method != "POST":
            request.setResponseCode(http.NOT_ALLOWED)
            return ""

        try:
            request.content.seek(0)
            request.json = request.content.read()
            d = self.on_json_request(request)
            return server.NOT_DONE_YET
        except Exception, e:
            return self.on_json_request_failed(e, request)

    def register_object(self, obj, name=None):
        """
        Registers an object to export it's rpc methods.  These methods should
        be exported with the export decorator prior to registering the object.

        :param obj: the object that we want to export
        :type obj: object
        :param name: the name to use, if None, it will be the class name of the object
        :type name: string
        """
        name = name or obj.__class__.__name__
        name = name.lower()

        for d in dir(obj):
            if d[0] == "_":
                continue
            if getattr(getattr(obj, d), '_json_export', False):
                log.debug("Registering method: %s", name + "." + d)
                self.methods[name + "." + d] = getattr(obj, d)

class ConnectableJsonRpc(JsonRpc):
    pass
