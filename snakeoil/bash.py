# Copyright: 2005-2011 Brian Harring <ferringb@gmail.com>
# License: BSD/GPL2

"""
Functionality for reading bash like files


Please note that while this functionality can do variable interpolation,
it strictly treats the source as non-executable code.  It cannot parse
subshells, variable additions, etc.

It's primary usage is for reading things like gentoo make.conf's, or
libtool .la files that are bash compatible, but non executable.
"""

__all__ = ("iter_read_bash", "read_bash", "read_bash_dict", "bash_parser",
    "BashParseError")

import re
from shlex import shlex

from snakeoil.mappings import ProtectedDict
from snakeoil.compatibility import raise_from
# demandloaded since their is a !@#*ing import cycle.
# that cycle goes away once fileutils stops it's compatibility for bash access
from snakeoil.demandload import demandload
demandload(globals(),
    'snakeoil.osutils:readlines_utf8',
)

def iter_read_bash(bash_source, allow_inline_comments=True):
    """
    iterate over a file honoring bash commenting rules.

    Note that it's considered good behaviour to close filehandles, as
    such, either iterate fully through this, or use read_bash instead.
    Once the file object is no longer referenced the handle will be
    closed, but be proactive instead of relying on the garbage
    collector.

    :param bash_source: either a file to read from
        or a string holding the filename to open.
    :param allow_inline_comments: whether or not to prune characters
        after a # that isn't at the start of a line.
    :return: yields lines w/ commenting stripped out
    """
    if isinstance(bash_source, basestring):
        bash_source = readlines_utf8(bash_source, True)
    for s in bash_source:
        s = s.strip()
        if s and s[0] != "#":
            if allow_inline_comments:
                s = s.split("#", 1)[0].rstrip()
            yield s


def read_bash(bash_source, allow_inline_comments=True):
    """
    read a file honoring bash commenting rules

    see :py:func:`iter_read_bash` for the details of parameters.

    returns a list of lines w/ comments stripped out.

    """
    return list(iter_read_bash(bash_source,
        allow_inline_comments=allow_inline_comments))


def read_bash_dict(bash_source, vars_dict=None, sourcing_command=None):
    """
    read bash source, yielding a dict of vars

    :param bash_source: either a file to read from
        or a string holding the filename to open
    :param vars_dict: initial 'env' for the sourcing.
        Is protected from modification.
    :type vars_dict: dict or None
    :param sourcing_command: controls whether a source command exists.
        If one does and is encountered, then this func is called.
    :type sourcing_command: callable
    :raise BashParseError: thrown if invalid syntax is encountered.
    :return: dict representing the resultant env if bash executed the source.
    """

    # quite possibly I'm missing something here, but the original
    # portage_util getconfig/varexpand seemed like it only went
    # halfway. The shlex posix mode *should* cover everything.

    if vars_dict is not None:
        d, protected = ProtectedDict(vars_dict), True
    else:
        d, protected = {}, False

    close = False
    infile = None
    if isinstance(bash_source, basestring):
        f = open(bash_source, "r")
        close = True
        infile = bash_source
    else:
        f = bash_source
    s = bash_parser(f, sourcing_command=sourcing_command, env=d, infile=infile)

    try:
        tok = ""
        try:
            while tok is not None:
                key = s.get_token()
                if key is None:
                    break
                elif key.isspace():
                    # we specifically have to check this, since we're
                    # screwing with the whitespace filters below to
                    # detect empty assigns
                    continue
                eq = s.get_token()
                if eq != '=':
                    raise BashParseError(bash_source, s.lineno,
                        "got token %r, was expecting '='" % eq)
                val = s.get_token()
                if val is None:
                    val = ''
                # look ahead to see if we just got an empty assign.
                next_tok = s.get_token()
                if next_tok == '=':
                    # ... we did.
                    # leftmost insertions, thus reversed ordering
                    s.push_token(next_tok)
                    s.push_token(val)
                    val = ''
                else:
                    s.push_token(next_tok)
                d[key] = val
        except ValueError, e:
            raise_from(BashParseError(bash_source, s.lineno, str(e)))
    finally:
        if close and f is not None:
            f.close()
    if protected:
        d = d.new
    return d


