"""Read selected members of a remote ZIP (wheel) with HTTP range requests.

Only the central directory and the requested members are downloaded, so a 300 MB compiled wheel
costs a few hundred KB when only its .py/.pyi files are needed.
"""
import asyncio, random, struct, zlib

EOCD = b"PK\x05\x06"
EOCD64_LOC = b"PK\x06\x07"
CD_SIG = b"PK\x01\x02"
LH_SIG = b"PK\x03\x04"


class RangeError(Exception):
    pass


async def get_range(session, url, start, end, tries=6):
    """Inclusive byte range."""
    err = None
    for attempt in range(tries):
        try:
            async with session.get(url, headers={"Range": f"bytes={start}-{end}"}) as r:
                if r.status in (200, 206):
                    data = await r.read()
                    if r.status == 200:  # server ignored range
                        data = data[start:end + 1]
                    return data
                if r.status not in (429, 500, 502, 503, 504):
                    raise RangeError(f"http {r.status}")
                err = f"http {r.status}"
        except RangeError:
            raise
        except Exception as e:
            err = repr(e)[:120]
        await asyncio.sleep(min(30, 2 ** attempt) + random.random())
    raise RangeError(err)


def _zip64_extra(extra, need_usize, need_csize, need_offset):
    i = 0
    while i + 4 <= len(extra):
        hid, sz = struct.unpack_from("<HH", extra, i)
        if hid == 1:
            vals = []
            j = i + 4
            while j + 8 <= i + 4 + sz:
                vals.append(struct.unpack_from("<Q", extra, j)[0]); j += 8
            out = {}
            k = 0
            for key, need in (("usize", need_usize), ("csize", need_csize), ("offset", need_offset)):
                if need and k < len(vals):
                    out[key] = vals[k]; k += 1
            return out
        i += 4 + sz
    return {}


async def central_directory(session, url, size):
    tail_len = min(size, 1 << 16)
    tail = await get_range(session, url, size - tail_len, size - 1)
    p = tail.rfind(EOCD)
    if p < 0:
        raise RangeError("no EOCD")
    (_, _, _, _, n_total, cd_size, cd_off, _) = struct.unpack_from("<4s4H2LH", tail, p)
    if cd_off == 0xFFFFFFFF or n_total == 0xFFFF:
        q = tail.rfind(EOCD64_LOC)
        if q < 0:
            raise RangeError("zip64 without locator")
        eocd64_off = struct.unpack_from("<Q", tail, q + 8)[0]
        rec = await get_range(session, url, eocd64_off, eocd64_off + 55)
        n_total, cd_size, cd_off = struct.unpack_from("<QQQ", rec, 32)
    tail_start = size - tail_len
    if cd_off >= tail_start:
        cd = tail[cd_off - tail_start: cd_off - tail_start + cd_size]
    else:
        cd = await get_range(session, url, cd_off, cd_off + cd_size - 1)
    entries = []
    i = 0
    while i + 46 <= len(cd) and cd[i:i + 4] == CD_SIG:
        (_, _, _, flags, method, _, _, crc, csize, usize, nlen, elen, clen, _, _, _, off) = \
            struct.unpack_from("<4s6H3L5H2L", cd, i)
        name = cd[i + 46:i + 46 + nlen].decode("utf-8", "replace")
        extra = cd[i + 46 + nlen:i + 46 + nlen + elen]
        if 0xFFFFFFFF in (csize, usize, off):
            z = _zip64_extra(extra, usize == 0xFFFFFFFF, csize == 0xFFFFFFFF, off == 0xFFFFFFFF)
            usize = z.get("usize", usize); csize = z.get("csize", csize); off = z.get("offset", off)
        entries.append({"name": name, "method": method, "crc": crc, "csize": csize, "usize": usize,
                        "offset": off, "nlen": nlen, "flags": flags})
        i += 46 + nlen + elen + clen
    return entries


def _decompress(method, raw):
    if method == 0:
        return raw
    if method == 8:
        return zlib.decompressobj(-15).decompress(raw)
    raise RangeError(f"unsupported compression {method}")


async def read_members(session, url, entries, max_gap=1 << 17, max_span=8 << 20):
    """Return {name: bytes} for the given central-directory entries, coalescing nearby ranges."""
    ents = sorted(entries, key=lambda e: e["offset"])
    groups, cur = [], []
    for e in ents:
        end = e["offset"] + 30 + e["nlen"] + e["csize"] + 1024
        if cur and (e["offset"] - cur_end > max_gap or end - cur_start > max_span):
            groups.append((cur_start, cur_end, cur)); cur = []
        if not cur:
            cur_start = e["offset"]; cur_end = end
        cur.append(e); cur_end = max(cur_end, end)
    if cur:
        groups.append((cur_start, cur_end, cur))
    out = {}
    for start, end, group in groups:
        blob = await get_range(session, url, start, end - 1)
        for e in group:
            o = e["offset"] - start
            hdr = blob[o:o + 30]
            if len(hdr) < 30 or hdr[:4] != LH_SIG:
                raise RangeError("bad local header")
            nlen, elen = struct.unpack_from("<HH", hdr, 26)
            ds = o + 30 + nlen + elen
            raw = blob[ds:ds + e["csize"]]
            if len(raw) < e["csize"]:  # local extra larger than our slack
                a = e["offset"] + 30 + nlen + elen
                raw = await get_range(session, url, a, a + e["csize"] - 1)
            data = _decompress(e["method"], raw)
            if zlib.crc32(data) & 0xFFFFFFFF != e["crc"]:
                raise RangeError(f"crc mismatch {e['name']}")
            out[e["name"]] = data
    return out
