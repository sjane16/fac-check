(function () {
    "use strict";

    window.setSubmissionBusy = function (button, busy) {
        if (!button) return;
        button.disabled = busy;
        if (busy) button.setAttribute("aria-busy", "true");
        else button.removeAttribute("aria-busy");
    };

    document.addEventListener("submit", function (event) {
        const form = event.target;
        if (form.method.toLowerCase() !== "post" || event.defaultPrevented) return;
        if (form.dataset.submitting === "true") {
            event.preventDefault();
            return;
        }
        form.dataset.submitting = "true";
        // Keep the clicked button's value in the POST when disabling it.
        const submitter = event.submitter;
        if (submitter && submitter.name) {
            const value = document.createElement("input");
            value.type = "hidden";
            value.name = submitter.name;
            value.value = submitter.value;
            value.dataset.submissionValue = "true";
            form.appendChild(value);
        }
        Array.from(form.elements).forEach(function (button) {
            if (button.type !== "submit" || button.disabled) return;
            button.dataset.submissionLocked = "true";
            window.setSubmissionBusy(button, true);
        });
    });

    // Restore controls when the browser returns to a cached page.
    window.addEventListener("pageshow", function () {
        document.querySelectorAll('[data-submitting="true"]').forEach(function (form) {
            delete form.dataset.submitting;
        });
        document.querySelectorAll('[data-submission-locked="true"]').forEach(function (button) {
            delete button.dataset.submissionLocked;
            window.setSubmissionBusy(button, false);
        });
        document.querySelectorAll('[data-submission-value="true"]').forEach(function (input) {
            input.remove();
        });
    });
})();
