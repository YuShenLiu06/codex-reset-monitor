"""Reject unsafe and ambiguous payload layouts before files are extracted."""
import hashlib
import struct
import unittest
from scripts.windows_payload import safe_name,manifest,parse_manifest,ENTRY

class PayloadTest(unittest.TestCase):
    def test_rejects_windows_escape_and_alias_paths(self):
        for name in ('../outside','/root','a/../../x','C:/bad','a\\bad','a:b','a//b','a/','CON','NUL.txt','a.','a ','a/COM1.dll','a/\x00x'):
            with self.subTest(name=name):self.assertFalse(safe_name(name))
        self.assertTrue(safe_name('worker/中文 空格.dll'))
    def test_manifest_rejects_duplicates_bounds_truncation_and_overlaps(self):
        first=('worker/test.dll',20,10,0,hashlib.sha256(b'content').hexdigest())
        raw=manifest([first]);self.assertEqual(parse_manifest(raw,10),[first])
        for value,size in ((raw[:-1],10),(raw,9),(raw+b'extra',10)):
            with self.assertRaises((ValueError,struct.error)):parse_manifest(value,size)
        with self.assertRaises(ValueError):manifest([first,('WORKER/Test.dll',20,10,10,first[-1])])
        with self.assertRaises(ValueError):parse_manifest(manifest([first,('other',20,10,1,first[-1])]),20)
        with self.assertRaises(ValueError):manifest([('file',2**31,10,0,first[-1])])
