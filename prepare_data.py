"""Download the full Loghub BGL and OpenStack datasets and turn the raw logs into structured CSVs.

    python prepare_data.py              # download (if needed), extract, parse -> data/*.log_structured.csv
    python load.py                      # then load them into SereneDB (picks up the CSVs automatically)

The Zenodo archives contain raw log files only. This script parses them into the same columns as Loghub's
structured samples (so sql/schema.sql works unchanged) and derives a message template per line by masking
variable parts (numbers, hex, IPs, IDs, paths) as <*>. Template ids (E1, E2, …) are assigned in order of first
appearance within each dataset, so they differ from the ids in Loghub's 2k samples.
"""
import csv
import re
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

DATA = Path(__file__).parent / "data"
RAW = DATA / "raw"
ARCHIVES = {  # keep ?download=1, otherwise Zenodo serves an HTML preview page
    "BGL.zip": "https://zenodo.org/records/8196385/files/BGL.zip?download=1",
    "OpenStack.tar.gz": "https://zenodo.org/records/8196385/files/OpenStack.tar.gz?download=1",
}
BGL_CSV = DATA / "BGL.log_structured.csv"
OPENSTACK_CSV = DATA / "OpenStack.log_structured.csv"

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_DIGIT = re.compile(r"\d")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # a few BGL lines carry binary garbage
_RUNS = re.compile(r"<\*>(?:[\s,]+<\*>)+")
_BGL_TIME = re.compile(r"\d{4}-\d{2}-\d{2}-\d{2}\.\d{2}\.\d{2}\.\d{6}$")
# After the timestamp: [NodeRepeat] Type Component Level [Content]. NodeRepeat is missing on some lines and holds
# garbled text with spaces on others, so anchor on the first "Type Component Level" triple instead of counting spaces.
_BGL_REST = re.compile(r"^(?:(.*?) )?(RAS|NULL) (\S+) (INFO|FATAL|ERROR|WARNING|SEVERE|FAILURE)(?: (.*))?$")
# ADDR ([req-… …]) and the message are both optional: some ERROR lines carry no message text at all
_OPENSTACK = re.compile(r"^(\S+) (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}(?:\.\d+)?) (\d+) (\S+) (\S+)(?: \[(.*?)\])?(?: (.*))?$")


_KEY = re.compile(r"^([A-Za-z][A-Za-z_\-]*[:=])")  # named field such as lr:, pid=


def _mask(tok):
    if not (_DIGIT.search(tok) or tok.startswith("/")):
        return tok
    key = _KEY.match(tok)
    return key.group(1) + "<*>" if key else "<*>"  # keep the field name: lr:00004ed0 -> lr:<*>


def template(content):
    """Mask variable tokens: any token containing a digit, an absolute path, or a UUID becomes <*>
    (keeping a field name such as 'lr:'). Messages with nothing constant left become '<numeric-only message>'."""
    out = _RUNS.sub("<*>", " ".join(_mask(t) for t in _UUID.sub("<*>", content).split()))
    return "<numeric-only message>" if out == "<*>" else out


class Templates:
    def __init__(self):
        self.ids = {}

    def __call__(self, content):
        t = template(content)
        if t not in self.ids:
            self.ids[t] = f"E{len(self.ids) + 1}"
        return self.ids[t], t


def download():
    DATA.mkdir(exist_ok=True)
    for name, url in ARCHIVES.items():
        dest = DATA / name
        if dest.exists() and dest.stat().st_size > 1_000_000:
            print(f"  {name}: already downloaded ({dest.stat().st_size / 1e6:.1f} MB)")
            continue
        print(f"  downloading {name} …")
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:  # urllib follows Zenodo's redirects
            while chunk := r.read(1 << 20):
                f.write(chunk)
        tmp.rename(dest)
        print(f"  {name}: {dest.stat().st_size / 1e6:.1f} MB")


