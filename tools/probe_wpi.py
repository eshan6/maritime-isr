"""Find a working World Port Index download URL.

NGA republishes Pub 150 and changes the key/filename between editions, so a
hard-coded URL rots. This tries the known candidates and reports what each one
returns, so we replace a guess with a measurement.

    python tools/probe_wpi.py

Whatever comes back 200, put it in .env as:
    MISR_WPI_URL=<the working url>
"""
from __future__ import annotations

import sys

import requests

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

BASE = "https://msi.nga.mil/api/publications/download"
CANDIDATES = [
    f"{BASE}?type=view&key=16920959/SFH00000/UpdatedPub150.zip",
    f"{BASE}?key=16920959%2FSFH00000%2FUpdatedPub150.zip&type=view",
    f"{BASE}?key=16920959%2FSFH00000%2FUpdatedPub150.zip&type=download",
    f"{BASE}?type=view&key=16920959/SFH00000/WPI.zip",
    f"{BASE}?type=view&key=16920959/SFH00000/Pub150.zip",
    "https://msi.nga.mil/api/publications/download?type=view&key=16920959/SFH00000/WPI_Explanation_of_Data_Fields.pdf",
]


def main() -> int:
    print("Probing WPI download candidates (with a browser user agent)\n")
    ok = []
    for url in CANDIDATES:
        try:
            r = requests.get(url, headers=UA, timeout=60, stream=True)
            size = r.headers.get("Content-Length", "?")
            ctype = r.headers.get("Content-Type", "?")
            mark = "OK  " if r.status_code == 200 else "    "
            print(f"  {mark}HTTP {r.status_code}  {ctype:<28} {size:>10}  {url}")
            if r.status_code == 200:
                ok.append(url)
            r.close()
        except Exception as e:  # noqa: BLE001
            print(f"      ERR  {type(e).__name__}  {url}")

    print()
    if ok:
        print("Working URL(s) found. Add the first one to .env:")
        print(f"  MISR_WPI_URL={ok[0]}")
        print("\nThen: python -m maritime_isr.cli ingest registries --only wpi")
    else:
        print("None worked. NGA is likely down — WPI is the least critical table")
        print("(port positions for context), so carry on and retry later.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
