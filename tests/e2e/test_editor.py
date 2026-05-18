"""
Editor and JS Library E2E tests.

Covers:
  - Opening editors from Manager UI (popup windows with Ace Editor)
  - Editing and saving code via the editor UI
  - JS library endpoint serving JavaScript with correct content-type
  - HTML views loading JS library scriptlets via <script src="...">
  - Full integration: jslib function callable from view body after loading via head
"""

import time

import pytest


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SET_NAME = "editor_e2e"
TASK_NAME = "sample_task"
VIEW_NAME = "sample_view"
JSLIB_NAME = "utils"
CTRL_NAME = "jstest"
LIB_NAME = "sample_lib"
CTRL_SIMPLE_NAME = "simple_ctrl"
MD_NAME = "README"

# Head view loads the JS library
HEAD_VIEW = "jstest_head"
# Menu view — minimal
MENU_VIEW = "jstest_menu"
# Body view calls a function defined in the JS library
BODY_VIEW = "jstest_body"

BASE_URL = "http://127.0.0.1:18321"

TASK_CODE = 'print("hello from task")\n'
VIEW_CODE = "<h1>Sample View</h1>\n"

JSLIB_CODE = """\
function jslib_greet(name) {
    return "hello " + name + " from jslib";
}

function jslib_add(a, b) {
    return a + b;
}
"""

UPDATED_TASK_CODE = 'print("updated task code")\n'
UPDATED_VIEW_CODE = "<h1>Updated View</h1>\n"
UPDATED_JSLIB_CODE = """\
function jslib_greet(name) {
    return "updated hello " + name;
}
"""

LIB_CODE = """\
def lib_multiply(a, b):
    return a * b


def lib_greet(name):
    return f"greetings, {name}!"
"""

UPDATED_LIB_CODE = """\
def lib_multiply(a, b):
    return a * b * 2


def lib_greet(name):
    return f"updated greetings, {name}!"
"""

CTRL_CODE_SIMPLE = (
    f'name = "Simple Controller"\n'
    f'set = "{SET_NAME}"\n'
    f'service = ""\n'
    f'head = ""\n'
    f'menu = ""\n'
    f'body = ""\n'
    f'debug = False\n'
)

MD_CODE = "# Sample Readme\n\nThis is a test.\n"
UPDATED_MD_CODE = "# Updated Readme\n\nThis content was updated.\n"

# Controller + views for jslib integration test
CTRL_CODE = f"""\
name = "JS Test Page"
set = "{SET_NAME}"
service = ""
head = "{HEAD_VIEW}"
menu = "{MENU_VIEW}"
body = "{BODY_VIEW}"
debug = False
"""

HEAD_CODE = f'<script src="/scriptlet/jslib/{SET_NAME}/{JSLIB_NAME}"></script>'
MENU_CODE = '<li class="nav-item"><span class="nav-link text-light">JS Test</span></li>'
BODY_CODE = """\
<div class="container mt-4">
    <h1 id="jslib-heading">JS Lib Test</h1>
    <p id="jslib-greet"></p>
    <p id="jslib-add"></p>
    <script>
        document.getElementById("jslib-greet").textContent = jslib_greet("e2e");
        document.getElementById("jslib-add").textContent = "sum=" + jslib_add(3, 4);
    </script>
</div>
"""


# ---------------------------------------------------------------------------
# Setup helper
# ---------------------------------------------------------------------------


def _setup_basic(api, set_factory, commit_set):
    """Create set with a task, view, and jslibrary scriptlet via API."""
    set_factory(SET_NAME)

    for name, type_, code in [
        (TASK_NAME, "task", TASK_CODE),
        (VIEW_NAME, "view", VIEW_CODE),
        (JSLIB_NAME, "jslibrary", JSLIB_CODE),
    ]:
        resp = api.put(
            "/scriptlet/inventory/item",
            json={"set": SET_NAME, "name": name, "type": type_},
        )
        assert resp.status_code == 200, f"Create {name} failed: {resp.text}"
        resp = api.post(
            "/scriptlet/inventory/draft",
            json={"set": SET_NAME, "name": name, "code": code},
        )
        assert resp.status_code == 200, f"Draft {name} failed: {resp.text}"
    commit_set(SET_NAME, "add basic scriptlets")


def _setup_jslib_integration(api, set_factory, commit_set):
    """Create the full controller + views + jslib stack for integration tests."""
    _setup_basic(api, set_factory, commit_set)

    for name, type_, code in [
        (CTRL_NAME, "controller", CTRL_CODE),
        (HEAD_VIEW, "view", HEAD_CODE),
        (MENU_VIEW, "view", MENU_CODE),
        (BODY_VIEW, "view", BODY_CODE),
    ]:
        resp = api.put(
            "/scriptlet/inventory/item",
            json={"set": SET_NAME, "name": name, "type": type_},
        )
        assert resp.status_code == 200, f"Create {name} failed: {resp.text}"
        resp = api.post(
            "/scriptlet/inventory/draft",
            json={"set": SET_NAME, "name": name, "code": code},
        )
        assert resp.status_code == 200, f"Draft {name} failed: {resp.text}"
    commit_set(SET_NAME, "add jslib-integration scriptlets")


