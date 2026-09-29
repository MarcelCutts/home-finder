"""Build a local evidence-only review deck from an existing batch, without scores.

All images come from original source URLs and retained caches, not augmented
merged galleries. The JSON manifest includes hashes for independent review.
Run with development dependencies; no network or model call is made.
"""

import argparse
import hashlib
import html
import json
import os
from pathlib import Path

from pytest_socket import disable_socket
from replay_batch_dedup import load_inputs

from home_finder.utils.image_cache import find_cached_file

PAGE = r"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' file:;
style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none';">
<title>Home Finder — retained listing review</title>
<style>
body{font:16px system-ui;margin:16px;background:#f1f3f5;color:#17202a}
header{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:14px}
h1{font-size:22px;margin:0}button,input,a{font:inherit}input{width:270px;padding:6px}
article{background:white;border:1px solid #bac4cc;margin-bottom:10px;padding:8px}
h2{font-size:18px;margin:0 0 4px}.meta{color:#384959;font-size:14px;margin-bottom:6px}
.photos{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:7px}
figure{margin:0;background:#eef0f2}img{width:100%;height:225px;object-fit:contain}
figcaption{font-size:12px;padding:2px 4px}details{font-size:14px;margin-top:4px}
pre{white-space:pre-wrap;font:inherit}a{color:#07599a}#status{font-size:14px}
</style><header><h1>Retained listing evidence</h1>
<button id="prev">Previous</button><button id="next">Next</button><span id="status"></span>
<input id="ids" placeholder="Compare IDs, e.g. R13,O08,O09" aria-label="Listing IDs">
<button id="compare">Compare</button><a href="?page=0">All listings</a>
</header><main></main><script>
const records=__RECORDS__, PAGE_SIZE=5;
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',
'"':'&quot;',"'":'&#39;'}[c]));
const query=new URLSearchParams(location.search), page=Math.max(0,Number(query.get('page'))||0);
const ids=(query.get('ids')||'').split(',').map(s=>s.trim().toUpperCase()).filter(Boolean);
const selected=ids.length?ids.map(id=>records.find(r=>r.id===id)).filter(Boolean):
records.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE);
document.querySelector('#status').textContent=ids.length?`${selected.length} selected`:
`Page ${page+1}/${Math.ceil(records.length/PAGE_SIZE)} · ${records.length} listings`;
document.querySelector('#ids').value=ids.join(',');
document.querySelector('#prev').onclick=()=>location.search=`?page=${Math.max(0,page-1)}`;
document.querySelector('#next').onclick=()=>location.search=`?page=${Math.min(Math.ceil(records.length/PAGE_SIZE)-1,page+1)}`;
document.querySelector('#compare').onclick=()=>location.search='?ids='+encodeURIComponent(document.querySelector('#ids').value);
document.querySelector('main').innerHTML=selected.map(r=>`<article id="${r.id}">
<h2>${r.id} · ${escape(r.address)} · £${r.price_pcm} · ${r.bedrooms} bed</h2>
<div class="meta">${escape(r.unique_id)} · ${escape(r.postcode)} ·
${r.photos.length} retained images</div>
<div class="photos">${r.photos.map(p=>`<figure><a href="${escape(p.href)}" target="_blank">
<img src="${escape(p.href)}" alt="${r.id} ${p.kind} ${p.index}"></a>
<figcaption>${p.kind} ${p.index}</figcaption></figure>`).join('')}</div>
<details><summary>Listing text and features</summary><pre>${escape(r.description)}</pre>
<ul>${r.features.map(x=>`<li>${escape(x)}</li>`).join('')}</ul></details></article>`).join('');
</script></html>"""


def build(source: Path, output: Path) -> dict:
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    (output / "images").mkdir(mode=0o700)
    merged, _ = load_inputs(source)
    counters: dict[str, int] = {}
    records = []
    for item in merged:
        prop = item.canonical
        prefix = {"rightmove": "R", "onthemarket": "O"}[prop.source.value]
        counters[prefix] = counters.get(prefix, 0) + 1
        label = f"{prefix}{counters[prefix]:02}"
        photos = []
        for index, img in enumerate((*item.images, *((item.floorplan,) if item.floorplan else ()))):
            original = find_cached_file(str(source), prop.unique_id, str(img.url), img.image_type)
            assert original is not None
            name = f"{label}_{index}_{original.name}"
            target = output / "images" / name
            target.symlink_to(original.resolve())
            photos.append(
                {
                    "kind": img.image_type,
                    "index": index,
                    "href": "images/" + name,
                    "sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
                    "source_url": str(img.url),
                    "original_file": str(original),
                }
            )
        evidence = item.source_text[prop.source]
        records.append(
            {
                "id": label,
                "unique_id": prop.unique_id,
                "address": prop.address,
                "postcode": prop.postcode,
                "price_pcm": prop.price_pcm,
                "bedrooms": prop.bedrooms,
                "description": prop.description,
                "features": list(evidence.features or ()),
                "coordinates": [prop.latitude, prop.longitude],
                "photos": photos,
            }
        )
    records.sort(key=lambda r: (r["address"].lower(), r["price_pcm"], r["id"]))
    manifest = {
        "schema_version": 1,
        "source": str(source),
        "records": records,
        "review_kind": "agent development review; no scores or predicted groups in deck",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # Escape embedded source text so it cannot close the script element.
    encoded = json.dumps(records).replace("<", "\\u003c").replace(">", "\\u003e")
    (output / "review.html").write_text(PAGE.replace("__RECORDS__", encoded))
    (output / "catalogue.md").write_text(
        "| ID | Source listing | Address | Rent | Beds |\n|---|---|---|---:|---:|\n"
        + "\n".join(
            f"| {r['id']} | {r['unique_id']} | {html.escape(r['address'])} | "
            f"{r['price_pcm']} | {r['bedrooms']} |"
            for r in records
        )
        + "\n"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    disable_socket()
    manifest = build(args.input.resolve(), args.output.resolve())
    print(
        json.dumps({"listings": len(manifest["records"]), "deck": str(args.output / "review.html")})
    )


if __name__ == "__main__":
    main()
