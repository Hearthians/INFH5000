"""Assemble the six completed Qwen-Image-2.1 experiments into comparison.png."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
CASES = {
    "rose": "添加 · 在木桌上放一枝红玫瑰",
    "patio": "删除 · 移除露台长椅上标记的灰色靠垫",
    "fern": "替换 · 把选中的靴子换成盆栽蕨类",
    "headphones": "改色 · 把选中的耳罩改为深青色",
    "cats": "移动 · 把下层的猫摆件移至上层右端",
    "suitcase": "替换 · 把徒步者身边的折叠椅换成红色行李箱",
}
HEADINGS = ("标注输入", "作者参考", "仅画布", "画布＋通用提示", "逐例文字")


def font(size):
    for path in (
        Path.home() / ".local/share/fonts/NotoSansCJK-Regular.ttc",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
    ):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    raise FileNotFoundError("Install Noto Sans CJK or Droid Sans Fallback to render the Chinese labels.")


def sources(key):
    if key == "suitcase":
        return (ASSETS / "method-annotated.webp", ASSETS / "method-target.webp",
                ROOT / "suitcase-canvas.png", ROOT / "suitcase-generic.png", ROOT / "suitcase-text.png")
    return (ASSETS / f"{key}-canvas.webp", ASSETS / f"{key}-output.webp",
            ROOT / f"{key}-canvas.png", ROOT / f"{key}-generic.png", ROOT / f"{key}-guided.png")


def main():
    margin, gap, cell_w, cell_h = 32, 16, 528, 364
    width = margin * 2 + cell_w * len(HEADINGS) + gap * (len(HEADINGS) - 1)
    first_row, row_height = 146, cell_h + 72
    board = Image.new("RGB", (width, first_row + len(CASES) * row_height + 12), "white")
    draw = ImageDraw.Draw(board)
    title, label, note = font(38), font(25), font(22)
    ink, muted = "#112b33", "#4b5f64"
    draw.text((margin, 12), "VibeEdit 论文示例 · Qwen-Image-2.1 实测", font=title, fill=ink)
    draw.text((margin, 67), "种子 42，40 步。通用提示对六组使用相同的画布规则说明。逐例文字写明具体编辑目标，前五组搭配标注图，最后一组搭配干净原图。", font=note, fill=muted)
    columns = [margin + i * (cell_w + gap) for i in range(len(HEADINGS))]
    for x, heading in zip(columns, HEADINGS):
        draw.text((x, 106), heading, font=label, fill=ink)

    for row, (key, instruction) in enumerate(CASES.items()):
        top = first_row + row * row_height
        draw.line((margin, top, width - margin, top), fill="#cad3d5", width=2)
        draw.text((margin, top + 7), instruction, font=label, fill=ink)
        if key == "suitcase":
            draw.text((columns[1], top + 9), "方法图中的训练目标", font=note, fill=muted)
            draw.text((columns[4], top + 9), "本列输入为干净原图＋文字", font=note, fill=muted)
        y = top + 50
        for x, path in zip(columns, sources(key)):
            with Image.open(path) as image:
                panel = ImageOps.contain(image.convert("RGB"), (cell_w, cell_h), Image.Resampling.LANCZOS)
            draw.rectangle((x, y, x + cell_w - 1, y + cell_h - 1), fill="#f0f2f1")
            board.paste(panel, (x + (cell_w - panel.width) // 2, y + (cell_h - panel.height) // 2))

    board.save(ROOT / "comparison.png")


if __name__ == "__main__":
    main()
