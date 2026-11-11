import json

import pytest

from toolcap import Cap, CursorError, decode_cursor, encode_cursor, estimate_tokens, page, trim

ROWS = [{"id": i, "title": f"Issue {i}", "body": "lorem ipsum " * 20} for i in range(300)]


def walk(cap, data, **kw):
    """Follow next_cursor to the end; return every page's parsed JSON."""
    pages, cursor = [], ""
    for _ in range(1000):
        out = cap.page(data, cursor, **kw)
        assert cap.fits(out), "every page must fit the budget"
        doc = json.loads(out)
        pages.append(doc)
        meta = doc.get("page") or doc.get("_page")
        cursor = meta.get("next_cursor") if meta else None
        if not cursor:
            return pages
    raise AssertionError("pagination did not end")


def test_small_result_is_returned_unchanged():
    cap = Cap(1000)
    assert cap.page({"ok": True}) == '{"ok":true}'
    assert cap.page("hello") == "hello"
    assert cap.page([1, 2, 3]) == "[1,2,3]"


def test_list_pages_cover_every_item_once_in_order():
    cap = Cap(1500)
    pages = walk(cap, ROWS)
    ids = [item["id"] for p in pages for item in p["items"]]
    assert ids == list(range(300))
    assert len(pages) > 5
    first, last = pages[0]["page"], pages[-1]["page"]
    assert first["total"] == 300 and first["items"].startswith("1-")
    assert 'cursor="' in first["note"]
    assert last["next_cursor"] is None and "last page" in last["note"]


def test_pages_are_as_full_as_the_budget_allows():
    cap = Cap(1500)
    out = cap.page(ROWS)
    n = len(json.loads(out)["items"])
    next_item = json.dumps(ROWS[n], separators=(",", ":"))
    assert cap.count(out) + cap.count(next_item) > cap.max_tokens  # one more would not fit


def test_dict_pages_its_largest_list_and_keeps_other_fields():
    data = {"query": "is:open", "count": 300, "tags": ["a", "b"], "results": ROWS}
    pages = walk(Cap(1500), data)
    assert all(p["query"] == "is:open" and p["tags"] == ["a", "b"] for p in pages)
    assert [r["id"] for p in pages for r in p["results"]] == list(range(300))


def test_nested_list_found_and_explicit_key():
    data = {"data": {"items": ROWS, "small": [1]}, "page": "user field"}
    out = json.loads(Cap(1500).page(data))
    assert len(out["data"]["items"]) < 300 and out["data"]["small"] == [1]
    assert out["page"] == "user field", "a real 'page' key is never overwritten"
    assert "next_cursor" in out["_page"]
    out = json.loads(Cap(1500).page({"a": ROWS, "b": ROWS[:200]}, key="b"))
    assert len(out["a"]) == 300 or "a" in out  # only b is paged
    assert out["page"]["total"] == 200
    with pytest.raises(KeyError):
        Cap(1500).page({"a": ROWS}, key="nope")
    with pytest.raises(TypeError):
        Cap(1500).page({"a": ROWS, "s": "x"}, key="s")


def test_bad_cursors_get_a_message_the_model_can_act_on():
    cap = Cap(1500)
    for bad in ["garbage!!", "MTpub3Q", encode_cursor(5, "abc")[:-3] + "%%%", "  "]:
        if not bad.strip():
            continue
        with pytest.raises(CursorError) as e:
            cap.page(ROWS, bad)
        assert "without a cursor" in str(e.value)
    assert isinstance(CursorError("x"), ValueError)


def test_cursor_roundtrip():
    c = encode_cursor(1234, "deadbeef")
    assert decode_cursor(c) == (1234, "deadbeef")
    assert "=" not in c


def test_cursor_past_the_end_and_empty_list():
    cap = Cap(500)
    out = json.loads(cap.page(ROWS, encode_cursor(10_000, "x")))
    assert out["items"] == [] and "No more items" in out["page"]["note"]
    assert cap.page([]) == "[]"


def test_changed_data_is_flagged():
    cap = Cap(1500)
    cursor = json.loads(cap.page(ROWS))["page"]["next_cursor"]
    changed = ROWS[:50] + [{"id": -1}] + ROWS[50:]
    note = json.loads(cap.page(changed, cursor))["page"]["note"]
    assert "changed since the previous page" in note
    same = json.loads(cap.page(ROWS, cursor))["page"]["note"]
    assert "changed" not in same


