import json
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUCKETS = ("steam", "nintendo", "xbox", "playstation", "games-other")


def generate_partition(bucket):
    source = (ROOT / "scripts/update_geodata_assets.sh").read_text()
    functions = "\n".join(
        re.search(r"^" + name + r"\(\) \{.*?^\}", source, re.M | re.S).group(0)
        for name in ("normalize_site_tokens", "emit_source")
    )
    spec = json.dumps({"type": "local_games_partition", "path": "rules_ng2/site/games.txt",
                       "bucket": "other" if bucket == "games-other" else bucket})
    result = subprocess.run(
        ["sh", "-c", functions + '\nemit_source site "$SOURCE_SPEC"'],
        env=dict(os.environ, REPO_ROOT=str(ROOT), RULES_ROOT=str(ROOT / "rules_ng2"), SOURCE_SPEC=spec),
        capture_output=True, text=True, check=True,
    )
    return set(result.stdout.splitlines())


def read_varint(buf, pos):
    value = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, pos
        shift += 7


def fields(buf):
    pos = 0
    while pos < len(buf):
        key, pos = read_varint(buf, pos)
        if key & 7 == 0:
            value, pos = read_varint(buf, pos)
        elif key & 7 == 2:
            size, pos = read_varint(buf, pos)
            value = buf[pos:pos + size]
            pos += size
        else:
            raise AssertionError("unexpected protobuf wire type")
        yield key >> 3, value


def compiled_categories():
    categories = {}
    for _, blob in fields((ROOT / "rules_ng2/dat/geosite.dat").read_bytes()):
        name = ""
        entries = set()
        for key, value in fields(blob):
            if key == 1:
                name = value.decode().lower()
            elif key == 2:
                entry = dict(fields(value))
                prefix = {0: "keyword", 1: "regexp", 2: "domain", 3: "full"}[entry.get(1, 0)]
                entries.add(prefix + ":" + entry[2].decode())
        categories[name] = entries
    return categories


class GamePartitionTests(unittest.TestCase):
    def assert_ownership(self, parts):
        expected = {
            "domain:ruinedking.com": "games-other",
            "domain:king.com": "xbox",
            "domain:bnet.163.com": "xbox",
            "domain:bnetcmsus-a.akamaihd.net": "xbox",
            "domain:bnetproduct-a.akamaihd.net": "xbox",
            "domain:bnetshopus.akamaized.net": "xbox",
            "domain:blizzcon-a.akamaihd.net": "xbox",
            "domain:donkeykongcountryreturns.com": "nintendo",
            "domain:wariolandshakeit.com": "nintendo",
            "domain:wariowarediy.com": "nintendo",
            "domain:yoshisnewisland.com": "nintendo",
        }
        for domain, owner in expected.items():
            self.assertEqual([bucket for bucket in BUCKETS if domain in parts[bucket]], [owner], domain)

    def test_generated_ownership_and_committed_sources(self):
        parts = {bucket: generate_partition(bucket) for bucket in BUCKETS}
        self.assert_ownership(parts)
        for bucket, entries in parts.items():
            self.assertEqual(entries, set((ROOT / f"rules_ng2/site/{bucket}.txt").read_text().splitlines()), bucket)
        games = set((ROOT / "rules_ng2/site/games.txt").read_text().splitlines())
        self.assertEqual(set.union(*parts.values()), games)
        self.assertEqual(sum(map(len, parts.values())), len(games))

    def test_compiled_ownership_and_partition(self):
        parts = compiled_categories()
        self.assert_ownership(parts)
        union = set.union(*(parts[bucket] for bucket in BUCKETS))
        self.assertEqual(union, parts["games"])
        self.assertEqual(sum(len(parts[bucket]) for bucket in BUCKETS), len(union))


if __name__ == "__main__":
    unittest.main()
