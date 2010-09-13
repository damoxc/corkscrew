# -*- coding: utf-8 -*-
#
# corkscrew/auth.py
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

AUTH_LEVEL_NONE = 0
AUTH_LEVEL_READONLY = 1
AUTH_LEVEL_NORMAL = 5
AUTH_LEVEL_ADMIN = 10

AUTH_LEVEL_DEFAULT = AUTH_LEVEL_NORMAL

class AuthError(Exception):
    """
    An exception that might be raised when checking a request for
    authentication.
    """

import time
import random
import hashlib
import logging
from datetime import datetime, timedelta
from email.utils import formatdate

from twisted.internet.defer import Deferred
from twisted.internet.task import LoopingCall

from corkscrew.jsonrpc import export

log = logging.getLogger(__name__)

def make_checksum(session_id):
    return reduce(lambda x,y:x+y, map(ord, session_id))

def get_session_id(session_id):
    """
    Checks a session id against its checksum
    """
    if not session_id:
        return None
    
    try:
        checksum = int(session_id[-4:])
        session_id = session_id[:-4]
        
        if checksum == make_checksum(session_id):
            return session_id
        return None
    except Exception, e:
        log.exception(e)
        return None

def make_expires(timeout):
    dt = timedelta(seconds=timeout)
    expires = time.mktime((datetime.now() + dt).timetuple())
    expires_str = formatdate(timeval=expires, localtime=False, usegmt=True)
    return expires, expires_str

class Auth(object):
    """
    The component that implements authentification into the JSON interface.
    """
    
    def __init__(self):
        self.config = {
            'sessions': {}
        }
        self.worker = LoopingCall(self._clean_sessions)
        self.worker.start(5)
    
    def _clean_sessions(self):
        session_ids = self.config['sessions'].keys()
        
        now = time.gmtime()
        for session_id in session_ids:
            session = self.config['sessions'][session_id]
            
            if 'expires' not in session:
                del self.config['sessions'][session_id]
                continue
                
            if time.gmtime(session['expires']) < now:
                del self.config['sessions'][session_id]
                continue
    
    def _create_session(self, request, login='admin'):
        """
        Creates a new session.
        
        :keyword login: the username of the user logging in, currently \
        only for future use currently.
        :type login: string
        """
        m = hashlib.md5()
        m.update(login)
        m.update(str(time.time()))
        m.update(str(random.getrandbits(40)))
        m.update(m.hexdigest())
        session_id = m.hexdigest()

        expires, expires_str = make_expires(self.config['session_timeout'])
        checksum = str(make_checksum(session_id))
        
        request.addCookie('_session_id', session_id + checksum,
                path="/json", expires=expires_str)
        
        log.debug("Creating session for %s", login)

        if type(self.config['sessions']) is list:
            self.config['sessions'] = {}

        self.config['sessions'][session_id] = {
            'login': login,
            'level': AUTH_LEVEL_ADMIN,
            'expires': expires
        }
        return True
    
    def check_password(self, password):
        log.debug("Received a password auth request")
        s = hashlib.sha1()
        s.update(self.config["pwd_salt"])
        s.update(password)
        if s.hexdigest() == self.config["pwd_sha1"]:
            return True

    def check_request(self, request, method=None, level=None):
        """
        Check to ensure that a request is authorised to call the specified
        method of authentication level.
        
        :param request: The HTTP request in question
        :type request: twisted.web.http.Request
        :keyword method: Check the specified method
        :type method: function
        :keyword level: Check the specified auth level
        :type level: integer
        
        :raises: Exception
        """

        session_id = get_session_id(request.getCookie("_session_id"))
        
        if session_id not in self.config["sessions"]:
            auth_level = AUTH_LEVEL_NONE
            session_id = None
        else:
            session = self.config["sessions"][session_id]
            auth_level = session["level"]
            expires, expires_str = make_expires(
                self.config["session_timeout"])
            session["expires"] = expires

            _session_id = request.getCookie("_session_id")
            request.addCookie('_session_id', _session_id,
                    path="/json", expires=expires_str)
        
        if method:
            if not hasattr(method, "_json_export"):
                raise Exception("Not an exported method")
            
            method_level = getattr(method, "_json_auth_level")
            if method_level is None:
                raise Exception("Method has no auth level")

            level = method_level
        
        if level is None:
            raise Exception("No level specified to check against")
        
        request.auth_level = auth_level
        request.session_id = session_id
        
        if auth_level < level:
            raise AuthError("Not authenticated")
    
    def _change_password(self, new_password):
        """
        Change the password. This is to allow the UI to change/reset a
        password.
        
        :param new_password: the password to change to
        :type new_password: string
        """
        log.debug("Changing password")
        salt = hashlib.sha1(str(random.getrandbits(40))).hexdigest()
        s = hashlib.sha1(salt)
        s.update(new_password)
        self.config["pwd_salt"] = salt
        self.config["pwd_sha1"] = s.hexdigest()
        return True
    
    @export
    def change_password(self, old_password, new_password):
        """
        Change the password.
        
        :param old_password: the current password
        :type old_password: string
        :param new_password: the password to change to
        :type new_password: string
        """
        if not self.check_password(old_password):
            return False
        return self._change_password(new_password)
    
    @export(AUTH_LEVEL_NONE)
    def check_session(self, session_id=None):
        """
        Check a session to see if it's still valid.
        
        :returns: True if the session is valid, False if not.
        :rtype: booleon
        """
        return __request__.session_id is not None
    
    @export
    def delete_session(self):
        """
        Removes a session.
        
        :param session_id: the id for the session to remove
        :type session_id: string
        """
        d = Deferred()
        del self.config["sessions"][__request__.session_id]
        return True
    
    @export(AUTH_LEVEL_NONE)
    def login(self, password):
        """
        Test a password to see if it's valid.
        
        :param password: the password to test
        :type password: string
        :returns: a session id or False
        :rtype: string or False
        """
        
        if self.check_password(password):
            return self._create_session(__request__)
        else:
            return False