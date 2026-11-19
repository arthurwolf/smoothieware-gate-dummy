"""Synthetic archives exercise the untrusted download boundary."""
import hashlib
import io
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))


def archive(entries):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as handle:
        for name,body in entries:handle.writestr(name,body)
    return output.getvalue()


class ArtifactArchiveTests(unittest.TestCase):
    def test_digest_checked_before_extracting_regular_result_files(self):
        import artifact_archive
        payload=archive([('manifest.json',b'{}'),('nested/size.result.json',b'{"stage":"size"}')])
        with tempfile.TemporaryDirectory() as temporary:
            dest=Path(temporary)/'result'
            artifact_archive.extract(payload,hashlib.sha256(payload).hexdigest(),dest)
            self.assertEqual((dest/'nested/size.result.json').read_bytes(),b'{"stage":"size"}')
            with self.assertRaises((ValueError,OSError)):
                artifact_archive.extract(payload,hashlib.sha256(payload).hexdigest(),dest)
            self.assertEqual((dest/'manifest.json').read_bytes(),b'{}')
            wrong=Path(temporary)/'wrong'
            with self.assertRaises(ValueError):artifact_archive.extract(payload,'a'*64,wrong)
            self.assertFalse(wrong.exists())

    def test_bad_archives_create_no_destination(self):
        import artifact_archive
        cases=[archive([('../escape.result.json',b'{}')]),archive([('/absolute.result.json',b'{}')]),
               archive([('nested/../manifest.json',b'{}')]),archive([('bad\\name.result.json',b'{}')]),
               archive([('unexpected.sh',b'echo unsafe')]),archive([('manifest.json',b'{}'),('manifest.json',b'{}')]),
               archive([('large.result.json',b'a'*(5*1024**2+1))]),
               archive([(str(i)+'.result.json',b'{}') for i in range(257)]),
               archive([('first.result.json',b'{}'),('a'*245+'.result.json',b'{}')]),
               archive([]),b'not zip']
        linked=io.BytesIO()
        with zipfile.ZipFile(linked,'w') as handle:
            info=zipfile.ZipInfo('link.result.json');info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16
            handle.writestr(info,b'../escape')
        cases.append(linked.getvalue())
        # ZipInfo.filename truncates at NUL; orig_filename retains the wire name.
        cases.append(archive([('manifest.jsonXevil',b'{}')]).replace(b'manifest.jsonXevil',b'manifest.json\x00evil'))
        corrupt=io.BytesIO()
        with zipfile.ZipFile(corrupt,'w',compression=zipfile.ZIP_STORED) as handle:
            handle.writestr('manifest.json',b'BODY')
        cases.append(corrupt.getvalue().replace(b'BODY',b'BOdY'))
        for index,payload in enumerate(cases):
            with self.subTest(index=index),tempfile.TemporaryDirectory() as temporary:
                dest=Path(temporary)/'result'
                with self.assertRaises(ValueError):artifact_archive.extract(payload,hashlib.sha256(payload).hexdigest(),dest)
                self.assertFalse(dest.exists())


    def test_excessive_actual_directory_is_rejected_before_zipfile_allocation(self):
        import artifact_archive
        import struct
        from unittest import mock
        payload=bytearray(archive([('d'+str(i)+'/',b'') for i in range(1025)]))
        end=payload.rfind(b'PK\x05\x06')
        # Lie about both entry counts; actual bounded parsing must catch this.
        struct.pack_into('<HH',payload,end+8,1,1)
        payload=bytes(payload)
        with tempfile.TemporaryDirectory() as temporary,mock.patch.object(artifact_archive.zipfile,'ZipFile',side_effect=AssertionError('unbounded metadata allocation')):
            dest=Path(temporary)/'result'
            with self.assertRaises(ValueError):artifact_archive.extract(payload,hashlib.sha256(payload).hexdigest(),dest)
            self.assertFalse(dest.exists())

    def test_zip64_locator_hidden_in_comment_cannot_switch_checked_directory(self):
        import artifact_archive
        import struct
        from unittest import mock
        inner=archive([('d'+str(i)+'/',b'') for i in range(1025)])
        end=inner.rfind(b'PK\x05\x06')
        size,offset=struct.unpack_from('<LL',inner,end+12)
        hidden=inner[offset:offset+size]
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as handle:
            info=zipfile.ZipInfo('manifest.json');info.comment=b'X'*(len(hidden)+76)
            handle.writestr(info,b'{}')
        payload=bytearray(output.getvalue());end=payload.rfind(b'PK\x05\x06')
        ordinary_offset=struct.unpack_from('<L',payload,end+16)[0]
        name,extra=struct.unpack_from('<HH',payload,ordinary_offset+28)
        comment_start=ordinary_offset+46+name+extra
        record_offset=comment_start+len(hidden)
        record=struct.pack('<4sQ2H2L4Q',b'PK\x06\x06',44,45,45,0,0,1025,1025,len(hidden),comment_start)
        locator=struct.pack('<4sLQL',b'PK\x06\x07',0,record_offset,1)
        payload[comment_start:end]=hidden+record+locator;payload=bytes(payload)
        with zipfile.ZipFile(io.BytesIO(payload)) as actual:
            self.assertEqual(len(actual.infolist()),1025,'fixture must select the hidden ZIP64 directory')
        with tempfile.TemporaryDirectory() as temporary,mock.patch.object(artifact_archive.zipfile,'ZipFile',side_effect=AssertionError('ZIP64 bypassed checked metadata')):
            with self.assertRaises(ValueError):
                artifact_archive.extract(payload,hashlib.sha256(payload).hexdigest(),Path(temporary)/'result')
