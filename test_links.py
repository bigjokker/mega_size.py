"""Local tests for MEGA link extraction. No network."""

from mega_crypto import file_aes_parts
from mega_links import extract_mega_links, parse_mega_url


def test_single_new_folder():
    link = parse_mega_url("https://mega.nz/folder/AAAAA#BBBBB")
    assert link is not None
    assert link.kind == "folder"
    assert link.handle == "AAAAA"
    assert link.key == "BBBBB"
    assert link.has_key


def test_keyless_file():
    link = parse_mega_url("https://mega.nz/file/CCCCC")
    assert link is not None
    assert link.kind == "file"
    assert not link.has_key


def test_old_formats():
    folder = parse_mega_url("https://mega.nz/#F!oldfolder!oldkey")
    file_link = parse_mega_url(
        "https://mega.nz/#!HeIXjKyJ!jWc31PqApNIRa1cbgnUYNOZv5QQ85a5YFMjLhbAH4Ok"
    )
    assert folder and folder.kind == "folder" and folder.handle == "oldfolder"
    assert file_link and file_link.kind == "file"
    assert file_link.handle == "HeIXjKyJ"
    assert file_link.key.startswith("jWc31PqAp")


def test_extract_from_webpage_blob():
    blob = """
    Check these:
    https://mega.nz/folder/AAAAA#BBBBB
    and also <a href="https://mega.nz/file/CCCCC#DDDDD">file</a>
    mega.nz/folder/AAAAA#BBBBB
    plus a keyless one https://mega.nz/folder/EEEEE
    and old https://mega.nz/#F!FFFFF!GGGGG
    """
    links = extract_mega_links(blob)
    kinds = {(item.kind, item.handle, bool(item.key)) for item in links}
    assert ("folder", "AAAAA", True) in kinds
    assert ("file", "CCCCC", True) in kinds
    assert ("folder", "EEEEE", False) in kinds
    assert ("folder", "FFFFF", True) in kinds
    assert len(links) == 4


def test_prefers_keyed_duplicate():
    blob = "https://mega.nz/folder/AAAAAAAA  later https://mega.nz/folder/AAAAAAAA#KEYKEYKEY"
    links = extract_mega_links(blob)
    assert len(links) == 1
    assert links[0].key == "KEYKEYKEY"


def test_html_wbr_does_not_truncate_handle():
    blob = '<a href="https://mega.nz/folder/ABCD<wbr>1234#WXYZ5678KEYKEYKEYKEY">link</a>'
    links = extract_mega_links(blob)
    assert len(links) == 1
    assert links[0].handle == "ABCD1234"
    assert links[0].key == "WXYZ5678KEYKEYKEYKEY"


def test_line_broken_url():
    blob = "https://mega.nz/folder/ABCD\n1234#WXYZ5678KEYKEYKEYKEY"
    links = extract_mega_links(blob)
    assert len(links) == 1
    assert links[0].handle == "ABCD1234"


def test_file_aes_parts_accepts_list_or_tuple():
    from_list = file_aes_parts([1, 2, 3, 4, 5, 6, 7, 8])
    from_tuple = file_aes_parts((1, 2, 3, 4, 5, 6, 7, 8))
    assert from_list == from_tuple


def test_does_not_glue_two_separate_links():
    blob = "https://mega.nz/folder/AAAAAAAA#BBBBBBBB\nhttps://mega.nz/folder/CCCCCCCC#DDDDDDDD"
    links = extract_mega_links(blob)
    assert len(links) == 2
    assert {item.handle for item in links} == {"AAAAAAAA", "CCCCCCCC"}


if __name__ == "__main__":
    test_single_new_folder()
    test_keyless_file()
    test_old_formats()
    test_extract_from_webpage_blob()
    test_prefers_keyed_duplicate()
    test_html_wbr_does_not_truncate_handle()
    test_line_broken_url()
    test_file_aes_parts_accepts_list_or_tuple()
    test_does_not_glue_two_separate_links()
    print("link extraction tests passed")
