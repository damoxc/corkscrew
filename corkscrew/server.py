#
# corkscrew/server.py
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

import os
import fnmatch
import logging
import mimetypes

from twisted.internet import reactor, defer, error
from twisted.web import http, resource, server, static

from corkscrew.common import Template, compress, get_version, windows_check
from corkscrew.jsonrpc import JsonRpc

log = logging.getLogger(__name__)

class GetText(resource.Resource):

    def __init__(self, path):
        self.path = path

    def render(self, request):
        request.setHeader('content-type', 'text/javascript; encoding=utf-8')
        template = Template(filename=self.path)
        return compress(template.render(), request)

class StaticResources(resource.Resource):

    def __init__(self, prefix=''):
        resource.Resource.__init__(self)
        self.__resources = {
            'normal': {
                'filemap': {},
                'order': []
            },
            'debug': {
                'filemap': {},
                'order': []
            },
            'dev': {
                'filemap': {},
                'order': []
            }
        }
        self.__prefix = prefix

    def _get_script_dicts(self, type):
        """
        Return the order list and the filemap dict for the specified
        type.

        :param type: The type to return for (dev, debug, normal)
        :type type: string
        """
        if type not in ('dev', 'debug', 'normal'):
            type = 'normal'
        type = type.lower()
        return (self.__resources[type]['filemap'],
            self.__resources[type]['order'])

    def _get_files(self, dirpath, urlpath, base):
        """
        Returns all the files within a directory in the correct order.

        :param dirpath: The physical directory the files are in
        :type dirpath: string
        """
        files = fnmatch.filter(os.listdir(dirpath), "*.js")
        files.sort()
        dirpath = dirpath[len(base) + 1:]
        if dirpath:
            return [self.__prefix + '/%s/%s/%s' % (urlpath, dirpath, f) for f in files]
        else:
            return [self.__prefix + '/%s/%s' % (urlpath, f) for f in files]

    def _adjust_order(self, dirpath, urlpath, files):
        """
        Fix the order of a files list by doing a .sort() and checking
        for a .order file in the dirpath.

        :param dirpath: The physical directory the files are in
        :type dirpath: string
        :param urlpath: The urlpath the files are in
        :type urlpath: string
        :param files: The list of files to adjust the order for
        :type files: list
        """
        order_file = os.path.join(dirpath, '.order')

        if os.path.isfile(order_file):
            for line in open(order_file, 'rb'):
                line = line.strip()
                if not line or line[0] == '#':
                    continue
                try:
                    pos, filename = line.split()
                    filename = self.__prefix + '/' + urlpath + '/' + filename
                    files.pop(files.index(filename))
                    if pos == '+':
                        files.insert(0, filename)
                    else:
                        files.append(filename)
                except:
                    pass

    def add_file(self, path, filepath, type=None):
        """
        Adds a file to the resource.

        :param path: The path of the resource
        :type path: string
        :param filepath: The physical location of the resource
        :type filepath: string
        :keyword type: The type of script to add (normal, debug, dev)
        :type type: string
        """

        (filemap, order) = self._get_script_dicts(type)
        filemap[path] = filepath
        order.append(path)

    def add_folder(self, path, filepath, type=None, recurse=True):
        """
        Adds a folder to the resource.

        :param path: The path of the resource
        :type path: string
        :param filepath: The physical location of the resource
        :type filepath: string
        :keyword type: The type of script to add (normal, debug, dev)
        :type type: string
        :keyword recurse: Whether or not to recurse into subfolders
        :type recurse: bool
        """

        (filemap, order) = self._get_script_dicts(type)
        filemap[path] = (filepath, recurse)
        order.append(path)

    def get_resources(self, type=None):
        """
        Returns a list of the resources that can be used for producing
        script/link tags.

        :keyword type: The type of resources to get (normal, debug, dev)
        :param type: string
        """
        files = []
        (filemap, order) = self._get_script_dicts(type)

        for urlpath in order:
            filepath = filemap[urlpath]

            # this is a folder
            if isinstance(filepath, tuple):
                (filepath, recurse) = filepath

                if recurse:
                    myfiles = []
                    for dirpath, dirnames, filenames in os.walk(filepath, False):
                        myfiles.extend(self._get_files(dirpath, urlpath, filepath))
                        self._adjust_order(dirpath, urlpath, myfiles)
                else:
                    myfiles = self._get_files(filepath, urlpath, None)
                    self._adjust_order(filepath, urlpath, myfiles)

                files.extend(myfiles)

            else:
                files.append(self.__prefix + '/' + urlpath)

        return files

    def getChild(self, path, request):
        if hasattr(request, 'lookup_path'):
            request.lookup_path = os.path.join(request.lookup_path, path)
        else:
            request.lookup_path = path
        return self

    def render(self, request):
        log.debug('requested path: %s', request.lookup_path)

        for type in ('dev', 'debug', 'normal'):
            filemap = self.__resources[type]['filemap']
            for urlpath in filemap:
                if not request.lookup_path.startswith(urlpath):
                    continue

                filepath = filemap[urlpath]
                if isinstance(filepath, tuple):
                    filepath = filepath[0]

                path = filepath + request.lookup_path[len(urlpath):]
                if not os.path.isfile(path):
                    continue

                log.debug('serving path: %s', path)
                mime_type = mimetypes.guess_type(path)
                request.setHeader('content-type', mime_type[0])
                return compress(open(path, 'rb').read(), request)
        
        request.setResponseCode(http.NOT_FOUND)
        return '<h1>404 - Not Found</h1>'

