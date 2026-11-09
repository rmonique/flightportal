"""Tests for json_filter.py, run on a computer: python3 tools/test_json_filter.py

Compares the streaming filter against a simple reference implementation, on a
realistic FR24-style response and on lots of random JSON, fed in random-sized
chunks (including 1 byte at a time) to exercise chunk boundaries.
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from json_filter import JsonFilter  # noqa: E402

WANTED = (
    "identification.number.default",
    "identification.callsign",
    "aircraft.model.code",
    "aircraft.model.text",
    "airline.name",
    "airport.origin.name",
    "airport.origin.code.iata",
    "airport.destination.name",
    "airport.destination.code.iata",
)


def reference(obj, wanted, stop_key, path="", root=True):
    out = {}
    for k, v in obj.items():
        if root and k == stop_key:
            break
        p = path + "." + k if path else k
        if p in wanted:
            out[k] = v
        elif any(w.startswith(p + ".") for w in wanted):
            if isinstance(v, dict):
                out[k] = reference(v, wanted, stop_key, p, False)
            elif not isinstance(v, list):
                out[k] = v
    return out


def run_filter(raw, wanted, stop_key, size, chunker):
    buf = bytearray(size)
    f = JsonFilter(buf, wanted, stop_key)
    pos = 0
    while pos < len(raw) and not f.done and not f.error:
        n = chunker()
        f.feed(raw[pos:pos + n])
        pos += n
    return f, bytes(buf[:f.length])


def fr24_like():
    img = lambda i: {"src": f"https://cdn.jetphotos.com/200/6/{i}_123.jpg?v=0", "link": f"https://www.jetphotos.com/photo/{i}",
                     "copyright": "Some \"Spotter\" {name}", "source": "JetPhotos.com"}
    hist = lambda i: {"identification": {"id": f"42{i:06x}", "number": {"default": f"B6{i}"}},
                      "airport": {"origin": {"code": {"iata": "BOS", "icao": "KBOS"}}, "destination": None},
                      "time": {"real": {"departure": 1700000000 + i}}}
    return {
        "identification": {"id": "428177da", "row": 5291, "number": {"default": "9K1037", "alternative": None},
                           "callsign": "KAP1037"},
        "status": {"live": True, "text": "Estimated- 18:21", "icon": "green",
                   "generic": {"status": {"text": "estimated", "color": "green"}, "eventTime": {"utc": 1}}},
        "level": "limited", "promote": False,
        "aircraft": {"model": {"code": "C402", "text": "Cessna 402C"}, "countryId": 228, "registration": "N106CA",
                     "age": None, "msn": None,
                     "images": {"thumbnails": [img(i) for i in range(3)], "medium": [img(i) for i in range(3)],
                                "large": [img(i) for i in range(3)]},
                     "hex": "a02b1c"},
        "airline": {"name": "Cape Air", "short": "Cape Air", "code": {"iata": "9K", "icao": "KAP"}, "url": "cape-air-kap"},
        "owner": None, "airspace": None,
        "airport": {
            "origin": {"name": "Boston Logan International Airport", "code": {"iata": "BOS", "icao": "KBOS"},
                       "position": {"latitude": 42.36, "longitude": -71.0, "altitude": 20,
                                    "country": {"name": "United States", "code": "US"},
                                    "region": {"city": "Boston"}},
                       "timezone": {"name": "America/New_York", "offset": -18000, "abbr": "EST", "isDst": False},
                       "visible": True, "website": "http://www.massport.com/logan-airport/",
                       "info": {"terminal": "C", "baggage": None, "gate": "C40"}},
            "destination": {"name": "Barnstable Municipal Airport", "code": {"iata": "HYA", "icao": "KHYA"},
                            "position": {"country": {"name": "United States"}}, "info": {"terminal": None}},
            "real": None},
        "flightHistory": {"aircraft": [hist(i) for i in range(20)]},
        "ems": None, "availability": ["AGE", "MSN"],
        "time": {"scheduled": {"departure": 1, "arrival": 2}, "real": {"departure": 3, "arrival": None}},
        "trail": [{"lat": 42.1, "lng": -70.9, "alt": 4000, "spd": 170, "ts": 1, "hd": 140}] * 200,
        "firstTimestamp": 1, "s": "abc",
    }


def random_value(rng, depth):
    r = rng.random()
    if depth > 4 or r < 0.4:
        return rng.choice([None, True, False, rng.randint(-1000, 1000), rng.random() * 1e6, -1.5e-7,
                           "", "plain", 'quo"te', "back\\slash", "br{ac}es[]", "comma, colon:", "unicode ✈ café",
                           "\n\t\u0001"])
    if r < 0.6:
        return [random_value(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    keys = ["identification", "number", "default", "callsign", "aircraft", "model", "code", "text", "airline",
            "name", "airport", "origin", "destination", "iata", "trail", "images", "x", 'we"ird', "a\\b"]
    return {rng.choice(keys): random_value(rng, depth + 1) for _ in range(rng.randint(0, 6))}


def check(obj, rng, label, pretty=False, size=4096):
    raw = json.dumps(obj, indent=2 if pretty else None, ensure_ascii=rng.random() < 0.5).encode()
    expected = reference(obj, WANTED, "trail")
    for chunker in (lambda: 1, lambda: 256, lambda: rng.randint(1, 300)):
        f, out = run_filter(raw, WANTED, "trail", size, chunker)
        assert f.error is None, f"{label}: error {f.error}"
        assert f.done, f"{label}: not done"
        got = json.loads(out)
        assert got == expected, f"{label}:\n got {got}\n exp {expected}"
    return len(raw), len(out)


def main():
    rng = random.Random(1234)
    total, kept = check(fr24_like(), rng, "fr24-like")
    print(f"fr24-like: {total} byte response -> {kept} bytes kept")
    print("  ", bytes(run_filter(json.dumps(fr24_like()).encode(), WANTED, "trail", 4096, lambda: 256)[1]).decode())
    check(fr24_like(), rng, "fr24-like pretty", pretty=True)

    for i in range(3000):
        obj = {k: random_value(rng, 1) for k in rng.sample(
            ["identification", "aircraft", "airline", "airport", "status", "trail", "time", "x"], rng.randint(0, 8))}
        check(obj, rng, f"random #{i}", pretty=rng.random() < 0.3)
    print("3000 random documents: OK")

    # Overflow and bad input are reported, not crashed on
    f, _ = run_filter(json.dumps(fr24_like()).encode(), WANTED, "trail", 50, lambda: 256)
    assert f.error == "output buffer full", f.error
    f, _ = run_filter(b"<html>blocked</html>", WANTED, "trail", 4096, lambda: 256)
    assert f.error == "response is not a JSON object", f.error
    f, _ = run_filter(b'{"airline": {"name": "Cut off', WANTED, "trail", 4096, lambda: 256)
    assert not f.done and f.error is None
    print("overflow / HTML / truncated response handling: OK")
    print("ALL TESTS PASSED")


main()