def extract():
    bgl_dir, os_dir = RAW / "BGL", RAW / "OpenStack"
    if not (bgl_dir / "BGL.log").exists():
        print("  extracting BGL.zip …")
        with zipfile.ZipFile(DATA / "BGL.zip") as z:
            for m in z.namelist():
                if m.startswith("/") or ".." in Path(m).parts:
                    raise ValueError(f"unsafe path in BGL.zip: {m}")
            z.extractall(bgl_dir)
    if not (os_dir / "openstack_normal1.log").exists():
        print("  extracting OpenStack.tar.gz …")
        with tarfile.open(DATA / "OpenStack.tar.gz") as t:
            t.extractall(os_dir, filter="data")  # rejects absolute paths, '..' and links outside the folder
    return bgl_dir / "BGL.log", sorted(os_dir.glob("openstack_*.log"))


def parse_bgl(src):
    """Raw BGL line: Label Timestamp Date Node Time NodeRepeat Type Component Level Content"""
    cols = ["LineId", "Label", "Timestamp", "Date", "Node", "Time", "NodeRepeat", "Type", "Component", "Level",
            "Content", "EventId", "EventTemplate"]
    tpl, n, skipped = Templates(), 0, 0
    with open(src, encoding="utf-8", errors="replace") as f, open(BGL_CSV, "w", newline="") as out:
        w = csv.writer(out)
        w.writerow(cols)
        for line in f:
            head = _CONTROL.sub("?", line.rstrip("\n")).split(" ", 5)
            if len(head) < 6 or not head[1].isdigit() or not _BGL_TIME.match(head[4]):
                skipped += 1  # malformed line: no usable timestamp
                continue
            m = _BGL_REST.match(head[5])
            if m:
                p = head[:5] + [m.group(1) or "", m.group(2), m.group(3), m.group(4), m.group(5) or ""]
            else:  # no recognisable level: keep the line, split by position
                p = head[:5] + head[5].split(" ", 4)
                p += [""] * (10 - len(p))
            n += 1
            eid, t = tpl(p[9])
            w.writerow([n, *p, eid, t])
            if n % 1_000_000 == 0:
                print(f"    {n:,} lines …")
    return n, skipped, len(tpl.ids)


def parse_openstack(files):
    """Raw OpenStack line: Logrecord Date Time Pid Level Component [ADDR] Content. All files merged in time order."""
    rows, skipped = [], 0
    for path in files:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = _OPENSTACK.match(_CONTROL.sub("?", line.rstrip("\n")))
                if not m:
                    skipped += 1
                    continue
                rows.append(m.groups())
    rows.sort(key=lambda r: (r[1], r[2]))  # three files cover different days; order by timestamp
    tpl = Templates()
    with open(OPENSTACK_CSV, "w", newline="") as out:
        w = csv.writer(out)
        w.writerow(["LineId", "Logrecord", "Date", "Time", "Pid", "Level", "Component", "ADDR", "Content",
                    "EventId", "EventTemplate"])
        for i, (rec, d, t, pid, lvl, comp, addr, content) in enumerate(rows, 1):
            content = content or ""
            eid, tp = tpl(content)
            w.writerow([i, rec, d, t, pid, lvl, comp, addr or "", content, eid, tp])
    return len(rows), skipped, len(tpl.ids)


def main():
    started = time.time()
    print("1/3 download")
    download()
    print("2/3 extract")
    bgl_log, os_logs = extract()
    print("3/3 parse")
    n, skipped, templates = parse_bgl(bgl_log)
    print(f"  BGL: {n:,} lines, {templates:,} templates, {skipped:,} malformed lines skipped -> {BGL_CSV}")
    n, skipped, templates = parse_openstack(os_logs)
    print(f"  OpenStack: {n:,} lines from {len(os_logs)} files, {templates:,} templates, {skipped:,} skipped -> {OPENSTACK_CSV}")
    print(f"done in {time.time() - started:.0f}s. Next: python load.py")


if __name__ == "__main__":
    sys.exit(main())