class TopLevelBase(resource.Resource):

    addSlash = True
    auth = False
    jsonrpc = None

    def __init__(self, base=None, dev_mode=False):
        resource.Resource.__init__(self)
        self.dev_mode = dev_mode
        self.base = '' if base is None else base
        if self.jsonrpc:
            self.putChild(self.jsonrpc, JsonRpc(self.auth))

    def getChild(self, path, request):
        if path == '':
            return self
        else:
            return resource.Resource.getChild(self, path, request)

    def get_request_base(self, request):
        """
        Sets the base path that the server should be using. This enables running the
        server behind a proxy.
        """
        header = request.getHeader('x-corkscrew-base')
        base = header or self.base

        # validate the base parameter
        if not base:
            base = '/'
        if base[0] != '/':
            base = '/' + base
        if base[-1] != '/':
            base += '/'
        return base

    def get_request_mode(self, request):
        """
        Returns the mode that server should handle this request as.

        :param request: The http request to check
        :type request: twisted.web.server.Request
        :returns: The servers mode
        :rtype: string or NoneType
        """
        if 'dev' in request.args and ('true', 'yes', '1') in request.args.get('dev')[-1]:
            return 'dev'

        if self.dev_mode:
            return 'dev'

        if 'debug' in request.args and ('true', 'yes', '1') in request.args.get('debug')[-1]:
            return 'debug'

        return None


