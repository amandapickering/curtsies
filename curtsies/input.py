"""
New terminal
"""

import fcntl
import locale
import os
import signal
import select
import sys
import termios
import time
import tty


from .termhelpers import Nonblocking
from . import events

# note: if select doesn't work out for reading input with a timeout,
# try a stdin read with a timeout instead?: http://stackoverflow.com/a/2918103/398212

PY3 = sys.version_info[0] >= 3

READ_SIZE = 1024
assert READ_SIZE >= events.MAX_KEYPRESS_SIZE
# if a keypress could require more bytes than we read at a time to be identified,
# the paste logic that reads more data as needed might not work.

class Input(object):
    """Coroutine-interface respecting keypress generator"""
    def __init__(self, in_stream=sys.stdin, keynames='curtsies', paste_threshold=events.MAX_KEYPRESS_SIZE+1, sigint_event=False):
        """in_stream should be standard input
        keynames are how keypresses should be named - one of 'curtsies', 'curses', or 'plain'
        paste_threshold is how many bytes must be read in a single read for
          the keypresses they represent to be combined into a single paste event
        """
        self.in_stream = in_stream
        self.unprocessed_bytes = [] # leftover from stdin, unprocessed yet
        self.keynames = keynames
        self.paste_threshold = paste_threshold
        self.sigint_event = sigint_event
        self.sigints = []

    #prospective: this could be useful for an external select loop
    def fileno(self):
        return self.in_stream.fileno()

    def __enter__(self):
        self.original_stty = termios.tcgetattr(self.in_stream)
        tty.setcbreak(self.in_stream)
        if self.sigint_event:
            self.orig_sigint_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, self.sigint_handler)
        return self

    def __exit__(self, type, value, traceback):
        if self.sigint_event:
            signal.signal(signal.SIGINT, self.orig_sigint_handler)
        termios.tcsetattr(self.in_stream, termios.TCSANOW, self.original_stty)

    def sigint_handler(self, signum, frame):
        self.sigints.append(events.SigIntEvent())

    def __iter__(self):
        return self

    def next(self):
        return self.send(None)

    def send(self, timeout=None):
        """Returns a key or None if no key pressed"""

        def find_key():
            """Returns the keypress identified by adding unprocessed bytes, or None"""
            current_bytes = []
            while self.unprocessed_bytes:
                current_bytes.append(self.unprocessed_bytes.pop(0))
                e = events.get_key(current_bytes, getpreferredencoding(), keynames=self.keynames, full=len(self.unprocessed_bytes)==0)
                if e is not None:
                    self.current_bytes = []
                    return e
            if current_bytes: # incomplete keys shouldn't happen
                raise ValueError("Couldn't identify key sequence: %r" % self.current_bytes)

        if self.sigints:
            return self.sigints.pop()

        # try to find an already pressed key from prev input
        e = find_key()
        if e is not None:
            return e

        def wait_for_read_ready_or_timeout():
            remaining_timeout = timeout
            t0 = time.time()
            while True:
                try:
                    (rs, _, _) = select.select([self.in_stream.fileno()], [], [], remaining_timeout)
                    return rs, None
                except select.error:
                    if self.sigints:
                        return [], self.sigints.pop()
                    if remaining_timeout is not None:
                        remaining_timeout = max(timeout - (time.time() - t0), 0)

        rs, sigint = wait_for_read_ready_or_timeout()
        if sigint:
            return sigint
        if not rs:
            return None
        num_bytes = self.nonblocking_read()
        assert num_bytes > 0
        if self.paste_threshold is not None and num_bytes > self.paste_threshold:
            paste = events.PasteEvent()
            while True:
                if len(self.unprocessed_bytes) < events.MAX_KEYPRESS_SIZE:
                    self.nonblocking_read() # may need to read to get the rest of a keypress
                e = find_key()
                if e is None:
                    return paste
                else:
                    paste.events.append(e)
        else:
            e = find_key()
            assert e is not None
            return e

    def nonblocking_read(self):
        """Returns the number of characters read and adds them to self.unprocessed_bytes"""
        with Nonblocking(self.in_stream):
            if PY3:
                data = os.read(self.in_stream.fileno(), READ_SIZE)
                if data:
                    self.unprocessed_bytes.extend(data)
                    return len(data)
                else:
                    return 0
            else:
                try:
                    data = os.read(self.in_stream.fileno(), READ_SIZE)
                except OSError:
                    return 0
                else:
                    self.unprocessed_bytes.extend(data)
                    return len(data)

def getpreferredencoding():
    return locale.getpreferredencoding() or sys.getdefaultencoding()

def main():
    with Input() as input_generator:
        print(repr(input_generator.send(2)))
        print(repr(input_generator.send(1)))
        print(repr(input_generator.send(.5)))
        print(repr(input_generator.send(.2)))
        for e in input_generator:
            print(repr(e))

if __name__ == '__main__':
    main()
