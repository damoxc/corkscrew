# -*- coding: utf-8 -*-
#
# corkscrew/events.py
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

class EventManager(object):
    """
    This class provides an easy means for having events within a web
    application.
    """

    def __init__(self):
        self.__events = {}
        self.__handlers = {}
        self.__queue = {}

    def add_listener(self, listener_id, event):
        """
        Add a listener for an event.

        :param listener_id: A unique id for the listener
        :type listener_id: string
        :param event: The event name
        :type event: string
        """
        if event not in self.__events:
            self._add_listener(listener_id, event)
            self.__handlers[event] = on_event
            self.__events[event] = [listener_id]
        elif listener_id not in self.__events[event]:
            self.__events[event].append(listener_id)

    def _add_listener(self, listener_id, event):
        """
        A method to allow subclasses to easily hook into another event
        system to provide an event proxy for the web application. By
        default this does nothing.

        :param listener_id: A unique id for the listener
        :type listener_id: string
        :param event: The event name
        :type event: string
        """

    def fire_event(self, event, *args):
        """
        Fires an event with the specified parameters.

        :param event: The event name
        :type event: string
        """
        for listener in self.__events[event]:
            if listener not in self.__queue:
                self.__queue[listener] = []
            self.__queue[listener].append((event, args))

    def get_events(self, listener_id):
        """
        Retrieve the pending events for the listener.

        :param listener_id: A unique id for the listener
        :type listener_id: string
        """
        if listener_id in self.__queue:
            queue = self.__queue[listener_id]
            del self.__queue[listener_id]
            return queue
        return None

    def remove_listener(self, listener_id, event):
        """
        Removes a listener for an event

        :param listener_id: A unique id for the listener
        :type listener_id: string
        :param event: The event name
        :type event: string
        """
        self.__events[event].remove(listener_id)
        if not self.__events[event]:
            self._remove_listener(listener_id, event)
            del self.__events[event]
            del self.__handlers[event]

    def _remove_listener(self, listener_id, event):
        """
        The removal equivalent of the _add_listener method.

        :param listener_id: A unique id for the listener
        :type listener_id: string
        :param event: The event name
        :type event: string
        """
