import os
import logging


def post_fork(server, worker):
      server.log.info("Worker post-fork (pid: %s)", worker.pid)


def pre_fork(server, worker):
      server.log.info("Worker pre-fork (pid: %s)", worker.pid)


def worker_int(worker):
      pass


def worker_abort(worker):
      pass


def when_ready(server):
      server.log.info("Inside custom when_ready() hook")

      # Use the file directly from configuration (--log-file/--error-log)
      # errorlog = server.cfg.errorlog
      # if errorlog:
      #     if errorlog != "-":
      #         fd = open(errorlog, "a+").fileno()
      #         os.dup2(fd, 1)
      #         os.dup2(fd, 2)

      # Use the already opened file of the first registered FileHandler
      server.log.info("Looking for existing logging handlers")
      for h in server.log.error_log.handlers:
          server.log.debug("Found handler %s", h)
          if isinstance(h, logging.FileHandler):
              name = h.stream.name
              fd = h.stream.fileno()

              server.log.info("Redirecting stdout/stderr to %s (%s)",
                  name, fd)

              os.dup2(fd, 1)
              os.dup2(fd, 2)

              break

      server.log.info("Server is ready. Spawning workers")
