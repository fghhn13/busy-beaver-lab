"""Headless sampled spacetime export with no graphics dependencies."""
import colorsys
from html import escape
import uuid
from experiments.storage import write_json


def color(symbol):
    if symbol == 0: return "#eef2e8"
    if symbol == 1: return "#315e48"
    rgb = colorsys.hls_to_rgb(((symbol*137.508+195)%360)/360, .44, .48)
    return "#" + "".join(f"{round(x*255):02x}" for x in rgb)


def cells(snapshot):
    return snapshot.get("cells", [{"position": p, "symbol": 1} for p in snapshot.get("nonzero_cells", [])])


def save_spacetime_svg(path, definition, trace):
    samples = trace["samples"]
    if not samples: raise ValueError("No trace samples available")
    positions = [int(s["head"]) for s in samples]
    for sample in samples:
        positions.extend(int(c["position"]) for c in cells(sample))
        positions.extend(int(sample[k]) for k in ("min_head", "max_head"))
    low, high = min(positions)-2, max(positions)+2
    span, steps = high-low+1, int(samples[-1]["steps"])+1
    x = lambda p: 70+(int(p)-low)*860/span
    y = lambda s: 80+int(s)*540/steps
    cw, rh = max(.6,860/span), max(.6,540/steps)
    content = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="700" viewBox="0 0 1000 700">',
               '<rect width="1000" height="700" fill="#fafbf8"/>',
               f'<text x="70" y="28" font-family="sans-serif" font-size="18">{escape(definition["id"])} · sampled spacetime</text>',
               f'<text x="70" y="49" font-family="sans-serif" font-size="11">X: absolute position / Y: actual step / {len(samples)} samples / gaps are unsampled time</text>',
               '<rect x="70" y="80" width="860" height="540" fill="#eef2e8"/>']
    for sample in samples:
        sy = y(sample["steps"])
        for cell in cells(sample):
            content.append(f'<rect x="{x(cell["position"]):.3f}" y="{sy:.3f}" width="{cw:.3f}" height="{rh:.3f}" fill="{color(cell["symbol"])}"/>')
        content.append(f'<rect x="{x(sample["head"]):.3f}" y="{sy:.3f}" width="{max(2,cw):.3f}" height="{max(2,rh):.3f}" fill="none" stroke="#d79447" stroke-width="1.5"/>')
    content += [f'<text x="70" y="647" font-family="monospace" font-size="12">{low}</text>',
                f'<text x="930" y="647" text-anchor="end" font-family="monospace" font-size="12">{high}</text>',
                '<text x="60" y="85" text-anchor="end" font-family="monospace" font-size="12">0</text>',
                f'<text x="60" y="620" text-anchor="end" font-family="monospace" font-size="12">{steps-1}</text>']
    for i, symbol in enumerate(definition["symbols"]):
        lx, ly = 70+(i%16)*50, 666+(i//16)*16
        content.append(f'<rect x="{lx}" y="{ly-9}" width="9" height="9" fill="{color(symbol)}"/><text x="{lx+13}" y="{ly}" font-family="monospace" font-size="11">{symbol}</text>')
    content.append('</svg>')
    output = path / "visualizations" / f"spacetime_{uuid.uuid4().hex}.svg"
    output.write_text("\n".join(content), encoding="utf-8")
    metadata = output.with_suffix(".json")
    write_json(metadata, {"view": "sampled_spacetime", "sample_steps": [s["steps"] for s in samples],
                         "stored_interval": trace["stored_interval"], "response_stride": trace["response_stride"],
                         "cli_sampled": trace["cli_sampled"], "bounds": {"min": str(low), "max": str(high)}})
    return {"path": str(output), "metadata_path": str(metadata), "sample_count": len(samples)}
