from ocr import _normalize_result


def test_normalize_current_paddleocr_result_shape() -> None:
    result = {
        "res": {
            "rec_texts": ["ИВАН", ""],
            "rec_scores": [0.97, 0.20],
            "rec_boxes": [[10, 20, 110, 45], [0, 0, 1, 1]],
        }
    }

    lines = _normalize_result(result, page_number=2)

    assert len(lines) == 1
    assert lines[0].page == 2
    assert lines[0].text == "ИВАН"
    assert lines[0].confidence == 0.97
    assert lines[0].box.left == 10