def _setup_library(api, set_factory, commit_set):
    """Create set with a library scriptlet."""
    set_factory(SET_NAME)
    resp = api.put(
        "/scriptlet/inventory/item",
        json={"set": SET_NAME, "name": LIB_NAME, "type": "library"},
    )
    assert resp.status_code == 200, f"Create {LIB_NAME} failed: {resp.text}"
    resp = api.post(
        "/scriptlet/inventory/draft",
        json={"set": SET_NAME, "name": LIB_NAME, "code": LIB_CODE},
    )
    assert resp.status_code == 200, f"Draft {LIB_NAME} failed: {resp.text}"
    commit_set(SET_NAME, f"add {LIB_NAME}")


def _setup_controller(api, set_factory, commit_set):
    """Create set with a simple controller scriptlet."""
    set_factory(SET_NAME)
    resp = api.put(
        "/scriptlet/inventory/item",
        json={"set": SET_NAME, "name": CTRL_SIMPLE_NAME, "type": "controller"},
    )
    assert resp.status_code == 200, f"Create {CTRL_SIMPLE_NAME} failed: {resp.text}"
    resp = api.post(
        "/scriptlet/inventory/draft",
        json={"set": SET_NAME, "name": CTRL_SIMPLE_NAME, "code": CTRL_CODE_SIMPLE},
    )
    assert resp.status_code == 200, f"Draft {CTRL_SIMPLE_NAME} failed: {resp.text}"
    commit_set(SET_NAME, f"add {CTRL_SIMPLE_NAME}")


def _setup_markdown(api, set_factory, commit_set):
    """Create set (README.md is auto-created by get_repo_path) and write initial content.

    README.md is always created when a new set is initialised, so a PUT is not needed.
    """
    set_factory(SET_NAME)
    resp = api.post(
        "/scriptlet/inventory/draft",
        json={"set": SET_NAME, "name": MD_NAME, "code": MD_CODE},
    )
    assert resp.status_code == 200, f"Draft README failed: {resp.text}"
    commit_set(SET_NAME, "update README")


# ---------------------------------------------------------------------------
# UI navigation helpers
# ---------------------------------------------------------------------------


def _nav_manager(e2e_page):
    e2e_page.locator('[data-testid="menu-link-scriptlet_manager"]').click()
    frame = e2e_page.frame_locator('[data-testid="content-frame"]')
    frame.locator('[data-testid="manager-navbar"]').wait_for(state="visible", timeout=10_000)
    return frame


def _nav_sets(e2e_page):
    e2e_page.locator('[data-testid="menu-link-scriptlet_sets"]').click()
    frame = e2e_page.frame_locator('[data-testid="content-frame"]')
    frame.locator('[data-testid="sets-create"]').wait_for(state="visible", timeout=10_000)
    return frame


def _open_editor_popup(e2e_page, frame, set_name, name):
    """Click the Edit button in Manager and return the popup Page."""
    frame.locator(f'[data-testid="scriptlet-row-{set_name}-{name}"]').wait_for(
        state="visible", timeout=10_000
    )
    with e2e_page.expect_popup() as popup_info:
        frame.locator(f'[data-testid="scriptlet-edit-{set_name}-{name}"]').click()
    editor_page = popup_info.value
    # Use "load" instead of "networkidle" — editor's save_draft() timer
    # fires every 1s, preventing networkidle from ever resolving.
    editor_page.wait_for_load_state("load")
    return editor_page


def _wait_for_ace(editor_page, timeout=10_000):
    """Wait for Ace editor to initialise and load code."""
    editor_page.wait_for_function(
        "typeof ace !== 'undefined' && ace.edit('editor') && ace.edit('editor').getValue().length > 0",
        timeout=timeout,
    )


def _get_ace_value(editor_page):
    """Return the current Ace editor text."""
    return editor_page.evaluate("ace.edit('editor').getValue()")


def _set_ace_value(editor_page, code):
    """Replace the Ace editor content and wait for the dirty flag to clear.

    The editor no longer has an explicit save button — drafts auto-save
    every 1s when the dirty flag is set, then the loop clears it. We
    wait for that clear here so callers can assume the draft has hit
    the server."""
    editor_page.evaluate("(code) => ace.edit('editor').setValue(code)", code)
    # editor_dirty is set true on change and false after the autosave loop
    # fires; this gives us a deterministic handle on "draft persisted".
    editor_page.wait_for_function("!editor_dirty", timeout=5_000)


def _click_save(editor_page):
    """No-op kept for call-site stability — `_set_ace_value` already
    waits for the autosave loop to land the draft."""
    pass


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------


