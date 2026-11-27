import json

from flatresult import flatten, links, needs_flattening

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def texts(result):
    return [b["text"] for b in result["content"] if b["type"] == "text"]


def test_plain_results_are_returned_as_the_same_object():
    for result in ({"content": [{"type": "text", "text": "hi"}]},
                   {"content": [{"type": "image", "data": PNG, "mimeType": "image/png"}]},
                   {"content": [{"type": "text", "text": "{}"}], "structuredContent": {}},
                   {"content": []}, {}):
        flat = flatten(result)
        assert flat.result is result and not flat.changed
        assert not needs_flattening(result)


def test_structured_content_only_gets_a_text_block_and_keeps_structured_content():
    result = {"content": [], "structuredContent": {"temp": 21.5, "city": "Zürich"}}
    flat = flatten(result)
    assert json.loads(texts(flat.result)[0]) == {"temp": 21.5, "city": "Zürich"}
    assert "Zürich" in texts(flat.result)[0]  # not \u-escaped
    assert flat.result["structuredContent"] == result["structuredContent"]
    assert result["content"] == []  # the input is not changed
    assert str(flat.changes[0]).startswith("structuredContent: structuredContent -> text block")


def test_structured_content_with_missing_content_key_and_on_errors():
    flat = flatten({"isError": True, "structuredContent": {"error": "quota"}})
    assert flat.result["isError"] is True
    assert json.loads(texts(flat.result)[0]) == {"error": "quota"}


def test_structured_content_next_to_an_image_only_still_gets_text():
    flat = flatten({"content": [{"type": "image", "data": PNG, "mimeType": "image/png"}],
                    "structuredContent": {"n": 1}})
    assert [b["type"] for b in flat.result["content"]] == ["image", "text"]


def test_null_structured_content_is_ignored():
    result = {"content": [], "structuredContent": None}
    assert flatten(result).result is result


def test_resource_link_without_a_reader_becomes_a_line_naming_it():
    flat = flatten({"content": [{"type": "resource_link", "uri": "https://x.test/r.txt",
                                 "name": "report", "title": "Weekly report",
                                 "mimeType": "text/plain", "size": 1234,
                                 "description": "Generated report "}]})
    assert flat.result["content"] == [{"type": "text", "text":
        "Resource: Weekly report <https://x.test/r.txt> (text/plain, 1,234 bytes)\n"
        "Generated report"}]
    assert str(flat.changes[0]) == "content[0]: resource_link -> text line"


def test_resource_link_with_a_reader_puts_text_and_images_inline():
    store = {"file:///a.json": [{"uri": "file:///a.json", "mimeType": "application/json",
                                 "text": '{"w": 1}'}],
             "file:///a.png": [{"uri": "file:///a.png", "mimeType": "image/png", "blob": PNG}]}
    flat = flatten({"content": [
        {"type": "resource_link", "uri": "file:///a.json", "name": "a"},
        {"type": "resource_link", "uri": "file:///a.png", "name": "a.png"}]}, read=store.get)
    assert flat.result["content"] == [
        {"type": "text", "text": 'Resource: a <file:///a.json>\n\n{"w": 1}'},
        {"type": "text", "text": "Resource: a.png <file:///a.png>"},
        {"type": "image", "data": PNG, "mimeType": "image/png"}]
    assert [c.note for c in flat.changes] == ["inline contents: text block",
                                              "inline contents: text block + image block"]


def test_a_reader_that_fails_or_finds_nothing_keeps_the_link_line():
    def boom(uri):
        raise RuntimeError("down")
    for read in (boom, lambda uri: None, lambda uri: []):
        flat = flatten({"content": [{"type": "resource_link", "uri": "file:///x"}]}, read=read)
        assert texts(flat.result) == ["Resource: <file:///x>"]


def test_long_resource_text_is_cut_with_a_note():
    flat = flatten({"content": [{"type": "resource", "resource": {
        "uri": "file:///big.log", "text": "x" * 50}}]}, max_text=10)
    assert texts(flat.result) == [
        "file:///big.log:\nxxxxxxxxxx\n[... 40 more characters of file:///big.log not shown]"]


def test_embedded_text_resource_becomes_text_with_its_uri():
    flat = flatten({"content": [
        {"type": "text", "text": "Here:"},
        {"type": "resource", "resource": {"uri": "file:///n.md", "mimeType": "text/markdown",
                                          "text": "# Notes"}}]})
    assert texts(flat.result) == ["Here:", "file:///n.md (text/markdown):\n# Notes"]
    assert [c.index for c in flat.changes] == [1]


def test_embedded_blobs_become_images_or_a_note():
    flat = flatten({"content": [
        {"type": "resource", "resource": {"uri": "file:///p.png", "mimeType": "image/png",
                                          "blob": PNG}},
        {"type": "resource", "resource": {"uri": "file:///r.pdf",
                                          "mimeType": "application/pdf", "blob": "AAAA"}},
        {"type": "resource", "resource": {"uri": "file:///bad.png", "mimeType": "image/png",
                                          "blob": "not base64!"}}]})
    assert flat.result["content"][0] == {"type": "image", "data": PNG, "mimeType": "image/png"}
    assert texts(flat.result) == [
        "[binary resource file:///r.pdf: application/pdf, 3 bytes, not shown]",
        "[binary resource file:///bad.png: image/png, 8 bytes, not shown]"]


def test_audio_becomes_a_note():
    flat = flatten({"content": [{"type": "audio", "data": "AAAAAA==", "mimeType": "audio/wav"}]})
    assert texts(flat.result) == ["[audio: audio/wav, 4 bytes, not shown]"]


def test_keep_leaves_the_original_blocks_before_the_text():
    link = {"type": "resource_link", "uri": "file:///x", "name": "x"}
    flat = flatten({"content": [link]}, keep=True)
    assert flat.result["content"] == [link, {"type": "text", "text": "Resource: x <file:///x>"}]


def test_unknown_and_malformed_blocks_are_left_alone():
    result = {"content": [{"type": "widget", "x": 1}, "junk", {"no": "type"},
                          {"type": "resource_link"}, {"type": "resource", "resource": "x"}]}
    assert flatten(result).result is result


def test_non_dict_results_and_content_are_left_alone():
    for result in (None, [], "x", {"content": "text"}):
        assert flatten(result).result is result


def test_links_lists_resource_link_uris():
    assert links({"content": [{"type": "resource_link", "uri": "a"}, {"type": "text"},
                              {"type": "resource_link", "uri": 3},
                              {"type": "resource_link", "uri": "b"}]}) == ["a", "b"]
    assert links(None) == [] and links({"content": "x"}) == []
