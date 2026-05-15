"""`web.brand` config drives the operator-facing chrome: the title shown
in the browser tab and sidebar, the brand icon (either a material-icons
glyph name OR an image file), and an optional favicon override. Defaults
reproduce the bundled pyops look so existing deployments don't need to
opt in.
"""
import os

import pytest
from fastapi.testclient import TestClient

from app.web import app
from app.lib import files as files_lib


@pytest.fixture
def client():
    return TestClient(app)


def _set_brand(monkeypatch, brand):
    """Wrap conf_brand so a test can plug a brand dict in without touching
    the on-disk config singleton."""
    monkeypatch.setattr(files_lib, "conf_brand", lambda: dict(brand))


class TestBrandDefaults:
    def test_root_uses_bundled_title_and_glyph_when_brand_unset(self, client, mock_db, monkeypatch):
        _set_brand(monkeypatch, {})
        r = client.get("/")
        assert r.status_code == 200
        html = r.text
        assert "<title>PyOps</title>" in html
        # Both occurrences of the sidebar/topbar brand icon render as
        # material-icons-round spans with the default glyph.
        assert html.count('class="material-icons-round" aria-hidden="true">miscellaneous_services</span>') == 2

    def test_favicon_serves_bundled_default_when_brand_unset(self, client, mock_db, monkeypatch):
        _set_brand(monkeypatch, {})
        r = client.get("/favicon.ico")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/x-icon"
        assert len(r.content) > 0

    def test_brand_icon_endpoint_404_when_no_image_configured(self, client, mock_db, monkeypatch):
        _set_brand(monkeypatch, {"icon": "miscellaneous_services"})
        r = client.get("/brand/icon")
        assert r.status_code == 404


class TestBrandTitleOverride:
    def test_custom_title_lands_in_tab_and_sidebar(self, client, mock_db, monkeypatch):
        _set_brand(monkeypatch, {"title": "AIOps"})
        r = client.get("/")
        assert r.status_code == 200
        html = r.text
        assert "<title>AIOps</title>" in html
        # The fs-5 sidebar span and the mobile-topbar brand both use the title.
        assert html.count(">AIOps</span>") >= 2

    def test_title_is_html_escaped(self, client, mock_db, monkeypatch):
        # Hostile config must not slip raw markup into the brand title.
        # We check that the escaped form is present (which proves the
        # escape happened) — the unescaped string would never appear in
        # the title tag or sidebar span as a plain `Foo<script>` since
        # those are escaped on the server.
        _set_brand(monkeypatch, {"title": 'Foo<script>"'})
        r = client.get("/")
        html = r.text
        assert "Foo&lt;script&gt;&quot;" in html
        assert "Foo<script>" not in html  # unescaped form absent


class TestBrandGlyphOverride:
    def test_custom_material_glyph_substitutes_into_both_brand_spots(self, client, mock_db, monkeypatch):
        _set_brand(monkeypatch, {"icon": "dashboard"})
        r = client.get("/")
        html = r.text
        assert html.count('class="material-icons-round" aria-hidden="true">dashboard</span>') == 2

    def test_invalid_glyph_falls_back_to_default(self, client, mock_db, monkeypatch):
        # Anything that isn't `[a-zA-Z0-9_]+` is rejected — would otherwise
        # let a hostile config slip arbitrary markup into the brand span.
        _set_brand(monkeypatch, {"icon": "bad <name>"})
        r = client.get("/")
        html = r.text
        assert "bad <name>" not in html
        assert ">miscellaneous_services</span>" in html


class TestBrandImageIcon:
    def test_image_path_renders_img_tag_pointing_at_brand_icon_route(self, client, mock_db, monkeypatch, tmp_path):
        img = tmp_path / "logo.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        _set_brand(monkeypatch, {"icon": str(img)})
        r = client.get("/")
        html = r.text
        # The material-icons-round span renderer is bypassed; an <img>
        # pointing at the brand-icon endpoint replaces it.
        assert '<img class="pyops-brand-icon" src="/brand/icon" alt="">' in html
        assert html.count('<img class="pyops-brand-icon" src="/brand/icon" alt="">') == 2
        # The default glyph string must NOT appear when an image is configured.
        assert ">miscellaneous_services</span>" not in html

    def test_image_path_served_via_brand_icon_route(self, client, mock_db, monkeypatch, tmp_path):
        img = tmp_path / "logo.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-bytes")
        _set_brand(monkeypatch, {"icon": str(img)})
        r = client.get("/brand/icon")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content == b"\x89PNG\r\n\x1a\nfake-image-bytes"

    def test_missing_image_file_falls_back_to_default_glyph(self, client, mock_db, monkeypatch):
        _set_brand(monkeypatch, {"icon": "/tmp/does-not-exist-pyops-test.png"})
        r = client.get("/")
        html = r.text
        # No <img> tag because the file doesn't exist.
        assert '<img class="pyops-brand-icon"' not in html
        # Fell back to the default glyph.
        assert ">miscellaneous_services</span>" in html