var_find = re.compile(r'\\?(\${\w+}|\$\w+)')
backslash_find = re.compile(r'\\.')
def _nuke_backslash(s):
    s = s.group()
    if s == "\\\n":
        return "\n"
    try:
        return chr(ord(s))
    except TypeError:
        return s[1]

class bash_parser(shlex):

    """Fixed up shlex version correcting corner cases in quote expansion,
    and adding variable interpolation

    While it's a fair bit slower than stdlib shlex, it parses a more complete
    subset of bash syntax than stdlib shlex
    """

    def __init__(self, source, sourcing_command=None, env=None, infile=None):
        """
        instantiate the parser

        :param source: file handle to read from
        :param sourcing_command: token to treat as an include command
        :type sourcing_command: either None, or a string; if None, no includes
            are allowed in this parsing
        :param env: initial environment to use for variable interpolation
        :type env: must be a mapping; if None, an empty dict is used
        """
        self.__dict__['state'] = ' '
        shlex.__init__(self, source, posix=True, infile=infile)
        self.wordchars += "@${}/.-+/:~^*"
        self.wordchars = frozenset(self.wordchars)
        if sourcing_command is not None:
            self.source = sourcing_command
        if env is None:
            env = {}
        self.env = env
        self.__pos = 0

    def __setattr__(self, attr, val):
        if attr == "state":
            if (self.state, val) in (
                ('"', 'a'), ('a', '"'), ('a', ' '), ("'", 'a')):
                strl = len(self.token)
                if self.__pos != strl:
                    self.changed_state.append(
                        (self.state, self.token[self.__pos:]))
                self.__pos = strl
        self.__dict__[attr] = val

    def sourcehook(self, newfile):
        try:
            return shlex.sourcehook(self, newfile)
        except IOError, ie:
            raise_from(BashParseError(newfile, 0, str(ie)))

    def read_token(self):
        self.changed_state = []
        self.__pos = 0
        token = shlex.read_token(self)
        if token is None:
            return token
        if self.state is None:
            # eof reached.
            self.changed_state.append((self.state, token[self.__pos:]))
        else:
            self.changed_state.append((self.state, self.token[self.__pos:]))
        tok = ''
        for s, t in self.changed_state:
            if s in ('"', "a"):
                tok += self.var_expand(t).replace("\\\n", '')
            else:
                tok += t
        return tok

    def var_expand(self, val):
        prev, pos = 0, 0
        l = []
        match = var_find.search(val)
        while match is not None:
            pos = match.start()
            if val[pos] == '\\':
                # it's escaped. either it's \\$ or \\${ , either way,
                # skipping two ahead handles it.
                pos += 2
            else:
                var = val[match.start():match.end()].strip("${}")
                if prev != pos:
                    l.append(val[prev:pos])
                if var in self.env:
                    if not isinstance(self.env[var], basestring):
                        raise ValueError(
                            "env key %r must be a string, not %s: %r" % (
                                var, type(self.env[var]), self.env[var]))
                    l.append(self.env[var])
                else:
                    l.append("")
                prev = pos = match.end()
            match = var_find.search(val, pos)

        # do \\ cleansing, collapsing val down also.
        val = backslash_find.sub(_nuke_backslash, ''.join(l) + val[prev:])
        return val


class BashParseError(Exception):

    """Exception thrown when a handle being parsed isn't valid bash"""

    def __init__(self, filename, line, errmsg=None):
        if errmsg is not None:
            Exception.__init__(self,
                               "error parsing '%s' on or before line %i: err %s" %
                               (filename, line, errmsg))
        else:
            Exception.__init__(self,
                               "error parsing '%s' on or before line %i" %
                               (filename, line))
        self.file, self.line, self.errmsg = filename, line, errmsg
