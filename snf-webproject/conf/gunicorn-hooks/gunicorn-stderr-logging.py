# -*- coding: utf-8 -
#
# Copyright (C) 2016 GRNET S.A. and individual contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import logging


def when_ready(server):
    """Hook function to redirect stderr/stdout to logfile.

    Hook function that is running when gunicorn server is ready and that is
    responsible to set stdout and stderr to the file descriptor of the first
    logging file handler.

    FIXME: Handle logfile rotation.

    """

    server.log.info("Server ready, redirecting stdout/stderr to logfile")

    # Use the already opened file of the first registered FileHandler
    for h in server.log.error_log.handlers:
        if isinstance(h, logging.FileHandler):
            name = h.stream.name
            fd = h.stream.fileno()

            server.log.info("Redirecting stdout/stderr to %s (%s)", name, fd)

            os.dup2(fd, 1)
            os.dup2(fd, 2)

            break
    else:
        server.log.warn("Could not find file handler!")

    return
