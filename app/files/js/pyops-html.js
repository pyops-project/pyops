// Shared output encoding for the PyOps browser pages (window.pyopsHtml).
//
// Free text from the server — job ids (they carry the owner), params,
// details, error texts — goes through `esc` wherever a page puts it into
// markup, so it shows as text and never acts as markup:
//
//   esc(value)    HTML text and quoted attribute values (& < > " ' escaped;
//                 renders exactly what `${value}` would, but as text)

(function () {
    "use strict";

    const ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

    function esc(value) {
        return String(value).replace(/[&<>"']/g, (c) => ENTITIES[c]);
    }

    window.pyopsHtml = Object.freeze({ esc });
})();
