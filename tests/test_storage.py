from tracker.storage import guess_extension, save_raw, sha256_bytes


def test_save_is_content_addressed_and_idempotent(tmp_path):
    a = save_raw(b"hello", source="tceq_air", raw_dir=tmp_path, ext="pdf")
    b = save_raw(b"hello", source="tceq_air", raw_dir=tmp_path, ext=".pdf")
    assert a.path == b.path == tmp_path / "tceq_air" / f"{sha256_bytes(b'hello')}.pdf"
    assert a.path.read_bytes() == b"hello"
    assert not list(a.path.parent.glob(".partial-*"))


def test_guess_extension():
    assert guess_extension(None, "https://x.gov/a/37589sob.pdf") == ".pdf"
    assert guess_extension("application/pdf", "https://x.gov/view?id=1") == ".pdf"
    assert guess_extension(None, None) == ".bin"
