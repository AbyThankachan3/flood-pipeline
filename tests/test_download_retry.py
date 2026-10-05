"""Verify download retry behaviour without hitting the real server."""
import importlib.util, os, sys, tempfile
FLOOD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FLOOD)
os.environ["FLOOD_DATA_ROOT"] = tempfile.mkdtemp()
os.environ["FLOOD_DOWNLOAD_RETRIES"] = "3"
spec = importlib.util.spec_from_file_location("dl", os.path.join(FLOOD, "01-download-flood-data.py"))
dl = importlib.util.module_from_spec(spec); spec.loader.exec_module(dl)
import config

fails = []
def check(label, cond):
    print(f"[{'ok' if cond else 'FAIL'}]   {label}")
    if not cond: fails.append(label)

class FakeResp:
    def __init__(self, status=200, body=b"hello-world", clen=None, cut=False):
        self.status_code = status
        self._body = body
        self.headers = {"content-length": str(clen if clen is not None else len(body))}
        self._cut = cut
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def iter_content(self, n):
        if self._cut:
            yield self._body[:3]          # short read, no exception
        else:
            yield self._body

calls = {"n": 0}

# 1) transient SSL-style failure then success
def flaky(url, **kw):
    calls["n"] += 1
    if calls["n"] < 3:
        raise IOError("SSL: UNEXPECTED_EOF_WHILE_READING")
    return FakeResp()
dl.requests.get = flaky
dl.time.sleep = lambda s: None            # don't actually wait
status, name, path = dl.download_one("http://x/f.tif", "riverine", "a.tif")
check(f"transient failure recovers (took {calls['n']} attempts)", status == "downloaded")
check("file written", os.path.exists(path) and os.path.getsize(path) == 11)

# 2) 403 must NOT be retried
calls["n"] = 0
def forbidden(url, **kw):
    calls["n"] += 1
    return FakeResp(status=403)
dl.requests.get = forbidden
status, _, _ = dl.download_one("http://x/g.tif", "riverine", "b.tif")
check("403 returns immediately", status == "http_403")
check(f"403 not retried (1 call, got {calls['n']})", calls["n"] == 1)

# 3) truncated read is retried, then reported if it never recovers
calls["n"] = 0
def always_short(url, **kw):
    calls["n"] += 1
    return FakeResp(cut=True)
dl.requests.get = always_short
status, _, path = dl.download_one("http://x/h.tif", "riverine", "c.tif")
check(f"truncated read retried {config.DOWNLOAD_RETRIES}x (got {calls['n']})",
      calls["n"] == config.DOWNLOAD_RETRIES)
check("gives up with a clear status", status.startswith("error_after_"))
check("no .part left behind", not os.path.exists(path + ".part"))
check("no truncated file kept", not os.path.exists(path))

# 4) already-present file is skipped without any request
calls["n"] = 0
status, _, _ = dl.download_one("http://x/f.tif", "riverine", "a.tif")
check("existing file skipped", status == "skipped")
check("skip makes no request", calls["n"] == 0)

print()
if fails:
    print(f"{len(fails)} FAILURE(S): " + "; ".join(fails)); sys.exit(1)
print("retry behaviour correct")