def _cleanup_scriptlets(e2e_page, names):
    """Delete listed scriptlets from Manager, verify rows gone.

    Skips items whose delete button does not exist (e.g. jslibrary has no
    actions in the Manager UI).  Set deletion handles those.
    """
    frame = _nav_manager(e2e_page)
    frame.locator('[data-testid="manager-refresh"]').click()
    time.sleep(0.5)
    for name in names:
        row = frame.locator(f'[data-testid="scriptlet-row-{SET_NAME}-{name}"]')
        if row.count() == 0 or not row.is_visible():
            continue
        delete_btn = frame.locator(f'[data-testid="scriptlet-delete-{SET_NAME}-{name}"]')
        if delete_btn.count() == 0:
            continue
        e2e_page.once("dialog", lambda d: d.accept())
        delete_btn.click()
        row.wait_for(state="hidden", timeout=5_000)


def _cleanup_set(e2e_page):
    """Remove the test set from Sets page and verify gone."""
    frame = _nav_sets(e2e_page)
    frame.locator('[data-testid="sets-refresh"]').click()
    time.sleep(0.5)
    row = frame.locator(f'[data-testid="set-row-{SET_NAME}"]')
    if row.count() > 0 and row.is_visible():
        e2e_page.once("dialog", lambda d: d.accept())
        frame.locator(f'[data-testid="set-remove-{SET_NAME}"]').click()
        # DELETE blocks on the bootstrap lock if the per-set venv pip install
        # is still running (lazy ensure_set_environment kicked off by the runner spawn path).
        # Cold devpi cache + aarch64 wheels can push that to ~30s.
        row.wait_for(state="hidden", timeout=60_000)
    assert frame.locator(f'[data-testid="set-row-{SET_NAME}"]').count() == 0


def _full_cleanup(e2e_page, names):
    """Delete scriptlets and set via UI."""
    _cleanup_scriptlets(e2e_page, names)
    _cleanup_set(e2e_page)


# ---------------------------------------------------------------------------
# Tests: editor workflow
# ---------------------------------------------------------------------------


BASIC_NAMES = [TASK_NAME, VIEW_NAME, JSLIB_NAME]
ALL_NAMES = [TASK_NAME, VIEW_NAME, JSLIB_NAME, CTRL_NAME, HEAD_VIEW, MENU_VIEW, BODY_VIEW]
# README is intentionally excluded — its delete button is disabled in Manager;
# set deletion (in _full_cleanup) removes it implicitly.


