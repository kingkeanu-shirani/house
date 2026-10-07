import tempfile
import unittest
from pathlib import Path

from house_sim.catalog import ROOT, parse_catalog


class CatalogTests(unittest.TestCase):
    def test_every_nonblank_source_line_is_accounted_and_verbatim(self):
        path=ROOT/'catalog/raw/household_catalog.txt';raw=path.read_bytes()
        entries,manifest=parse_catalog(path)
        self.assertEqual(len(entries),2719)
        self.assertEqual(len({e['id'] for e in entries}),len(entries))
        self.assertEqual(len(entries)+len(manifest['headings']),sum(bool(l.strip()) for l in raw.splitlines()))
        for entry in entries+manifest['headings']:
            self.assertEqual(raw[entry['byte_start']:entry['byte_end']].decode().rstrip('\r\n'),entry['original_text'])
        self.assertEqual(sum(e['integrity']=='INCOMPLETE' for e in entries),1)
        self.assertTrue(any(e['fused_heading'] and e['section']=='Bedrooms' for e in entries))
        self.assertGreater(sum(e['text']=='Fold towels.' for e in entries),1)

    def test_existing_ids_survive_unrelated_append(self):
        path=ROOT/'catalog/raw/household_catalog.txt';entries,_=parse_catalog(path)
        with tempfile.TemporaryDirectory() as d:
            altered=Path(d)/'catalog.txt';altered.write_bytes(path.read_bytes()+b'\nNew test command.\n')
            added,_=parse_catalog(altered)
            self.assertEqual([e['id'] for e in entries],[e['id'] for e in added[:-1]])


if __name__=='__main__':unittest.main()