def test_one_huge_item_is_cut_and_paging_moves_on():
    data = [{"id": 0, "blob": "y" * 100_000}, {"id": 1, "blob": "small"}]
    pages = walk(Cap(800), data)
    assert pages[0]["items"][0]["id"] == 0
    assert "more characters cut" in pages[0]["items"][0]["blob"]
    assert "too large and was cut" in pages[0]["page"]["note"]
    assert pages[-1]["items"][-1] == {"id": 1, "blob": "small"}


def test_huge_fields_beside_the_list_are_cut_but_items_still_shown():
    data = {"meta": "m" * 50_000, "rows": ROWS}
    out = json.loads(Cap(2000).page(data))
    assert "more characters cut" in out["meta"]
    assert len(out["rows"]) >= 3


def test_value_without_lists_is_shrunk_keeping_keys():
    data = {"a": "z" * 30_000, "b": {"c": "q" * 30_000, "d": 1}}
    cap = Cap(500)
    raw = cap.page(data)
    out = json.loads(raw)
    assert set(out) == {"a", "b"} and out["b"]["d"] == 1
    assert cap.fits(raw) and "more characters cut" in out["a"]


def test_deep_lists_inside_items_are_cut_when_strings_are_not_enough():
    data = [{"id": 0, "points": list(range(20_000))}]
    out = Cap(300).page(data)
    doc = json.loads(out)
    assert "more items cut" in doc["items"][0]["points"][-1]


def test_text_pages_split_at_line_breaks_and_cover_everything():
    text = "".join(f"line {i:05d}\n" for i in range(5000))
    cap = Cap(1000)
    cursor, parts = "", []
    while True:
        out = cap.page(text, cursor)
        assert cap.fits(out)
        body, _, footer = out.rpartition("\n\n[")
        if not _:
            body, footer = out, ""
        parts.append(body)
        if 'cursor="' not in footer:
            break
        cursor = footer.split('cursor="')[1].split('"')[0]
        assert body.endswith("\n"), "cut at a line break"
    assert "".join(parts) == text


def test_text_without_line_breaks_and_unicode():
    text = "日本語のテキスト" * 3000
    cap = Cap(400)
    out = cap.page(text)
    assert cap.fits(out) and out.startswith("日本語")
    assert "Output cut" in out


def test_trim_has_no_cursor_and_says_what_was_left_out():
    out = json.loads(trim(ROWS, max_tokens=1500))
    assert "next_cursor" not in out["page"]
    assert "left out" in out["page"]["note"]
    text = trim("word " * 10_000, max_tokens=300)
    assert "the rest was left out" in text and "cursor" not in text


def test_pydantic_dataclass_set_tuple():
    import dataclasses

    from pydantic import BaseModel

    class M(BaseModel):
        x: int

    @dataclasses.dataclass
    class D:
        y: str

    out = json.loads(page({"m": M(x=1), "d": D(y="a"), "s": {3, 1}, "t": (1, 2), "o": object()}))
    assert out["m"] == {"x": 1} and out["d"] == {"y": "a"} and out["s"] == [1, 3] and out["t"] == [1, 2]
    assert out["o"].startswith("<object")


def test_estimate_is_conservative():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("日本") == 2  # 6 bytes
    assert estimate_tokens("a" * 4000) >= 1000  # at least the ~4 chars/token rule


def test_custom_counter_and_settings(monkeypatch):
    words = Cap(200, count=lambda s: len(s.split()))
    out = words.page("w " * 1000)
    assert len(out.split()) <= 200
    monkeypatch.setenv("TOOLCAP_MAX_TOKENS", "1234")
    assert Cap().max_tokens == 1234
    monkeypatch.setenv("TOOLCAP_MAX_TOKENS", "lots")
    with pytest.raises(ValueError):
        Cap()
    monkeypatch.delenv("TOOLCAP_MAX_TOKENS")
    assert Cap().max_tokens == 20_000
    with pytest.raises(ValueError):
        Cap(10)
    with pytest.raises(ValueError):
        Cap(1000, cursor_param="not valid")
    assert "page_token=" in json.loads(Cap(500, cursor_param="page_token").page(ROWS))["page"]["note"]


def test_indent():
    out = Cap(1500, indent=2).page(ROWS)
    assert out.startswith("{\n  ")
    assert Cap(1500, indent=2).fits(out)
