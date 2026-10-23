#!/usr/bin/env python3
"""Build per-TAC JSON shards from the ISED Terrestrial Spectrum Licence Site Data Extract.

Usage: python build_shards_v2.py <path-to-Site_Data_Extract_FX.csv> [out_dir]

Changes from v1:

 1. MNC NORMALIZATION.  Licensees file the PLMN two different ways - some write
    the full MCC+MNC (302220), others just the MNC (220).  v1 took the string
    verbatim, so Bell ended up split across 610/ (196,430 cells) and 302610/
    (133 cells) with zero overlap, and a lookup built from the gateway's PLMN
    silently missed whichever half it did not guess.  v2 folds the bare form
    into the MCC+MNC form so one PLMN means one directory.

 2. SITE TABLE (layout C).  ~15 cells share each physical tower, and v1 repeated
    the lat/lon/address on every one of them.  v2 writes each site once and has
    the cells point at it by index:

        {"s": [[43.799914,-79.239744,"20 Mclevin Ave."], ...],
         "c": {"140596009": [0, 0], "140596209": [0, 120]}}
              key = ECI (decimal)   [site index, azimuth]

    Same five fields, ~2.3x smaller.

Nothing is dropped that v1 kept.  The odd-looking directories (0000, 00000,
001001, 07) are hand-typed placeholder cell_ids and are preserved as-is; 40 and
250 are real private networks (a mine in Nunavut, a Mississauga campus) and a
gateway could legitimately sit on one.

Row selection is unchanged: for each cell keep the row with the LATEST
upload_date* (first row wins within that date).  Most-frequent would return the
OLD site for any cell relocated in the last few months.
"""
import csv, sys, os, json, collections

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

csv.field_size_limit(10 ** 9)
if not 2 <= len(sys.argv) <= 3:
    sys.exit(__doc__)
src = sys.argv[1]
outdir = sys.argv[2] if len(sys.argv) == 3 else 'cellular_data'

# bare MNC -> MCC+MNC.  Verified lossless: zero shared tac+eci in any pair.
MNC_ALIAS = {'610': '302610', '220': '302220', '720': '302720',
             '660': '302660', '880': '302880'}

malformed = collections.Counter()
latest = {}     # (mnc, tac, eci) -> (upload_date, rec)
aliased = collections.Counter()
total = skipped = 0

with open(src, newline='', encoding='utf-8-sig', errors='replace') as f:
    r = csv.reader(f)
    hdr = next(r)
    I = {n: i for i, n in enumerate(hdr)}
    c_cid, c_la, c_lo = I['cell_id'], I['latitude'], I['longitude']
    c_loc, c_az, c_up = I['location'], I['tx_ant_azimuth'], I['upload_date*']
    need = max(c_cid, c_la, c_lo, c_loc, c_az, c_up)

    for row in r:
        total += 1
        if len(row) <= need:
            skipped += 1; continue
        parts = [x.strip() for x in row[c_cid].strip().split('-')]
        # mnc/tac become a directory and a filename, so they must be plain digits
        if len(parts) != 3 or not (parts[0].isdigit() and parts[1].isdigit() and parts[2]):
            malformed[row[c_cid].strip()] += 1
            skipped += 1; continue
        mnc, tac, eci = parts
        if mnc in MNC_ALIAS:
            aliased[mnc] += 1
            mnc = MNC_ALIAS[mnc]

        try:
            fla, flo = float(row[c_la].strip()), float(row[c_lo].strip())
        except ValueError:
            skipped += 1; continue
        if fla == 0 or flo == 0:
            skipped += 1; continue

        try:
            az = int(float(row[c_az].strip()))
        except ValueError:
            az = None

        rec = (round(fla, 6), round(flo, 6), row[c_loc].strip(), az)
        up = row[c_up].strip()
        key = (mnc, tac, eci)
        prev = latest.get(key)
        if prev is None or up > prev[0]:
            latest[key] = (up, rec)

print('rows read            : %d' % total)
print('rows skipped         : %d' % skipped)
print('distinct cells       : %d' % len(latest))
print('malformed cell_ids   : %d distinct, %d rows'
      % (len(malformed), sum(malformed.values())))
print('rows folded by alias : %s' % dict(aliased))

shards = collections.defaultdict(dict)
for (mnc, tac, eci), (_, rec) in latest.items():
    shards[(mnc, tac)][eci] = rec

os.makedirs(outdir, exist_ok=True)
sizes = []
for (mnc, tac), cells in shards.items():
    sites, idx, out = [], {}, {}
    # sorted iteration keeps the site table order reproducible, so an unchanged
    # input regenerates byte-identical files and git shows no diff
    for eci in sorted(cells):
        la, lo, loc, az = cells[eci]
        site = (la, lo, loc)
        if site not in idx:
            idx[site] = len(sites)
            sites.append([la, lo, loc])
        out[eci] = [idx[site], az]
    d = os.path.join(outdir, mnc)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, tac + '.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump({'s': sites, 'c': out}, f, separators=(',', ':'), sort_keys=True)
    sizes.append(os.path.getsize(p))

sizes.sort()
n = len(sizes)
print()
print('directories          : %d' % len({m for m, _ in shards}))
print('shards written       : %d' % n)
print('total size           : %.1f MB' % (sum(sizes) / 1e6))
print('shard size: median %.1f KB, p90 %.1f KB, p99 %.1f KB, max %.1f KB'
      % (sizes[n // 2] / 1024, sizes[int(n * .90)] / 1024,
         sizes[int(n * .99)] / 1024, sizes[-1] / 1024))
print('output dir           : %s' % os.path.abspath(outdir))