class ExtJSTopLevel(TopLevelBase):

    @property
    def css(self):
        return self.__css
    
    @property
    def js(self):
        return self.__js

    def __init__(self, public, templates, base=None, dev_mode=False, gettext=None):
        TopLevelBase.__init__(self, base, dev_mode)
        css = StaticResources('css')
        css.add_file('ext-all-notheme.css', os.path.join(public, 'css', 'ext-all-notheme.css'), 'dev')
        css.add_folder('ext-extensions', os.path.join(public, 'css', 'ext-extensions'), 'dev')
        css.add_file('ext-all-notheme.css', os.path.join(public, 'css', 'ext-all-notheme.css'), 'debug')
        css.add_file('ext-extensions-debug.css', os.path.join(public, 'css', 'ext-extensions-debug.css'), 'debug')
        css.add_file('ext-all-notheme.css', os.path.join(public, 'css', 'ext-all-notheme.css'))
        css.add_file('ext-extensions.css', os.path.join(public, 'css', 'ext-extensions.css'))
        self.putChild('css', css)
        self.__css = css

        self.__icons = StaticResources('icons')
        self.__images = StaticResources('images')

        js = StaticResources('js')
        js.add_file('ext-base-debug.js', os.path.join(public, 'js', 'ext-base-debug.js'), 'dev')
        js.add_file('ext-all-debug.js', os.path.join(public, 'js', 'ext-all-debug.js'), 'dev')
        js.add_folder('ext-extensions', os.path.join(public, 'js', 'ext-extensions'), 'dev')

        js.add_file('ext-base-debug.js', os.path.join(public, 'js', 'ext-base-debug.js'), 'debug')
        js.add_file('ext-all-debug.js', os.path.join(public, 'js', 'ext-all-debug.js'), 'debug')
        js.add_file('ext-extensions-debug.js', os.path.join(public, 'js', 'ext-extensions-debug.js'), 'debug')

        js.add_file('ext-base.js', os.path.join(public, 'js', 'ext-base.js'))
        js.add_file('ext-all.js', os.path.join(public, 'js', 'ext-all.js'))
        js.add_file('ext-extensions.js', os.path.join(public, 'js', 'ext-extensions.js'))
        self.putChild('js', js)
        self.__js = js

        if gettext:
            self.putChild('gettext.js', GetText(gettext))
        self.putChild('icons', self.__icons)
        self.putChild('images', self.__images)
        self.putChild('themes', static.File(os.path.join(public, 'themes')))
        self.theme = 'blue'
        self.public = public
        self.templates = templates

    def render(self, request):
        mode = self.get_request_mode(request)
        scripts = self.__js.get_resources(mode)
        scripts.insert(0, "gettext.js")

        stylesheets = self.__css.get_resources(mode)
        stylesheets.append('themes/css/xtheme-%s.css' % self.theme)

        template = Template(filename=os.path.join(self.templates, "index.html"))
        request.setHeader("content-type", "text/html; charset=utf-8")

        js_config = '{}'
        return compress(template.render(
            scripts     = scripts,
            stylesheets = stylesheets,
            debug       = mode in ('dev', 'debug'),
            base        = self.get_request_base(request),
            js_config   = js_config
        ), request)

class CorkscrewServer(object):
    
    def __init__(self, top_level, port=8080, https=False):
        self.socket = None
        self.top_level = top_level
        self.site = server.Site(self.top_level)
        self.port = port
        self.https = https
        self.base = '/'
        CorkscrewServer.instance = self

    def install_signal_handlers(self):
        # Since twisted assigns itself all the signals may as well make
        # use of it.
        reactor.addSystemEventTrigger("after", "shutdown", self.shutdown)

        # Twisted doesn't handle windows specific signals so we still
        # need to attach to those to handle the close correctly.
        if windows_check():
            from win32api import SetConsoleCtrlHandler
            from win32con import CTRL_CLOSE_EVENT, CTRL_SHUTDOWN_EVENT
            def win_handler(ctrl_type):
                log.debug('ctrl type: %s', ctrl_type)
                if ctrl_type == CTRL_CLOSE_EVENT or \
                    ctrl_type == CTRL_SHUTDOWN_EVENT:
                    self.shutdown()
                    return 1
            SetConsoleCtrlHandler(win_handler)

    def start(self, start_reactor=True):
        log.info('%s %s.', 'Starting server in PID', os.getpid())
        if self.https:
            self.start_ssl()
        else:
            self.start_normal()

        if start_reactor:
            reactor.run()
    
    def start_normal(self):
        self.socket = reactor.listenTCP(self.port, self.site)
        log.info('serving on %s:%s view at http://127.0.0.1:%s', '0.0.0.0',
            self.port, self.port)

    def start_ssl(self):
        check_ssl_keys()
        self.socket = reactor.listenSSL(self.port, self.site, ServerContextFactory())
        log.info('serving on %s:%s view at https://127.0.0.1:%s', '0.0.0.0',
            self.port, self.port)

    def stop(self):
        log.info('Shutting down webserver')
        log.debug('Saving configuration file')

        if self.socket:
            d = self.socket.stopListening()
            self.socket = None
        else:
            d = defer.Deferred()
            d.callback(False)
        return d

    def shutdown(self, *args):
        self.stop()
        try:
             reactor.stop()
        except:
            log.debug('Reactor not running')
