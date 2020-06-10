#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import codecs
import enum

from ...lib.enumeration import makeinstance
from .. import arg, Unit


class Handler(enum.Enum):
    STRICT = 'strict'
    IGNORE = 'ignore'
    REPLACE = 'replace'
    XMLREF = 'xmlcharrefreplace'
    BACKSLASH = 'backslashreplace'
    SURROGATE = 'surrogateescape'


class recode(Unit):
    """
    Expects input string data encoded in the `from` encoding and encodes it in
    the `to` encoding, then outputs the result.
    """

    def __init__(
        self,
        decode: arg(metavar='decode-as', type=str, help='Input encoding; Guess encoding by default.') = None,
        encode: arg(metavar='encode-as', type=str, help=F'Output encoding; The default is {Unit.codec}.') = Unit.codec,
        decerr: arg.option('-d', choices=Handler,
            help='Specify an error handler for decoding.') = None,
        encerr: arg.option('-e', choices=Handler,
            help='Specify an error handler for encoding.') = None,
        errors: arg.option('-E', choices=Handler, help=(
            'Specify an error handler for both encoding and decoding. '
            'The possible choices are the following: {choices}')) = None,
    ):
        super().__init__(
            decode=decode,
            encode=encode,
            decerr=makeinstance(Handler, decerr or errors or 'STRICT').value,
            encerr=makeinstance(Handler, encerr or errors or 'STRICT').value
        )

    def detect(self, data):
        mv = memoryview(data)
        if not any(mv[1::2]): return 'utf-16le'
        if not any(mv[0::2]): return 'utf-16be'
        import chardet
        detection = chardet.detect(data)
        codec = detection['encoding']
        self.log_info(lambda: F'Using input encoding: {codec}, detected with {int(detection["confidence"]*100)}% confidence.')
        return codec

    def process(self, data):
        codec = self.args.decode
        if codec is None:
            codec = self.detect(data)
        return codecs.encode(
            codecs.decode(data, codec, errors=self.args.decerr),
            self.args.encode,
            errors=self.args.encerr
        )
