from jalebi.masking import build_masker


def test_masks_token() -> None:
    mask = build_masker("ghp_secret", [])
    assert mask("token is ghp_secret here") == "token is *** here"


def test_masks_regex_patterns() -> None:
    mask = build_masker(None, [r"AKIA[0-9A-Z]{16}", r"password=(\w+)"])
    assert mask("key AKIAABCDEFGHIJKLMNOP password=hunter2") == "key *** ***"


def test_invalid_pattern_ignored() -> None:
    mask = build_masker(None, ["[invalid", "ghp_[a-z]+"])
    assert mask("ghp_abc") == "***"


def test_no_secret_noop() -> None:
    mask = build_masker(None, [])
    assert mask("plain text") == "plain text"


def test_empty_string() -> None:
    mask = build_masker("ghp_secret", [])
    assert mask("") == ""
