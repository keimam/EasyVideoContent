from pathlib import Path
from PIL import Image, ImageDraw

out_dir = Path("resource/branding")
out_dir.mkdir(parents=True, exist_ok=True)

# 1. Generate SVG
svg_content = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%">
  <rect x="32" y="32" width="448" height="448" rx="112" ry="112" fill="#8FA6FF" />
  <circle cx="256" cy="256" r="136" fill="none" stroke="#0D1015" stroke-width="36" />
  <polygon points="232,196 232,316 324,256" fill="#0D1015" />
</svg>"""

(out_dir / "logo.svg").write_text(svg_content.strip(), encoding="utf-8")
print("logo.svg created")

# 2. Render PNG at 1024x1024 then supersample down
def draw_logo(size: int) -> Image.Image:
    scale = 4
    canvas_size = size * scale
    img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Scale parameters relative to 512 base viewBox
    factor = canvas_size / 512.0
    padding = 32 * factor
    rect_w = 448 * factor
    radius = 112 * factor
    
    # Rounded rect
    draw.rounded_rectangle(
        [padding, padding, padding + rect_w, padding + rect_w],
        radius=radius,
        fill="#8FA6FF"
    )
    
    # Circle outline
    cx = 256 * factor
    cy = 256 * factor
    r = 136 * factor
    stroke_w = 36 * factor
    
    # Draw circle outline by drawing outer circle and inner circle
    draw.ellipse(
        [cx - r - stroke_w/2, cy - r - stroke_w/2, cx + r + stroke_w/2, cy + r + stroke_w/2],
        fill="#0D1015"
    )
    draw.ellipse(
        [cx - r + stroke_w/2, cy - r + stroke_w/2, cx + r - stroke_w/2, cy + r - stroke_w/2],
        fill="#8FA6FF"
    )
    
    # Triangle (play button)
    p1 = (232 * factor, 196 * factor)
    p2 = (232 * factor, 316 * factor)
    p3 = (324 * factor, 256 * factor)
    draw.polygon([p1, p2, p3], fill="#0D1015")
    
    return img.resize((size, size), Image.Resampling.LANCZOS)

img_256 = draw_logo(256)
img_256.save(out_dir / "logo.png")
print("logo.png created (256x256)")

img_64 = draw_logo(64)
img_64.save(out_dir / "favicon.png")
print("favicon.png created (64x64)")