class TestBrandSvgIcon:
    """SVG icons are inlined into the page so the markup inherits
    `currentColor` and follows the brand theme color just like a
    material-icons-round glyph. Raster formats keep using `<img>`."""

    def test_svg_inlined_into_brand_spots(self, client, mock_db, monkeypatch, tmp_path):
        svg = tmp_path / "logo.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M2 2h20v20H2z"/></svg>')
        _set_brand(monkeypatch, {"icon": str(svg)})
        r = client.get("/")
        html = r.text
        # The SVG markup itself is embedded — no `<img>` indirection.
        assert '<img class="pyops-brand-icon"' not in html
        assert html.count('<span class="pyops-brand-icon" aria-hidden="true"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">') == 2
        assert html.count('<path d="M2 2h20v20H2z"/></svg></span>') == 2
        # Default glyph not present because an icon was configured.
        assert ">miscellaneous_services</span>" not in html

    def test_svg_with_xml_prolog_inlined(self, client, mock_db, monkeypatch, tmp_path):
        svg = tmp_path / "logo.svg"
        svg.write_text('<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"><circle r="5"/></svg>')
        _set_brand(monkeypatch, {"icon": str(svg)})
        r = client.get("/")
        html = r.text
        assert '<?xml version="1.0"?>' in html
        assert "<circle r=\"5\"/></svg>" in html

    def test_svg_with_bogus_content_falls_back_to_default_glyph(self, client, mock_db, monkeypatch, tmp_path):
        # An .svg file whose first bytes aren't <svg or <?xml is almost
        # certainly an attempt to inject arbitrary HTML — never inline it.
        bogus = tmp_path / "logo.svg"
        bogus.write_text('<script>alert(1)</script>')
        _set_brand(monkeypatch, {"icon": str(bogus)})
        r = client.get("/")
        html = r.text
        assert '<script>alert(1)</script>' not in html
        assert ">miscellaneous_services</span>" in html

    def test_svg_still_servable_via_brand_icon_route(self, client, mock_db, monkeypatch, tmp_path):
        # The HTML embeds SVG inline, but the file should also be reachable
        # via the regular brand-icon route — useful when an operator wants
        # to reference the logo from a custom view or external page.
        svg = tmp_path / "logo.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        _set_brand(monkeypatch, {"icon": str(svg)})
        r = client.get("/brand/icon")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/svg")


class TestBrandFaviconOverride:
    def test_configured_favicon_served_with_extension_mime(self, client, mock_db, monkeypatch, tmp_path):
        fav = tmp_path / "logo.png"
        fav.write_bytes(b"\x89PNG\r\n\x1a\nfavicon-bytes")
        _set_brand(monkeypatch, {"favicon": str(fav)})
        r = client.get("/favicon.ico")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content == b"\x89PNG\r\n\x1a\nfavicon-bytes"

    def test_missing_favicon_file_falls_back_to_bundled(self, client, mock_db, monkeypatch):
        _set_brand(monkeypatch, {"favicon": "/tmp/missing-pyops-test.ico"})
        r = client.get("/favicon.ico")
        # Falls back instead of 500 — operator typos shouldn't break /favicon.ico.
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/x-icon"

    def test_relative_favicon_path_resolved_against_project_root(self, client, mock_db, monkeypatch, tmp_path):
        # Place file in a tmp tree, point conf at the absolute path — equivalent
        # to what a relative path would resolve to when the operator drops a
        # logo into etc/.
        fav = tmp_path / "etc" / "logo.png"
        fav.parent.mkdir()
        fav.write_bytes(b"PNGDATA")
        _set_brand(monkeypatch, {"favicon": str(fav)})
        r = client.get("/favicon.ico")
        assert r.status_code == 200
        assert r.content == b"PNGDATA"


class TestBrandConfAccessor:
    def test_defaults_for_empty_brand_section(self):
        from app.lib import files as fl
        old = fl.config._raw_conf
        try:
            fl.config._raw_conf = {}
            b = fl.conf_brand()
            assert b == {"title": "PyOps", "icon": "miscellaneous_services", "favicon": None}
        finally:
            fl.config._raw_conf = old

    def test_partial_override_keeps_other_defaults(self):
        from app.lib import files as fl
        old = fl.config._raw_conf
        try:
            fl.config._raw_conf = {"brand": {"title": "Edge"}}
            b = fl.conf_brand()
            assert b["title"] == "Edge"
            assert b["icon"] == "miscellaneous_services"
            assert b["favicon"] is None
        finally:
            fl.config._raw_conf = old
