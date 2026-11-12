// POI admin: show only the icon fields the chosen icon kind uses.
"use strict";
window.addEventListener("load", function () {
    var kind = document.getElementById("id_icon_kind");
    if (!kind) return;
    var fields = {
        icon_argb: "colored_circle",
        icon_file: "bundled",
        icon_tint: "bundled",
    };
    function update() {
        Object.keys(fields).forEach(function (name) {
            var row = document.querySelector(".form-row.field-" + name);
            if (row) row.style.display = kind.value === fields[name] ? "" : "none";
        });
    }
    kind.addEventListener("change", update);
    update();
});
