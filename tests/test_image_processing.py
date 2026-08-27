from pathlib import Path

from PIL import Image

from image_processing import prepare_document


def test_prepare_image_creates_ocr_ready_png(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    Image.new("RGB", (320, 200), color="white").save(source)

    pages = prepare_document(source, tmp_path / "processed")

    assert len(pages) == 1
    assert pages[0].name == "page-1.png"
    with Image.open(pages[0]) as processed:
        assert processed.format == "PNG"
        assert max(processed.size) == 1600
