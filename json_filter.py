# Streaming JSON filter for the MatrixPortal.
#
# FR24's flight details response is far bigger than the bits we display, and
# the board doesn't have the memory to hold it. This filter is fed the response
# a chunk at a time and copies only the wanted fields into a small fixed buffer,
# producing a valid JSON object with everything else dropped.
#
# wanted is a tuple of dotted paths, e.g. ("airline.name", "airport.origin.code.iata").
# A wanted path keeps its whole value. Objects on the way to a wanted path are kept
# but filtered. Arrays are only kept if their path is wanted outright.
# stop_key: a top-level key that ends processing early (e.g. the huge "trail").
#
# Kept as plain Python so it runs on CircuitPython and can be tested on a computer.

_QUOTE = 34
_BSLASH = 92
_LBRACE = 123
_RBRACE = 125
_LBRACK = 91
_RBRACK = 93
_COMMA = 44
_COLON = 58
_WHITESPACE = (32, 9, 10, 13)
_SCALAR_END = (_COMMA, _RBRACE, _RBRACK, 32, 9, 10, 13)

# Structural states (inside an object we're filtering)
_KEY_OR_END = 0
_IN_KEY = 1
_EXPECT_COLON = 2
_EXPECT_VALUE = 3
_AFTER_VALUE = 4

# Value-consuming modes (copying or skipping one whole value)
_V_NONE = 0
_V_CONTAINER = 1
_V_STRING = 2
_V_SCALAR = 3

_MAX_KEY = 64


class JsonFilter:
    def __init__(self, buf, wanted, stop_key=None):
        self.buf = buf
        self.size = len(buf)
        self.wanted = wanted
        self.stop_key = stop_key
        self.keybuf = bytearray(_MAX_KEY)
        self.reset()

    def reset(self):
        self.length = 0        # bytes written to buf
        self.done = False      # complete JSON object written
        self.error = None      # string if something went wrong
        self.started = False
        self.stack = []        # one [path, members_written] per filtered object
        self.state = _KEY_OR_END
        self.klen = 0
        self.pending = ""      # path of the member whose value is next
        self.esc = False
        self.vmode = _V_NONE
        self.vemit = False
        self.vdepth = 0
        self.vstr = False

    def _put(self, c):
        if self.length >= self.size:
            self.error = "output buffer full"
            return
        self.buf[self.length] = c
        self.length += 1

    def _put_member_header(self):
        frame = self.stack[-1]
        if frame[1] > 0:
            self._put(_COMMA)
        frame[1] += 1
        self._put(_QUOTE)
        for i in range(self.klen):
            self._put(self.keybuf[i])
        self._put(_QUOTE)
        self._put(_COLON)

    def _start_value(self, c, emit):
        self.vemit = emit
        self.esc = False
        if emit:
            self._put(c)
        if c == _QUOTE:
            self.vmode = _V_STRING
        elif c == _LBRACE or c == _LBRACK:
            self.vmode = _V_CONTAINER
            self.vdepth = 1
            self.vstr = False
        else:
            self.vmode = _V_SCALAR

    def _end_value(self):
        self.vmode = _V_NONE
        self.state = _AFTER_VALUE

    def _is_prefix(self, path):
        p = path + "."
        for w in self.wanted:
            if w.startswith(p):
                return True
        return False

    def feed(self, chunk):
        """Process the next chunk. Check .done / .error afterwards."""
        i = 0
        n = len(chunk)
        while i < n:
            if self.done or self.error:
                return
            c = chunk[i]
            vm = self.vmode

            # Copying or skipping a value
            if vm:
                if vm == _V_STRING:
                    if self.vemit:
                        self._put(c)
                    if self.esc:
                        self.esc = False
                    elif c == _BSLASH:
                        self.esc = True
                    elif c == _QUOTE:
                        self._end_value()
                elif vm == _V_SCALAR:
                    if c in _SCALAR_END:
                        # number/true/false/null ended; reprocess this byte structurally
                        self._end_value()
                        continue
                    if self.vemit:
                        self._put(c)
                else:
                    if self.vemit:
                        self._put(c)
                    if self.vstr:
                        if self.esc:
                            self.esc = False
                        elif c == _BSLASH:
                            self.esc = True
                        elif c == _QUOTE:
                            self.vstr = False
                    elif c == _QUOTE:
                        self.vstr = True
                    elif c == _LBRACE or c == _LBRACK:
                        self.vdepth += 1
                    elif c == _RBRACE or c == _RBRACK:
                        self.vdepth -= 1
                        if self.vdepth == 0:
                            self._end_value()
                i += 1
                continue

            i += 1
            st = self.state

            # Reading a key inside a filtered object
            if st == _IN_KEY:
                if self.esc:
                    self.esc = False
                elif c == _BSLASH:
                    self.esc = True
                elif c == _QUOTE:
                    key = bytes(self.keybuf[:self.klen]).decode("utf-8")
                    if len(self.stack) == 1 and key == self.stop_key:
                        # Everything we want comes before this; close the root and stop
                        self._put(_RBRACE)
                        self.done = True
                        return
                    parent = self.stack[-1][0]
                    self.pending = parent + "." + key if parent else key
                    self.state = _EXPECT_COLON
                    continue
                if self.klen < _MAX_KEY:
                    self.keybuf[self.klen] = c
                    self.klen += 1
                continue

            if c in _WHITESPACE:
                continue

            if not self.started:
                if c == _LBRACE:
                    self.started = True
                    self._put(c)
                    self.stack.append(["", 0])
                    self.state = _KEY_OR_END
                else:
                    self.error = "response is not a JSON object"
                continue

            if st == _KEY_OR_END or st == _AFTER_VALUE:
                if c == _RBRACE:
                    self._put(c)
                    self.stack.pop()
                    if not self.stack:
                        self.done = True
                    else:
                        self.state = _AFTER_VALUE
                elif c == _COMMA and st == _AFTER_VALUE:
                    self.state = _KEY_OR_END
                elif c == _QUOTE and st == _KEY_OR_END:
                    self.klen = 0
                    self.esc = False
                    self.state = _IN_KEY
                else:
                    self.error = "unexpected character in object"
            elif st == _EXPECT_COLON:
                if c == _COLON:
                    self.state = _EXPECT_VALUE
                else:
                    self.error = "expected ':'"
            elif st == _EXPECT_VALUE:
                path = self.pending
                if path in self.wanted:
                    self._put_member_header()
                    self._start_value(c, True)
                elif self._is_prefix(path):
                    if c == _LBRACE:
                        self._put_member_header()
                        self._put(c)
                        self.stack.append([path, 0])
                        self.state = _KEY_OR_END
                    elif c == _LBRACK:
                        self._start_value(c, False)
                    else:
                        # e.g. "airline": null - keep it, parsing handles None
                        self._put_member_header()
                        self._start_value(c, True)
                else:
                    self._start_value(c, False)
