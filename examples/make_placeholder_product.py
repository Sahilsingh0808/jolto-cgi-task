"""Generate a synthetic placeholder product image for offline demos.

Replace `examples/product.jpg` with a real product photograph before a real run.
This exists so reviewers can run the deterministic stages without needing
their own jewellery photography.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


def make_placeholder(out: Path, size: tuple[int, int] = (1024, 1024)) -> None:
    img = Image.new("RGB", size, (18, 18, 20))
    draw = ImageDraw.Draw(img)

    cx, cy = size[0] // 2, size[1] // 2
    ring_outer = 240
    ring_inner = 170

    for r_out, r_in, colour in (
        (ring_outer + 8, ring_inner - 8, (40, 30, 12)),
        (ring_outer, ring_inner, (214, 174, 82)),
        (ring_outer - 6, ring_inner + 6, (240, 210, 130)),
    ):
        draw.ellipse(
            (cx - r_out, cy - r_out, cx + r_out, cy + r_out),
            outline=None,
            fill=colour,
        )
        draw.ellipse(
            (cx - r_in, cy - r_in, cx + r_in, cy + r_in),
            fill=(18, 18, 20),
        )

    stone_cx, stone_cy = cx, cy - ring_outer + 14
    for r, colour in ((46, (255, 255, 255)), (36, (235, 245, 255)), (22, (180, 210, 240))):
        draw.ellipse(
            (stone_cx - r, stone_cy - r, stone_cx + r, stone_cy + r),
            fill=colour,
        )

    img = img.filter(ImageFilter.GaussianBlur(radius=0.8))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=92)


if __name__ == "__main__":
    make_placeholder(Path(__file__).parent / "product.jpg")
    print("wrote examples/product.jpg")
