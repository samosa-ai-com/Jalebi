"""JALEBI_PUBLIC_URLS parsing + notification link helpers (LAN/Tailscale/public)."""

from jalebi.config import Config, parse_public_urls


def test_parse_labeled_urls() -> None:
    links = parse_public_urls(
        "LAN=http://192.0.2.1:2052, Tailscale=http://198.51.100.5:2052"
    )
    assert links == [
        ("LAN", "http://192.0.2.1:2052"),
        ("Tailscale", "http://198.51.100.5:2052"),
    ]


def test_parse_bare_url_gets_hostname_label() -> None:
    assert parse_public_urls("http://192.0.2.1:2052") == [
        ("192.0.2.1", "http://192.0.2.1:2052")
    ]


def test_parse_normalizes_scheme_and_trailing_slash() -> None:
    assert parse_public_urls("LAN=192.0.2.1:2052/") == [
        ("LAN", "http://192.0.2.1:2052")
    ]


def test_parse_bare_url_with_query_string_kept_intact() -> None:
    links = parse_public_urls("https://example.com/tunnel?token=abc")
    assert links == [("example.com", "https://example.com/tunnel?token=abc")]


def test_parse_skips_junk_and_empties() -> None:
    assert parse_public_urls("LAN=, , =http://x/") == []
    assert parse_public_urls("") == []


def test_parse_schemeless_typos_never_raise() -> None:
    # Regression: naive string surgery raised IndexError on these.
    assert parse_public_urls("http://") == []
    assert parse_public_urls("/") == []
    assert parse_public_urls("LAN=http://, Tailscale=http://198.51.100.5:2052") == [
        ("Tailscale", "http://198.51.100.5:2052")
    ]


def test_parse_labels_may_contain_dots_and_parens() -> None:
    # Regression: the old label allow-list rejected these, corrupting the URL.
    assert parse_public_urls("Laptop.local=http://192.0.2.1:2052") == [
        ("Laptop.local", "http://192.0.2.1:2052")
    ]
    assert parse_public_urls("LAN (Wi-Fi)=http://192.0.2.1:2052") == [
        ("LAN (Wi-Fi)", "http://192.0.2.1:2052")
    ]


def test_parse_bare_ipv6_url() -> None:
    assert parse_public_urls("http://[::1]:2052") == [("::1", "http://[::1]:2052")]


def test_links_empty_when_unset() -> None:
    config = Config(port=2052)
    assert config.public_links() == []
    assert config.primary_link("/tasks/3") == "http://127.0.0.1:2052/tasks/3"


def test_primary_link_is_first_url() -> None:
    config = Config(
        port=2052,
        public_urls="LAN=http://192.0.2.1:2052, Tailscale=http://198.51.100.5:2052",
    )
    assert config.primary_link("/screenings") == "http://192.0.2.1:2052/screenings"


def test_open_actions_single_url_keeps_legacy_label() -> None:
    config = Config(port=2052, public_urls="LAN=http://192.0.2.1:2052")
    assert config.open_actions("/tasks/3", "Open task") == [
        {
            "action": "view",
            "label": "Open task",
            "url": "http://192.0.2.1:2052/tasks/3",
        }
    ]


def test_open_actions_unset_keeps_loopback_legacy_button() -> None:
    config = Config(port=2052)
    assert config.open_actions("/tasks/3", "Open task") == [
        {
            "action": "view",
            "label": "Open task",
            "url": "http://127.0.0.1:2052/tasks/3",
        }
    ]


def test_open_actions_multiple_urls_one_button_each() -> None:
    config = Config(
        port=2052,
        public_urls="LAN=http://192.0.2.1:2052, Tailscale=http://198.51.100.5:2052",
    )
    assert config.open_actions("/tasks/3", "Open task") == [
        {
            "action": "view",
            "label": "Open (LAN)",
            "url": "http://192.0.2.1:2052/tasks/3",
        },
        {
            "action": "view",
            "label": "Open (Tailscale)",
            "url": "http://198.51.100.5:2052/tasks/3",
        },
    ]


def test_open_actions_capped_at_three_buttons() -> None:
    config = Config(
        port=2052,
        public_urls="A=http://a/, B=http://b/, C=http://c/, D=http://d/",
    )
    actions = config.open_actions("/", "Open Jalebi")
    assert [a["label"] for a in actions] == ["Open (A)", "Open (B)", "Open (C)"]


def test_load_config_reads_public_urls_env(monkeypatch, tmp_path) -> None:
    import os

    from jalebi.config import load_config

    monkeypatch.setenv("JALEBI_PUBLIC_URLS", "LAN=http://192.0.2.1:2052")
    monkeypatch.setenv("JALEBI_DATA_DIR", str(tmp_path / "data"))
    # load_config reads the repo-root .env too; ensure env wins either way.
    assert os.environ["JALEBI_PUBLIC_URLS"] == "LAN=http://192.0.2.1:2052"
    config = load_config()
    assert config.public_links() == [("LAN", "http://192.0.2.1:2052")]