class TestEditorWorkflow:
    """Verify that editors open from Manager, load code, and save changes."""

    def test_task_editor_opens_via_manager(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Clicking Edit on a task opens the task editor with correct code."""
        _setup_basic(api_anon, set_factory, commit_set)
        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()

        editor_page = _open_editor_popup(e2e_page, frame, SET_NAME, TASK_NAME)
        editor_page.locator('[data-testid="editor-task-navbar"]').wait_for(
            state="visible", timeout=10_000
        )
        _wait_for_ace(editor_page)

        code = _get_ace_value(editor_page)
        assert "hello from task" in code

        editor_page.close()
        _full_cleanup(e2e_page, BASIC_NAMES)
        pyops_server.assert_no_unexpected_errors()

    def test_view_editor_opens_via_manager(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Clicking Edit on a view opens the view editor with correct HTML."""
        _setup_basic(api_anon, set_factory, commit_set)
        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()

        editor_page = _open_editor_popup(e2e_page, frame, SET_NAME, VIEW_NAME)
        editor_page.locator('[data-testid="editor-view-navbar"]').wait_for(
            state="visible", timeout=10_000
        )
        _wait_for_ace(editor_page)

        code = _get_ace_value(editor_page)
        assert "Sample View" in code

        editor_page.close()
        _full_cleanup(e2e_page, BASIC_NAMES)
        pyops_server.assert_no_unexpected_errors()

    def test_jslibrary_editor_opens_directly(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """JS library editor loads via direct URL with correct JavaScript code."""
        _setup_basic(api_anon, set_factory, commit_set)

        # jslibrary has no edit button in Manager; navigate directly.
        editor_page = e2e_page.context.new_page()
        editor_page.goto(
            f"{BASE_URL}/scriptlet/editor_jslibrary?set={SET_NAME}&name={JSLIB_NAME}"
        )
        editor_page.wait_for_load_state("load")
        editor_page.locator('[data-testid="editor-jslibrary-navbar"]').wait_for(
            state="visible", timeout=10_000
        )
        _wait_for_ace(editor_page)

        code = _get_ace_value(editor_page)
        assert "jslib_greet" in code
        assert "jslib_add" in code

        editor_page.close()
        _full_cleanup(e2e_page, BASIC_NAMES)
        pyops_server.assert_no_unexpected_errors()

    def test_edit_and_save_task_code(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Modify task code in the editor and save; verify updated content."""
        _setup_basic(api_anon, set_factory, commit_set)
        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()

        editor_page = _open_editor_popup(e2e_page, frame, SET_NAME, TASK_NAME)
        _wait_for_ace(editor_page)

        _set_ace_value(editor_page, UPDATED_TASK_CODE)
        _click_save(editor_page)

        # Reload editor to verify persisted content.
        editor_page.reload()
        editor_page.wait_for_load_state("load")
        _wait_for_ace(editor_page)
        code = _get_ace_value(editor_page)
        assert "updated task code" in code

        editor_page.close()
        _full_cleanup(e2e_page, BASIC_NAMES)
        pyops_server.assert_no_unexpected_errors()

    def test_edit_and_save_view_code(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Modify view HTML in the editor and save; verify updated content."""
        _setup_basic(api_anon, set_factory, commit_set)
        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()

        editor_page = _open_editor_popup(e2e_page, frame, SET_NAME, VIEW_NAME)
        _wait_for_ace(editor_page)

        _set_ace_value(editor_page, UPDATED_VIEW_CODE)
        _click_save(editor_page)

        editor_page.reload()
        editor_page.wait_for_load_state("load")
        _wait_for_ace(editor_page)
        code = _get_ace_value(editor_page)
        assert "Updated View" in code

        editor_page.close()
        _full_cleanup(e2e_page, BASIC_NAMES)
        pyops_server.assert_no_unexpected_errors()

    def test_edit_and_save_jslibrary_code(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Modify JS library code in the editor and save; verify updated content."""
        _setup_basic(api_anon, set_factory, commit_set)

        editor_page = e2e_page.context.new_page()
        editor_page.goto(
            f"{BASE_URL}/scriptlet/editor_jslibrary?set={SET_NAME}&name={JSLIB_NAME}"
        )
        editor_page.wait_for_load_state("load")
        _wait_for_ace(editor_page)

        _set_ace_value(editor_page, UPDATED_JSLIB_CODE)
        _click_save(editor_page)

        editor_page.reload()
        editor_page.wait_for_load_state("load")
        _wait_for_ace(editor_page)
        code = _get_ace_value(editor_page)
        assert "updated hello" in code

        editor_page.close()
        _full_cleanup(e2e_page, BASIC_NAMES)
        pyops_server.assert_no_unexpected_errors()

    def test_editor_revert_discards_changes(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Clicking Revert restores the last saved version."""
        _setup_basic(api_anon, set_factory, commit_set)
        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()

        editor_page = _open_editor_popup(e2e_page, frame, SET_NAME, TASK_NAME)
        _wait_for_ace(editor_page)

        # Make a draft change (don't save explicitly).
        _set_ace_value(editor_page, 'print("draft only")\n')
        # Wait for the autosave to fire so the draft is persisted.
        editor_page.wait_for_function("!editor_dirty", timeout=5_000)

        # Click Revert — rollback does DELETE + refresh.
        editor_page.locator('[data-testid="editor-revert"]').click()
        # Wait until the editor content no longer contains draft text.
        editor_page.wait_for_function(
            '!ace.edit("editor").getValue().includes("draft only")',
            timeout=10_000,
        )
        code = _get_ace_value(editor_page)
        assert "hello from task" in code
        assert "draft only" not in code

        editor_page.close()
        _full_cleanup(e2e_page, BASIC_NAMES)
        pyops_server.assert_no_unexpected_errors()

    def test_library_editor_opens_via_manager(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Clicking Edit on a library scriptlet opens the library editor with its code."""
        _setup_library(api_anon, set_factory, commit_set)
        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()

        editor_page = _open_editor_popup(e2e_page, frame, SET_NAME, LIB_NAME)
        editor_page.locator('[data-testid="editor-library-navbar"]').wait_for(
            state="visible", timeout=10_000
        )
        _wait_for_ace(editor_page)

        code = _get_ace_value(editor_page)
        assert "lib_multiply" in code
        assert "lib_greet" in code

        editor_page.close()
        _full_cleanup(e2e_page, [LIB_NAME])
        pyops_server.assert_no_unexpected_errors()

    def test_controller_editor_opens_via_manager(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Clicking Edit on a controller opens the shared library editor with its code."""
        _setup_controller(api_anon, set_factory, commit_set)
        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()

        editor_page = _open_editor_popup(e2e_page, frame, SET_NAME, CTRL_SIMPLE_NAME)
        editor_page.locator('[data-testid="editor-library-navbar"]').wait_for(
            state="visible", timeout=10_000
        )
        _wait_for_ace(editor_page)

        code = _get_ace_value(editor_page)
        assert "Simple Controller" in code

        editor_page.close()
        _full_cleanup(e2e_page, [CTRL_SIMPLE_NAME])
        pyops_server.assert_no_unexpected_errors()

    def test_edit_and_save_library_code(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Modify library code in the editor and save; verify updated content."""
        _setup_library(api_anon, set_factory, commit_set)
        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()

        editor_page = _open_editor_popup(e2e_page, frame, SET_NAME, LIB_NAME)
        _wait_for_ace(editor_page)

        _set_ace_value(editor_page, UPDATED_LIB_CODE)
        _click_save(editor_page)

        editor_page.reload()
        editor_page.wait_for_load_state("load")
        _wait_for_ace(editor_page)
        code = _get_ace_value(editor_page)
        assert "updated greetings" in code

        editor_page.close()
        _full_cleanup(e2e_page, [LIB_NAME])
        pyops_server.assert_no_unexpected_errors()

    def test_markdown_editor_opens_via_manager(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Clicking Edit on README opens the markdown editor with its content."""
        _setup_markdown(api_anon, set_factory, commit_set)
        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()

        editor_page = _open_editor_popup(e2e_page, frame, SET_NAME, MD_NAME)
        editor_page.locator('[data-testid="editor-md-navbar"]').wait_for(
            state="visible", timeout=10_000
        )
        _wait_for_ace(editor_page)

        code = _get_ace_value(editor_page)
        assert "Sample Readme" in code

        editor_page.close()
        # README delete button is disabled; set deletion handles cleanup.
        _full_cleanup(e2e_page, [])
        pyops_server.assert_no_unexpected_errors()

    def test_edit_and_save_markdown_code(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Modify markdown content in the editor and save; verify updated content."""
        _setup_markdown(api_anon, set_factory, commit_set)
        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()

        editor_page = _open_editor_popup(e2e_page, frame, SET_NAME, MD_NAME)
        _wait_for_ace(editor_page)

        _set_ace_value(editor_page, UPDATED_MD_CODE)
        _click_save(editor_page)

        editor_page.reload()
        editor_page.wait_for_load_state("load")
        _wait_for_ace(editor_page)
        code = _get_ace_value(editor_page)
        assert "Updated Readme" in code

        editor_page.close()
        _full_cleanup(e2e_page, [])
        pyops_server.assert_no_unexpected_errors()


# ---------------------------------------------------------------------------
# Tests: JS library endpoint
# ---------------------------------------------------------------------------


class TestJsLibEndpoint:
    """Verify the /scriptlet/jslib/{set}/{name} endpoint."""

    def test_jslib_serves_javascript(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """JS library endpoint returns JavaScript with correct content-type."""
        _setup_basic(api_anon, set_factory, commit_set)

        # Fetch via browser to check response.
        result = e2e_page.evaluate(
            """async (url) => {
                const resp = await fetch(url);
                const text = await resp.text();
                const ct = resp.headers.get("content-type");
                return { status: resp.status, text: text, contentType: ct };
            }""",
            f"/scriptlet/jslib/{SET_NAME}/{JSLIB_NAME}",
        )

        assert result["status"] == 200
        assert "javascript" in result["contentType"]
        assert "jslib_greet" in result["text"]
        assert "jslib_add" in result["text"]

        _full_cleanup(e2e_page, BASIC_NAMES)
        pyops_server.assert_no_unexpected_errors()

    def test_jslib_nonexistent_returns_404(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Requesting a nonexistent JS library returns 404."""
        _setup_basic(api_anon, set_factory, commit_set)

        result = e2e_page.evaluate(
            """async (url) => {
                const resp = await fetch(url);
                return { status: resp.status };
            }""",
            f"/scriptlet/jslib/{SET_NAME}/nonexistent",
        )

        assert result["status"] == 404

        _full_cleanup(e2e_page, BASIC_NAMES)
        pyops_server.assert_no_unexpected_errors()

    def test_jslib_wrong_type_returns_400(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Requesting a non-JS scriptlet via jslib endpoint returns 400."""
        _setup_basic(api_anon, set_factory, commit_set)

        # TASK_NAME is a task, not jslibrary.
        result = e2e_page.evaluate(
            """async (url) => {
                const resp = await fetch(url);
                return { status: resp.status };
            }""",
            f"/scriptlet/jslib/{SET_NAME}/{TASK_NAME}",
        )

        assert result["status"] == 400

        _full_cleanup(e2e_page, BASIC_NAMES)
        pyops_server.assert_no_unexpected_errors()


# ---------------------------------------------------------------------------
# Tests: HTML view + JS library integration
# ---------------------------------------------------------------------------


class TestJsLibIntegration:
    """Verify that HTML views can load and execute JS library scriptlets."""

    def test_view_loads_jslib_functions(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Controller page with <script src="/scriptlet/jslib/..."> executes JS."""
        _setup_jslib_integration(api_anon, set_factory, commit_set)

        e2e_page.goto(f"{BASE_URL}/controller/{SET_NAME}/{CTRL_NAME}")
        e2e_page.wait_for_load_state("networkidle")

        # The body view calls jslib_greet("e2e") and jslib_add(3, 4).
        greet = e2e_page.locator("#jslib-greet")
        greet.wait_for(state="visible", timeout=10_000)
        assert greet.inner_text() == "hello e2e from jslib"

        add_result = e2e_page.locator("#jslib-add")
        assert add_result.inner_text() == "sum=7"

        # Navigate back to app for cleanup.
        e2e_page.goto(BASE_URL)
        e2e_page.wait_for_load_state("networkidle")
        _full_cleanup(e2e_page, ALL_NAMES)
        pyops_server.assert_no_unexpected_errors()

    def test_updated_jslib_reflects_in_controller(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """After editing jslib code, the controller page uses the updated version."""
        _setup_jslib_integration(api_anon, set_factory, commit_set)

        # Verify original.
        e2e_page.goto(f"{BASE_URL}/controller/{SET_NAME}/{CTRL_NAME}")
        e2e_page.wait_for_load_state("networkidle")
        assert e2e_page.locator("#jslib-greet").inner_text() == "hello e2e from jslib"

        # Update jslib code via editor.
        editor_page = e2e_page.context.new_page()
        editor_page.goto(
            f"{BASE_URL}/scriptlet/editor_jslibrary?set={SET_NAME}&name={JSLIB_NAME}"
        )
        editor_page.wait_for_load_state("load")
        _wait_for_ace(editor_page)

        new_code = JSLIB_CODE.replace("hello ", "howdy ")
        _set_ace_value(editor_page, new_code)
        _click_save(editor_page)
        editor_page.close()
        # The editor only drafts now — the jslib route serves committed
        # content by default. Promote the draft into HEAD so the
        # controller picks up the updated jslib.
        commit_set(SET_NAME, "update jslib")

        # Reload controller and verify updated output.
        e2e_page.goto(f"{BASE_URL}/controller/{SET_NAME}/{CTRL_NAME}")
        e2e_page.wait_for_load_state("networkidle")
        assert e2e_page.locator("#jslib-greet").inner_text() == "howdy e2e from jslib"

        e2e_page.goto(BASE_URL)
        e2e_page.wait_for_load_state("networkidle")
        _full_cleanup(e2e_page, ALL_NAMES)
        pyops_server.assert_no_unexpected_errors()

    def test_jslib_available_across_multiple_page_loads(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """JS library functions work consistently across page reloads."""
        _setup_jslib_integration(api_anon, set_factory, commit_set)

        for _ in range(3):
            e2e_page.goto(f"{BASE_URL}/controller/{SET_NAME}/{CTRL_NAME}")
            e2e_page.wait_for_load_state("networkidle")
            assert e2e_page.locator("#jslib-greet").inner_text() == "hello e2e from jslib"
            assert e2e_page.locator("#jslib-add").inner_text() == "sum=7"

        e2e_page.goto(BASE_URL)
        e2e_page.wait_for_load_state("networkidle")
        _full_cleanup(e2e_page, ALL_NAMES)
        pyops_server.assert_no_unexpected_errors()


# ---------------------------------------------------------------------------
# Tests: editor interactive actions (Run, Cancel, Clear Logs, Font Size)
# ---------------------------------------------------------------------------


def _ace_value(editor_page, element_id):
    return editor_page.evaluate(f"ace.edit('{element_id}').getValue()")


def _ace_font_size(editor_page, element_id):
    return editor_page.evaluate(
        f"String(ace.edit('{element_id}').getOption('fontSize'))"
    )


class TestEditorInteractiveActions:
    """Exercise Run/Cancel/Clear Logs/Font Size in the task editor."""

    def test_editor_run_populates_stdout_panel(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Clicking Run executes the draft code and shows output in the stdout Ace panel."""
        _setup_basic(api_anon, set_factory, commit_set)
        # Replace default code with something that prints a deterministic string.
        api_anon.post(
            "/scriptlet/inventory/draft",
            json={
                "set": SET_NAME,
                "name": TASK_NAME,
                "code": 'print("editor_run_marker_42")\n',
            },
        )
        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()

        editor_page = _open_editor_popup(e2e_page, frame, SET_NAME, TASK_NAME)
        _wait_for_ace(editor_page)

        editor_page.locator('[data-testid="editor-run"]').click()

        # Wait until the job poller has written the expected marker into #out.
        editor_page.wait_for_function(
            "ace.edit('out').getValue().includes('editor_run_marker_42')",
            timeout=30_000,
        )
        # Run button re-enabled once terminal state is observed.
        editor_page.wait_for_function(
            "!document.querySelector('[data-testid=\"editor-run\"]').disabled",
            timeout=10_000,
        )

        editor_page.close()
        _full_cleanup(e2e_page, BASIC_NAMES)
        pyops_server.assert_no_unexpected_errors()

    def test_editor_clear_logs_empties_output_panels(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Clear Logs empties both stdout and stderr Ace panels."""
        _setup_basic(api_anon, set_factory, commit_set)
        api_anon.post(
            "/scriptlet/inventory/draft",
            json={
                "set": SET_NAME,
                "name": TASK_NAME,
                "code": 'print("before_clear")\n',
            },
        )
        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()

        editor_page = _open_editor_popup(e2e_page, frame, SET_NAME, TASK_NAME)
        _wait_for_ace(editor_page)

        editor_page.locator('[data-testid="editor-run"]').click()
        editor_page.wait_for_function(
            "ace.edit('out').getValue().includes('before_clear')",
            timeout=30_000,
        )
        editor_page.wait_for_function(
            "!document.querySelector('[data-testid=\"editor-run\"]').disabled",
            timeout=10_000,
        )

        editor_page.locator('[data-testid="editor-clear-logs"]').click()
        editor_page.wait_for_function(
            "ace.edit('out').getValue() === '' && ace.edit('errout').getValue() === ''",
            timeout=5_000,
        )
        assert _ace_value(editor_page, "out") == ""
        assert _ace_value(editor_page, "errout") == ""

        editor_page.close()
        _full_cleanup(e2e_page, BASIC_NAMES)
        pyops_server.assert_no_unexpected_errors()

    def test_editor_cancel_stops_running_draft(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Clicking Cancel on a running draft aborts it and re-enables Run."""
        _setup_basic(api_anon, set_factory, commit_set)
        # Long-running draft — sleeps 60s so Cancel has a clear window.
        long_code = "import time\nfor _ in range(120):\n    time.sleep(0.5)\n"
        api_anon.post(
            "/scriptlet/inventory/draft",
            json={"set": SET_NAME, "name": TASK_NAME, "code": long_code},
        )
        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()

        editor_page = _open_editor_popup(e2e_page, frame, SET_NAME, TASK_NAME)
        _wait_for_ace(editor_page)

        editor_page.locator('[data-testid="editor-run"]').click()
        # Cancel should be enabled once a job is in flight.
        editor_page.wait_for_function(
            "!document.querySelector('[data-testid=\"editor-cancel\"]').disabled",
            timeout=10_000,
        )
        editor_page.locator('[data-testid="editor-cancel"]').click()

        # After cancel, Run re-enables, Cancel disables again.
        editor_page.wait_for_function(
            "!document.querySelector('[data-testid=\"editor-run\"]').disabled "
            "&& document.querySelector('[data-testid=\"editor-cancel\"]').disabled",
            timeout=30_000,
        )

        editor_page.close()
        _full_cleanup(e2e_page, BASIC_NAMES)
        pyops_server.assert_no_unexpected_errors()

    def test_editor_font_size_change_propagates_to_ace(self, e2e_page, api_anon, pyops_server, set_factory, commit_set):
        """Changing the Font Size select updates every Ace panel's fontSize."""
        _setup_basic(api_anon, set_factory, commit_set)
        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()

        editor_page = _open_editor_popup(e2e_page, frame, SET_NAME, TASK_NAME)
        _wait_for_ace(editor_page)

        select = editor_page.locator('[data-testid="editor-font-size"]')
        # Pick the largest available option (different from default).
        options = select.evaluate(
            "s => Array.from(s.options).map(o => o.value)"
        )
        assert options, "font-size select had no options"
        target = options[-1]
        select.select_option(target)

        # Every Ace editor on the page should adopt the new size.
        for ace_id in ("editor", "out", "errout", "param"):
            assert _ace_font_size(editor_page, ace_id) == target, (
                f"{ace_id} font did not update: "
                f"got {_ace_font_size(editor_page, ace_id)!r}, expected {target!r}"
            )

        editor_page.close()
        _full_cleanup(e2e_page, BASIC_NAMES)
        pyops_server.assert_no_unexpected_errors()


# ---------------------------------------------------------------------------
# Tests: Manager "Create & Edit" flow
# ---------------------------------------------------------------------------


class TestManagerCreateAndEdit:
    """Verify the Create & Edit button creates the scriptlet AND opens an editor."""

    def test_create_and_edit_opens_task_editor_for_new_scriptlet(
        self, e2e_page, api_anon, pyops_server, set_factory):
        """manager-create-edit creates the scriptlet and pops open the matching editor."""
        set_name = "create_edit_set"
        new_name = "fresh_task"
        set_factory(set_name)

        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()
        frame.locator('[data-testid="manager-create"]').click()
        frame.locator('[data-testid="manager-create-modal"]').wait_for(
            state="visible", timeout=5_000
        )
        frame.locator('[data-testid="manager-create-set"]').select_option(set_name)
        frame.locator('[data-testid="manager-create-name"]').fill(new_name)
        frame.locator('[data-testid="manager-create-type"]').select_option("task")

        # Create & Edit — should create the scriptlet AND open the task editor popup.
        with e2e_page.expect_popup() as popup_info:
            frame.locator('[data-testid="manager-create-edit"]').click()
        editor_page = popup_info.value
        editor_page.wait_for_load_state("load")
        editor_page.locator('[data-testid="editor-task-navbar"]').wait_for(
            state="visible", timeout=10_000
        )
        _wait_for_ace(editor_page)

        # Scriptlet must exist in inventory now.
        items = api_anon.get("/scriptlet/inventory/list").json()
        assert any(
            i["set"] == set_name and i["name"] == new_name and i["type"] == "task"
            for i in items
        ), f"scriptlet not listed: {items}"

        editor_page.close()

        # Cleanup: delete scriptlet + set.
        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()
        row = frame.locator(f'[data-testid="scriptlet-row-{set_name}-{new_name}"]')
        if row.count() > 0 and row.is_visible():
            e2e_page.once("dialog", lambda d: d.accept())
            frame.locator(f'[data-testid="scriptlet-delete-{set_name}-{new_name}"]').click()
            row.wait_for(state="hidden", timeout=5_000)
        # cleanup via tmp_path teardown — set removal endpoint is gone
        pyops_server.assert_no_unexpected_errors()

    @pytest.mark.parametrize(
        "scriptlet_type,expected_navbar",
        [
            ("library", "editor-library-navbar"),
            ("controller", "editor-library-navbar"),  # controller reuses the library editor
            ("view", "editor-view-navbar"),
        ],
    )
    def test_create_and_edit_opens_correct_editor_per_type(
        self, e2e_page, api_anon, pyops_server, scriptlet_type, expected_navbar, set_factory):
        """manager-create-edit picks the editor matching the selected scriptlet type."""
        set_name = f"ce_{scriptlet_type}_set"
        new_name = f"fresh_{scriptlet_type}"
        set_factory(set_name)

        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()
        frame.locator('[data-testid="manager-create"]').click()
        frame.locator('[data-testid="manager-create-modal"]').wait_for(
            state="visible", timeout=5_000
        )
        frame.locator('[data-testid="manager-create-set"]').select_option(set_name)
        frame.locator('[data-testid="manager-create-name"]').fill(new_name)
        frame.locator('[data-testid="manager-create-type"]').select_option(scriptlet_type)

        with e2e_page.expect_popup() as popup_info:
            frame.locator('[data-testid="manager-create-edit"]').click()
        editor_page = popup_info.value
        editor_page.wait_for_load_state("load")
        editor_page.locator(f'[data-testid="{expected_navbar}"]').wait_for(
            state="visible", timeout=10_000
        )
        _wait_for_ace(editor_page)

        items = api_anon.get("/scriptlet/inventory/list").json()
        assert any(
            i["set"] == set_name and i["name"] == new_name and i["type"] == scriptlet_type
            for i in items
        ), f"scriptlet not listed: {items}"

        editor_page.close()

        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()
        row = frame.locator(f'[data-testid="scriptlet-row-{set_name}-{new_name}"]')
        if row.count() > 0 and row.is_visible():
            e2e_page.once("dialog", lambda d: d.accept())
            frame.locator(f'[data-testid="scriptlet-delete-{set_name}-{new_name}"]').click()
            row.wait_for(state="hidden", timeout=5_000)
        # cleanup via tmp_path teardown — set removal endpoint is gone
        pyops_server.assert_no_unexpected_errors()


class TestManagerCreateModal:
    """Paths on the manager create modal that are not part of Create & Edit."""

    def test_manager_create_cancel_closes_without_creating(
        self, e2e_page, api_anon, pyops_server, set_factory):
        """Cancel on the create modal closes it and creates no scriptlet."""
        set_name = "mcc_set"
        set_factory(set_name)

        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()
        frame.locator('[data-testid="manager-create"]').click()
        frame.locator('[data-testid="manager-create-modal"]').wait_for(
            state="visible", timeout=5_000
        )
        frame.locator('[data-testid="manager-create-set"]').select_option(set_name)
        frame.locator('[data-testid="manager-create-name"]').fill("never_made")
        frame.locator('[data-testid="manager-create-cancel"]').click()
        frame.locator('[data-testid="manager-create-modal"]').wait_for(
            state="hidden", timeout=5_000
        )

        items = api_anon.get("/scriptlet/inventory/list").json()
        assert not any(
            i["set"] == set_name and i["name"] == "never_made" for i in items
        )
        # cleanup via tmp_path teardown — set removal endpoint is gone
        pyops_server.assert_no_unexpected_errors()

    def test_manager_create_without_set_shows_inline_error(
        self, e2e_page, api_anon, pyops_server
    ):
        """Submitting with no set selected populates manager-create-error and stays open."""
        frame = _nav_manager(e2e_page)
        frame.locator('[data-testid="manager-refresh"]').click()
        frame.locator('[data-testid="manager-create"]').click()
        frame.locator('[data-testid="manager-create-modal"]').wait_for(
            state="visible", timeout=5_000
        )
        # Don't select a set — leave the default empty option.
        frame.locator('[data-testid="manager-create-name"]').fill("orphan")
        frame.locator('[data-testid="manager-create-submit"]').click()

        err = frame.locator('[data-testid="manager-create-error"]').inner_text()
        assert "set" in err.lower(), f"expected set error, got {err!r}"
        # Modal still visible.
        assert frame.locator('[data-testid="manager-create-modal"]').is_visible()

        # No scriptlet created server-side.
        items = api_anon.get("/scriptlet/inventory/list").json()
        assert not any(i["name"] == "orphan" for i in items)

        # Close cleanly.
        frame.locator('[data-testid="manager-create-cancel"]').click()
        frame.locator('[data-testid="manager-create-modal"]').wait_for(
            state="hidden", timeout=5_000
        )
        pyops_server.assert_no_unexpected_errors()
