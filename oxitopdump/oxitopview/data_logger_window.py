# -*- coding: utf-8 -*-
# vim: set et sw=4 sts=4:

# Copyright 2012 Dave Hughes.
#
# This file is part of oxitopdump.
#
# oxitopdump is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# oxitopdump is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# oxitopdump.  If not, see <http://www.gnu.org/licenses/>.

"""Module implementing the oxitopview data logger window."""

from __future__ import (
    unicode_literals,
    print_function,
    absolute_import,
    division,
    )

import os

from PyQt4 import QtCore, QtGui, uic

class DataLoggerWindow(QtGui.QtWidget):
    "Document window for the data logger connection"

    def __init__(self, data_logger):
        super(DataLoggerWindow, self).__init__(None)
        self.data_logger = data_logger
