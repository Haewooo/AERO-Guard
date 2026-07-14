from backend.audio.normalizer import normalize


def test_digit_words_merge():
    assert normalize("runway three six") == "runway 36"


def test_niner_and_tree():
    assert normalize("heading tree six niner") == "heading 369"


def test_thousands():
    assert normalize("maintain four thousand") == "maintain 4000"
    assert normalize("maintain four thousand five hundred") == "maintain 4500"


def test_decimal_frequency():
    assert normalize("contact tower one one eight decimal seven") == "contact tower 118.7"


def test_phonetic_alphabet():
    assert normalize("via alpha bravo") == "via A B"


def test_mixed_callsign():
    assert normalize("KAF five zero two, descend") == "kaf 502 descend"


def test_numeric_passthrough():
    assert normalize("Runway 36, cleared for takeoff") == "runway 36 cleared for takeoff"


def test_sentence_period_on_digit_words():
    # ASR emits sentence punctuation glued to spoken digits
    assert normalize("squawk four five two one. QNH one zero one three.") == "squawk 4521 qnh 1013"


def test_sentence_period_on_niner():
    assert normalize("wind tree one zero at niner.") == "wind 310 at 9"


def test_decimal_frequency_survives_sentence_period():
    assert normalize("contact tower 118.7. good day") == "contact tower 118.7 good day"
