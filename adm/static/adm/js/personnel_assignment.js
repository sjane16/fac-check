(function () {
    "use strict";

    const dialog = document.getElementById("personnel-dialog");
    if (!dialog) return;
    const form = document.getElementById("personnel-assignment-form");
    const summary = document.getElementById("med-personnel-summary");
    const choices = Array.from(form.querySelectorAll('[name="personnel_names"]'));
    const others = document.getElementById("include-other-personnel");
    const otherWrapper = document.getElementById("other-personnel-wrapper");
    const rows = document.getElementById("other-personnel-rows");
    const error = document.getElementById("personnel-save-error");
    const submit = document.getElementById("personnel-save-button");
    const overallSave = document.querySelector('#report-save-form button[type="submit"]');
    let savedSlugs = choices.filter(input => input.checked).map(input => input.value);
    let savedOthers = JSON.parse(document.getElementById("saved-other-personnel").textContent).map(item => item.name);
    let busy = false;

    const personnelFlag = document.getElementById("pending-awaiting-personnel");
    const LOCKED_TITLE = 'Check "With Assigned Personnel" first';

    function syncPersonnelButtons() {
        if (!personnelFlag) return;
        const locked = !personnelFlag.checked;
        document.querySelectorAll("[data-open-personnel]").forEach(button => {
            if (button.dataset.originalTitle === undefined) button.dataset.originalTitle = button.title;
            button.disabled = locked;
            button.title = locked ? LOCKED_TITLE : button.dataset.originalTitle;
        });
    }

    if (personnelFlag) personnelFlag.addEventListener("change", syncPersonnelButtons);
    syncPersonnelButtons();

    function addOtherRow(name = "") {
        const row = document.createElement("div");
        row.className = "flex items-center gap-2";
        const input = document.createElement("input");
        input.type = "text";
        input.name = "other_personnel";
        input.value = name;
        input.maxLength = 100;
        input.placeholder = "Enter external or outsourced personnel";
        input.setAttribute("aria-label", "Other personnel name");
        input.className = "w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500";
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "✕";
        remove.className = "rounded-lg p-2 text-gray-500 hover:text-red-600";
        remove.setAttribute("aria-label", "Remove other personnel name");
        remove.addEventListener("click", function () {
            if (rows.children.length === 1) input.value = "";
            else row.remove();
        });
        row.append(input, remove);
        rows.appendChild(row);
        return input;
    }

    document.addEventListener("click", function (event) {
        const trigger = event.target.closest("[data-open-personnel]");
        if (!trigger || trigger.disabled) return;
        choices.forEach(input => { input.checked = savedSlugs.includes(input.value); });
        others.checked = savedOthers.length > 0;
        otherWrapper.hidden = !others.checked;
        rows.replaceChildren();
        (savedOthers.length ? savedOthers : [""]).forEach(addOtherRow);
        error.hidden = true;
        dialog.showModal();
        document.body.style.overflow = "hidden";
    });
    dialog.querySelectorAll("[data-close-personnel]").forEach(button => {
        button.addEventListener("click", () => { if (!busy) dialog.close(); });
    });
    dialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
    dialog.addEventListener("close", () => { document.body.style.overflow = ""; });
    others.addEventListener("change", () => { otherWrapper.hidden = !others.checked; });
    document.getElementById("add-other-personnel").addEventListener("click", () => addOtherRow().focus());

    form.addEventListener("submit", async function (event) {
        event.preventDefault();
        if (busy) return;
        const payload = new FormData(form);
        const overallWasDisabled = overallSave && overallSave.disabled;
        busy = true;
        submit.disabled = true;
        submit.textContent = "Saving...";
        if (overallSave) overallSave.disabled = true;
        error.hidden = true;
        // Keep the submitted selection stable until the server responds.
        form.querySelectorAll("input, button").forEach(control => { control.disabled = true; });
        try {
            const response = await fetch(form.action, {
                method: "POST",
                credentials: "same-origin",
                headers: { "X-Requested-With": "XMLHttpRequest" },
                body: payload,
            });
            if (response.redirected) throw new Error("Your session changed. Refresh the page and try again.");
            const data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.error || "Couldn't save personnel. Please try again.");
            savedSlugs = data.selected_slugs;
            savedOthers = data.other_names;
            summary.innerHTML = data.html;
            syncPersonnelButtons();
            dialog.close();
            summary.querySelector("[data-open-personnel]").focus();
        } catch (exception) {
            error.textContent = exception.message || "Couldn't reach the server. Please try again.";
            error.hidden = false;
        } finally {
            busy = false;
            form.querySelectorAll("input, button").forEach(control => { control.disabled = false; });
            submit.textContent = "Submit";
            if (overallSave) overallSave.disabled = overallWasDisabled;
        }
    });
})();
